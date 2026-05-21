#!/usr/bin/env python3
"""
测试脚本：验证用户档案保存到世界书数据库的功能
"""

import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from src.chat.features.personal_memory.services.personal_memory_service import personal_memory_service

async def test_save_profile():
    """测试保存用户档案功能"""
    print("开始测试用户档案保存功能...")
    
    # 模拟用户数据
    test_user_id = 123456789
    test_profile_data = {
        'name': '测试用户',
        'personality': '开朗、幽默',
        'background': '来自未来的旅行者',
        'preferences': '喜欢科幻电影，不喜欢剧透'
    }
    
    try:
        # 调用保存方法
        await personal_memory_service.save_user_profile(test_user_id, test_profile_data)
        print("✅ 用户档案保存测试完成")
        
        # 验证数据是否保存到世界书数据库
        import sqlite3
        from src import config
        
        db_path = os.path.join(config.DATA_DIR, 'world_book.sqlite3')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT content_json FROM community_members WHERE discord_number_id = ?",
            (str(test_user_id),)
        )
        result = cursor.fetchone()
        
        if result:
            import json
            content = json.loads(result[0])
            print("✅ 数据成功保存到世界书数据库")
            print(f"保存的内容: {content}")
        else:
            print("❌ 数据未找到在世界书数据库中")
            
        conn.close()
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_save_profile())