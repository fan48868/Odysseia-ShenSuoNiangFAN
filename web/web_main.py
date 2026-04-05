import io
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta

import psutil
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

try:
    import docker
except ImportError:
    docker = None

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

dotenv_path = os.path.join(project_root, ".env")
load_dotenv(dotenv_path=dotenv_path, override=True, encoding="utf-8")

from web.log import run_daily_cleanup_if_needed

template_dir = current_dir
static_dir = os.path.join(template_dir, "root")
login_page_path = os.path.join(static_dir, "html", "login.html")
main_page_path = os.path.join(static_dir, "html", "main.html")

web_app = FastAPI()
web_app.mount("/static", StaticFiles(directory=static_dir), name="static")

logger = logging.getLogger("webui")
logger.setLevel(logging.DEBUG)
logger.propagate = False

log_stream = io.StringIO()
stream_handler = logging.StreamHandler(log_stream)
stream_handler.setLevel(logging.DEBUG)
stream_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
if not logger.handlers:
    logger.addHandler(stream_handler)

LOG_DIR = os.path.join(project_root, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

multiline_token = os.getenv("WEBUI_ADMIN_TOKEN")
TOKEN_ARRAY = []
if multiline_token:
    TOKEN_ARRAY = [line.strip() for line in multiline_token.splitlines() if line.strip()]
else:
    logger.error("TOKEN Error: TOKEN未设置")

BOT_CONTAINER_NAME = os.getenv("BOT_CONTAINER_NAME", "Odysseia_Guidance")
last_heartbeat_time = datetime.utcnow()
heartbeat_tolerance_seconds = 5.0
SYSTEM_STATS_HISTORY = deque(maxlen=1440)


def is_logged_in(request: Request) -> bool:
    token = request.cookies.get("webui_token", "")
    return token in TOKEN_ARRAY

def unauthorized_response(api: bool):
    if api:
        return JSONResponse(
            {"status": "error", "message": "Authentication required"},
            status_code=401,
        )
    return RedirectResponse(url="/", status_code=302)


def collect_system_stats():
    while True:
        try:
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/").percent
            net_io = psutil.net_io_counters()

            SYSTEM_STATS_HISTORY.append(
                {
                    "timestamp": datetime.utcnow(),
                    "cpu_usage": cpu,
                    "ram_usage_percent": mem.percent,
                    "ram_usage_mb": mem.used / (1024**2),
                    "disk_usage_percent": disk,
                    "net_sent": net_io.bytes_sent,
                    "net_recv": net_io.bytes_recv,
                }
            )
        except Exception as exc:
            logger.error(f"Error collecting system stats: {exc}")
        time.sleep(60)


@web_app.post("/api/log")
async def receive_log(request: Request):
    global last_heartbeat_time
    last_heartbeat_time = datetime.utcnow()
    run_daily_cleanup_if_needed(LOG_DIR, logger)
    today_utc_str = last_heartbeat_time.strftime("%Y-%m-%d")
    log_file_path = os.path.join(LOG_DIR, f"{today_utc_str}.txt")

    data = await request.json()
    if data and data.get("logs"):
        try:
            with open(log_file_path, "a", encoding="utf-8") as handle:
                for log_entry in data["logs"]:
                    handle.write(log_entry + "\n")
        except Exception as exc:
            logger.error(f"Failed to write logs: {exc}")
            return PlainTextResponse("Error writing logs", status_code=500)

    return PlainTextResponse("OK")


@web_app.get("/")
def config_page(request: Request):
    if is_logged_in(request):
        return RedirectResponse(url="/main", status_code=302)
    return FileResponse(login_page_path)


@web_app.post("/login")
async def login(request: Request):
    data = await request.json()
    token = data.get("token")
    if token in TOKEN_ARRAY:
        response = JSONResponse({"message": "登录成功"})
        response.set_cookie(
            key="webui_token",
            value=token,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response
    return JSONResponse({"message": "无效的用户名或密码"}, status_code=401)


@web_app.get("/main")
def main_page(request: Request):
    if not is_logged_in(request):
        return unauthorized_response(api=False)
    return FileResponse(main_page_path)


@web_app.get("/logout")
def logout(request: Request):
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie("webui_token", path="/")
    return response


@web_app.get("/api/status")
def get_status():
    global last_heartbeat_time
    logger.info("API call to /api/status received.")
    time_diff = (datetime.utcnow() - last_heartbeat_time).total_seconds()
    status = "RUNNING" if time_diff < heartbeat_tolerance_seconds else "DOWN"
    return JSONResponse({"status": status})


@web_app.post("/api/bot/restart")
def restart_bot(request: Request):
    if not is_logged_in(request):
        return unauthorized_response(api=True)
    try:
        if docker is None:
            raise RuntimeError("Python package 'docker' is not installed.")
        client = docker.from_env()
        bot_container = client.containers.get(BOT_CONTAINER_NAME)
        bot_container.restart()
        return JSONResponse(
            {
                "status": "success",
                "message": f"Container '{BOT_CONTAINER_NAME}' is restarting.",
            }
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "message": str(exc)},
            status_code=500,
        )


@web_app.post("/api/bot/shutdown")
def shutdown_bot(request: Request):
    if not is_logged_in(request):
        return unauthorized_response(api=True)
    try:
        if docker is None:
            raise RuntimeError("Python package 'docker' is not installed.")
        client = docker.from_env()
        bot_container = client.containers.get(BOT_CONTAINER_NAME)
        bot_container.stop()
        return JSONResponse(
            {
                "status": "success",
                "message": f"Container '{BOT_CONTAINER_NAME}' is stopping.",
            }
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "message": str(exc)},
            status_code=500,
        )


@web_app.get("/api/logs")
def get_logs(request: Request, date: str | None = None):
    if not is_logged_in(request):
        return unauthorized_response(api=True)
    date_str = date or datetime.utcnow().strftime("%Y-%m-%d")
    log_file_path = os.path.join(LOG_DIR, f"{date_str}.txt")
    try:
        with open(log_file_path, "r", encoding="utf-8") as handle:
            content = handle.read()
        return JSONResponse({"logs": content, "date": date_str})
    except FileNotFoundError:
        logger.warning(f"Log file for {date_str} not found.")
        return JSONResponse({"logs": f"Log file for {date_str} not found.", "date": date_str})
    except Exception as exc:
        logger.error(f"Error in get_logs: {exc}")
        return JSONResponse(
            {"status": "error", "message": "Internal server error"},
            status_code=500,
        )


@web_app.get("/api/webui-logs")
def get_webui_logs(request: Request):
    if not is_logged_in(request):
        return unauthorized_response(api=True)
    try:
        return JSONResponse({"logs": log_stream.getvalue()})
    except Exception as exc:
        logger.error(f"Error fetching webui logs from stream: {exc}")
        return JSONResponse(
            {"status": "error", "message": str(exc)},
            status_code=500,
        )


@web_app.get("/api/system-info")
def system_info(request: Request):
    if not is_logged_in(request):
        return unauthorized_response(api=True)
    try:
        cpu_usage_current = psutil.cpu_percent(interval=0.1)
        mem_current = psutil.virtual_memory()
        ram_usage_current_percent = mem_current.percent
        ram_usage_current_mb = mem_current.used / (1024**2)
        ram_total_mb = mem_current.total / (1024**2)
        disk_usage_details = psutil.disk_usage("/")
        net_io_current = psutil.net_io_counters()

        now = datetime.utcnow()
        past_24_hours = now - timedelta(hours=24)
        relevant_stats = [
            stat
            for stat in list(SYSTEM_STATS_HISTORY)
            if stat["timestamp"] > past_24_hours
        ]

        if relevant_stats:
            cpu_values = [stat["cpu_usage"] for stat in relevant_stats]
            ram_percent_values = [stat["ram_usage_percent"] for stat in relevant_stats]
            ram_mb_values = [stat["ram_usage_mb"] for stat in relevant_stats]
            cpu_avg_24h = sum(cpu_values) / len(cpu_values)
            cpu_peak_24h = max(cpu_values)
            ram_avg_24h_percent = sum(ram_percent_values) / len(ram_percent_values)
            ram_peak_24h_percent = max(ram_percent_values)
            ram_avg_24h_mb = sum(ram_mb_values) / len(ram_mb_values)
            ram_peak_24h_mb = max(ram_mb_values)
        else:
            cpu_avg_24h = cpu_peak_24h = 0
            ram_avg_24h_percent = ram_peak_24h_percent = 0
            ram_avg_24h_mb = ram_peak_24h_mb = 0

        return JSONResponse(
            {
                "current": {
                    "cpu_usage": cpu_usage_current,
                    "ram_usage_percent": ram_usage_current_percent,
                    "ram_usage_mb": ram_usage_current_mb,
                    "ram_total_mb": ram_total_mb,
                    "disk_usage": {
                        "total": f"{(disk_usage_details.total / (1024**3)):.2f} GB",
                        "used": f"{(disk_usage_details.used / (1024**3)):.2f} GB",
                        "free": f"{(disk_usage_details.free / (1024**3)):.2f} GB",
                        "percent": disk_usage_details.percent,
                    },
                    "net_io": {
                        "bytes_sent": net_io_current.bytes_sent,
                        "bytes_recv": net_io_current.bytes_recv,
                    },
                },
                "stats_24h": {
                    "cpu_avg": cpu_avg_24h,
                    "cpu_peak": cpu_peak_24h,
                    "ram_avg_percent": ram_avg_24h_percent,
                    "ram_peak_percent": ram_peak_24h_percent,
                    "ram_avg_mb": ram_avg_24h_mb,
                    "ram_peak_mb": ram_peak_24h_mb,
                    "sample_count": len(relevant_stats),
                },
            }
        )
    except Exception as exc:
        logger.error(f"Error in system_info: {exc}")
        return JSONResponse(
            {"status": "error", "message": "Internal server error"},
            status_code=500,
        )


logger.info("Initializing and starting background threads.")
stats_thread = threading.Thread(target=collect_system_stats, daemon=True)
stats_thread.start()
