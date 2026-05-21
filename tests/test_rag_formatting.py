#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试RAG搜索结果格式化功能
"""

import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.chat.services.prompt_service import PromptService

def test_rag_formatting():
    """测试RAG搜索结果格式化功能"""
    prompt_service = PromptService()
    
    # 模拟RAG搜索结果数据
    test_entries = [
        {
            'id': '用户偏好',
            'content': '用户喜欢喝咖啡，每天早晨都会喝一杯。\n未提供：用户不喜欢的饮料',
            'distance': 0.2,
            'metadata': {
                'category': '用户偏好',
                'source': '用户对话记录'
            }
        },
        {
            'id': '工作习惯',
            'content': '用户通常在上午9点开始工作，下午5点结束。\n午餐时间在12点到1点之间。',
            'distance': 0.4,
            'metadata': {
                'category': '工作日程',
                'source': '日程安排'
            }
        },
        {
            'id': '兴趣爱好',
            'content': '用户喜欢阅读科幻小说和看电影。\n未提供：用户不喜欢的娱乐活动',
            'distance': 0.6,
            'metadata': {
                'category': '娱乐',
                'source': '聊天记录'
            }
        }
    ]
    
    # 测试格式化功能
    result = prompt_service._format_world_book_entries(test_entries, "测试用户")
    
    print("=" * 60)
    print("RAG搜索结果格式化测试")
    print("=" * 60)
    print("输入数据:")
    for i, entry in enumerate(test_entries):
        print(f"\n条目 {i+1}:")
        print(f"  ID: {entry['id']}")
        print(f"  内容: {entry['content']}")
        print(f"  距离: {entry['distance']}")
        print(f"  元数据: {entry['metadata']}")
    
    print("\n" + "=" * 60)
    print("格式化输出:")
    print("=" * 60)
    print(result)
    
    # 验证输出
    print("\n" + "=" * 60)
    print("验证结果:")
    print("=" * 60)
    
    # 检查是否包含编号的搜索结果
    if "搜索结果 1" in result and "搜索结果 2" in result and "搜索结果 3" in result:
        print("✓ 搜索结果编号正确")
    else:
        print("✗ 搜索结果编号缺失")
    
    # 检查是否包含元数据信息
    if "相关性:" in result and "分类:" in result and "来源:" in result:
        print("✓ 元数据信息完整")
    else:
        print("✗ 元数据信息缺失")
    
    # 检查是否过滤了"未提供"内容
    if "未提供" not in result:
        print("✓ '未提供'内容已正确过滤")
    else:
        print("✗ '未提供'内容未正确过滤")
    
    # 检查是否包含world_book_context标签
    if "<world_book_context>" in result and "</world_book_context>" in result:
        print("✓ world_book_context标签完整")
    else:
        print("✗ world_book_context标签缺失")
    
    print("\n测试完成!")

if __name__ == "__main__":
    test_rag_formatting()