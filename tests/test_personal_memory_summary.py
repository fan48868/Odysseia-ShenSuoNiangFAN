# -*- coding: utf-8 -*-
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

# 重要的：在导入我们自己的模块之前，需要设置PYTHONPATH
# 这通常在运行测试的脚本或CI/CD配置中完成，但为了可移植性，我们在这里也加上
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from src.chat.features.personal_memory.services.personal_memory_service import PersonalMemoryService

@pytest.mark.asyncio
class TestPersonalMemorySummary:
    """
    测试个人记忆分层总结功能
    """

    @pytest.fixture
    def personal_memory_service(self):
        """提供 PersonalMemoryService 的实例"""
        return PersonalMemoryService()

    async def test_initial_summary_creation(self, personal_memory_service):
        """
        测试用例1: 首次记忆生成
        - 场景: 用户没有历史摘要，首次触发总结。
        - 验证: 系统能根据近期对话生成一个准确的初始摘要，并用它覆盖数据库。
        """
        user_id = 12345
        guild_id = 67890

        # --- 模拟输入数据 ---
        # 1. 模拟数据库返回空的旧摘要
        mock_user_profile = {'personal_summary': None}
        
        # 2. 模拟数据库返回的近期对话历史
        mock_conversation_history = [
            {'role': 'user', 'parts': ['你好啊']},
            {'role': 'model', 'parts': ['你好！有什么可以帮你的吗？']},
            {'role': 'user', 'parts': ['我最近在玩《赛博朋克2077》，超酷的！']},
            {'role': 'model', 'parts': ['哦！那款游戏确实很棒！']},
            {'role': 'user', 'parts': ['是啊，我特别喜欢里面的剧情和夜之城的氛围。']}
        ]
        mock_context = {'conversation_history': mock_conversation_history}

        # 3. 模拟AI服务返回的新摘要
        expected_new_summary = "- 用户表达了对游戏《赛博朋克2077》的喜爱，特别是其剧情和游戏氛围。"

        # --- 使用 Mock 模拟外部依赖 ---
        with patch('src.chat.features.personal_memory.services.personal_memory_service.chat_db_manager', new_callable=AsyncMock) as mock_db_manager, \
             patch('src.chat.features.personal_memory.services.personal_memory_service.gemini_service', new_callable=MagicMock) as mock_gemini_service:

            # 配置 Mock 对象的返回值
            mock_db_manager.get_user_profile.return_value = mock_user_profile
            mock_db_manager.get_ai_conversation_context.return_value = mock_context
            mock_gemini_service.generate_text.return_value = expected_new_summary

            # --- 执行被测试的函数 ---
            await personal_memory_service.summarize_and_save_memory(user_id, guild_id)

            # --- 断言与验证 ---
            # 1. 验证是否正确获取了用户档案和对话历史
            mock_db_manager.get_user_profile.assert_called_once_with(user_id)
            mock_db_manager.get_ai_conversation_context.assert_called_once_with(user_id, guild_id)

            # 2. 验证AI生成服务的调用参数是否正确
            # 我们需要检查传递给 generate_text 的 prompt 是否包含了正确的旧摘要和对话历史
            call_args, call_kwargs = mock_gemini_service.generate_text.call_args
            prompt_arg = call_kwargs.get('prompt', '')
            
            assert "【过往记忆摘要】:\n无" in prompt_arg
            assert "用户: 我最近在玩《赛博朋克2077》，超酷的！" in prompt_arg
            
            # 3. 验证数据库更新操作是否正确
            # 核心：确认是使用新的摘要 **覆盖** 了数据库
            mock_db_manager.update_personal_summary.assert_called_once_with(user_id, expected_new_summary)

            # 4. 验证消息计数器是否被重置
            mock_db_manager.reset_personal_message_count.assert_called_once_with(user_id, guild_id)

    # TODO: 在这里添加更多的测试用例

    # async def test_summary_iteration_and_condensation(self, personal_memory_service):
    #     """
    #     测试用例2: 记忆迭代与浓缩
    #     - 场景: 用户已有旧摘要，再次触发总结，新旧信息需要整合。
    #     - 验证: 新摘要融合了新旧信息，并且比简单拼接更精炼。
    #     """
    #     # --- 模拟输入数据 ---
    #     # 1. 模拟旧摘要
    #     old_summary = "- 用户是《赛博朋克2077》的忠实玩家。"
    #     mock_user_profile = {'personal_summary': old_summary}
        
    #     # 2. 模拟新的对话历史
    #     mock_conversation_history = [
    #         {'role': 'user', 'parts': ['最近开始玩《艾尔登法环》了']},
    #         {'role': 'model', 'parts': ['哦，感觉怎么样？']},
    #         {'role': 'user', 'parts': ['虽然一直死，但很有趣。']}
    #     ]
    #     mock_context = {'conversation_history': mock_conversation_history}

    #     # 3. 模拟AI服务返回的、经过浓缩的新摘要
    #     expected_new_summary = "- 用户是《赛博朋克2077》的玩家，但最近的兴趣点转移到了《艾尔登法环》。"

    #     # --- Mock & 执行 & 断言 ---
    #     # ... 此处省略与上一个测试类似的 mock 设置 ...
    #     # 你需要在这里填充 mock、调用函数和断言的代码
    #     pass


    # async def test_summary_correction(self, personal_memory_service):
    #     """
    #     测试用例3: 记忆修正
    #     - 场景: 用户在新的对话中表达了与旧摘要相悖的观点。
    #     - 验证: 新摘要能够修正旧的信息。
    #     """
    #     # ... 在这里设计你的输入数据、mock、调用和断言 ...
    #     pass
