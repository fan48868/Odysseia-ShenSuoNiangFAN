# -*- coding: utf-8 -*-

import os
import shutil
import logging
import subprocess
from datetime import datetime

log = logging.getLogger(__name__)

# --- 路径配置 ---
# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 源数据目录
DATA_DIR = os.path.join(BASE_DIR, "data")
# 备份目标目录
BACKUP_DIR = os.path.join(BASE_DIR, "src", "backup")
# 数据库文件扩展名
DB_EXTENSIONS = (".db", ".sqlite3")
PARADEDB_BACKUP_PREFIX = "odysseia_paradedb_"
PARADEDB_BACKUP_SUFFIX = ".dump"


def _get_backup_output_mount_path() -> str:
    """返回容器内用于接收外部备份挂载的路径。"""
    return os.getenv("BACKUP_OUTPUT_MOUNT_PATH", "/backup-output")


def _is_managed_backup_file(filename: str) -> bool:
    """判断文件是否属于本模块管理的备份文件。"""
    is_sqlite_backup = ".bak." in filename
    is_paradedb_backup = (
        filename.startswith(PARADEDB_BACKUP_PREFIX)
        and filename.endswith(PARADEDB_BACKUP_SUFFIX)
    )
    return is_sqlite_backup or is_paradedb_backup


def _cleanup_old_backups(target_dir: str, backup_date_format: str):
    """清理目标目录中的过期备份，仅保留当天日期的备份文件。"""
    if not os.path.isdir(target_dir):
        return

    for filename in os.listdir(target_dir):
        if not _is_managed_backup_file(filename):
            continue
        if backup_date_format in filename:
            continue

        file_to_delete = os.path.join(target_dir, filename)
        try:
            os.remove(file_to_delete)
            log.info(f"已删除过期备份文件: '{file_to_delete}'")
        except Exception as e:
            log.error(f"删除过期备份 '{file_to_delete}' 失败: {e}", exc_info=True)


def _sync_backups_to_output_dir(backup_date_format: str):
    """
    可选地将备份复制到 BACKUP_OUTPUT_DIR 对应目录。

    - 若未配置 BACKUP_OUTPUT_DIR：跳过
    - 若配置了：复制所有“受管理的备份文件”到目标目录，并执行同样的旧备份清理
    """
    backup_output_dir = os.getenv("BACKUP_OUTPUT_DIR", "").strip()
    if not backup_output_dir:
        return

    # 容器模式下优先使用挂载点路径；否则回退使用 BACKUP_OUTPUT_DIR 本身。
    mount_path = _get_backup_output_mount_path()
    target_dir = mount_path if os.path.isdir(mount_path) else backup_output_dir

    # 支持相对路径（相对项目根目录）
    if not os.path.isabs(target_dir):
        target_dir = os.path.join(BASE_DIR, target_dir)

    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        log.error(f"创建备份输出目录失败 '{target_dir}': {e}", exc_info=True)
        return

    # 复制当前备份目录中的受管理备份文件
    for filename in os.listdir(BACKUP_DIR):
        if not _is_managed_backup_file(filename):
            continue

        src_path = os.path.join(BACKUP_DIR, filename)
        dst_path = os.path.join(target_dir, filename)
        try:
            shutil.copy2(src_path, dst_path)
            log.info(f"已同步备份文件到输出目录: '{dst_path}'")
        except Exception as e:
            log.error(f"同步备份文件失败 '{filename}' -> '{target_dir}': {e}", exc_info=True)

    _cleanup_old_backups(target_dir, backup_date_format)


def _backup_paradedb(backup_date_format: str):
    """
    备份 ParadeDB（PostgreSQL）到 src/backup 目录。

    产物文件名：odysseia_paradedb_YYYYMMDD.dump（pg_dump 自定义格式）。
    """
    db_host = os.getenv("DB_HOST", "db")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "odysseia_db")
    db_user = os.getenv("POSTGRES_USER", "user")
    db_password = os.getenv("POSTGRES_PASSWORD", "")

    backup_filename = (
        f"{PARADEDB_BACKUP_PREFIX}{backup_date_format}{PARADEDB_BACKUP_SUFFIX}"
    )
    destination_path = os.path.join(BACKUP_DIR, backup_filename)

    env = os.environ.copy()
    if db_password:
        env["PGPASSWORD"] = db_password

    # 方案 1：直接使用 pg_dump（适用于主机或应用容器中已安装 pg_dump）
    direct_pg_dump_cmd = [
        "pg_dump",
        "-h",
        db_host,
        "-p",
        str(db_port),
        "-U",
        db_user,
        "-d",
        db_name,
        "-F",
        "c",
        "-f",
        destination_path,
    ]

    try:
        subprocess.run(
            direct_pg_dump_cmd,
            check=True,
            env=env,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
        )
        log.info(f"成功备份 ParadeDB 到 '{destination_path}'（通过 pg_dump）")
        return
    except FileNotFoundError:
        log.warning("未找到 pg_dump，尝试使用 docker compose exec 进行备份。")
    except subprocess.CalledProcessError as e:
        log.warning(
            "通过 pg_dump 备份 ParadeDB 失败，尝试 docker compose 方案。"
            f" stderr={e.stderr.strip() if e.stderr else 'N/A'}"
        )

    # 方案 2：回退到 docker compose（在 db 容器内执行 pg_dump，输出重定向到本地文件）
    docker_pg_dump_cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "db",
        "pg_dump",
        "-U",
        db_user,
        "-d",
        db_name,
        "-F",
        "c",
    ]

    try:
        with open(destination_path, "wb") as dump_file:
            subprocess.run(
                docker_pg_dump_cmd,
                check=True,
                env=env,
                cwd=BASE_DIR,
                stdout=dump_file,
                stderr=subprocess.PIPE,
            )
        log.info(
            f"成功备份 ParadeDB 到 '{destination_path}'（通过 docker compose exec）"
        )
    except FileNotFoundError as e:
        log.error(f"ParadeDB 备份失败：未找到命令 {e.filename}", exc_info=True)
    except subprocess.CalledProcessError as e:
        stderr_text = (
            e.stderr.decode(errors="ignore").strip() if e.stderr else "N/A"
        )
        log.error(f"ParadeDB 备份失败：{stderr_text}", exc_info=True)


def backup_databases():
    """
    将所有数据库文件备份到 src/backup 目录，并清理旧备份。
    只保留昨天的备份。
    """
    # 确保备份目录存在
    os.makedirs(BACKUP_DIR, exist_ok=True)

    today = datetime.now()
    # 今天备份文件的日期格式
    backup_date_format = today.strftime("%Y%m%d")

    log.info("开始执行数据库备份任务...")

    if not os.path.isdir(DATA_DIR):
        log.warning(f"数据目录 {DATA_DIR} 不存在，跳过备份。")
        return

    # --- 步骤 1: 创建今天的备份 ---
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(DB_EXTENSIONS):
            continue

        source_path = os.path.join(DATA_DIR, filename)
        backup_filename = f"{filename}.bak.{backup_date_format}"
        destination_path = os.path.join(BACKUP_DIR, backup_filename)

        try:
            shutil.copy2(source_path, destination_path)
            log.info(f"成功备份 '{filename}' 到 '{destination_path}'")
        except Exception as e:
            log.error(f"备份 '{filename}' 失败: {e}", exc_info=True)

    # --- 步骤 1.5: 备份 ParadeDB ---
    _backup_paradedb(backup_date_format)

    # --- 步骤 2: 清理过期的本地备份 ---
    log.info("开始清理过期备份...")
    _cleanup_old_backups(BACKUP_DIR, backup_date_format)

    # --- 步骤 3: 可选同步到外部备份目录 ---
    _sync_backups_to_output_dir(backup_date_format)

    log.info("数据库备份与清理任务完成。")


if __name__ == "__main__":
    # 用于直接运行测试
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    backup_databases()
