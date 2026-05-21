# -*- coding: utf-8 -*-

import os
import logging
import argparse

# --- 配置 ---
# 设置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 定义要搜索的根目录
SEARCH_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src', 'guidance'))

# 定义要查找和替换的模式
REPLACEMENT_PATTERNS = {
    "from src.guidance.utils.modals": "from src.guidance.ui.modals",
    "from src.guidance.utils.views": "from src.guidance.ui.views"
}

def fix_imports_in_file(file_path, dry_run=False):
    """
    读取文件，执行替换。
    如果 dry_run 为 True，则只打印将要进行的更改。
    否则，实际写入文件。
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        logging.error(f"无法读取文件 {file_path}: {e}")
        return False

    updated = False
    new_lines = []
    for line_num, line in enumerate(lines, 1):
        original_line = line
        for find_str, replace_str in REPLACEMENT_PATTERNS.items():
            if find_str in line:
                line = line.replace(find_str, replace_str)
        
        if original_line != line:
            updated = True
            if dry_run:
                logging.info(f"[模拟] 在文件 {file_path} (行 {line_num}) 中:")
                logging.info(f"  - 找到: {original_line.strip()}")
                logging.info(f"  - 替换为: {line.strip()}")
        new_lines.append(line)

    if updated and not dry_run:
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            logging.info(f"已修复文件: {file_path}")
        except Exception as e:
            logging.error(f"无法写入文件 {file_path}: {e}")
            return False
            
    return updated

def main():
    """
    遍历指定目录下的所有 .py 文件并修复导入。
    """
    parser = argparse.ArgumentParser(description="修复 guidance UI 模块中错误的导入路径。")
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="模拟运行模式，只显示将要进行的更改，不实际修改文件。"
    )
    args = parser.parse_args()

    if args.dry_run:
        logging.info("--- 运行在模拟（dry-run）模式 ---")

    logging.info(f"开始在目录 {SEARCH_DIR} 中扫描并修复错误的 UI 导入...")
    
    updated_files_count = 0
    
    for root, _, files in os.walk(SEARCH_DIR):
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                if fix_imports_in_file(file_path, dry_run=args.dry_run):
                    updated_files_count += 1
    
    logging.info("--- 扫描完成 ---")
    if updated_files_count > 0:
        mode_str = "将被更新" if args.dry_run else "已被更新"
        logging.info(f"总计 {updated_files_count} 个文件{mode_str}。")
    else:
        logging.info("没有文件需要更新。")

if __name__ == "__main__":
    main()