# -*- coding: utf-8 -*-

import os
import shutil
import logging
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


def _get_backup_output_mount_path() -> str:
    """返回容器内用于接收外部备份挂载的路径。"""
    return os.getenv("BACKUP_OUTPUT_MOUNT_PATH", "/backup-output")


def _is_managed_backup_file(filename: str) -> bool:
    """判断文件是否属于本模块管理的备份文件。"""
    return ".bak." in filename


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


def backup_databases():
    """
    将所有数据库文件备份到 src/backup 目录，并清理旧备份。
    只保留当天的备份。
    """
    # 确保备份目录存在
    os.makedirs(BACKUP_DIR, exist_ok=True)

    today = datetime.now()
    # 今天备份文件的日期格式
    backup_date_format = today.strftime("%Y%m%d")

    log.info("开始执行数据库备份任务...")

    # --- 步骤 1: 创建今天的备份 ---
    if not os.path.isdir(DATA_DIR):
        log.warning(f"数据目录 {DATA_DIR} 不存在，跳过本地 SQLite 备份。")
    else:
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
