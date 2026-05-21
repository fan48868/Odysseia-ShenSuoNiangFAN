import unittest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, date, timezone, timedelta

# 确保 src 目录在 Python 的搜索路径中
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

class TestCoinService(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.get_event_loop()

        # --- 模拟依赖 ---
        self.db_manager_mock = AsyncMock()
        self.config_mock = {
            "DAILY_FIRST_CHAT_REWARD": 10
        }
        self.affection_service_mock = AsyncMock()

        # --- 配置模拟对象的返回值 ---
        # 模拟 _execute 来直接执行传入的函数
        async def execute_side_effect(func, *args, **kwargs):
            return func(*args, **kwargs)
        self.db_manager_mock._execute.side_effect = execute_side_effect

        # --- 应用 Patch ---
        self.patcher_db = patch('src.chat.features.odysseia_coin.service.coin_service.chat_db_manager', self.db_manager_mock)
        self.patcher_config = patch('src.chat.features.odysseia_coin.service.coin_service.COIN_CONFIG', self.config_mock)
        # 我们需要模拟 affection_service，因为它在 purchase_item 中被导入
        self.patcher_affection = patch('src.chat.features.odysseia_coin.service.coin_service.affection_service', self.affection_service_mock)
        # 模拟 sqlite3.connect
        self.patcher_sqlite = patch('sqlite3.connect')

        self.mock_connect = self.patcher_sqlite.start()
        self.patcher_db.start()
        self.patcher_config.start()
        self.patcher_affection.start()

        # --- 实例化被测试的服务 ---
        from src.chat.features.odysseia_coin.service.coin_service import CoinService
        # 阻止 _setup_initial_items 自动运行
        with patch('asyncio.create_task'):
            self.coin_service = CoinService()

    def tearDown(self):
        self.patcher_db.stop()
        self.patcher_config.stop()
        self.patcher_affection.stop()
        self.patcher_sqlite.stop()

    def _run_async(self, coro):
        return self.loop.run_until_complete(coro)

    def test_add_coins(self):
        """测试增加金币功能。"""
        user_id = 1
        
        # 模拟数据库操作
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (110,) # 新余额
        self.mock_connect.return_value.cursor.return_value = mock_cursor

        new_balance = self._run_async(self.coin_service.add_coins(user_id, 10, "测试"))

        self.assertEqual(new_balance, 110)
        # 验证 SQL 语句是否被正确调用
        self.assertEqual(mock_cursor.execute.call_count, 3)
        # 验证是否提交了事务
        self.mock_connect.return_value.commit.assert_called_once()

    def test_remove_coins_insufficient_balance(self):
        """测试余额不足时扣款失败。"""
        user_id = 2
        
        # 模拟 get_balance 返回一个较小的余额
        self.db_manager_mock.get_affection.return_value = None # 重置
        with patch.object(self.coin_service, 'get_balance', new_callable=AsyncMock) as mock_get_balance:
            mock_get_balance.return_value = 50
            
            result = self._run_async(self.coin_service.remove_coins(user_id, 100, "测试购买"))
            
            self.assertIsNone(result)
            # 验证没有进行数据库连接
            self.mock_connect.assert_not_called()

    def test_grant_daily_reward_first_time(self):
        """测试用户当天第一次发言获得奖励。"""
        user_id = 3
        
        mock_cursor = MagicMock()
        # 模拟数据库返回 None，表示用户是第一次发言或没有昨天的记录
        mock_cursor.fetchone.return_value = None
        self.mock_connect.return_value.cursor.return_value = mock_cursor

        result = self._run_async(self.coin_service.grant_daily_message_reward(user_id))

        self.assertTrue(result)
        self.mock_connect.return_value.commit.assert_called_once()

    def test_grant_daily_reward_already_granted(self):
        """测试用户当天重复发言不再获得奖励。"""
        user_id = 4
        today_str = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        
        mock_cursor = MagicMock()
        # 模拟数据库返回今天的日期
        mock_cursor.fetchone.return_value = (today_str,)
        self.mock_connect.return_value.cursor.return_value = mock_cursor

        result = self._run_async(self.coin_service.grant_daily_message_reward(user_id))

        self.assertFalse(result)
        # 验证没有提交事务
        self.mock_connect.return_value.commit.assert_not_called()

    def test_purchase_gift_and_rollback_on_failure(self):
        """测试购买礼物失败时金币是否回滚。"""
        user_id, guild_id, item_id = 5, 105, 1
        
        # 模拟商品信息
        mock_item = {'item_id': item_id, 'name': '泰迪熊', 'price': 120, 'target': 'ai', 'effect_id': None}
        
        # 模拟 get_item_by_id
        with patch.object(self.coin_service, 'get_item_by_id', new_callable=AsyncMock) as mock_get_item:
            mock_get_item.return_value = mock_item
            
            # 模拟余额充足
            with patch.object(self.coin_service, 'get_balance', new_callable=AsyncMock) as mock_get_balance:
                mock_get_balance.return_value = 200
                
                # 模拟扣款成功
                with patch.object(self.coin_service, 'remove_coins', new_callable=AsyncMock) as mock_remove_coins:
                    mock_remove_coins.return_value = 80 # 新余额
                    
                    # 关键：模拟送礼失败
                    self.affection_service_mock.increase_affection_for_gift.return_value = (False, "今天已经送过啦！")
                    
                    # 模拟加款（回滚）
                    with patch.object(self.coin_service, 'add_coins', new_callable=AsyncMock) as mock_add_coins:
                        
                        success, msg, new_balance = self._run_async(self.coin_service.purchase_item(user_id, guild_id, item_id))

                        self.assertFalse(success)
                        self.assertIn("已经送过", msg)
                        self.assertEqual(new_balance, 200) # 余额应恢复原状
                        
                        # 验证扣款和加款都被调用了
                        mock_remove_coins.assert_called_once_with(user_id, 120, "购买 1x 泰迪熊")
                        mock_add_coins.assert_called_once_with(user_id, 120, "送礼失败返还: 泰迪熊")

if __name__ == '__main__':
    unittest.main()