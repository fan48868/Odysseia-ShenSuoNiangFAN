# -*- coding: utf-8 -*-

import os
import re

# 定义要搜索的根目录
ROOT_DIR = "src"

# 定义导入路径的替换规则
# (旧的导入前缀, 新的导入前缀)
REFACTOR_RULES = [
    # --- 配置文件拆分 ---
    # 从 src.config 移动到 src.chat.config.chat_config
    ("from src.config import GEMINI_MODEL", "from src.chat.config.chat_config import GEMINI_MODEL"),
    ("from src.config import QUERY_REWRITING_MODEL", "from src.chat.config.chat_config import QUERY_REWRITING_MODEL"),
    ("from src.config import GEMINI_CHAT_CONFIG", "from src.chat.config.chat_config import GEMINI_CHAT_CONFIG"),
    ("from src.config import GEMINI_TEXT_GEN_CONFIG", "from src.chat.config.chat_config import GEMINI_TEXT_GEN_CONFIG"),
    ("from src.config import COOLDOWN_RATES", "from src.chat.config.chat_config import COOLDOWN_RATES"),
    ("from src.config import BLACKLIST_BAN_DURATION_MINUTES", "from src.chat.config.chat_config import BLACKLIST_BAN_DURATION_MINUTES"),
    ("from src.config import COIN_REWARD_FORUM_CHANNEL_IDS", "from src.chat.config.chat_config import COIN_REWARD_FORUM_CHANNEL_IDS"),
    ("from src.config import AFFECTION_CONFIG", "from src.chat.config.chat_config import AFFECTION_CONFIG"),
    ("from src.config import COIN_CONFIG", "from src.chat.config.chat_config import COIN_CONFIG"),
    ("from src.config import VECTOR_DB_PATH", "from src.chat.config.chat_config import VECTOR_DB_PATH"),
    ("from src.config import VECTOR_DB_COLLECTION_NAME", "from src.chat.config.chat_config import VECTOR_DB_COLLECTION_NAME"),

    # 从 src.config 移动到 src.guidance.config
    ("from src.config import USER_STATUS_IN_PROGRESS", "from src.guidance.config import USER_STATUS_IN_PROGRESS"),
    ("from src.config import USER_STATUS_COMPLETED", "from src.guidance.config import USER_STATUS_COMPLETED"),
    ("from src.config import USER_STATUS_CANCELLED", "from src.guidance.config import USER_STATUS_CANCELLED"),
    ("from src.config import USER_STATUS_PENDING_SELECTION", "from src.guidance.config import USER_STATUS_PENDING_SELECTION"),
    ("from src.config import MAX_PATH_STEPS", "from src.guidance.config import MAX_PATH_STEPS"),
    ("from src.config import TEMPLATE_TYPES", "from src.guidance.config import TEMPLATE_TYPES"),
    ("from src.config import GUIDANCE_COMPLETION_MESSAGE", "from src.guidance.config import GUIDANCE_COMPLETION_MESSAGE"),

    # --- 根目录 config 文件夹移动 ---
    # from config.prompts -> from src.chat.config.prompts
    (r"from config\.prompts", "from src.chat.config.prompts"),
    (r"from config\.emoji_config", "from src.chat.config.emoji_config"),
    (r"from config\.thread_prompts", "from src.chat.config.thread_prompts"),
]

def refactor_imports_in_file(file_path):
    """重构单个文件中的导入语句"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        print(f"Skipping file with encoding issue: {file_path}")
        return

    original_content = content
    for old_import, new_import in REFACTOR_RULES:
        # 使用正则表达式以确保替换的是完整的导入语句
        # \b 是单词边界，防止替换部分匹配的导入
        pattern = re.compile(r"\b" + re.escape(old_import) + r"\b")
        content = pattern.sub(new_import, content)

    if content != original_content:
        print(f"Refactoring imports in: {file_path}")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

def main():
    """遍历项目文件并执行导入重构"""
    for root, _, files in os.walk(ROOT_DIR):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                refactor_imports_in_file(file_path)
    print("\nImport refactoring complete.")

if __name__ == "__main__":
    main()