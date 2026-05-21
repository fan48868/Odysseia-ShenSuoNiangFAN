#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os

# 添加项目根目录到 sys.path
current_script_path = os.path.abspath(__file__)
script_dir = os.path.dirname(current_script_path)
project_root = script_dir
sys.path.insert(0, project_root)

from src.chat.config.chat_config import WORLD_BOOK_CONFIG

def test_config():
    """测试配置是否正确加载"""
    print("测试世界之书配置...")
    print(f"向量索引更新间隔: {WORLD_BOOK_CONFIG['VECTOR_INDEX_UPDATE_INTERVAL_HOURS']} 小时")
    
    # 验证配置值
    interval = WORLD_BOOK_CONFIG["VECTOR_INDEX_UPDATE_INTERVAL_HOURS"]
    if isinstance(interval, int) and interval > 0:
        print("✓ 配置验证成功")
        return True
    else:
        print("✗ 配置验证失败: 间隔时间必须是正整数")
        return False

if __name__ == "__main__":
    success = test_config()
    sys.exit(0 if success else 1)