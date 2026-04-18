# -*- coding: utf-8 -*-

import asyncio
import base64
import io
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from PIL import Image


log = logging.getLogger(__name__)


class MoonshotVisionService:
    """Moonshot 视觉识别服务（OpenAI 兼容接口）。"""

    def __init__(self) -> None:
        self.moonshot_url = os.getenv("MOONSHOT_URL")
        self.moonshot_api_keys = self._split_api_keys(os.getenv("MOONSHOT_API_KEY"))
        self.model_name = "moonshot-v1-8k-vision-preview"

    @staticmethod
    def _split_api_keys(raw_api_keys: Optional[str]) -> List[str]:
        """
        解析 MOONSHOT_API_KEY：
        - 支持逗号、中文逗号、换行分隔
        - 自动去重与去空白
        """
        raw = (raw_api_keys or "").strip()
        if not raw:
            return []

        parts = re.split(r"[,\n\r，]+", raw)
        normalized: List[str] = []
        seen = set()

        for part in parts:
            key = part.strip().strip('"').strip("'").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(key)

        return normalized

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

    @staticmethod
    def _extract_image_bytes(image_payload: Dict[str, Any]) -> Optional[bytes]:
        direct_bytes = image_payload.get("data") or image_payload.get("bytes")
        if isinstance(direct_bytes, (bytes, bytearray)) and direct_bytes:
            return bytes(direct_bytes)

        image_base64 = image_payload.get("image_base64")
        if isinstance(image_base64, str) and image_base64.strip():
            try:
                return base64.b64decode(image_base64.strip(), validate=True)
            except Exception:
                log.warning("Moonshot 收到的 image_base64 不是有效的 Base64 数据。")

        data_preview = image_payload.get("data_preview")
        if isinstance(data_preview, str) and data_preview.strip():
            normalized_preview = data_preview.strip()
            try:
                return bytes.fromhex(normalized_preview)
            except ValueError:
                try:
                    return base64.b64decode(normalized_preview, validate=True)
                except Exception:
                    log.warning("Moonshot 收到的 data_preview 既不是有效 hex，也不是有效 Base64。")

        return None

    @staticmethod
    def _guess_mime_type_from_magic_bytes(image_bytes: bytes) -> Optional[str]:
        if not image_bytes or len(image_bytes) < 4:
            return None

        if image_bytes.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if image_bytes.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            return "image/webp"

        return None

    @staticmethod
    def _normalize_image_for_moonshot(
        image_bytes: bytes, mime_type: str
    ) -> tuple[bytes, str]:
        """
        将 Moonshot 可能不稳定支持的图片格式统一转为 PNG。
        目前优先放行 JPEG/PNG，其余 Pillow 可解码格式（GIF/WEBP 等）统一转 PNG。
        """
        normalized_mime_type = str(mime_type or "").strip().lower()
        if normalized_mime_type in {"image/png", "image/jpeg", "image/jpg"}:
            return image_bytes, "image/jpeg" if normalized_mime_type == "image/jpg" else normalized_mime_type

        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                output_buffer = io.BytesIO()
                image.convert("RGBA").save(output_buffer, format="PNG")
                converted_bytes = output_buffer.getvalue()
            return converted_bytes, "image/png"
        except Exception as e:
            log.warning(
                "Moonshot 图片转 PNG 失败，将继续使用原始格式。mime=%s error=%s",
                normalized_mime_type or "<empty>",
                e,
            )
            return image_bytes, normalized_mime_type or "application/octet-stream"

    async def recognize_image(
        self,
        image_payload: Dict[str, Any],
        prompt: str = "请识别这张图片的主要内容，描述关键对象、场景和文字信息，如果图片是以文字为主，**必须**完整提取全部文字，禁止自行概括/删改。",
        user_input_text: Optional[str] = None,
    ) -> str:
        """
        识别图片内容。

        image_payload 预期结构：
        {
            "type": "image",
            "mime_type": "image/webp",
            "data": b"..."
        }
        """
        if not self.moonshot_url or not self.moonshot_api_keys:
            return "（图片识别不可用：MOONSHOT_URL 或 MOONSHOT_API_KEY 未配置）"

        if not isinstance(image_payload, dict):
            return "（图片识别失败：图片数据格式异常）"

        mime_type = image_payload.get("mime_type")
        declared_size = image_payload.get("data_size")

        if not isinstance(mime_type, str) or not mime_type:
            return "（图片识别失败：缺少 mime_type）"

        image_bytes = self._extract_image_bytes(image_payload)
        if not image_bytes:
            return "（图片识别失败：缺少图片数据）"

        direct_bytes = image_payload.get("data") or image_payload.get("bytes")
        payload_source = image_payload.get("source", "unknown")
        payload_name = image_payload.get("name")
        payload_has_data_preview = bool(image_payload.get("data_preview"))
        payload_data_source = (
            "direct-bytes"
            if isinstance(direct_bytes, (bytes, bytearray)) and direct_bytes
            else "encoded-preview"
        )
        magic_mime_type = self._guess_mime_type_from_magic_bytes(image_bytes)
        if magic_mime_type and str(mime_type).strip().lower() != magic_mime_type:
            log.warning(
                "Moonshot 图片 MIME 与文件头不一致 | incoming_mime=%s | magic_mime=%s | source=%s | name=%s",
                mime_type,
                magic_mime_type,
                payload_source,
                payload_name,
            )

        if (
            isinstance(declared_size, int)
            and declared_size > 0
            and declared_size != len(image_bytes)
        ):
            log.warning(
                "Moonshot 图片长度与声明不一致：declared=%s, actual=%s",
                declared_size,
                len(image_bytes),
            )

        image_bytes, mime_type = self._normalize_image_for_moonshot(
            image_bytes,
            mime_type,
        )

        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        image_data_url = f"data:{mime_type};base64,{image_base64}"

        user_content_blocks: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]

        cleaned_user_input = str(user_input_text).strip() if user_input_text else ""
        if cleaned_user_input:
            # 纯图片消息会在上层过滤；此处再兜底一次，避免把占位文本注入到视觉模型里
            if "用户消息:(图片消息)" not in cleaned_user_input and cleaned_user_input not in {
                "(图片消息)",
                "图片消息",
            }:
                user_content_blocks.append(
                    {
                        "type": "text",
                        "text": f"用户当前输入，可以参考回答：{cleaned_user_input}",
                    }
                )

        user_content_blocks.append(
            {"type": "image_url", "image_url": {"url": image_data_url}}
        )

        request_payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个图像理解助手。请准确描述图片可见内容，不要编造看不见的信息。",
                },
                {
                    "role": "user",
                    "content": user_content_blocks,
                },
            ],
            "temperature": 0.1,
            "stream": False,
        }

        api_url = self._build_api_url(self.moonshot_url)

        last_error: Optional[str] = None
        total_keys = len(self.moonshot_api_keys)
        timeout_seconds = 10.0
        timeout_fallback = "图像识别超时，已熔断"
        start_time = asyncio.get_running_loop().time()

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            for idx, api_key in enumerate(self.moonshot_api_keys, start=1):
                key_tail = api_key[-4:] if api_key else "????"
                try:
                    elapsed = asyncio.get_running_loop().time() - start_time
                    remaining = timeout_seconds - elapsed
                    if remaining <= 0:
                        log.warning(
                            "Moonshot 视觉识别总耗时超过 %.1fs，触发熔断。", timeout_seconds
                        )
                        return timeout_fallback

                    response = await asyncio.wait_for(
                        client.post(
                            api_url,
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                            },
                            json=request_payload,
                        ),
                        timeout=remaining,
                    )
                    response.raise_for_status()
                    data = response.json()

                    text = self._extract_text_from_response(data)
                    if text:
                        if idx > 1:
                            log.warning(
                                "Moonshot 视觉识别已切换到后续 key 成功 | key_index=%s/%s | key_tail=%s",
                                idx,
                                total_keys,
                                key_tail,
                            )
                        return text

                    last_error = "视觉服务响应中缺少文本结果"
                    log.warning(
                        "Moonshot 视觉识别失败，尝试下一个 key | key_index=%s/%s | key_tail=%s | reason=%s",
                        idx,
                        total_keys,
                        key_tail,
                        last_error,
                    )
                except asyncio.TimeoutError:
                    log.warning(
                        "Moonshot 视觉识别请求超时并触发熔断 | key_index=%s/%s | key_tail=%s",
                        idx,
                        total_keys,
                        key_tail,
                    )
                    return timeout_fallback
                except Exception as e:
                    if isinstance(e, httpx.HTTPStatusError):
                        status_code = e.response.status_code if e.response else "unknown"
                        try:
                            body_preview = (e.response.text or "")[:800] if e.response else ""
                        except Exception:
                            body_preview = "<无法读取响应体>"
                        last_error = f"HTTP {status_code} | {body_preview}"
                    else:
                        last_error = str(e)

                    log.warning(
                        "Moonshot 视觉识别失败，尝试下一个 key | key_index=%s/%s | key_tail=%s | reason=%s",
                        idx,
                        total_keys,
                        key_tail,
                        last_error,
                        exc_info=True,
                    )

        log.error("Moonshot 视觉识别全部 key 失败 | total_keys=%s | last_error=%s", total_keys, last_error)
        return "（图片识别失败：视觉服务暂时不可用）"


moonshot_vision_service = MoonshotVisionService()
