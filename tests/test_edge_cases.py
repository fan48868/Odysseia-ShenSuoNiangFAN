#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试RAG格式化功能的边缘情况
"""

import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.chat.services.prompt_service import PromptService

def test_edge_cases():
    """测试RAG格式化功能的边缘情况"""
    prompt_service = PromptService()
    
    print("=" * 60)
    print("RAG格式化边缘情况测试")
    print("=" * 60)
    
    # 测试1: 空输入
    print("\n1. 测试空输入:")
    result = prompt_service._format_world_book_entries(None, "测试用户")
    print(f"结果: '{result}' (应为空字符串)")
    
    # 测试2: 空列表
    print("\n2. 测试空列表:")
    result = prompt_service._format_world_book_entries([], "测试用户")
    print(f"结果: '{result}' (应为空字符串)")
    
    # 测试3: 所有内容都被过滤的情况
    print("\n3. 测试所有内容都被过滤:")
    test_entries = [
        {
            'id': '测试',
            'content': '未提供：所有信息',
            'distance': 0.1,
            'metadata': {'category': '测试'}
        }
    ]
    result = prompt_service._format_world_book_entries(test_entries, "测试用户")
    print(f"结果: '{result}' (应为空字符串)")
    
    # 测试4: 缺少元数据的情况
    print("\n4. 测试缺少元数据:")
    test_entries = [
        {
            'id': '测试',
            'content': '这是一个测试内容',
            'distance': 0.3
            # 没有metadata字段
        }
    ]
    result = prompt_service._format_world_book_entries(test_entries, "测试用户")
    print("结果包含:")
    print(result)
    
    # 测试5: 缺少distance的情况
    print("\n5. 测试缺少distance:")
    test_entries = [
        {
            'id': '测试',
            'content': '这是一个测试内容',
            'metadata': {'category': '测试', 'source': '测试'}
            # 没有distance字段
        }
    ]
    result = prompt_service._format_world_book_entries(test_entries, "测试用户")
    print("结果包含:")
    print(result)
    
    # 测试6: 列表内容格式
    print("\n6. 测试列表内容格式:")
    test_entries = [
        {
            'id': '测试',
            'content': ['这是一个列表格式的内容'],
            'distance': 0.5,
            'metadata': {'category': '测试'}
        }
    ]
    result = prompt_service._format_world_book_entries(test_entries, "测试用户")
    print("结果包含:")
    print(result)
    
    print("\n边缘情况测试完成!")

if __name__ == "__main__":
    test_edge_cases()