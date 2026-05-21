#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
个人记忆功能完整流程测试脚本
从购买商品到触发总结的完整测试
"""

import asyncio
import sqlite3
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.chat.utils.database import chat_db_manager
from src.chat.features.personal_memory.services.personal_memory_service import personal_memory_service
from src.chat.config.chat_config import PERSONAL_MEMORY_CONFIG
from src.chat.services.gemini_service import gemini_service

async def test_personal_memory_flow():
    """测试个人记忆功能的完整流程"""
    print("=== 个人记忆功能完整流程测试 ===")
    
    # 测试用户ID
    test_user_id = 999999  # 使用一个不存在的用户ID进行测试
    test_guild_id = 123456
    
    # 1. 初始化服务
    print("1. 初始化服务...")
    await chat_db_manager.init_async()

    # 为测试手动初始化 Gemini 服务
    api_key = "AIzaSyAH4fG-x5zlWUcTetyfMv80QCI6CZhoYQI"
    gemini_service.api_keys = [api_key]
    gemini_service.clients = {}  # 清空旧的客户端以强制重新初始化
    gemini_service.initialize_clients()

    if gemini_service.is_available():
        print("  Gemini 服务已为测试手动初始化。")
    else:
        print("  警告: Gemini 服务手动初始化失败，AI功能将不可用。")
    
    # 2. 检查用户当前状态
    print("2. 检查用户当前状态...")
    user_profile = await chat_db_manager.get_user_profile(test_user_id)
    if user_profile:
        print(f"  用户 {test_user_id} 已存在，has_personal_memory: {user_profile['has_personal_memory']}")
        print(f"  当前摘要: {user_profile['personal_summary'] if user_profile['personal_summary'] else '无'}")
    else:
        print(f"  用户 {test_user_id} 不存在，将创建新用户")
    
    # 3. 模拟购买商品后解锁功能
    print("3. 解锁个人记忆功能...")
    await personal_memory_service.unlock_feature(test_user_id)
    
    # 4. 验证功能已解锁
    print("4. 验证功能已解锁...")
    user_profile = await chat_db_manager.get_user_profile(test_user_id)
    if user_profile and user_profile['has_personal_memory']:
        print("  ✅ 个人记忆功能已成功解锁")
    else:
        print("  ❌ 个人记忆功能解锁失败")
        return False
    
    # 5. 模拟几次对话，增加消息计数
    print("5. 模拟对话，增加消息计数...")
    summary_threshold = PERSONAL_MEMORY_CONFIG['summary_threshold']
    print(f"  总结阈值: {summary_threshold} 条消息")
    
    # 准备模拟的对话历史
    mock_history = [
        {"role": "user", "parts": ["你好，我是测试用户"]},
        {"role": "model", "parts": ["你好！很高兴认识你。"]},
        {"role": "user", "parts": ["我喜欢编程和玩游戏"]},
        {"role": "model", "parts": ["听起来很棒！我也是。"]},
        {"role": "user", "parts": ["希望我们能成为好朋友"]},
    ]
    
    # 更新对话历史到数据库
    await chat_db_manager.update_ai_conversation_context(test_user_id, test_guild_id, mock_history)
    print("  已为用户创建模拟对话历史。")

    for i in range(summary_threshold + 2):  # 多触发几次
        new_count = await personal_memory_service.increment_and_check_message_count(test_user_id, test_guild_id)
        print(f"  第 {i+1} 次对话后，消息计数: {new_count}")
        
        if new_count >= summary_threshold:
            print(f"  ✅ 达到阈值 {summary_threshold}，应该触发总结")
            break
    
    # 6. 检查计数是否正确
    print("6. 检查消息计数是否正确...")
    context = await chat_db_manager.get_ai_conversation_context(test_user_id, test_guild_id)
    if context and 'personal_message_count' in context:
        print(f"  数据库中的消息计数: {context['personal_message_count']}")
    else:
        print("  ❌ 无法获取消息计数")
    
    # 7. 手动触发总结（模拟达到阈值后的自动触发）
    print("7. 手动触发总结过程...")
    await personal_memory_service.summarize_and_save_memory(test_user_id, test_guild_id)
    
    # 8. 检查总结结果
    print("8. 检查总结结果...")
    user_profile = await chat_db_manager.get_user_profile(test_user_id)
    if user_profile and user_profile['personal_summary']:
        print("  ✅ 个人记忆摘要已生成并保存")
        print(f"  摘要内容预览: {user_profile['personal_summary'][:100]}...")
    else:
        print("  ❌ 个人记忆摘要生成失败")
    
    # 9. 检查计数是否重置
    print("9. 检查消息计数是否重置...")
    context = await chat_db_manager.get_ai_conversation_context(test_user_id, test_guild_id)
    if context and context['personal_message_count'] == 0:
        print("  ✅ 消息计数已正确重置为 0")
    else:
        print(f"  ❌ 消息计数未正确重置，当前值: {context['personal_message_count'] if context else '无上下文'}")
    
    print("\n=== 测试完成 ===")
    return True

async def check_database_schema():
    """检查数据库表结构"""
    print("=== 检查数据库表结构 ===")
    
    db_path = os.path.join(project_root, "data", "chat.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 检查 ai_conversation_contexts 表结构
    cursor.execute("PRAGMA table_info(ai_conversation_contexts)")
    columns = cursor.fetchall()
    print("ai_conversation_contexts 表列信息:")
    for col in columns:
        print(f"  {col[1]} ({col[2]})")
    
    # 检查是否有 personal_message_count 列
    has_personal_count = any(col[1] == 'personal_message_count' for col in columns)
    print(f"  是否有 personal_message_count 列: {has_personal_count}")
    
    conn.close()
    return has_personal_count

if __name__ == "__main__":
    # 先检查数据库结构
    schema_ok = asyncio.run(check_database_schema())
    
    if schema_ok:
        print("\n数据库结构正常，开始功能测试...")
        success = asyncio.run(test_personal_memory_flow())
        if success:
            print("\n🎉 测试成功！个人记忆功能正常工作")
        else:
            print("\n❌ 测试失败！请检查日志")
    else:
        print("\n❌ 数据库结构有问题，请先运行数据库迁移脚本")