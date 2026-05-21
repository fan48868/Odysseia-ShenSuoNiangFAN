# -*- coding: utf-8 -*-

import os
import sys
import logging
import sqlite3
from typing import Set

# -- 配置项目根目录 --
# 这使得脚本可以从任何地方运行，同时能正确导入 src 下的模块
# 获取当前脚本文件所在目录的绝对路径
current_script_dir = os.path.dirname(os.path.abspath(__file__))
# 获取项目根目录（假设 an_odysseia_guidance 是根目录）
project_root = os.path.abspath(os.path.join(current_script_dir, '..'))
# 将项目根目录添加到 sys.path
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# --------------------

from src import config
from src.chat.services.vector_db_service import vector_db_service

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
log = logging.getLogger(__name__)
# -----------------

def get_valid_sqlite_ids() -> Set[str]:
    """
    从 world_book.sqlite3 数据库中获取所有有效的条目ID。
    - community_members 的 ID 直接使用。
    - general_knowledge 的 ID 会被加上 'db_' 前缀。
    """
    db_path = os.path.join(config.DATA_DIR, 'world_book.sqlite3')
    if not os.path.exists(db_path):
        log.error(f"主数据库文件未找到: {db_path}")
        return set()

    valid_ids = set()
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 1. 获取 community_members 的 ID
        cursor.execute("SELECT id FROM community_members")
        rows = cursor.fetchall()
        for row in rows:
            valid_ids.add(row[0])
        log.info(f"从 'community_members' 表中加载了 {len(rows)} 个有效ID。")

        # 2. 获取 general_knowledge 的 ID 并添加前缀
        cursor.execute("SELECT id FROM general_knowledge")
        rows = cursor.fetchall()
        for row in rows:
            valid_ids.add(f"db_{row[0]}")
        log.info(f"从 'general_knowledge' 表中加载了 {len(rows)} 个有效ID (已添加 'db_' 前缀)。")

        conn.close()
    except Exception as e:
        log.error(f"从 SQLite 数据库获取ID时出错: {e}", exc_info=True)

    return valid_ids

def main():
    """主执行函数"""
    log.info("--- 开始清理孤儿向量数据 ---")

    # 检查向量数据库是否可用
    if not vector_db_service.is_available():
        log.error("向量数据库服务不可用，无法继续。请检查 ChromaDB 配置和连接。")
        return

    # 1. 从主数据库获取所有有效ID
    valid_base_ids = get_valid_sqlite_ids()
    if not valid_base_ids:
        log.warning("未能从主数据库中获取任何有效ID，清理操作已中止以防意外删除。")
        return
    log.info(f"总共找到 {len(valid_base_ids)} 个有效的基准ID。")

    # 2. 从向量数据库获取所有文档块ID
    try:
        all_vector_ids = vector_db_service.get_all_ids()
        if not all_vector_ids:
            log.info("向量数据库中没有任何文档，无需清理。")
            return
        log.info(f"从向量数据库中检索到 {len(all_vector_ids)} 个文档块ID。")
    except Exception as e:
        log.error(f"从向量数据库获取所有ID时出错: {e}", exc_info=True)
        return

    # 3. 识别孤儿ID
    orphaned_ids = []
    for vec_id in all_vector_ids:
        # 文档块ID的格式通常是 'base_id:chunk_index'
        base_id = vec_id.split(':')[0]
        if base_id not in valid_base_ids:
            orphaned_ids.append(vec_id)

    # 4. 交互式选择与删除
    if not orphaned_ids:
        log.info("恭喜！没有发现任何孤儿向量数据，数据库状态一致。")
    else:
        log.warning(f"发现 {len(orphaned_ids)} 个孤儿向量ID。")
        print("\n--- 请选择要删除的孤儿ID ---")
        for i, orphan_id in enumerate(orphaned_ids):
            print(f"  {i + 1}: {orphan_id}")
        print("---------------------------------")
        print("输入提示:")
        print("  - 输入ID前的数字来选择。")
        print("  - 删除多个: 使用逗号分隔 (例如: 1, 3, 5)")
        print("  - 删除连续范围: 使用连字符 (例如: 1-5)")
        print("  - 混合使用: 1, 3-5, 8")
        print("  - 删除全部: 输入 'ALL'")
        print("  - 取消操作: 直接按 Enter 键")
        print("---------------------------------")

        try:
            user_input = input("请输入要删除的ID编号: ").strip()

            if not user_input:
                log.info("操作已取消，没有删除任何数据。")
                return

            ids_to_delete_final = []
            if user_input.upper() == 'ALL':
                ids_to_delete_final = orphaned_ids
            else:
                selected_indices = parse_selection(user_input, len(orphaned_ids))
                if not selected_indices:
                    log.warning("输入无效或没有选中任何ID，操作已中止。")
                    return
                
                ids_to_delete_final = [orphaned_ids[i] for i in selected_indices]

            if not ids_to_delete_final:
                log.info("没有选中任何要删除的ID。")
                return

            print("\n--- 将要删除以下ID ---")
            for item in ids_to_delete_final:
                print(f"  - {item}")
            print("--------------------------\n")
            
            confirm = input("确认删除? (y/n): ").strip().lower()
            if confirm == 'y':
                log.info(f"正在删除 {len(ids_to_delete_final)} 个选中的ID...")
                vector_db_service.delete_documents(ids=ids_to_delete_final)
                log.info("删除成功。")
            else:
                log.info("操作已取消。")

        except Exception as e:
            log.error(f"处理删除操作时出错: {e}", exc_info=True)

def parse_selection(selection_str: str, max_index: int) -> Set[int]:
    """解析用户输入的选择字符串 (例如 '1, 3-5, 8')"""
    indices = set()
    parts = selection_str.split(',')
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            try:
                start, end = map(int, part.split('-'))
                if start > end:
                    start, end = end, start
                for i in range(start, end + 1):
                    if 1 <= i <= max_index:
                        indices.add(i - 1)
            except ValueError:
                log.warning(f"无法解析范围 '{part}'，已忽略。")
        else:
            try:
                index = int(part)
                if 1 <= index <= max_index:
                    indices.add(index - 1)
            except ValueError:
                log.warning(f"无法解析数字 '{part}'，已忽略。")
    return sorted(list(indices))

    log.info("--- 清理工作完成 ---")

if __name__ == "__main__":
    main()