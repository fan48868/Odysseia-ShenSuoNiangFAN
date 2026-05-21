# -*- coding: utf-8 -*-

import os
import unittest
from unittest.mock import patch, MagicMock
import asyncio
import logging

# 在导入我们自己的模块之前，确保根目录在 sys.path 中
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- 更精确的 Mocking ---
# 我们只 mock 我们需要控制的部分，而不是整个 'google' 命名空间
# 这样可以避免 'google.protobuf' 等依赖项出现 ModuleNotFoundError

# 1. 创建模拟的异常类，它们需要继承自 Exception
class MockGoogleAPIError(Exception):
    pass

class MockInternalServerError(MockGoogleAPIError):
    pass

class MockServiceUnavailable(MockGoogleAPIError):
    pass

class MockResourceExhausted(MockGoogleAPIError):
    pass

class MockGenAIClientError(Exception):
    pass

# 2. 使用 patch 来替换模块中的特定属性
# 我们将在测试方法内部使用 patcher，这样更安全
google_exceptions_patcher = patch.dict('sys.modules', {
    'google.api_core.exceptions': MagicMock(
        InternalServerError=MockInternalServerError,
        ServiceUnavailable=MockServiceUnavailable,
        ResourceExhausted=MockResourceExhausted
    )
})

genai_errors_patcher = patch.dict('sys.modules', {
    'google.genai.errors': MagicMock(
        ClientError=MockGenAIClientError
    )
})

# 3. Mock 掉会触发复杂依赖导入的模块
# 在 gemini_service 导入之前，将这些模块替换为 Mock 对象
# 这样当 gemini_service 尝试从它们导入时，得到的是我们的 Mock
sys.modules['src.chat.services.context_service'] = MagicMock()
sys.modules['src.chat.features.affection.service.affection_service'] = MagicMock()
sys.modules['src.chat.services.prompt_service'] = MagicMock()


# 现在可以安全地导入我们的服务了
from src.chat.services.gemini_service import GeminiService

# 配置日志记录器，以便在测试输出中看到服务的日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TestInvalidKeyHandler(unittest.IsolatedAsyncioTestCase):
    """
    测试 GeminiService 中无效 API 密钥的处理逻辑。
    """

    @patch('google.genai.Client')
    async def test_removes_invalid_key_and_continues(self, mock_genai_client_class):
        """
        测试当一个 API 密钥失效时，服务能否：
        1. 捕获 ClientError 异常。
        2. 识别出是 API_KEY_INVALID 错误。
        3. 从可用客户端列表中移除对应的客户端。
        4. 使用下一个有效的密钥成功完成请求。
        5. 在测试结束时报告剩余的有效密钥。
        """
        # 启动我们的 mock patchers
        google_exceptions_patcher.start()
        genai_errors_patcher.start()

        try:
            print("\n--- 开始测试：无效 API 密钥自动停用逻辑 ---")

            # 1. 设置环境
            invalid_key = "key-this-one-is-invalid"
            valid_keys = ["key-valid-alpha", "key-valid-beta"]
            all_keys_list = [invalid_key, valid_keys[0], valid_keys[1]] # 将无效key放在第一个
            all_keys_str = ",".join(all_keys_list)
            
            with patch.dict(os.environ, {"GEMINI_API_KEYS": all_keys_str}):
                
                # 2. 配置 Mock 行为
                def client_side_effect(api_key, *args, **kwargs):
                    mock_client_instance = MagicMock()
                    if api_key == invalid_key:
                        error_message = "API key not valid. Please pass a valid API key. [API_KEY_INVALID]"
                        mock_client_instance.models.generate_content.side_effect = MockGenAIClientError(error_message)
                        print(f"Mocking: 密钥 '{api_key[:4]}...{api_key[-4:]}' 将触发 API_KEY_INVALID 错误。")
                    else:
                        mock_response = MagicMock()
                        mock_response.parts = [MagicMock(text="成功获得回复")]
                        mock_response.text = "成功获得回复"
                        mock_client_instance.models.generate_content.return_value = mock_response
                        print(f"Mocking: 密钥 '{api_key[:4]}...{api_key[-4:]}' 将返回成功响应。")
                    return mock_client_instance

                mock_genai_client_class.side_effect = client_side_effect

                # 3. 初始化服务
                gemini_service = GeminiService()
                
                self.assertEqual(len(gemini_service.clients), 3)
                print(f"\n服务已初始化，加载了 {len(gemini_service.clients)} 个密钥: {list(gemini_service.clients.keys())}")

                # 4. 执行操作
                print("\n调用 generate_response，预期将触发错误处理流程...")
                gemini_service.current_key_index = 0 # 确保从第一个（无效的）key开始
                response = await gemini_service.generate_response(user_id=123, guild_id=456, message="这是一个测试")

                # 5. 断言结果
                print("\n操作完成，开始验证结果...")
                self.assertIn("成功获得回复", response)
                self.assertEqual(len(gemini_service.clients), 2)
                self.assertNotIn(invalid_key, gemini_service.clients)
                
                remaining_keys = set(gemini_service.clients.keys())
                self.assertEqual(remaining_keys, set(valid_keys))

                # 6. 报告最终状态
                print("\n--- 测试结果报告 ---")
                print(f"初始密钥列表 ({len(all_keys_list)}): {all_keys_list}")
                print(f"检测到并已停用的无效密钥: {invalid_key}")
                print(f"当前剩余的有效密钥 ({len(remaining_keys)}): {list(remaining_keys)}")
                print("测试成功：服务能正确处理无效密钥并继续运行。")
        
        finally:
            # 确保在测试结束时停止 patchers
            google_exceptions_patcher.stop()
            genai_errors_patcher.stop()
            # 清理 sys.modules 中的 mock，恢复原状
            for mod in [
                'src.chat.services.context_service',
                'src.chat.features.affection.service.affection_service',
                'src.chat.services.prompt_service'
            ]:
                if mod in sys.modules:
                    del sys.modules[mod]


if __name__ == '__main__':
    # 运行测试
    unittest.main()