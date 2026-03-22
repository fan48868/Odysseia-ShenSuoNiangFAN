import os
import sys
import logging
import secrets
import docker
import io
import psutil
from datetime import datetime, timedelta
import threading
import time
from collections import deque
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
# --- 路径和环境变量设置 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path=dotenv_path, override=True, encoding="utf-8")

from web.log import audit_logs, run_daily_cleanup_if_needed, login_required

# --- Flask 应用初始化 ---
template_dir = os.path.abspath(os.path.dirname(__file__))
web_app = Flask(__name__, template_folder=template_dir, static_folder=os.path.join(template_dir, 'root'))
web_app.secret_key = secrets.token_hex(16)

# --- WebUI 日志流 ---
log_stream = io.StringIO()
stream_handler = logging.StreamHandler(log_stream)
stream_handler.setLevel(logging.DEBUG) # 设置为 DEBUG 级别
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
stream_handler.setFormatter(formatter)
web_app.logger.addHandler(stream_handler)
web_app.logger.setLevel(logging.DEBUG)

# --- 日志路径等等初始化 ---
DATA_DIR = os.path.join(project_root, 'admin')
LOG_DIR = os.path.join(project_root, 'logs')
ADMIN_LOG_FILE = os.path.join(DATA_DIR, 'admin.txt')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# --- 将配置加载到 Flask App ---
web_app.config['LOG_DIR'] = LOG_DIR
web_app.config['ADMIN_LOG_FILE'] = ADMIN_LOG_FILE

# --- 加载管理员 TOKEN ---
multiline_token = os.getenv("WEBUI_ADMIN_TOKEN")
token_array = []
if multiline_token:
    lines = [line.strip() for line in multiline_token.splitlines() if line.strip()]
    for item in lines:
        try:
            token_array.append(item)
        except ValueError:
            logging.error("TOKEN Error: 请联系管理员 ")
else:
    logging.error("TOKEN Error: TOKEN未设置 ")
web_app.config['TOKEN_ARRAY'] = token_array

BOT_CONTAINER_NAME = os.getenv("BOT_CONTAINER_NAME", "Odysseia_Guidance")
 
 # --- webui心跳 ---
last_heartbeat_time = datetime.utcnow()
heartbeat_tolerance_seconds = 5.0 # 心跳包最大间隔时间

# --- 系统信息历史数据 ---
SYSTEM_STATS_HISTORY = deque(maxlen=1440) # 60 minutes * 24 hours = 1440 samples

def collect_system_stats():
    """后台线程函数，定期收集系统统计信息"""
    while True:
        try:
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/').percent
            net_io = psutil.net_io_counters()
            
            SYSTEM_STATS_HISTORY.append({
                "timestamp": datetime.utcnow(),
                "cpu_usage": cpu,
                "ram_usage_percent": mem.percent,
                "ram_usage_mb": mem.used / (1024**2),
                "disk_usage_percent": disk,
                "net_sent": net_io.bytes_sent,
                "net_recv": net_io.bytes_recv
            })
        except Exception as e:
            web_app.logger.error(f"Error collecting system stats: {e}")
        time.sleep(60)

# --- 路由定义 ---
@web_app.route('/api/log', methods=['POST']) #心跳包请求路径
def receive_log():
    global last_heartbeat_time
    last_heartbeat_time = datetime.utcnow()
    run_daily_cleanup_if_needed()
    today_utc_str = last_heartbeat_time.strftime('%Y-%m-%d')
    log_file_path = os.path.join(web_app.config['LOG_DIR'], f"{today_utc_str}.txt")

    data = request.get_json()
    if data and data.get('logs'):
        try:
            with open(log_file_path,'a') as f:
                for log_entry in data['logs']:
                    f.write(log_entry+'\n')
        except Exception as e:
            web_app.logger.error(f"Failed to write logs:{e}")
            return "Error writing logs",500
    return "OK",200

@web_app.route('/')
def config_page():
    return render_template('root/html/login.html')

@web_app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    token = data.get('token')
    if token in web_app.config.get('TOKEN_ARRAY', []):
        session['logged_in'] = True
        session['token'] = token
        return jsonify({'message': '登录成功'}), 200
    else:
        return jsonify({'message': '无效的用户名或密码'}), 401

@web_app.route('/main')
@login_required
def main_page():
    if not session.get('logged_in'):
        return redirect(url_for('config_page'))
    return render_template('root/html/main.html')

@web_app.route('/logout')
def logout():
    audit_logs("User logout")
    session.clear()
    return redirect(url_for('config_page'))

@web_app.route('/api/status', methods=['GET'])
def get_status():
    web_app.logger.info("API call to /api/status received.")
    global last_heartbeat_time
    time_diff = (datetime.utcnow() - last_heartbeat_time).total_seconds()
    if time_diff < heartbeat_tolerance_seconds:
        return jsonify({"status": "RUNNING"})
    else:
        return jsonify({"status": "DOWN"})

@web_app.route('/api/bot/restart',methods=['POST']) #重启
@login_required
def restart_bot():
    try:
        client = docker.from_env()
        container_name = BOT_CONTAINER_NAME
        bot_container = client.containers.get(container_name)
        bot_container.restart()
        audit_logs(f"Restarted bot container '{container_name}'")
        return jsonify({"status": "success", "message": f"Container '{container_name}' is restarting."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@web_app.route('/api/bot/shutdown', methods=['POST'])
@login_required
def shutdown_bot():
    try:
        client = docker.from_env()
        container_name = BOT_CONTAINER_NAME
        bot_container = client.containers.get(container_name)
        bot_container.stop()
        audit_logs(f"Stopped bot container '{container_name}'")
        return jsonify({"status": "success", "message": f"Container '{container_name}' is stopping."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@web_app.route('/api/logs',methods=['GET'])
@login_required
def get_logs():
    #web_app.logger.info("API call to /api/logs received.")
    date_str = request.args.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
    log_file_path = os.path.join(web_app.config['LOG_DIR'], f"{date_str}.txt")
    try:
        with open(log_file_path, 'r') as f:
            content = f.read()
        #web_app.logger.info(f"Successfully read log file for date: {date_str}")
        return jsonify({"logs": content, "date": date_str})
    except FileNotFoundError:
        web_app.logger.warning(f"Log file for {date_str} not found.")
        return jsonify({"logs": f"Log file for {date_str} not found.", "date": date_str})
    except Exception as e:
        web_app.logger.error(f"Error in get_logs: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

@web_app.route('/api/webui-logs', methods=['GET'])
@login_required
def get_webui_logs():
    try:
        log_content = log_stream.getvalue()
        return jsonify({"logs": log_content})
    except Exception as e:
        web_app.logger.error(f"Error fetching webui logs from stream: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@web_app.route('/api/system-info', methods=['GET'])
@login_required
def system_info():
    #web_app.logger.info("API call to /api/system-info received.")
    try:
        # --- 实时数据 ---
        cpu_usage_current = psutil.cpu_percent(interval=0.1)
        mem_current = psutil.virtual_memory()
        ram_usage_current_percent = mem_current.percent
        ram_usage_current_mb = mem_current.used / (1024**2)
        ram_total_mb = mem_current.total / (1024**2)
        disk_usage_details = psutil.disk_usage('/')
        net_io_current = psutil.net_io_counters()

        # --- 历史数据计算 ---
        now = datetime.utcnow()
        past_24_hours = now - timedelta(hours=24)
        
        relevant_stats = [s for s in list(SYSTEM_STATS_HISTORY) if s['timestamp'] > past_24_hours]
        
        if relevant_stats:
            # CPU
            cpu_values = [s['cpu_usage'] for s in relevant_stats]
            cpu_avg_24h = sum(cpu_values) / len(cpu_values)
            cpu_peak_24h = max(cpu_values)
            # RAM
            ram_percent_values = [s['ram_usage_percent'] for s in relevant_stats]
            ram_avg_24h_percent = sum(ram_percent_values) / len(ram_percent_values)
            ram_peak_24h_percent = max(ram_percent_values)
            
            ram_mb_values = [s['ram_usage_mb'] for s in relevant_stats]
            ram_avg_24h_mb = sum(ram_mb_values) / len(ram_mb_values)
            ram_peak_24h_mb = max(ram_mb_values)
        else:
            cpu_avg_24h = cpu_peak_24h = 0
            ram_avg_24h_percent = ram_peak_24h_percent = 0
            ram_avg_24h_mb = ram_peak_24h_mb = 0

        return jsonify({
            "current": {
                "cpu_usage": cpu_usage_current,
                "ram_usage_percent": ram_usage_current_percent,
                "ram_usage_mb": ram_usage_current_mb,
                "ram_total_mb": ram_total_mb,
                "disk_usage": {
                    "total": f"{(disk_usage_details.total / (1024**3)):.2f} GB",
                    "used": f"{(disk_usage_details.used / (1024**3)):.2f} GB",
                    "free": f"{(disk_usage_details.free / (1024**3)):.2f} GB",
                    "percent": disk_usage_details.percent
                },
                "net_io": {
                    "bytes_sent": net_io_current.bytes_sent,
                    "bytes_recv": net_io_current.bytes_recv
                }
            },
            "stats_24h": {
                "cpu_avg": cpu_avg_24h,
                "cpu_peak": cpu_peak_24h,
                "ram_avg_percent": ram_avg_24h_percent,
                "ram_peak_percent": ram_peak_24h_percent,
                "ram_avg_mb": ram_avg_24h_mb,
                "ram_peak_mb": ram_peak_24h_mb,
                "sample_count": len(relevant_stats)
            }
        })
    except Exception as e:
        web_app.logger.error(f"Error in system_info: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

web_app.logger.info("Initializing and starting background threads.")
stats_thread = threading.Thread(target=collect_system_stats, daemon=True)
stats_thread.start()

# --- 启动入口 ---
if __name__ == '__main__':
    web_app.logger.info("Flask app is starting.")
    # 启动后台线程
    web_app.run(host='0.0.0.0', port=80, debug=True)
