import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

# 使用 patch 来模拟 gemini_service 的导入，避免它在测试期间进行真实初始化
@patch('src.chat.features.affection.cogs.feeding_cog.gemini_service', new_callable=AsyncMock)
class TestFeedingCog(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # FeedingCog 的初始化现在会使用被 patch 的 gemini_service
        from src.chat.features.affection.cogs.feeding_cog import FeedingCog
        self.bot = MagicMock()
        self.cog = FeedingCog(self.bot)

        # Mock services
        self.cog.affection_service = AsyncMock()
        self.cog.coin_service = AsyncMock()
        # self.cog.gemini_service is already mocked by the class decorator
        self.cog.feeding_service = AsyncMock()

        # Mock interaction
        self.interaction = AsyncMock(spec=discord.Interaction)
        self.interaction.user.id = "test_user_123"
        self.interaction.guild.id = "test_guild_123"

        # Mock attachment
        self.image_attachment = MagicMock(spec=discord.Attachment)
        self.image_attachment.content_type = "image/png"
        self.image_attachment.read = AsyncMock(return_value=b"fake_image_bytes")
        self.image_attachment.filename = "test.png"

    async def test_feed_with_positive_coins(self, mock_gemini_service):
        """测试投喂获得正数类脑币奖励的情况"""
        # Arrange
        self.cog.feeding_service.can_feed.return_value = (True, "")
        mock_gemini_service.generate_text_with_image.return_value = "评价<affection:+5;coins:+50>"

        # Act
        await self.cog.feed(self.interaction, self.image_attachment)

        # Assert
        self.interaction.response.defer.assert_called_once()
        self.cog.affection_service.add_affection_points.assert_called_once_with("test_user_123", "test_guild_123", 5)
        self.cog.coin_service.add_coins.assert_called_once_with("test_user_123", 50, reason="投喂奖励")
        self.cog.coin_service.remove_coins.assert_not_called()
        
        # 验证发送的消息包含奖励文本
        send_call_args = self.interaction.followup.send.call_args
        self.assertIn("你获得了 50 枚类脑币！", send_call_args.kwargs['embed'].description)


    async def test_feed_with_negative_coins(self, mock_gemini_service):
        """测试投喂获得负数类脑币奖励的情况"""
        # Arrange
        self.cog.feeding_service.can_feed.return_value = (True, "")
        mock_gemini_service.generate_text_with_image.return_value = "评价<affection:-2;coins:-30>"

        # Act
        await self.cog.feed(self.interaction, self.image_attachment)

        # Assert
        self.interaction.response.defer.assert_called_once()
        self.cog.affection_service.add_affection_points.assert_called_once_with("test_user_123", "test_guild_123", -2)
        # 核心验证：不应调用 add_coins 或 remove_coins
        self.cog.coin_service.add_coins.assert_not_called()
        self.cog.coin_service.remove_coins.assert_not_called()

        # 验证发送的消息不包含奖励文本
        send_call_args = self.interaction.followup.send.call_args
        self.assertNotIn("你获得了", send_call_args.kwargs['embed'].description)


    async def test_feed_with_zero_coins(self, mock_gemini_service):
        """测试投喂获得零类脑币奖励的情况"""
        # Arrange
        self.cog.feeding_service.can_feed.return_value = (True, "")
        mock_gemini_service.generate_text_with_image.return_value = "评价<affection:+1;coins:0>"

        # Act
        await self.cog.feed(self.interaction, self.image_attachment)

        # Assert
        self.interaction.response.defer.assert_called_once()
        self.cog.affection_service.add_affection_points.assert_called_once_with("test_user_123", "test_guild_123", 1)
        # 核心验证：不应调用 add_coins 或 remove_coins
        self.cog.coin_service.add_coins.assert_not_called()
        self.cog.coin_service.remove_coins.assert_not_called()
        
        # 验证发送的消息不包含奖励文本
        send_call_args = self.interaction.followup.send.call_args
        self.assertNotIn("你获得了", send_call_args.kwargs['embed'].description)

if __name__ == '__main__':
    unittest.main()