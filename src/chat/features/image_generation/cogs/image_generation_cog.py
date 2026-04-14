# -*- coding: utf-8 -*-

import asyncio
import base64
import io
import json
import logging
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from src import config

log = logging.getLogger(__name__)

PRESET_CHARACTER_PROMPT = """主角:
狮子娘：一位动漫少女，拥有及肩的棕色渐变短发，一侧编有麻花辫，辫子上装饰着🤣图标发饰。她有着大而富有表现力的棕色眼睛。身穿白色翻领衬衫、黑色领带和棕色V领毛衣背心。
类脑娘：和狮子娘穿着外貌都相同，唯一不同的是类脑娘辫子上是ai公司claude的橘色菊花图标。"""


def _truncate_text(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _escape_code_block(text: str) -> str:
    return (text or "").replace("```", "`\u200b``")


def _format_prompt_block(prompt: str, limit: int = 3200) -> str:
    prompt_preview = _truncate_text(prompt, limit)
    return f"```text\n{_escape_code_block(prompt_preview)}\n```"


def _guess_mime_type_from_filename(filename: str) -> str:
    guessed_mime_type, _ = mimetypes.guess_type(str(filename or ""))
    return guessed_mime_type or "image/png"


def _strip_wrapping_quotes(value: str) -> str:
    value = (value or "").strip()
    quote_pairs = [
        ('"', '"'),
        ("'", "'"),
        ("“", "”"),
        ("‘", "’"),
    ]

    while value:
        changed = False
        for left, right in quote_pairs:
            if value.startswith(left) and value.endswith(right):
                value = value[len(left) : len(value) - len(right)].strip()
                changed = True
                break
        if not changed:
            break

    return value


def _strip_inline_comment(value: str) -> str:
    if not value:
        return ""

    result: list[str] = []
    open_quote_to_close = {
        '"': '"',
        "'": "'",
        "“": "”",
        "‘": "’",
    }
    expected_closers: list[str] = []

    for char in value:
        if expected_closers and char == expected_closers[-1]:
            result.append(char)
            expected_closers.pop()
            continue

        if not expected_closers and char in open_quote_to_close:
            result.append(char)
            expected_closers.append(open_quote_to_close[char])
            continue

        if not expected_closers and char == "#":
            break

        result.append(char)

    return "".join(result).strip()


def _extract_error_message(payload: Any) -> str:
    if payload is None:
        return ""

    if isinstance(payload, str):
        return payload.strip()

    if isinstance(payload, dict):
        for key in ("error", "message", "detail", "msg"):
            if key not in payload:
                continue
            nested = _extract_error_message(payload.get(key))
            if nested:
                return nested

        for value in payload.values():
            nested = _extract_error_message(value)
            if nested:
                return nested
        return ""

    if isinstance(payload, list):
        for item in payload:
            nested = _extract_error_message(item)
            if nested:
                return nested
        return ""

    return str(payload).strip()


def _extract_text_from_content_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue

        text = ""
        if item.get("type") == "text":
            text = str(item.get("text", "") or "").strip()
        elif "text" in item:
            text = str(item.get("text", "") or "").strip()

        if text:
            parts.append(text)

    return "\n".join(parts).strip()


def _extract_model_text_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    choices = payload.get("choices", [])
    if not isinstance(choices, list):
        return ""

    text_parts: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue

        message = choice.get("message")
        if isinstance(message, dict):
            content_text = _extract_text_from_content_blocks(message.get("content"))
            if content_text:
                text_parts.append(content_text)

        delta = choice.get("delta")
        if isinstance(delta, dict):
            delta_text = _extract_text_from_content_blocks(delta.get("content"))
            if delta_text:
                text_parts.append(delta_text)

    deduped_parts: list[str] = []
    for part in text_parts:
        if part and part not in deduped_parts:
            deduped_parts.append(part)

    return "\n\n".join(deduped_parts).strip()


def _get_developer_user_ids() -> set[int]:
    developer_ids = set(config.DEVELOPER_USER_IDS)
    raw_value = _strip_wrapping_quotes(str(os.getenv("DEVELOPER_USER_IDS", "") or ""))

    if not raw_value:
        env_path = os.path.join(config.BASE_DIR, ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as env_file:
                    for raw_line in env_file:
                        line = raw_line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue

                        key, value = line.split("=", 1)
                        if key.strip() != "DEVELOPER_USER_IDS":
                            continue

                        raw_value = _strip_wrapping_quotes(_strip_inline_comment(value))
                        break
            except Exception as exc:
                log.warning("读取 .env 中的 DEVELOPER_USER_IDS 失败: %s", exc)

    if not raw_value:
        return developer_ids

    for part in raw_value.split(","):
        cleaned = _strip_wrapping_quotes(part).strip()
        if cleaned.isdigit():
            developer_ids.add(int(cleaned))

    return developer_ids


@dataclass(slots=True)
class GeneratedImage:
    data: bytes
    filename: str


@dataclass(slots=True)
class ReferenceImageInput:
    data: bytes
    filename: str
    mime_type: str


@dataclass(slots=True)
class ImageGenerationError:
    message: str
    status_code: int | None = None
    payload: Any = None
    model_text: str | None = None

    def should_rotate_key(self) -> bool:
        return self.status_code == 402


@dataclass(slots=True)
class GlobalQuotaStatus:
    date_key: str
    daily_limit: int
    used_count: int

    @property
    def remaining(self) -> int:
        return max(self.daily_limit - self.used_count, 0)


@dataclass(slots=True)
class QuotaReservation:
    date_key: str


@dataclass(slots=True)
class ImageGenerationUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal("0")


class GatewayImageClient:
    CHAT_COMPLETIONS_API_URL = "https://site.atopes.de/v1/chat/completions"
    PRIMARY_ENV_KEY = "IMAGINE_API_KEY"
    LEGACY_ENV_KEYS = ()
    GEMINI_PRO_MODEL = "gemini-3-pro-image-preview"
    GEMINI_FLASH_MODEL = "gemini-3.1-flash-image-preview"
    DEFAULT_MODEL = GEMINI_FLASH_MODEL

    def __init__(self):
        self._lock = asyncio.Lock()
        self._active_key_index = 0
        self._active_key_value: str | None = None

    @staticmethod
    def _get_env_path() -> str:
        return os.path.join(config.BASE_DIR, ".env")

    def _read_raw_env_value(self, *env_keys: str) -> str:
        env_path = self._get_env_path()
        cleaned_keys = [key.strip() for key in env_keys if key and key.strip()]
        if not cleaned_keys:
            return ""

        parsed_values: dict[str, str] = {}
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as env_file:
                    for raw_line in env_file:
                        line = raw_line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue

                        key, value = line.split("=", 1)
                        normalized_key = key.strip()
                        if normalized_key not in cleaned_keys:
                            continue

                        parsed_values[normalized_key] = _strip_inline_comment(value)
            except Exception as exc:
                log.warning("读取 %s 失败，将回退到进程环境变量: %s", env_path, exc)

        for key in cleaned_keys:
            value = _strip_wrapping_quotes(parsed_values.get(key, "")).strip()
            if value:
                return parsed_values[key]

        for key in cleaned_keys:
            value = str(os.getenv(key, "") or "").strip()
            if value:
                return value

        return ""

    def _load_api_keys(self) -> list[str]:
        raw_value = self._read_raw_env_value(
            self.PRIMARY_ENV_KEY,
            *self.LEGACY_ENV_KEYS,
        )
        cleaned_value = _strip_wrapping_quotes(raw_value)
        return [
            _strip_wrapping_quotes(part)
            for part in cleaned_value.split(",")
            if _strip_wrapping_quotes(part)
        ]

    @classmethod
    def supported_models(cls) -> tuple[str, ...]:
        return (
            cls.GEMINI_PRO_MODEL,
            cls.GEMINI_FLASH_MODEL,
        )

    @classmethod
    def normalize_model_name(cls, model_name: str | None) -> str:
        normalized = str(model_name or "").strip()
        if normalized in cls.supported_models():
            return normalized
        return cls.DEFAULT_MODEL

    @classmethod
    def get_next_model(cls, current_model: str | None) -> str:
        normalized_model = cls.normalize_model_name(current_model)
        models = cls.supported_models()
        current_index = models.index(normalized_model)
        return models[(current_index + 1) % len(models)]

    @classmethod
    def get_model_label(cls, model_name: str | None) -> str:
        normalized_model = cls.normalize_model_name(model_name)
        if normalized_model == cls.GEMINI_PRO_MODEL:
            return "Gemini Pro"
        if normalized_model == cls.GEMINI_FLASH_MODEL:
            return "Gemini 3.1 Flash"
        return normalized_model

    @classmethod
    def uses_chat_completions_api(cls, model_name: str | None) -> bool:
        # 所有模型都使用 chat completions API
        return True

    @classmethod
    def supports_reference_image(cls, model_name: str | None) -> bool:
        return cls.uses_chat_completions_api(model_name)


    @staticmethod
    def _build_data_url(image_bytes: bytes, mime_type: str) -> str:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        normalized_mime_type = str(mime_type or "image/png").strip() or "image/png"
        return f"data:{normalized_mime_type};base64,{image_b64}"

    @classmethod
    def _build_request(
        cls,
        model_name: str,
        prompt: str,
        reference_images: list[ReferenceImageInput] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        normalized_model = cls.normalize_model_name(model_name)
        normalized_reference_images = list(reference_images or [])
        if normalized_reference_images:
            content_blocks: list[dict[str, Any]] = [
                {"type": "text", "text": prompt},
            ]
            for reference_image in normalized_reference_images:
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": cls._build_data_url(
                                reference_image.data,
                                reference_image.mime_type,
                            ),
                            "detail": "auto",
                        },
                    }
                )
        else:
            content_blocks = prompt
        return (
            cls.CHAT_COMPLETIONS_API_URL,
            {
                "model": normalized_model,
                "messages": [{"role": "user", "content": content_blocks}],
                "modalities": (
                    ["text", "image"] if normalized_reference_images else ["image"]
                ),
                "stream": False,
            },
        )

    @staticmethod
    async def _read_response_payload(response: aiohttp.ClientResponse) -> Any:
        text = await response.text()
        if not text:
            return None

        try:
            return await response.json(content_type=None)
        except Exception:
            return text

    @staticmethod
    def _build_api_error(status_code: int, payload: Any) -> ImageGenerationError:
        detail = _truncate_text(_extract_error_message(payload), 300)
        if detail:
            message = f"生图接口返回错误（HTTP {status_code}）：{detail}"
        else:
            message = f"生图接口返回错误（HTTP {status_code}）。"
        return ImageGenerationError(
            message=message,
            status_code=status_code,
            payload=payload,
        )

    @staticmethod
    def _build_filename(model_name: str, extension: str = ".png") -> str:
        suffix = extension if extension.startswith(".") else f".{extension}"
        safe_model = "".join(
            char if char.isalnum() else "_" for char in str(model_name or "").lower()
        ).strip("_")
        safe_model = safe_model or "generated_image"
        return f"{safe_model}_{int(time.time())}{suffix}"

    @staticmethod
    def _guess_extension_from_mime_type(mime_type: str) -> str:
        normalized_mime_type = str(mime_type or "").split(";", 1)[0].strip().lower()
        guessed_extension = mimetypes.guess_extension(normalized_mime_type)
        return guessed_extension or ".png"

    @classmethod
    def _decode_data_url_image(
        cls,
        data_url: str,
        model_name: str,
    ) -> tuple[GeneratedImage | None, ImageGenerationError | None]:
        normalized_data_url = str(data_url or "").strip()
        if not normalized_data_url.lower().startswith("data:"):
            return None, ImageGenerationError("图片数据格式不正确。")

        if "," not in normalized_data_url:
            return None, ImageGenerationError("图片 data URI 缺少数据体。")

        header, encoded_data = normalized_data_url.split(",", 1)
        if ";base64" not in header.lower():
            return None, ImageGenerationError("图片 data URI 不是 base64 编码。")

        mime_type = header[5:].split(";", 1)[0].strip() or "image/png"
        try:
            image_bytes = base64.b64decode(encoded_data)
        except Exception as exc:
            return None, ImageGenerationError(f"解析 data URI 图片失败：{exc}")

        return (
            GeneratedImage(
                data=image_bytes,
                filename=cls._build_filename(
                    model_name,
                    cls._guess_extension_from_mime_type(mime_type),
                ),
            ),
            None,
        )

    @staticmethod
    def _extract_markdown_image_url(text: str) -> str | None:
        """从 Markdown 文本中提取第一个图片 URL。
        支持格式：
        - ![alt](url)
        - [text](url) （仅当 url 以常见图片扩展名结尾时）
        - 直接 http/https URL 且以图片扩展名结尾
        """
        if not text:
            return None

        # 匹配 Markdown 图片: ![alt](url)
        match = re.search(r'!\[.*?\]\(([^)]+)\)', text)
        if match:
            url = match.group(1).strip()
            if url.startswith(('http://', 'https://')):
                return url

        # 匹配 Markdown 链接: [text](url) 且 url 是图片
        match = re.search(r'\[.*?\]\(([^)]+)\)', text)
        if match:
            url = match.group(1).strip()
            if url.startswith(('http://', 'https://')) and re.search(r'\.(png|jpg|jpeg|gif|webp)(\?.*)?$', url, re.I):
                return url

        # 匹配裸 URL 且是图片
        match = re.search(r'https?://[^\s<>]+?\.(?:png|jpg|jpeg|gif|webp)(?:\?[^\s<>]*)?', text, re.I)
        if match:
            return match.group(0)

        return None

    @classmethod
    def _collect_image_candidates(
        cls,
        model_name: str,
        payload: Any,
    ) -> list[Any]:
        if not isinstance(payload, dict):
            return []

        if cls.uses_chat_completions_api(model_name):
            candidates: list[Any] = []
            choices = payload.get("choices", [])
            if not isinstance(choices, list):
                return candidates

            for choice in choices:
                if not isinstance(choice, dict):
                    continue

                message = choice.get("message")
                if not isinstance(message, dict):
                    continue

                images = message.get("images", [])
                if isinstance(images, list):
                    candidates.extend(images)

            return candidates

        data = payload.get("data", [])
        if isinstance(data, list):
            return data
        return []

    @classmethod
    async def _build_generated_image_from_candidate(
        cls,
        session: aiohttp.ClientSession,
        candidate: Any,
        model_name: str,
    ) -> tuple[GeneratedImage | None, ImageGenerationError | None]:
        image_url = ""
        image_b64 = ""

        if isinstance(candidate, dict):
            image_url = str(candidate.get("url") or "").strip()
            image_b64 = str(candidate.get("b64_json") or "").strip()

            if not image_url:
                nested_image_url = candidate.get("image_url")
                if isinstance(nested_image_url, dict):
                    image_url = str(nested_image_url.get("url") or "").strip()
                elif isinstance(nested_image_url, str):
                    image_url = nested_image_url.strip()
        elif isinstance(candidate, str):
            image_url = candidate.strip()

        if image_url:
            if image_url.lower().startswith("data:"):
                return cls._decode_data_url_image(image_url, model_name)

            return await cls._download_image(
                session,
                image_url,
                cls._build_filename(model_name),
            )

        if image_b64:
            try:
                image_bytes = base64.b64decode(image_b64)
                return (
                    GeneratedImage(
                        data=image_bytes,
                        filename=cls._build_filename(model_name),
                    ),
                    None,
                )
            except Exception as exc:
                return None, ImageGenerationError(f"解析图片数据失败：{exc}")

        return None, None

    async def _extract_generated_image(
        self,
        session: aiohttp.ClientSession,
        model_name: str,
        response_payload: Any,
        *,
        status_code: int | None = None,
    ) -> tuple[GeneratedImage | None, ImageGenerationError | None]:
        candidates = self._collect_image_candidates(model_name, response_payload)
        first_error: ImageGenerationError | None = None

        for candidate in candidates:
            image, error = await self._build_generated_image_from_candidate(
                session,
                candidate,
                model_name,
            )
            if image is not None:
                return image, None
            if error is not None and first_error is None:
                first_error = error

        # 如果从标准 candidates 中没有找到图片，尝试从模型返回的文本中提取 Markdown 图片链接
        model_text = _extract_model_text_message(response_payload)
        if model_text:
            image_url = self._extract_markdown_image_url(model_text)
            if image_url:
                # 下载图片
                image, error = await self._download_image(
                    session,
                    image_url,
                    self._build_filename(model_name),
                )
                if image is not None:
                    return image, None
                if error is not None and first_error is None:
                    first_error = error

        if first_error is not None:
            return None, first_error

        return None, ImageGenerationError(
            "没有在返回结果中找到可用的图片数据。可能是被审核截断。",
            status_code=status_code,
            payload=response_payload,
            model_text=model_text or None,
        )

    @staticmethod
    async def _download_image(
        session: aiohttp.ClientSession,
        image_url: str,
        filename: str,
    ) -> tuple[GeneratedImage | None, ImageGenerationError | None]:
        try:
            async with session.get(image_url) as response:
                if response.status < 200 or response.status >= 300:
                    payload = await GatewayImageClient._read_response_payload(response)
                    return None, GatewayImageClient._build_api_error(
                        response.status,
                        payload,
                    )

                return GeneratedImage(data=await response.read(), filename=filename), None
        except asyncio.TimeoutError:
            return None, ImageGenerationError("下载生成图片超时，请稍后再试。")
        except aiohttp.ClientError as exc:
            return None, ImageGenerationError(f"下载生成图片失败：{exc}")

    async def _generate_with_key(
        self,
        api_key: str,
        prompt: str,
        model_name: str,
        reference_images: list[ReferenceImageInput] | None = None,
    ) -> tuple[GeneratedImage | None, ImageGenerationError | None]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        api_url, payload = self._build_request(
            model_name,
            prompt,
            reference_images=reference_images,
        )
        timeout = aiohttp.ClientTimeout(total=180)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    api_url,
                    headers=headers,
                    json=payload,
                ) as response:
                    response_payload = await self._read_response_payload(response)
                    if response.status < 200 or response.status >= 300:
                        error = self._build_api_error(
                            response.status,
                            response_payload,
                        )
                        return None, error

                    image, error = await self._extract_generated_image(
                        session,
                        model_name,
                        response_payload,
                        status_code=response.status,
                    )
                    return image, error
        except asyncio.TimeoutError:
            return None, ImageGenerationError("请求生图接口超时，请稍后再试。")
        except aiohttp.ClientError as exc:
            return None, ImageGenerationError(f"请求生图接口失败：{exc}")
        except Exception as exc:
            log.error("调用生图接口时发生未预期错误: %s", exc, exc_info=True)
            return None, ImageGenerationError(f"生成图片时发生错误：{exc}")

    async def generate_image(
        self,
        prompt: str,
        model_name: str | None = None,
        reference_images: list[ReferenceImageInput] | None = None,
    ) -> tuple[GeneratedImage | None, ImageGenerationError | None]:
        prompt = (prompt or "").strip()
        if not prompt:
            return None, ImageGenerationError("提示词不能为空。")

        normalized_model = self.normalize_model_name(model_name)
        normalized_reference_images = list(reference_images or [])
        if normalized_reference_images and not self.supports_reference_image(
            normalized_model
        ):
            return None, ImageGenerationError("当前模型暂不支持参考图。")

        async with self._lock:
            api_keys = self._load_api_keys()
            if not api_keys:
                return None, ImageGenerationError(
                    "未在 .env 中找到可用的 IMAGINE_API_KEY 配置。"
                )

            if self._active_key_value and self._active_key_value in api_keys:
                self._active_key_index = api_keys.index(self._active_key_value)
            elif self._active_key_index >= len(api_keys):
                self._active_key_index = 0

            for key_index in range(self._active_key_index, len(api_keys)):
                api_key = api_keys[key_index]
                self._active_key_index = key_index
                self._active_key_value = api_key

                image, error = await self._generate_with_key(
                    api_key,
                    prompt,
                    normalized_model,
                    reference_images=normalized_reference_images,
                )
                if image is not None:
                    return image, None

                if error is None:
                    return None, ImageGenerationError("生成图片失败，但没有拿到具体错误信息。")

                if error.should_rotate_key():
                    next_index = key_index + 1
                    if next_index < len(api_keys):
                        self._active_key_index = next_index
                        self._active_key_value = api_keys[next_index]
                        log.warning(
                            "生图当前 key 触发 402 payment required，切换到下一个 key。"
                        )
                        continue

                    return None, ImageGenerationError(
                        "所有 IMAGINE_API_KEY 都已触发 402 payment required，请检查余额。",
                        status_code=402,
                        payload=error.payload,
                    )

                return None, error

            return None, ImageGenerationError("没有可用的 IMAGINE_API_KEY。")


class GlobalDailyQuotaService:
    DEFAULT_DAILY_LIMIT = 10

    def __init__(self):
        self._lock = asyncio.Lock()
        try:
            self._tz = ZoneInfo("Asia/Shanghai")
        except ZoneInfoNotFoundError:
            self._tz = timezone(timedelta(hours=8))
        self._state_path = os.path.join(config.DATA_DIR, "image_generation_quota.json")

    def _today_key(self) -> str:
        return datetime.now(self._tz).date().isoformat()

    def _default_state(self) -> dict[str, Any]:
        return {
            "daily_limit": self.DEFAULT_DAILY_LIMIT,
            "days": {},
        }

    def _load_state_unlocked(self) -> dict[str, Any]:
        if not os.path.exists(self._state_path):
            return self._default_state()

        try:
            with open(self._state_path, "r", encoding="utf-8") as state_file:
                data = json.load(state_file)
                if not isinstance(data, dict):
                    return self._default_state()
                return data
        except Exception as exc:
            log.warning("读取生图配额文件失败，将使用默认值: %s", exc)
            return self._default_state()

    def _save_state_unlocked(self, state: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        with open(self._state_path, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, ensure_ascii=False, indent=2)

    def _normalize_state(self, state: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(state, dict):
            state = self._default_state()

        daily_limit = state.get("daily_limit", self.DEFAULT_DAILY_LIMIT)
        try:
            daily_limit = int(daily_limit)
        except Exception:
            daily_limit = self.DEFAULT_DAILY_LIMIT
        daily_limit = max(daily_limit, 0)

        days = state.get("days", {})
        if not isinstance(days, dict):
            days = {}

        normalized_days: dict[str, dict[str, int]] = {}
        for date_key, raw_day in days.items():
            if not isinstance(date_key, str) or not isinstance(raw_day, dict):
                continue
            used_count = raw_day.get("used_count", 0)
            try:
                used_count = int(used_count)
            except Exception:
                used_count = 0
            normalized_days[date_key] = {"used_count": max(used_count, 0)}

        return {
            "daily_limit": daily_limit,
            "days": normalized_days,
        }

    def _ensure_day_record(
        self,
        state: dict[str, Any],
        date_key: str,
    ) -> dict[str, int]:
        days = state.setdefault("days", {})
        day_record = days.get(date_key)
        if not isinstance(day_record, dict):
            day_record = {"used_count": 0}
            days[date_key] = day_record

        used_count = day_record.get("used_count", 0)
        try:
            used_count = int(used_count)
        except Exception:
            used_count = 0
        day_record["used_count"] = max(used_count, 0)
        return day_record

    def _build_status_from_state(
        self,
        state: dict[str, Any],
        date_key: str,
    ) -> GlobalQuotaStatus:
        day_record = self._ensure_day_record(state, date_key)
        return GlobalQuotaStatus(
            date_key=date_key,
            daily_limit=max(int(state.get("daily_limit", self.DEFAULT_DAILY_LIMIT)), 0),
            used_count=max(int(day_record.get("used_count", 0)), 0),
        )

    async def get_status(self) -> GlobalQuotaStatus:
        async with self._lock:
            state = self._normalize_state(self._load_state_unlocked())
            date_key = self._today_key()
            status = self._build_status_from_state(state, date_key)
            self._save_state_unlocked(state)
            return status

    async def set_daily_limit(self, new_limit: int) -> GlobalQuotaStatus:
        async with self._lock:
            state = self._normalize_state(self._load_state_unlocked())
            state["daily_limit"] = max(int(new_limit), 0)
            date_key = self._today_key()
            status = self._build_status_from_state(state, date_key)
            self._save_state_unlocked(state)
            return status

    async def reserve_generation(
        self,
        *,
        exempt: bool,
    ) -> tuple[bool, GlobalQuotaStatus, QuotaReservation | None]:
        async with self._lock:
            state = self._normalize_state(self._load_state_unlocked())
            date_key = self._today_key()
            status = self._build_status_from_state(state, date_key)

            if exempt:
                self._save_state_unlocked(state)
                return True, status, None

            if status.remaining <= 0:
                self._save_state_unlocked(state)
                return False, status, None

            day_record = self._ensure_day_record(state, date_key)
            day_record["used_count"] += 1
            status = self._build_status_from_state(state, date_key)
            self._save_state_unlocked(state)
            return True, status, QuotaReservation(date_key=date_key)

    async def refund_generation(
        self,
        reservation: QuotaReservation | None,
    ) -> GlobalQuotaStatus:
        async with self._lock:
            state = self._normalize_state(self._load_state_unlocked())
            if reservation is not None:
                day_record = self._ensure_day_record(state, reservation.date_key)
                if day_record["used_count"] > 0:
                    day_record["used_count"] -= 1

            today_key = self._today_key()
            status = self._build_status_from_state(state, today_key)
            self._save_state_unlocked(state)
            return status


class PromptInputModal(discord.ui.Modal, title="输入提示词"):
    def __init__(self, parent_view: "ImageGenerationPanelView", default_prompt: str = ""):
        super().__init__()
        self.parent_view = parent_view

        self.prompt_input = discord.ui.TextInput(
            label="提示词",
            placeholder="例如：一只站在雨夜霓虹街头的白狐，电影感构图，超高细节",
            style=discord.TextStyle.paragraph,
            required=True,
            default=default_prompt[:2000],
            max_length=2000,
        )
        self.add_item(self.prompt_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user or interaction.user.id != self.parent_view.opener_user_id:
            await interaction.response.send_message("这不是你的绘图面板。", ephemeral=True)
            return

        prompt = (self.prompt_input.value or "").strip()
        if not prompt:
            await interaction.response.send_message("提示词不能为空。", ephemeral=True)
            return

        self.parent_view.prompt = prompt
        self.parent_view.last_status = "提示词已更新，可以开始生成。"
        self.parent_view.last_error = None
        self.parent_view.last_public_message_url = None

        await interaction.response.defer(ephemeral=True)
        await self.parent_view.refresh_panel()


class PublicGeneratedImageView(discord.ui.View):
    def __init__(self, requester_user_id: int):
        super().__init__(timeout=None)
        self.requester_user_id = requester_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.requester_user_id:
            return True

        await interaction.response.send_message("只有生成这张图片的人可以删除它。", ephemeral=True)
        return False

    @discord.ui.button(
        label="删除",
        style=discord.ButtonStyle.danger,
        custom_id="generated_image_delete",
    )
    async def delete_generated_message(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.message is None:
            await interaction.response.send_message("找不到要删除的图片消息。", ephemeral=True)
            return

        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass

        try:
            await interaction.message.delete()
            self.stop()
        except discord.Forbidden:
            if interaction.response.is_done():
                await interaction.followup.send("我没有权限删除这条消息。", ephemeral=True)
            else:
                await interaction.response.send_message("我没有权限删除这条消息。", ephemeral=True)
        except discord.HTTPException as exc:
            error_message = f"删除图片消息失败：{exc}"
            if interaction.response.is_done():
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                await interaction.response.send_message(error_message, ephemeral=True)


class ImageGenerationPanelView(discord.ui.View):
    def __init__(
        self,
        *,
        origin_interaction: discord.Interaction,
        image_client: GatewayImageClient,
        quota_service: GlobalDailyQuotaService,
        is_developer: bool,
        reference_images: list[ReferenceImageInput] | None = None,
    ):
        super().__init__(timeout=config.VIEW_TIMEOUT)
        self.origin_interaction = origin_interaction
        self.opener_user_id = origin_interaction.user.id
        self.image_client = image_client
        self.quota_service = quota_service
        self.is_developer = is_developer
        self.reference_images = list(reference_images or [])

        self.prompt = ""
        self.use_spoiler = False
        self.is_generating = False
        self.selected_model = GatewayImageClient.DEFAULT_MODEL
        self.last_status = "点击按钮输入提示词。"
        self.last_error: str | None = None
        self.last_public_message_url: str | None = None
        self.quota_status = GlobalQuotaStatus(
            date_key="",
            daily_limit=GlobalDailyQuotaService.DEFAULT_DAILY_LIMIT,
            used_count=0,
        )

        self._rebuild_items()

    async def initialize(self, initial_status_message: str | None = None) -> None:
        await self._sync_quota_status()
        if initial_status_message:
            self.last_status = initial_status_message
        self._rebuild_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.opener_user_id:
            return True

        await interaction.response.send_message("这不是你的绘图面板。", ephemeral=True)
        return False

    async def _sync_quota_status(self) -> GlobalQuotaStatus:
        self.quota_status = await self.quota_service.get_status()
        return self.quota_status

    def _author_name(self) -> str:
        return self.origin_interaction.user.display_name

    def _author_icon(self) -> str:
        return self.origin_interaction.user.display_avatar.url

    def _quota_footer_text(self) -> str:
        remaining_text = (
            f"今日全局剩余次数：{self.quota_status.remaining}/{self.quota_status.daily_limit}"
        )
        if self.is_developer:
            return f"模型：{self.selected_model} | {remaining_text} | 开发者不限次"
        return f"模型：{self.selected_model} | {remaining_text}"

    def _model_button_label(self) -> str:
        model_label = self.image_client.get_model_label(self.selected_model)
        return f"模型：{model_label}"

    def _reference_image_status_line(self) -> str:
        reference_image_count = len(self.reference_images)
        if reference_image_count > 0:
            return f"当前已传入 {reference_image_count} 张参考图。"
        return "提示：现在可以传入参考图了，输入命令时加上附加参数吧！"

    def _can_generate_now(self) -> bool:
        if self.is_generating or not self.prompt:
            return False
        if self.is_developer:
            return True
        return self.quota_status.remaining > 0

    def _build_panel_embed(self) -> discord.Embed:
        color = config.EMBED_COLOR_PRIMARY
        if self.last_error:
            color = config.EMBED_COLOR_ERROR
        elif self.is_generating:
            color = config.EMBED_COLOR_WARNING

        embed = discord.Embed(title="AI 图片生成", color=color)
        embed.set_author(name=self._author_name(), icon_url=self._author_icon())

        if self.prompt:
            description_lines = [
                "**提示词：**",
                _format_prompt_block(self.prompt, limit=3000),
                f"**遮罩：** {'开启' if self.use_spoiler else '关闭'}",
            ]
        else:
            description_lines = [
                "还没有提示词。",
                "",
                "点击下方按钮输入提示词。",
            ]

        if self.last_error:
            description_lines.extend(["", f"**错误：** {self.last_error}"])
        elif self.last_status:
            description_lines.extend(["", f"**状态：** {self.last_status}"])

        description_lines.append(self._reference_image_status_line())

        if self.last_public_message_url:
            description_lines.extend(
                ["", f"**跳转：** [查看刚刚发送的图片]({self.last_public_message_url})"]
            )

        embed.description = "\n".join(description_lines)
        embed.set_footer(text=self._quota_footer_text())
        return embed

    def _build_public_embed(self, requester: discord.abc.User) -> discord.Embed:
        embed = discord.Embed(
            title="AI 图片生成",
            description=f"**提示词：**\n{_format_prompt_block(self.prompt, limit=3200)}",
            color=config.EMBED_COLOR_PRIMARY,
        )
        embed.set_author(
            name=requester.display_name,
            icon_url=requester.display_avatar.url,
        )
        embed.set_footer(text=f"模型：{self.selected_model}")
        return embed

    def _rebuild_items(self) -> None:
        self.clear_items()

        model_button = discord.ui.Button(
            label=self._model_button_label(),
            style=discord.ButtonStyle.secondary,
            disabled=self.is_generating,
            row=0,
        )
        model_button.callback = self._switch_model

        if not self.prompt:
            input_button = discord.ui.Button(
                label="点击输入提示词",
                style=discord.ButtonStyle.primary,
                disabled=self.is_generating,
                row=0,
            )
            input_button.callback = self._open_prompt_modal
            preset_input_button = discord.ui.Button(
                label="狮子娘&类脑娘",
                style=discord.ButtonStyle.success,
                disabled=self.is_generating,
                row=0,
            )
            preset_input_button.callback = self._open_preset_prompt_modal
            self.add_item(input_button)
            self.add_item(preset_input_button)
            self.add_item(model_button)
            return

        generate_label = "正在生成..." if self.is_generating else "生成图片"
        if not self.is_generating and not self.is_developer and self.quota_status.remaining <= 0:
            generate_label = "今日次数已用尽"

        generate_button = discord.ui.Button(
            label=generate_label,
            style=discord.ButtonStyle.success,
            disabled=not self._can_generate_now(),
            row=0,
        )
        generate_button.callback = self._generate_image
        self.add_item(generate_button)

        modify_button = discord.ui.Button(
            label="修改提示词",
            style=discord.ButtonStyle.secondary,
            disabled=self.is_generating,
            row=0,
        )
        modify_button.callback = self._open_prompt_modal
        self.add_item(modify_button)

        spoiler_button = discord.ui.Button(
            label=f"遮罩：{'开' if self.use_spoiler else '关'}",
            style=(
                discord.ButtonStyle.primary
                if self.use_spoiler
                else discord.ButtonStyle.secondary
            ),
            disabled=self.is_generating,
            row=0,
        )
        spoiler_button.callback = self._toggle_spoiler
        self.add_item(spoiler_button)
        self.add_item(model_button)

    async def refresh_panel(self) -> None:
        await self._sync_quota_status()
        self._rebuild_items()
        await self.origin_interaction.edit_original_response(
            embed=self._build_panel_embed(),
            view=self,
        )

    async def _open_prompt_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            PromptInputModal(self, default_prompt=self.prompt)
        )

    async def _open_preset_prompt_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            PromptInputModal(self, default_prompt=PRESET_CHARACTER_PROMPT)
        )

    async def _toggle_spoiler(self, interaction: discord.Interaction) -> None:
        await self._sync_quota_status()
        self.use_spoiler = not self.use_spoiler
        self.last_status = f"遮罩已{'开启' if self.use_spoiler else '关闭'}。"
        self.last_error = None
        self._rebuild_items()
        await interaction.response.edit_message(
            embed=self._build_panel_embed(),
            view=self,
        )

    async def _switch_model(self, interaction: discord.Interaction) -> None:
        await self._sync_quota_status()
        self.selected_model = self.image_client.get_next_model(self.selected_model)
        self.last_status = f"生成模型已切换为 {self.selected_model}。"
        self.last_error = None
        self._rebuild_items()
        await interaction.response.edit_message(
            embed=self._build_panel_embed(),
            view=self,
        )

    async def _send_public_result(
        self,
        channel: discord.abc.Messageable,
        requester: discord.abc.User,
        image: GeneratedImage,
    ) -> discord.Message:
        public_filename = image.filename
        if self.use_spoiler and not public_filename.startswith("SPOILER_"):
            public_filename = f"SPOILER_{public_filename}"

        image_file = discord.File(io.BytesIO(image.data), filename=public_filename)
        return await channel.send(
            file=image_file,
            embed=self._build_public_embed(requester),
            view=PublicGeneratedImageView(requester.id),
        )

    async def _generate_image(self, interaction: discord.Interaction) -> None:
        if not self.prompt:
            await interaction.response.send_message("请先输入提示词。", ephemeral=True)
            return

        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await interaction.response.send_message("当前频道无法公开发送图片。", ephemeral=True)
            return

        allowed, reserved_status, reservation = await self.quota_service.reserve_generation(
            exempt=self.is_developer
        )
        self.quota_status = reserved_status

        if not allowed:
            self.last_error = "今日全局生成次数已经用完了。"
            self.last_status = "请明天再来，或者联系开发者调整每日次数。"
            self._rebuild_items()
            await interaction.response.edit_message(
                embed=self._build_panel_embed(),
                view=self,
            )
            return

        self.is_generating = True
        self.last_error = None
        self.last_public_message_url = None
        self.last_status = "正在生成图片，请稍候..."
        self._rebuild_items()
        await interaction.response.edit_message(
            embed=self._build_panel_embed(),
            view=self,
        )

        try:
            image, error = await self.image_client.generate_image(
                self.prompt,
                self.selected_model,
                self.reference_images,
            )
            if error is not None:
                self.last_error = error.message
                self.last_status = "生成失败。"
                # 无论是否有模型文本，都发送一条仅发起者可见的消息
                try:
                    if error.model_text:
                        await interaction.followup.send(
                            f"模型返回文本：{_truncate_text(error.model_text, 1800)}",
                            ephemeral=True,
                        )
                    else:
                        await interaction.followup.send(
                            "模型未返回任何文本",
                            ephemeral=True,
                        )
                except Exception as exc:
                    log.warning("发送模型返回文本的私密消息失败: %s", exc)
                if reservation is not None:
                    self.quota_status = await self.quota_service.refund_generation(reservation)
                return

            if image is None:
                self.last_error = "没有返回图片结果。"
                self.last_status = "生成失败。"
                if reservation is not None:
                    self.quota_status = await self.quota_service.refund_generation(reservation)
                return

            public_message = await self._send_public_result(
                channel=channel,
                requester=interaction.user,
                image=image,
            )
            self.last_public_message_url = public_message.jump_url
            self.last_status = "图片已公开发送到当前频道。"
            self.last_error = None
        except Exception as exc:
            log.error("公开发送图片时发生错误: %s", exc, exc_info=True)
            self.last_error = f"公开发送失败：{exc}"
            self.last_status = "发送失败。"
            if reservation is not None:
                self.quota_status = await self.quota_service.refund_generation(reservation)
        finally:
            self.is_generating = False
            await self.refresh_panel()


class ImageGenerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.image_client = GatewayImageClient()
        self.quota_service = GlobalDailyQuotaService()
        self.developer_user_ids = _get_developer_user_ids()

    def _is_developer(self, user_id: int) -> bool:
        return user_id in self.developer_user_ids

    async def _read_reference_image(
        self,
        attachment: discord.Attachment | None,
    ) -> tuple[ReferenceImageInput | None, str | None]:
        if attachment is None:
            return None, None

        content_type = str(attachment.content_type or "").strip().lower()
        guessed_mime_type = _guess_mime_type_from_filename(attachment.filename).lower()
        effective_mime_type = (
            content_type if content_type.startswith("image/") else guessed_mime_type
        )

        if not effective_mime_type.startswith("image/"):
            return None, "参考图必须是图片文件。"

        try:
            image_bytes = await attachment.read()
        except Exception as exc:
            log.error("读取参考图失败: %s", exc, exc_info=True)
            return None, f"读取参考图失败：{exc}"

        if not image_bytes:
            return None, "参考图内容为空，请重新上传。"

        return (
            ReferenceImageInput(
                data=image_bytes,
                filename=attachment.filename or "reference_image",
                mime_type=effective_mime_type,
            ),
            None,
        )

    async def _read_reference_images(
        self,
        attachments: list[discord.Attachment | None],
    ) -> tuple[list[ReferenceImageInput], str | None]:
        resolved_images: list[ReferenceImageInput] = []
        for attachment in attachments:
            resolved_image, error_message = await self._read_reference_image(attachment)
            if error_message:
                return [], error_message
            if resolved_image is not None:
                resolved_images.append(resolved_image)

        return resolved_images, None

    @app_commands.command(name="绘制图片", description="打开图片生成面板")
    @app_commands.describe(
        reference_image_1="可选：上传第 1 张参考图片",
        reference_image_2="可选：上传第 2 张参考图片",
        reference_image_3="可选：上传第 3 张参考图片",
        daily_limit="开发者可用：设置全局每日次数",
    )
    async def draw_image(
        self,
        interaction: discord.Interaction,
        reference_image_1: discord.Attachment | None = None,
        reference_image_2: discord.Attachment | None = None,
        reference_image_3: discord.Attachment | None = None,
        daily_limit: app_commands.Range[int, 0, 9999] | None = None,
    ):
        is_developer = self._is_developer(interaction.user.id)
        initial_status_message: str | None = None
        await interaction.response.defer(ephemeral=True)
        resolved_reference_images, reference_image_error = await self._read_reference_images(
            [
                reference_image_1,
                reference_image_2,
                reference_image_3,
            ]
        )

        if reference_image_error:
            await interaction.edit_original_response(
                content=reference_image_error,
                embed=None,
                view=None,
            )
            return

        if daily_limit is not None:
            if not is_developer:
                await interaction.edit_original_response(
                    content="只有 DEVELOPER_USER_IDS 中的开发者可以修改每日次数。",
                    embed=None,
                    view=None,
                )
                return

            updated_status = await self.quota_service.set_daily_limit(int(daily_limit))
            initial_status_message = (
                f"已将全局每日次数设置为 {updated_status.daily_limit} 次。"
                f" 当前剩余 {updated_status.remaining} 次。"
            )

        view = ImageGenerationPanelView(
            origin_interaction=interaction,
            image_client=self.image_client,
            quota_service=self.quota_service,
            is_developer=is_developer,
            reference_images=resolved_reference_images,
        )
        await view.initialize(initial_status_message=initial_status_message)

        await interaction.edit_original_response(
            content=None,
            embed=view._build_panel_embed(),
            view=view,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ImageGenerationCog(bot))
