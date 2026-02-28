import logging
import os
from typing import Any, Dict

import httpx

from src.chat.features.chat_settings.services.chat_settings_service import (
    chat_settings_service,
)
from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)


@tool_metadata(
    name="查询API余额",
    description="根据当前启用模型，查询 DeepSeek 或 Kimi(Moonshot) 账户余额。",
    emoji="💰",
    category="系统",
)
async def get_balance(log_detailed: bool = False, **kwargs) -> Dict[str, Any]:
    """
    查询模型供应商账户余额（DeepSeek / Kimi）。

    [调用指南]
    - 仅当用户明确询问剩余余额时使用该工具。
    - 该工具会读取当前全局启用模型，并自动选择对应余额接口。

    Returns:
        统一结构的字典结果，包含:
        - ok: 是否成功
        - provider: deepseek / moonshot / unknown
        - model: 当前模型名
        - endpoint: 调用地址
        - data: 上游返回的 JSON（成功时）
        - error: 错误信息（失败时）
    """
    try:
        current_model = await chat_settings_service.get_current_ai_model()
    except Exception as e:
        log.error("读取当前 AI 模型失败。", exc_info=True)
        return {
            "ok": False,
            "provider": "unknown",
            "model": None,
            "endpoint": None,
            "error": f"读取当前模型失败: {str(e)}",
        }

    model_lower = (current_model or "").lower()

    provider = "unknown"
    endpoint = None
    api_key = None

    if "deepseek" in model_lower:
        provider = "deepseek"
        endpoint = "https://api.deepseek.com/user/balance"
        api_key = os.getenv("DEEPSEEK_API_KEY")
        missing_key_name = "DEEPSEEK_API_KEY"
    elif "kimi" in model_lower or "moonshot" in model_lower:
        provider = "moonshot"
        endpoint = "https://api.moonshot.cn/v1/users/me/balance"
        api_key = os.getenv("MOONSHOT_API_KEY")
        missing_key_name = "MOONSHOT_API_KEY"
    else:
        return {
            "ok": False,
            "provider": provider,
            "model": current_model,
            "endpoint": None,
            "error": f"当前模型 '{current_model}' 暂不支持余额查询。",
        }

    if not api_key:
        return {
            "ok": False,
            "provider": provider,
            "model": current_model,
            "endpoint": endpoint,
            "error": f"缺少环境变量 {missing_key_name}，无法查询余额。",
        }

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    if log_detailed:
        log.info(
            f"--- [工具执行]: get_balance, model={current_model}, provider={provider}, endpoint={endpoint} ---"
        )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, headers=headers)

        if response.status_code >= 400:
            body_preview = (response.text or "")[:800]
            log.warning(
                f"get_balance 请求失败: provider={provider}, status={response.status_code}, body={body_preview}"
            )
            return {
                "ok": False,
                "provider": provider,
                "model": current_model,
                "endpoint": endpoint,
                "error": f"余额接口请求失败，HTTP {response.status_code}",
                "response_preview": body_preview,
            }

        try:
            data = response.json()
        except Exception:
            data = {"raw_text": (response.text or "")[:2000]}

        return {
            "ok": True,
            "provider": provider,
            "model": current_model,
            "endpoint": endpoint,
            "data": data,
        }

    except httpx.TimeoutException:
        log.error(f"get_balance 请求超时: provider={provider}, endpoint={endpoint}")
        return {
            "ok": False,
            "provider": provider,
            "model": current_model,
            "endpoint": endpoint,
            "error": "余额接口请求超时，请稍后重试。",
        }
    except Exception as e:
        log.error("get_balance 调用异常。", exc_info=True)
        return {
            "ok": False,
            "provider": provider,
            "model": current_model,
            "endpoint": endpoint,
            "error": f"调用余额接口异常: {str(e)}",
        }