# -*- coding: utf-8 -*-

import asyncio
import discord
import logging
import random
import os
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import aiohttp
import discord.abc
from src import config

# 导入所需的服务
from src.chat.services.empty_response_retry import (
    SyntheticRetryRequest,
    build_empty_response_retry_request,
)
from src.chat.services.gemini_service import gemini_service
from src.chat.services.context_service_test import get_context_service  # 导入测试范围，注释
from src.chat.features.world_book.services.world_book_service import world_book_service
from src.chat.features.affection.service.affection_service import affection_service
from src.chat.utils.database import chat_db_manager
from src.chat.config import chat_config
from src.chat.features.chat_settings.services.chat_settings_service import (
    chat_settings_service,
)

log = logging.getLogger(__name__)

EMPTY_AI_RESPONSE_FALLBACK = "AI服务未返回回复,可能是PVP失败或者内容被截断"

@dataclass
class GeneratedReply:
    response_text: str
    user_name: str
    user_content_for_memory: str


@dataclass
class PreparedGenerationContext:
    user_content: str
    replied_content: str
    image_data_list: List[Dict[str, Any]]
    video_data_list: List[Dict[str, Any]]
    channel_context: List[Dict[str, Any]]
    world_book_entries: List[Dict[str, Any]]
    personal_summary: Optional[str]
    user_profile_data: Optional[Dict[str, Any]]
    affection_status: Optional[Dict[str, Any]]
    current_model: str
    user_id_for_settings: Optional[str]
    is_blacklisted: bool
    retrieval_query_text: Optional[str]
    retrieval_query_embedding: Optional[List[float]]
    rag_timeout_fallback: bool
    injected_text_context: Optional[str]


class ChatService:
    """
    负责编排整个AI聊天响应流程。
    """

    TEXT_ATTACHMENT_ALLOWED_EXTENSIONS = {
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".jsonl",
        ".csv",
        ".tsv",
        ".log",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".ts",
        ".py",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".rs",
        ".go",
        ".sh",
        ".bat",
        ".ps1",
        ".ini",
        ".toml",
        ".env",
        ".sql",
    }
    TEXT_ATTACHMENT_ALLOWED_MIME_PREFIXES = ("text/",)
    TEXT_ATTACHMENT_ALLOWED_MIME_TYPES = {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
        "application/x-javascript",
        "application/sql",
    }
    TEXT_ATTACHMENT_MAX_BYTES = 20 * 1024
    TEXT_ATTACHMENT_MAX_CHARS = 12000
    TEXT_ATTACHMENT_TIMEOUT_SECONDS = 10
    TEXT_ATTACHMENT_MAX_COUNT = 1
    TEXT_ATTACHMENT_TRUNCATION_NOTICE = (
        "\n...[由于长度安全限制，剩余内容已被丢弃]..."
    )

    def _is_text_attachment_allowed_for_user(self, user_id: int) -> bool:
        return user_id in config.DEVELOPER_USER_IDS

    def _is_supported_text_attachment(self, attachment: discord.Attachment) -> bool:
        content_type = (attachment.content_type or "").lower()
        if any(
            content_type.startswith(prefix)
            for prefix in self.TEXT_ATTACHMENT_ALLOWED_MIME_PREFIXES
        ):
            return True
        if content_type in self.TEXT_ATTACHMENT_ALLOWED_MIME_TYPES:
            return True

        extension = os.path.splitext(attachment.filename or "")[1].lower()
        return extension in self.TEXT_ATTACHMENT_ALLOWED_EXTENSIONS

    async def _read_attachment_prefix(
        self, attachment: discord.Attachment
    ) -> tuple[Optional[bytes], bool]:
        max_bytes = self.TEXT_ATTACHMENT_MAX_BYTES
        attachment_size = int(getattr(attachment, "size", 0) or 0)

        if attachment_size and attachment_size <= max_bytes:
            try:
                raw_bytes = await attachment.read()
                return raw_bytes, False
            except Exception as exc:
                log.warning("读取文本附件失败: %s", exc, exc_info=True)
                return None, False

        attachment_url = getattr(attachment, "url", None)
        if not attachment_url:
            log.warning("文本附件缺少 URL，无法安全读取前缀: %s", attachment.filename)
            return None, False

        timeout = aiohttp.ClientTimeout(total=self.TEXT_ATTACHMENT_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(attachment_url) as response:
                    response.raise_for_status()
                    raw_prefix = await response.content.read(max_bytes + 1)
                    return raw_prefix[:max_bytes], bool(
                        attachment_size > max_bytes or len(raw_prefix) > max_bytes
                    )
        except Exception as exc:
            log.warning("下载文本附件前缀失败: %s", exc, exc_info=True)
            return None, False

    def _decode_text_attachment_bytes(
        self, raw_bytes: bytes, filename: str
    ) -> Optional[str]:
        if not raw_bytes:
            return None

        for encoding in ("utf-8", "utf-8-sig", "utf-16", "gb18030"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                decoded = raw_bytes.decode(encoding, errors="ignore").strip()
                if decoded:
                    return decoded
            except Exception:
                continue

        try:
            decoded = raw_bytes.decode("latin-1", errors="ignore").strip()
        except Exception:
            decoded = ""

        if decoded:
            return decoded

        log.warning("无法解码文本附件: %s", filename)
        return None

    def _truncate_text_context(
        self, text: Optional[str], *, force_truncated_notice: bool = False
    ) -> Optional[str]:
        if not text:
            return None

        normalized = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return None

        was_truncated = force_truncated_notice
        if len(normalized) > self.TEXT_ATTACHMENT_MAX_CHARS:
            normalized = normalized[: self.TEXT_ATTACHMENT_MAX_CHARS]
            was_truncated = True

        if was_truncated:
            normalized += self.TEXT_ATTACHMENT_TRUNCATION_NOTICE

        return normalized

    async def _extract_one_time_text_context(
        self, message: discord.Message
    ) -> Optional[str]:
        author_id = getattr(getattr(message, "author", None), "id", None)
        if author_id is None or not self._is_text_attachment_allowed_for_user(author_id):
            return None

        attachments = list(getattr(message, "attachments", []) or [])
        if not attachments:
            return None

        text_blocks: list[str] = []
        for attachment in attachments:
            if len(text_blocks) >= self.TEXT_ATTACHMENT_MAX_COUNT:
                break
            if not self._is_supported_text_attachment(attachment):
                continue

            raw_bytes, was_truncated_by_bytes = await self._read_attachment_prefix(
                attachment
            )
            if raw_bytes is None:
                continue

            decoded_text = self._decode_text_attachment_bytes(
                raw_bytes, attachment.filename or "text.txt"
            )
            truncated_text = self._truncate_text_context(
                decoded_text,
                force_truncated_notice=was_truncated_by_bytes,
            )
            if not truncated_text:
                continue

            text_blocks.append(
                f"[本次附加文本: {attachment.filename or 'text.txt'}]\n{truncated_text}"
            )

        if not text_blocks:
            return None

        log.info(
            "已为管理员用户 %s 注入 %s 个本次有效的文本附件上下文",
            author_id,
            len(text_blocks),
        )
        return "\n\n".join(text_blocks)

    async def should_process_message(self, message: discord.Message) -> bool:
        """
        执行前置检查，判断消息是否应该被处理，以避免不必要的"输入中"状态。
        """
        author = message.author
        guild_id = message.guild.id if message.guild else 0

        # 1. 全局聊天开关检查
        if not await chat_settings_service.is_chat_globally_enabled(guild_id):
            log.info(f"服务器 {guild_id} 全局聊天已禁用，跳过前置检查。")
            return False

        # 2. 频道/分类设置检查
        effective_config = {}
        if isinstance(message.channel, discord.abc.GuildChannel):
            effective_config = await chat_settings_service.get_effective_channel_config(
                message.channel
            )

        if not effective_config.get("is_chat_enabled", True):
            log.info(
                f"频道 {message.channel.id} 聊天已禁用，跳过前置检查。"
            )
            return False

        # 3. 黑名单检查
        if await chat_db_manager.is_user_blacklisted(author.id, guild_id):
            log.info(
                f"用户 {author.id} 在服务器 {guild_id} 处于黑名单，已拒绝消息处理。"
            )
            # 可选：给用户一个提示回复（若上下文允许）
            # await message.reply("你已被加入黑名单，无法使用此功能。")
            return False   # 直接终止前置检查，不进入 AI 调用流程

        # 所有前置检查通过，允许处理消息
        return True

    async def generate_reply_for_message(
        self,
        message: discord.Message,
        processed_data: Dict[str, Any],
        guild_name: str,
        location_name: str,
    ) -> Optional[GeneratedReply]:
        """
        处理聊天消息，生成并返回AI的最终回复。

        Args:
            message (discord.Message): 原始的 discord 消息对象。
            processed_data (Dict[str, Any]): 由 MessageProcessor 处理后的数据。

        Returns:
            GeneratedReply: AI 生成结果以及发送成功后执行副作用所需的最小上下文。
        """
        author = message.author

        user_content = processed_data["user_content"]

        try:
            prepared_context = await self._prepare_generation_context(
                message,
                processed_data,
            )

            ai_response = await self._generate_single_ai_response(
                message=message,
                prepared_context=prepared_context,
                guild_name=guild_name,
                location_name=location_name,
            )

            if self._is_empty_ai_response(ai_response):
                log.info("AI服务首次返回空回复，命中隐藏自动续写重试 | user_id=%s", author.id)
                retry_request = build_empty_response_retry_request(
                    user_name=author.display_name,
                    original_message=prepared_context.user_content,
                    original_replied_message=prepared_context.replied_content,
                    original_channel_context=prepared_context.channel_context,
                    fallback_response_text=EMPTY_AI_RESPONSE_FALLBACK,
                )
                log.info("空回复自动续写重试将在 2 秒后开始 | user_id=%s", author.id)
                await asyncio.sleep(2)
                log.info("空回复自动续写重试开始 | user_id=%s", author.id)
                ai_response = await self._generate_single_ai_response(
                    message=message,
                    prepared_context=prepared_context,
                    guild_name=guild_name,
                    location_name=location_name,
                    request_override=retry_request,
                )

                if self._is_empty_ai_response(ai_response):
                    log.warning(
                        "空回复自动续写重试失败，最终向用户 %s 回落外显兜底提示。",
                        author.id,
                    )
                    return GeneratedReply(
                        response_text=EMPTY_AI_RESPONSE_FALLBACK,
                        user_name=author.display_name,
                        user_content_for_memory=prepared_context.user_content,
                    )

                log.info("空回复自动续写重试成功 | user_id=%s", author.id)

            # 4. --- 后处理与格式化 ---
            final_response = self._format_ai_response(ai_response)

            # --- 新增：为特定工具调用添加后缀 ---
            if (
                gemini_service.last_called_tools
                and "query_tutorial_knowledge_base" in gemini_service.last_called_tools
            ):
                final_response += chat_config.TUTORIAL_SEARCH_SUFFIX
                # 清空列表，避免影响下一次对话
                gemini_service.last_called_tools = []

            return GeneratedReply(
                response_text=final_response,
                user_name=author.display_name,
                user_content_for_memory=prepared_context.user_content,
            )

        except Exception as e:
            log.error(f"[ChatService] 处理聊天消息时出错: {e}", exc_info=True)
            return GeneratedReply(
                response_text="抱歉，处理你的消息时出现了问题，请稍后再试。",
                user_name=author.display_name,
                user_content_for_memory=processed_data.get("user_content", ""),
            )

    async def handle_chat_message(
        self,
        message: discord.Message,
        processed_data: Dict[str, Any],
        guild_name: str,
        location_name: str,
    ) -> Optional[str]:
        generated_reply = await self.generate_reply_for_message(
            message,
            processed_data,
            guild_name,
            location_name,
        )
        if generated_reply is None:
            return None
        return generated_reply.response_text

    async def _prepare_generation_context(
        self,
        message: discord.Message,
        processed_data: Dict[str, Any],
    ) -> PreparedGenerationContext:
        author = message.author
        guild_id = message.guild.id if message.guild else 0

        user_profile_data = await world_book_service.get_profile_by_discord_id(
            author.id,
            user_name=author.display_name,
        )
        personal_summary = None
        if user_profile_data:
            personal_summary = user_profile_data.get("personal_summary")

        user_content = processed_data["user_content"]
        replied_content = processed_data["replied_content"]
        image_data_list = list(processed_data["image_data_list"])
        video_data_list = list(processed_data.get("video_data_list", []))
        injected_text_context = await self._extract_one_time_text_context(message)

        is_blacklisted = False
        if guild_id:
            is_blacklisted = await chat_db_manager.is_user_blacklisted(
                author.id, guild_id
            )

        if is_blacklisted:
            punishment_phrase = random.choice(BLACKLIST_PUNISHMENT_PHRASES)
            user_content = punishment_phrase
            replied_content = ""
            image_data_list = []
            video_data_list = []
            processed_data["user_content"] = user_content
            processed_data["replied_content"] = replied_content
            processed_data["image_data_list"] = image_data_list
            processed_data["video_data_list"] = video_data_list
            injected_text_context = None

        raw_channel_context = await get_context_service().get_formatted_channel_history_new(
            message.channel.id,
            author.id,
            guild_id,
            exclude_message_id=message.id,
        )
        channel_context = (
            list(raw_channel_context) if isinstance(raw_channel_context, list) else []
        )

        (
            world_book_entries,
            retrieval_query_text,
            retrieval_query_embedding,
            rag_timeout_fallback,
        ) = await self._prepare_rag_context(
            author_id=author.id,
            guild_id=guild_id,
            user_name=author.display_name,
            user_content=user_content,
            replied_content=replied_content,
            channel_context=channel_context,
        )

        affection_status = await affection_service.get_affection_status(author.id)
        current_model = await chat_settings_service.get_current_ai_model()
        log.info(f"当前使用的AI模型: {current_model}")

        user_id_for_settings: Optional[str] = None
        log.info("本次消息将使用全局工具开关配置。")

        return PreparedGenerationContext(
            user_content=user_content,
            replied_content=replied_content,
            image_data_list=image_data_list,
            video_data_list=video_data_list,
            channel_context=channel_context,
            world_book_entries=world_book_entries,
            personal_summary=personal_summary,
            user_profile_data=user_profile_data,
            affection_status=affection_status,
            current_model=current_model,
            user_id_for_settings=user_id_for_settings,
            is_blacklisted=is_blacklisted,
            retrieval_query_text=retrieval_query_text,
            retrieval_query_embedding=retrieval_query_embedding,
            rag_timeout_fallback=rag_timeout_fallback,
            injected_text_context=injected_text_context,
        )

    async def _prepare_rag_context(
        self,
        *,
        author_id: int,
        guild_id: int,
        user_name: str,
        user_content: str,
        replied_content: str,
        channel_context: List[Dict[str, Any]],
    ) -> tuple[
        List[Dict[str, Any]],
        Optional[str],
        Optional[List[float]],
        bool,
    ]:
        rag_query = user_content
        if replied_content:
            rag_query = f"{replied_content}\n{user_content}"

        rag_start_time = asyncio.get_running_loop().time()
        rag_timeout_seconds = 10.0
        retrieval_query_text: Optional[str] = None
        retrieval_query_embedding: Optional[List[float]] = None
        rag_timeout_fallback = False

        async def _run_rag_with_shared_embedding():
            from src.chat.services.regex_service import regex_service
            import re as _re

            clean_query = regex_service.clean_user_input(rag_query)
            summarized_query = _re.sub(r"<@!?&?\d+>\s*", "", clean_query).strip()

            embedding: Optional[List[float]] = None
            if summarized_query and gemini_service.is_available():
                try:
                    embedding = await gemini_service.generate_embedding(
                        text=summarized_query, task_type="retrieval_query"
                    )
                except Exception as e:
                    log.warning(
                        f"生成 retrieval_query embedding 失败: {e}", exc_info=True
                    )
                    embedding = None

            if not embedding:
                embedding = []

            entries = await world_book_service.find_entries(
                latest_query=rag_query,
                user_id=author_id,
                guild_id=guild_id,
                user_name=user_name,
                conversation_history=channel_context,
                query_embedding=embedding,
            )
            return entries, summarized_query, embedding

        try:
            (
                world_book_entries,
                retrieval_query_text,
                retrieval_query_embedding,
            ) = await asyncio.wait_for(
                _run_rag_with_shared_embedding(),
                timeout=rag_timeout_seconds,
            )
            rag_elapsed = asyncio.get_running_loop().time() - rag_start_time
            log.info(f"RAG 搜索完成，耗时 {rag_elapsed:.3f}s。")
        except asyncio.TimeoutError:
            rag_elapsed = asyncio.get_running_loop().time() - rag_start_time
            log.warning(
                f"RAG 搜索触发熔断（超时阈值 {rag_timeout_seconds:.1f}s，实际耗时 {rag_elapsed:.3f}s），已强制终止并返回空结果。"
            )
            world_book_entries = []
            retrieval_query_text = None
            retrieval_query_embedding = None
            rag_timeout_fallback = True

        return (
            world_book_entries,
            retrieval_query_text,
            retrieval_query_embedding,
            rag_timeout_fallback,
        )

    async def _generate_single_ai_response(
        self,
        *,
        message: discord.Message,
        prepared_context: PreparedGenerationContext,
        guild_name: str,
        location_name: str,
        request_override: Optional[SyntheticRetryRequest] = None,
    ) -> str:
        request_message = prepared_context.user_content
        request_replied_message = prepared_context.replied_content
        request_images = prepared_context.image_data_list
        request_videos = prepared_context.video_data_list
        request_channel_context = prepared_context.channel_context

        if request_override is not None:
            request_message = request_override.message
            request_replied_message = request_override.replied_message
            request_images = request_override.images
            request_videos = request_override.videos
            request_channel_context = request_override.channel_context

        return await gemini_service.generate_response(
            message.author.id,
            message.guild.id if message.guild else 0,
            message=request_message,
            channel=message.channel,
            replied_message=request_replied_message,
            images=request_images if request_images else None,
            videos=request_videos if request_videos else None,
            user_name=message.author.display_name,
            channel_context=request_channel_context,
            world_book_entries=prepared_context.world_book_entries,
            personal_summary=prepared_context.personal_summary,
            affection_status=prepared_context.affection_status,
            user_profile_data=prepared_context.user_profile_data,
            guild_name=guild_name,
            location_name=location_name,
            model_name=prepared_context.current_model,
            user_id_for_settings=prepared_context.user_id_for_settings,
            blacklist_punishment_active=prepared_context.is_blacklisted,
            retrieval_query_text=prepared_context.retrieval_query_text,
            retrieval_query_embedding=prepared_context.retrieval_query_embedding,
            rag_timeout_fallback=prepared_context.rag_timeout_fallback,
            text_context=prepared_context.injected_text_context,
        )

    def _is_empty_ai_response(self, ai_response: Optional[str]) -> bool:
        if ai_response is None:
            return True
        if isinstance(ai_response, str):
            return not ai_response.strip()
        return False

    def _format_ai_response(self, ai_response: str) -> str:
        """清理和格式化AI的原始回复。"""
        # 移除可能包含的自身名字前缀
        bot_name_prefix = "狮子娘:"
        if ai_response.startswith(bot_name_prefix):
            ai_response = ai_response[len(bot_name_prefix) :].lstrip()
        # 将多段回复的双换行符替换为单换行符
        return ai_response.replace("\n\n", "\n")

    async def _perform_post_response_tasks(
        self,
        author: discord.User,
        guild_id: int,
        query: str,
        rag_entries: list,
        response: str,
    ):
        """执行发送回复后的任务，如记录日志。"""
        # 好感度和奖励逻辑已前置，此处保留用于未来可能的其他后处理任务

        # 记录 RAG 诊断日志
        # self._log_rag_summary(author, query, rag_entries, response)
        pass

    def _log_rag_summary(
        self, author: discord.User, query: str, entries: list, response: str
    ):
        """生成并记录 RAG 诊断摘要日志。"""
        try:
            if entries:
                doc_details = []
                for entry in entries:
                    distance = entry.get("distance", "N/A")
                    distance_str = (
                        f"{distance:.4f}"
                        if isinstance(distance, (int, float))
                        else str(distance)
                    )
                    content = str(entry.get("content", "N/A")).replace("\n", "\n    ")
                    doc_details.append(
                        f"  - Doc ID: {entry.get('id', 'N/A')}, Distance: {distance_str}\n"
                        f"    Content: {content}"
                    )
                retrieved_docs_summary = "\n" + "\n".join(doc_details)
            else:
                retrieved_docs_summary = " N/A"

            summary_log_message = (
                f"\n--- RAG DIAGNOSTIC SUMMARY ---\n"
                f"User: {author} ({author.id})\n"
                f'Initial Query: "{query}"\n'
                f"Retrieved Docs:{retrieved_docs_summary}\n"
                f'Final AI Response: "{response}"\n'
                f"------------------------------"
            )
            # log.info(summary_log_message)
        except Exception as log_e:
            log.error(f"生成 RAG 诊断摘要日志时出错: {log_e}")


# 创建一个单例
chat_service = ChatService()
