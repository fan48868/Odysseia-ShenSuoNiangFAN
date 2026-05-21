#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
简化版多API密钥测试脚本
只测试逻辑，不实际调用Google API
"""

import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_multi_api_logic():
    """测试多API密钥逻辑"""
    print("🔑 测试多API密钥逻辑...")
    
    # 模拟多个API密钥
    api_keys_str = "key1,key2,key3,key4"
    api_keys = [key.strip() for key in api_keys_str.split(",") if key.strip()]
    
    print(f"解析出的API密钥: {api_keys}")
    print(f"密钥数量: {len(api_keys)}")
    
    # 模拟轮询逻辑
    current_key_index = 0
    print("\n🔁 模拟轮询功能...")
    
    for i in range(12):  # 测试3轮完整的轮询
        selected_key = api_keys[current_key_index]
        current_key_index = (current_key_index + 1) % len(api_keys)
        
        print(f"请求 #{i+1}: 使用密钥 '{selected_key}' (索引: {current_key_index})")
    
    # 测试不同数量的密钥
    test_cases = [
        "single_key",
        "key1,key2",
        "key1,key2,key3,key4,key5",
        ""  # 空密钥
    ]
    
    print("\n🧪 测试不同格式的密钥输入...")
    for case in test_cases:
        keys = [key.strip() for key in case.split(",") if key.strip()]
        print(f"输入: '{case}' -> 解析出: {len(keys)} 个密钥")
    
    print("\n✅ 多API密钥逻辑测试完成！")

if __name__ == "__main__":
    test_multi_api_logic()