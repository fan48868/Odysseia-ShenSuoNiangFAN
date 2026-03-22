# -*- coding: utf-8 -*-

import base64
import copy
import io
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from PIL import Image

from src.chat.services.moonshot_vision_service import moonshot_vision_service

log = logging.getLogger(__name__)


class DeepSeekModelClient:
    """DeepSeek 通道客户端：负责配置管理、请求发送与 DeepSeek 专属内容构建。"""

    def __init__(self) -> None:
        self.base_url = os.getenv("DEEPSEEK_URL")
        self.api_key = os.getenv("DEEPSEEK_API_KEY")

        if self.base_url and self.api_key:
            log.info("✅ [DeepSeekModelClient] 已加载 DeepSeek 配置。URL: %s", self.base_url)

    @staticmethod
    def _build_chat_completions_url(base_url: str) -> str:
        normalized = (base_url or "").rstrip("/")
        if not normalized.endswith("/chat/completions"):
            normalized += "/chat/completions"
        return normalized

    @staticmethod
    def _build_moonshot_image_payload_from_pil(image: Image.Image) -> Dict[str, Any]:
        """将 PIL 图片转换为 Moonshot 识别所需 payload。"""
        mime_type = "image/webp"
        buffered = io.BytesIO()
        try:
            image.save(buffered, format="WEBP")
        except Exception:
            mime_type = "image/png"
            buffered = io.BytesIO()
            image.save(buffered, format="PNG")

        image_bytes = buffered.getvalue()
        return {
            "type": "image",
            "mime_type": mime_type,
            "data_size": len(image_bytes),
            "data_preview": image_bytes.hex(),
        }

    async def build_turn_content(self, parts: List[Any]) -> str:
        """
        构建单条消息在 DeepSeek 通道中的文本内容。

        对于图片，调用 Moonshot 进行识别，并将所有【图片识别结果】统一追加到该条消息文本末尾，
        避免当图片（例如表情/贴纸）出现在文本前方时，OCR 结果抢占 content 开头。
        """
        content_chunks: List[str] = []
        vision_chunks: List[str] = []

        # 额外注入“当前用户输入”到识图请求中（放在图片前），帮助视觉模型理解提问意图：
        # - 只传入输入本身，不包含“上下文提示/引用回复”等前缀
        # - 若该轮只有图片（无文本输入），则不注入
        user_input_text_for_vision: Optional[str] = None
        try:
            text_fragments: List[str] = []
            for part in parts or []:
                if hasattr(part, "thought") and getattr(part, "thought", False):
                    continue

                if isinstance(part, Image.Image) or (
                    isinstance(part, dict) and part.get("type") == "image"
                ):
                    continue

                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text_fragments.append(part["text"])
                else:
                    text_fragments.append(str(part))

            combined_text_for_vision = "".join(text_fragments).strip()
            if combined_text_for_vision:
                marker = "以下是当前输入内容:"
                candidate = (
                    combined_text_for_vision.split(marker, 1)[-1].strip()
                    if marker in combined_text_for_vision
                    else combined_text_for_vision
                )

                matches = list(re.finditer(r"\[[^\]]+\]:(?:\s*)", candidate))
                if matches:
                    candidate = candidate[matches[-1].end() :].strip()
                    if candidate and "用户消息:(图片消息)" not in candidate:
                        user_input_text_for_vision = candidate
        except Exception:
            user_input_text_for_vision = None

        first_effective_part_seen = False
        first_effective_part_is_image = False

        for part in parts or []:
            if hasattr(part, "thought") and getattr(part, "thought", False):
                continue

            if not first_effective_part_seen:
                first_effective_part_seen = True
                first_effective_part_is_image = isinstance(part, Image.Image) or (
                    isinstance(part, dict) and part.get("type") == "image"
                )

            if isinstance(part, dict) and isinstance(part.get("text"), str):
                content_chunks.append(part["text"])
                continue

            if isinstance(part, Image.Image):
                try:
                    image_payload = self._build_moonshot_image_payload_from_pil(part)
                    vision_text = await moonshot_vision_service.recognize_image(
                        image_payload,
                        user_input_text=user_input_text_for_vision,
                    )
                except Exception as e:
                    log.error("Moonshot 图片识别流程异常: %s", e, exc_info=True)
                    vision_text = "（图片识别失败：处理流程异常）"

                vision_chunks.append(str(vision_text))
                continue

            if isinstance(part, dict) and part.get("type") == "image":
                try:
                    vision_text = await moonshot_vision_service.recognize_image(
                        part,
                        user_input_text=user_input_text_for_vision,
                    )
                except Exception as e:
                    log.error("Moonshot 图片字典识别异常: %s", e, exc_info=True)
                    vision_text = "（图片识别失败：处理流程异常）"

                vision_chunks.append(str(vision_text))
                continue

            content_chunks.append(str(part))

        base_text = "".join(content_chunks).strip()

        vision_items: List[str] = []
        for vision_text in vision_chunks:
            cleaned = str(vision_text).strip()
            if not cleaned:
                continue
            vision_items.append(f"【图片识别结果】{cleaned}")

        if not vision_items:
            return base_text

        ocr_text = "   ".join(vision_items)

        if not base_text:
            return ocr_text

        combined = f"{base_text}   {ocr_text}"
        if first_effective_part_is_image:
            combined = "\n" + combined
        return combined

    async def post_process_tool_response(self, raw_response: Any) -> Any:
        """
        DeepSeek 专属工具结果后处理：
        对 get_user_profile 返回的头像/横幅图片做识图摘要，并移除原始 base64。
        """
        clean_response = copy.deepcopy(raw_response)

        if not isinstance(clean_response, dict):
            return clean_response

        result_data = clean_response.get("result", {})
        if not isinstance(result_data, dict):
            return clean_response

        profile = result_data.get("profile", {})
        if not isinstance(profile, dict):
            return clean_response

        image_fields = [
            (
                "avatar_image_base64",
                "avatar_mime_type",
                "avatar",
                "请识别这张用户头像图片，简洁描述可见人物、风格、配色与关键元素。",
                "头像",
            ),
            (
                "banner_image_base64",
                "banner_mime_type",
                "banner",
                "请识别这张用户横幅图片，简洁描述画面内容、风格、配色与关键信息。",
                "横幅",
            ),
        ]

        for (
            image_key,
            mime_key,
            label,
            vision_prompt,
            label_cn,
        ) in image_fields:
            image_b64 = profile.get(image_key)
            if not (isinstance(image_b64, str) and image_b64.strip()):
                continue

            try:
                image_bytes = base64.b64decode(image_b64)
                image_mime_type = profile.get(mime_key, "image/png")
                if not isinstance(image_mime_type, str) or not image_mime_type:
                    image_mime_type = "image/png"

                image_payload = {
                    "type": "image",
                    "mime_type": image_mime_type,
                    "data_size": len(image_bytes),
                    "data_preview": image_bytes.hex(),
                }
                image_vision_text = await moonshot_vision_service.recognize_image(
                    image_payload,
                    prompt=vision_prompt,
                )
                profile[f"{label}_image_vision"] = image_vision_text
            except Exception as e:
                log.error(
                    "处理 get_user_profile %s识图失败: %s",
                    label_cn,
                    e,
                    exc_info=True,
                )
                profile[f"{label}_image_vision"] = f"（{label_cn}识图失败：处理异常）"
            finally:
                profile.pop(image_key, None)
                profile.setdefault(
                    f"{label}_note",
                    f"（{label_cn}原始图片数据已省略，已提供识图摘要）",
                )

        return clean_response

    def get_validation_error(self, model_name: str) -> Optional[str]:
        if self.base_url and self.api_key:
            return None

        log.warning("请求使用 %s 但未配置 DEEPSEEK_URL 或 DEEPSEEK_API_KEY。", model_name)
        return "DeepSeek 配置缺失，请检查环境变量。"

    async def send(
        self,
        http_client: httpx.AsyncClient,
        payload: Dict[str, Any],
        override_base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        api_url = (override_base_url or self.base_url or "").rstrip("/")
        api_key = self.api_key or ""

        if not api_url:
            raise ValueError("OpenAI 兼容通道 URL 配置缺失，请检查配置。")
        if not api_key:
            raise ValueError("OpenAI 兼容通道 API Key 配置缺失，请检查配置。")

        api_url = self._build_chat_completions_url(api_url)

        response = await http_client.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()

        return {
            "response": response,
            "used_api_url": api_url,
            "used_slot_label": "deepseek",
            "used_slot_id": None,
            "used_key_tail": "N/A",
            "used_model_name": str(payload.get("model", "")),
            "skip_custom_site_for_this_turn": False,
        }