#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI功能测试脚本
用于验证Gemini AI集成和@mention功能是否正常工作
"""

import os
import sys
import asyncio
from dotenv import load_dotenv

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.abspath('.'))

async def test_gemini_service():
    """测试Gemini服务功能"""
    print("🧪 开始测试Gemini AI服务...")
    
    try:
        from src.services.gemini_service import gemini_service
        
        # 检查服务是否可用
        if not gemini_service.is_available():
            print("❌ Gemini服务不可用，请检查GEMINI_API_KEY环境变量")
            return False
        
        print("✅ Gemini服务初始化成功")
        
        # 测试生成回复
        test_message = "你好，请介绍一下你自己"
        print(f"📝 测试消息: {test_message}")
        
        response = await gemini_service.generate_response(12345, 67890, test_message)
        print(f"🤖 AI回复: {response}")
        
        if response and len(response) > 0:
            print("✅ AI回复生成成功")
            return True
        else:
            print("❌ AI回复生成失败")
            return False
            
    except Exception as e:
        print(f"❌ 测试过程中出现错误: {e}")
        return False

async def test_database_connection():
    """测试数据库连接和AI上下文功能"""
    print("\n🧪 开始测试数据库连接...")
    
    try:
        from src.utils.database import db_manager
        
        # 测试获取AI上下文
        context = await db_manager.get_ai_conversation_context(12345, 67890)
        print(f"📊 获取AI上下文: {context}")
        
        # 测试更新AI上下文
        test_history = [{"role": "user", "parts": ["你好"]}, {"role": "model", "parts": ["你好！"]}]
        await db_manager.update_ai_conversation_context(12345, 67890, test_history)
        print("✅ AI上下文更新成功")
        
        # 验证更新
        updated_context = await db_manager.get_ai_conversation_context(12345, 67890)
        if updated_context and updated_context.get('conversation_history') == test_history:
            print("✅ AI上下文验证成功")
            return True
        else:
            print("❌ AI上下文验证失败")
            return False
            
    except Exception as e:
        print(f"❌ 数据库测试过程中出现错误: {e}")
        return False

async def main():
    """主测试函数"""
    print("🚀 开始AI功能集成测试")
    print("=" * 50)
    
    # 加载环境变量
    load_dotenv()
    
    # 运行测试
    db_test_passed = await test_database_connection()
    ai_test_passed = await test_gemini_service()
    
    print("\n" + "=" * 50)
    print("📊 测试结果汇总:")
    print(f"数据库测试: {'✅ 通过' if db_test_passed else '❌ 失败'}")
    print(f"AI服务测试: {'✅ 通过' if ai_test_passed else '❌ 失败'}")
    
    if db_test_passed and ai_test_passed:
        print("\n🎉 所有测试通过！AI功能集成成功")
        return True
    else:
        print("\n💥 测试失败，请检查配置和错误信息")
        return False

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)