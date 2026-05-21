import unittest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import date, timedelta

# 在导入我们自己的模块之前，确保 src 目录在 Python 的搜索路径中
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

class TestAffectionService(unittest.TestCase):

    def setUp(self):
        """在每个测试方法运行前设置环境。"""
        # 为了支持异步测试，我们需要一个事件循环
        self.loop = asyncio.get_event_loop()

        # --- 模拟依赖 ---
        # 1. 模拟数据库管理器
        self.db_manager_mock = AsyncMock()

        # 2. 模拟配置文件
        self.config_mock = {
            "INCREASE_CHANCE": 1.0,  # 100% 几率触发，方便测试
            "DAILY_CHAT_AFFECTION_CAP": 10,
            "INCREASE_AMOUNT": 2,
            "BLACKLIST_PENALTY": -50,
            "DAILY_FLUCTUATION": [-1, 1]
        }
        
        # 3. 模拟 YAML 加载
        self.yaml_load_mock = [
            {"id": "level1", "min_affection": 0, "max_affection": 19, "level_name": "陌生", "prompt": "..."},
            {"id": "level2", "min_affection": 20, "max_affection": 49, "level_name": "熟悉", "prompt": "..."}
        ]

        # --- 应用 Patch ---
        # 我们使用 patch 来替换掉真实的依赖
        self.patcher_db = patch('src.chat.features.affection.service.affection_service.chat_db_manager', self.db_manager_mock)
        self.patcher_config = patch('src.chat.features.affection.service.affection_service.AFFECTION_CONFIG', self.config_mock)
        self.patcher_yaml_open = patch('builtins.open', unittest.mock.mock_open(read_data=""))
        self.patcher_yaml_load = patch('yaml.safe_load', return_value=self.yaml_load_mock)

        self.patcher_db.start()
        self.patcher_config.start()
        self.patcher_yaml_open.start()
        self.patcher_yaml_load.start()

        # --- 实例化被测试的服务 ---
        # 必须在 patch 启动后导入和实例化
        from src.chat.features.affection.service.affection_service import AffectionService
        self.affection_service = AffectionService()

    def tearDown(self):
        """在每个测试方法运行后清理环境。"""
        self.patcher_db.stop()
        self.patcher_config.stop()
        self.patcher_yaml_open.stop()
        self.patcher_yaml_load.stop()

    def _run_async(self, coro):
        """一个辅助函数，用于同步运行异步代码。"""
        return self.loop.run_until_complete(coro)

    def test_increase_affection_on_message_success(self):
        """测试用户发消息成功增加好感度。"""
        user_id, guild_id = 1, 101
        today = date.today().isoformat()
        
        # 模拟数据库返回一个未满上限的记录
        self.db_manager_mock.get_affection.return_value = {
            'user_id': user_id, 'guild_id': guild_id, 'affection_points': 20,
            'daily_affection_gain': 5, 'last_update_date': today, 'last_gift_date': None
        }

        result = self._run_async(self.affection_service.increase_affection_on_message(user_id, guild_id))

        self.assertEqual(result, self.config_mock["INCREASE_AMOUNT"])
        # 验证数据库更新是否被正确调用
        self.db_manager_mock.update_affection.assert_called_once_with(
            user_id, guild_id,
            affection_points=22,
            daily_affection_gain=7,
            last_interaction_date=today
        )

    def test_increase_affection_on_message_cap_reached(self):
        """测试用户发消息达到每日上限。"""
        user_id, guild_id = 2, 102
        today = date.today().isoformat()

        # 模拟数据库返回一个已满上限的记录
        self.db_manager_mock.get_affection.return_value = {
            'user_id': user_id, 'guild_id': guild_id, 'affection_points': 50,
            'daily_affection_gain': self.config_mock["DAILY_CHAT_AFFECTION_CAP"],
            'last_update_date': today, 'last_gift_date': None
        }

        result = self._run_async(self.affection_service.increase_affection_on_message(user_id, guild_id))

        self.assertIsNone(result)
        # 验证数据库更新没有被调用
        self.db_manager_mock.update_affection.assert_not_called()

    def test_increase_affection_for_gift_first_time(self):
        """测试用户当天第一次送礼。"""
        user_id, guild_id = 3, 103
        today = date.today().isoformat()
        
        # 模拟数据库返回一个昨天送过礼物的记录
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.db_manager_mock.get_affection.return_value = {
            'user_id': user_id, 'guild_id': guild_id, 'affection_points': 30,
            'daily_affection_gain': 0, 'last_update_date': today, 'last_gift_date': yesterday
        }

        gift_points = 15
        success, message = self._run_async(self.affection_service.increase_affection_for_gift(user_id, guild_id, gift_points))

        self.assertTrue(success)
        self.assertIn(str(gift_points), message)
        # 验证数据库更新是否被正确调用
        self.db_manager_mock.update_affection.assert_called_once_with(
            user_id, guild_id,
            affection_points=45,
            last_gift_date=today,
            last_interaction_date=today
        )

    def test_increase_affection_for_gift_already_gifted(self):
        """测试用户当天重复送礼。"""
        user_id, guild_id = 4, 104
        today = date.today().isoformat()

        # 模拟数据库返回一个今天已经送过礼物的记录
        self.db_manager_mock.get_affection.return_value = {
            'user_id': user_id, 'guild_id': guild_id, 'affection_points': 40,
            'daily_affection_gain': 0, 'last_update_date': today, 'last_gift_date': today
        }

        success, message = self._run_async(self.affection_service.increase_affection_for_gift(user_id, guild_id, 10))

        self.assertFalse(success)
        self.assertIn("已经送过礼物", message)
        # 验证数据库更新没有被调用
        self.db_manager_mock.update_affection.assert_not_called()

    def test_decrease_affection_on_blacklist(self):
        """测试用户被拉黑扣除好感度。"""
        user_id, guild_id = 5, 105
        today = date.today().isoformat()
        
        self.db_manager_mock.get_affection.return_value = {
            'user_id': user_id, 'guild_id': guild_id, 'affection_points': 100,
            'daily_affection_gain': 0, 'last_update_date': today, 'last_gift_date': None
        }

        new_points = self._run_async(self.affection_service.decrease_affection_on_blacklist(user_id, guild_id))
        
        expected_points = 100 + self.config_mock["BLACKLIST_PENALTY"]
        self.assertEqual(new_points, expected_points)
        self.db_manager_mock.update_affection.assert_called_once_with(
            user_id, guild_id,
            affection_points=expected_points
        )

if __name__ == '__main__':
    # 为了让 VS Code 的测试插件能发现并运行测试，我们通常不需要这个 main block。
    # 但直接运行此文件时，它会启动测试。
    unittest.main()