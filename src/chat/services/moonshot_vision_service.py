# -*- coding: utf-8 -*-

import base64
import logging
import os
from typing import Any, Dict, Optional

import httpx


log = logging.getLogger(__name__)


class MoonshotVisionService:
    """Moonshot 视觉识别服务（OpenAI 兼容接口）。"""

    def __init__(self) -> None:
        self.moonshot_url = os.getenv("MOONSHOT_URL")
        self.moonshot_api_key = os.getenv("MOONSHOT_API_KEY")
        self.model_name = "moonshot-v1-8k-vision-preview"

    @staticmethod
    def _extract_text_from_response(data: Dict[str, Any]) -> Optional[str]:
        """从 OpenAI 兼容响应结构中提取文本。"""
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None

        message = choices[0].get("message", {})
        content = message.get("content")

        # 常见结构：字符串
        if isinstance(content, str):
            return content.strip()

        # 部分兼容实现会返回块数组
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        text_parts.append(item["text"])
                    elif isinstance(item.get("content"), str):
                        text_parts.append(item["content"])
            merged = "\n".join([p for p in text_parts if p]).strip()
            return merged or None

        return None

    @staticmethod
    def _build_api_url(base_url: str) -> str:
        api_url = base_url.rstrip("/")
        if not api_url.endswith("/chat/completions"):
            api_url += "/chat/completions"
        return api_url

    async def recognize_image(
        self,
        image_payload: Dict[str, Any],
        prompt: str = 
        "请识别这张图片的主要内容，描述关键对象、场景和文字信息，如果图片是以文字为主，**必须**完整提取全部文字，禁止自行概括/删改。"
    ) -> str:
        """
        识别图片内容。

        image_payload 预期结构：
        {
            "type": "image",
            "mime_type": "image/webp",
            "data_size": 161822,
            "data_preview": "<完整hex数据>"
        }
        """
        if not self.moonshot_url or not self.moonshot_api_key:
            return "（图片识别不可用：MOONSHOT_URL 或 MOONSHOT_API_KEY 未配置）"

        if not isinstance(image_payload, dict):
            return "（图片识别失败：图片数据格式异常）"

        mime_type = image_payload.get("mime_type")
        data_hex = image_payload.get("data_preview")
        declared_size = image_payload.get("data_size")

        if not isinstance(mime_type, str) or not mime_type:
            return "（图片识别失败：缺少 mime_type）"
        if not isinstance(data_hex, str) or not data_hex:
            return "（图片识别失败：缺少 data_preview）"

        try:
            image_bytes = bytes.fromhex(data_hex)
        except ValueError:
            return "（图片识别失败：data_preview 不是有效的十六进制数据）"

        if isinstance(declared_size, int) and declared_size > 0 and declared_size != len(image_bytes):
            log.warning(
                "Moonshot 图片长度与声明不一致：declared=%s, actual=%s",
                declared_size,
                len(image_bytes),
            )

        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        image_data_url = f"data:{mime_type};base64,{image_base64}"

        request_payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个图像理解助手。请准确描述图片可见内容，不要编造看不见的信息。",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            "temperature": 0.1,
            "stream": False,
        }

        api_url = self._build_api_url(self.moonshot_url)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    api_url,
                    headers={
                        "Authorization": f"Bearer {self.moonshot_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_payload,
                )
                response.raise_for_status()
                data = response.json()

            text = self._extract_text_from_response(data)
            if text:
                return text
            return "（图片识别失败：视觉服务响应中缺少文本结果）"

        except Exception as e:
            log.error("Moonshot 视觉识别失败: %s", e, exc_info=True)
            return "（图片识别失败：视觉服务暂时不可用）"


moonshot_vision_service = MoonshotVisionService()