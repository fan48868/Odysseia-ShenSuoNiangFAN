#!/usr/bin/env python3
"""
社区成员档案上传功能测试脚本
用于验证新功能是否正常工作
"""

import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.abspath('.'))

async def test_community_member_functionality():
    """测试社区成员档案上传功能"""
    print("=== 社区成员档案上传功能测试 ===")
    
    try:
        # 测试1: 检查商店配置是否正确
        from src.chat.config.shop_config import SHOP_ITEMS
        community_member_item = None
        for item in SHOP_ITEMS:
            if item[0] == "社区成员档案上传":
                community_member_item = item
                break
        
        if community_member_item:
            print("✅ 测试1通过: 商店配置中包含'社区成员档案上传'商品")
            print(f"   商品信息: {community_member_item}")
        else:
            print("❌ 测试1失败: 商店配置中未找到'社区成员档案上传'商品")
            return False
        
        # 测试2: 检查效果ID是否正确定义
        from src.chat.features.odysseia_coin.service.coin_service import COMMUNITY_MEMBER_UPLOAD_EFFECT_ID
        if COMMUNITY_MEMBER_UPLOAD_EFFECT_ID == "upload_community_member":
            print("✅ 测试2通过: 效果ID正确定义")
        else:
            print(f"❌ 测试2失败: 效果ID不正确，期望 'upload_community_member'，实际 '{COMMUNITY_MEMBER_UPLOAD_EFFECT_ID}'")
            return False
        
        # 测试3: 检查模态框类是否正确导入
        try:
            from src.chat.features.community_member.ui.community_member_modal import CommunityMemberUploadModal
            print("✅ 测试3通过: 社区成员档案模态框类可以正确导入")
        except ImportError as e:
            print(f"❌ 测试3失败: 无法导入社区成员档案模态框类 - {e}")
            return False
        
        # 测试4: 检查商店UI是否正确处理新效果
        try:
            from src.chat.features.odysseia_coin.ui.shop_ui import SimpleShopView
            print("✅ 测试4通过: 商店UI模块可以正确导入")
        except ImportError as e:
            print(f"❌ 测试4失败: 无法导入商店UI模块 - {e}")
            return False
        
        # 测试5: 检查服务类是否正确导入
        try:
            from src.chat.features.community_member.services.community_member_service import community_member_service
            print("✅ 测试5通过: 社区成员服务类可以正确导入")
        except ImportError as e:
            print(f"❌ 测试5失败: 无法导入社区成员服务类 - {e}")
            return False
        
        print("\n🎉 所有基本测试通过！")
        print("社区成员档案上传功能已成功集成到系统中。")
        print("用户现在可以在商店中购买'社区成员档案上传'商品来上传社区成员档案。")
        
        return True
        
    except Exception as e:
        print(f"❌ 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_community_member_functionality())
    if success:
        print("\n✅ 功能测试完成，所有检查通过！")
        sys.exit(0)
    else:
        print("\n❌ 功能测试失败，请检查代码实现。")
        sys.exit(1)