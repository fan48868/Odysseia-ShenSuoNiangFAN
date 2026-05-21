# -*- coding: utf-8 -*-

import os
import sys
import yaml

# --- 配置项目根路径 ---
# 这使得脚本可以从任何位置运行，同时能够正确导入 src 和 scripts 目录下的模块
current_script_path = os.path.abspath(__file__)
script_dir = os.path.dirname(current_script_path)
project_root = os.path.dirname(script_dir)
sys.path.insert(0, project_root)
# --- 路径配置结束 ---

# 从索引脚本中导入我们想要测试的函数
from scripts.build_vector_index import build_document_text

# 定义知识库文件路径
KNOWLEDGE_FILE_PATH = os.path.join(project_root, 'src', 'world_book', 'data', 'knowledge.yml')

def test_builder():
    """
    读取知识库文件，并为每个条目调用文本构建器，然后打印结果以供审查。
    """
    print("--- 开始测试文本构建器 ---")
    
    try:
        with open(KNOWLEDGE_FILE_PATH, 'r', encoding='utf-8') as f:
            knowledge_entries = yaml.safe_load(f)
        print(f"成功从 '{KNOWLEDGE_FILE_PATH}' 加载了 {len(knowledge_entries)} 个知识条目。\n")
    except Exception as e:
        print(f"读取或解析 YAML 文件时出错: {e}")
        return

    for i, entry in enumerate(knowledge_entries):
        if not isinstance(entry, dict) or 'id' not in entry:
            print(f"--- 跳过格式不正确的条目 #{i+1} ---")
            continue

        entry_id = entry['id']
        category = entry.get("metadata", {}).get("category", "未知类别")
        title = entry.get("title", "无标题") # 获取标题
        
        print(f"--- (条目 {i+1}/{len(knowledge_entries)}) ---")
        print(f"ID: {entry_id}")
        print(f"标题: {title}") # 显示标题
        print(f"类别: {category}")
        
        # 调用构建器函数
        generated_text = build_document_text(entry)
        
        print(f"生成的文本:\n---\n{generated_text}\n---\n")

    print("--- 文本构建器测试完成 ---")

if __name__ == "__main__":
    test_builder()