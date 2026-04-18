# -*- coding: utf-8 -*-

import asyncio
import io
import logging
import re
from contextlib import asynccontextmanager
from typing import Optional

import discord
from discord.ext import commands

from src import config
from src.chat.config import chat_config
from src.chat.config.chat_config import CHAT_ENABLED, MESSAGE_SETTINGS
from src.chat.features.chat_settings.services.chat_settings_service import (
    chat_settings_service,
)
from src.chat.features.chat_settings.services.ai_reply_regex_service import (
    ai_reply_regex_service,
)
from src.chat.features.tools.functions.summarize_channel import text_to_summary_image
from src.chat.services.chat_service import GeneratedReply, chat_service
from src.chat.services.context_service_test import get_context_service
from src.chat.services.gemini_service import gemini_service
from src.chat.services.message_processor import message_processor
from src.chat.services.reply_recovery_manager import (
    ReplyRecoveryTask,
    ReplySideEffectPayload,
    reply_recovery_manager,
)
from src.chat.utils.database import chat_db_manager
from src.chat.utils.reliable_message_sender import (
    PendingTextDelivery,
    enqueue_pending_text_delivery,
    send_pending_text_delivery_with_recovery,
)

log = logging.getLogger(__name__)

TYPING_ENTER_TIMEOUT_SECONDS = 1.5


class AIChatCog(commands.Cog):
    """处理AI聊天功能的Cog，包括@mention回复和斜杠命令"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_text_length_without_emojis(self, text: str) -> int:
        emoji_pattern = r"<a?:.+?:\d+>"
        text_without_emojis = re.sub(emoji_pattern, "", text)
        return len(text_without_emojis)

    def _is_unrestricted_channel(self, channel: discord.abc.Messageable) -> bool:
        return bool(
            getattr(channel, "id", None) in chat_config.UNRESTRICTED_CHANNEL_IDS
            or isinstance(channel, discord.Thread)
        )

    def _build_location_context(
        self, message: discord.Message
    ) -> tuple[str, str]:
        guild_name = message.guild.name if message.guild else "私信"
        if isinstance(message.channel, discord.Thread):
            parent_channel_name = (
                message.channel.parent.name if message.channel.parent else "未知频道"
            )
            return guild_name, f"{parent_channel_name} -> {message.channel.name}"
        if isinstance(message.channel, discord.abc.GuildChannel):
            return guild_name, message.channel.name
        return guild_name, "私信中"

    def _split_response_chunks(self, response_text: str) -> list[str]:
        max_len = 1900
        chunks: list[str] = []
        remaining = response_text

        while remaining:
            if len(remaining) <= max_len:
                chunks.append(remaining)
                break

            split_pos = remaining.rfind("\n", 0, max_len)
            if split_pos == -1 or split_pos < max_len // 2:
                split_pos = max_len

            chunks.append(remaining[:split_pos].rstrip())
            remaining = remaining[split_pos:].lstrip()

        return chunks

    @asynccontextmanager
    async def _best_effort_typing(self, channel: discord.abc.Messageable):
        typing_cm = None
        entered = False
        try:
            typing_cm = channel.typing()
            await asyncio.wait_for(
                typing_cm.__aenter__(), timeout=TYPING_ENTER_TIMEOUT_SECONDS
            )
            entered = True
        except Exception as exc:
            log.warning("输入中状态启动失败，已降级继续处理 | error=%s", exc)

        try:
            yield
        finally:
            if entered and typing_cm is not None:
                try:
                    await typing_cm.__aexit__(None, None, None)
                except Exception as exc:
                    log.warning("输入中状态关闭失败，已忽略 | error=%s", exc)

    async def _send_channel_reply_with_recovery(
        self,
        message: discord.Message,
        content: str,
        *,
        mention_author: bool = True,
        task_id: Optional[int] = None,
        chunk_index: int = 0,
        chunk_total: int = 1,
        attempt_token: Optional[str] = None,
        apply_side_effects_on_completion: bool = True,
    ) -> bool:
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            if not await reply_recovery_manager.is_attempt_current(task_id, attempt_token):
                return False
            sent_message = await message.reply(content, mention_author=mention_author)
            await reply_recovery_manager.mark_chunk_confirmed(
                task_id,
                chunk_index=chunk_index,
                message_id=sent_message.id,
                chunk_total=chunk_total,
                attempt_token=attempt_token,
            )
            return True

        return await send_pending_text_delivery_with_recovery(
            self.bot,
            PendingTextDelivery(
                channel_id=message.channel.id,
                content=content,
                reply_to_message_id=message.id,
                mention_author=mention_author,
                task_id=task_id,
                chunk_index=chunk_index,
                chunk_total=chunk_total,
                attempt_token=attempt_token,
                apply_side_effects_on_completion=apply_side_effects_on_completion,
            ),
        )

    async def _send_channel_message_with_recovery(
        self,
        channel: discord.abc.Messageable,
        content: str,
        *,
        task_id: Optional[int] = None,
        chunk_index: int = 0,
        chunk_total: int = 1,
        attempt_token: Optional[str] = None,
        apply_side_effects_on_completion: bool = True,
    ) -> bool:
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            if not await reply_recovery_manager.is_attempt_current(task_id, attempt_token):
                return False
            sent_message = await channel.send(content)
            await reply_recovery_manager.mark_chunk_confirmed(
                task_id,
                chunk_index=chunk_index,
                message_id=sent_message.id,
                chunk_total=chunk_total,
                attempt_token=attempt_token,
            )
            return True

        return await send_pending_text_delivery_with_recovery(
            self.bot,
            PendingTextDelivery(
                channel_id=channel.id,
                content=content,
                task_id=task_id,
                chunk_index=chunk_index,
                chunk_total=chunk_total,
                attempt_token=attempt_token,
                apply_side_effects_on_completion=apply_side_effects_on_completion,
            ),
        )

    async def _queue_remaining_chunks(
        self,
        message: discord.Message,
        chunks: list[str],
        *,
        start_index: int,
        task_id: int,
        attempt_token: str,
    ) -> None:
        chunk_total = len(chunks)
        for chunk_index in range(start_index, chunk_total):
            await enqueue_pending_text_delivery(
                self.bot,
                PendingTextDelivery(
                    channel_id=message.channel.id,
                    content=chunks[chunk_index],
                    reply_to_message_id=message.id if chunk_index == 0 else None,
                    mention_author=chunk_index == 0,
                    task_id=task_id,
                    chunk_index=chunk_index,
                    chunk_total=chunk_total,
                    attempt_token=attempt_token,
                ),
            )

    async def _send_recoverable_text_response(
        self,
        message: discord.Message,
        response_text: str,
        *,
        task_id: int,
        attempt_token: str,
        apply_side_effects_on_completion: bool = True,
    ) -> bool:
        chunks = self._split_response_chunks(response_text)
        if not chunks:
            await reply_recovery_manager.drop_task(task_id, reason="empty_response_chunks")
            return False

        if not await reply_recovery_manager.begin_delivery(
            task_id, attempt_token, chunk_total=len(chunks)
        ):
            return False

        snapshot = await reply_recovery_manager.get_task_snapshot(task_id)
        confirmed_chunks = set(snapshot.delivery_state.get("confirmed_chunks", [])) if snapshot else set()

        queue_remaining_from: Optional[int] = None
        for chunk_index, chunk in enumerate(chunks):
            if chunk_index in confirmed_chunks:
                continue

            if not await reply_recovery_manager.is_attempt_current(task_id, attempt_token):
                return False

            if chunk_index == 0:
                delivered_now = await self._send_channel_reply_with_recovery(
                    message,
                    chunk,
                    mention_author=True,
                    task_id=task_id,
                    chunk_index=chunk_index,
                    chunk_total=len(chunks),
                    attempt_token=attempt_token,
                    apply_side_effects_on_completion=apply_side_effects_on_completion,
                )
            else:
                delivered_now = await self._send_channel_message_with_recovery(
                    message.channel,
                    chunk,
                    task_id=task_id,
                    chunk_index=chunk_index,
                    chunk_total=len(chunks),
                    attempt_token=attempt_token,
                    apply_side_effects_on_completion=apply_side_effects_on_completion,
                )

            if delivered_now:
                continue

            queue_remaining_from = chunk_index + 1
            break

        if queue_remaining_from is not None:
            await self._queue_remaining_chunks(
                message,
                chunks,
                start_index=queue_remaining_from,
                task_id=task_id,
                attempt_token=attempt_token,
            )
            return False

        return True

    async def _send_summary_image_response(
        self,
        message: discord.Message,
        response_text: str,
        *,
        task_id: int,
    ) -> None:
        await reply_recovery_manager.drop_task(task_id, reason="summary_image_response")
        image_bytes = text_to_summary_image(response_text)
        if not image_bytes:
            raise RuntimeError("总结图片生成失败")

        with io.BytesIO(image_bytes) as image_file:
            await message.reply(
                file=discord.File(image_file, "summary.png"),
                mention_author=True,
            )

    async def _send_overflow_dm_response(
        self,
        message: discord.Message,
        response_text: str,
        *,
        task_id: int,
    ) -> None:
        await reply_recovery_manager.drop_task(task_id, reason="dm_overflow_response")
        channel_mention = (
            message.channel.mention
            if isinstance(message.channel, (discord.TextChannel, discord.Thread))
            else "你们的私信"
        )
        await message.author.send(
            f"刚刚在 {channel_mention} 频道里，你想听我说的话有点多，在这里悄悄告诉你哦：\n\n{response_text}"
        )

    async def _deliver_generated_reply(
        self,
        message: discord.Message,
        generated_reply: GeneratedReply,
        *,
        task_id: int,
        attempt_token: str,
        last_tools: list[str],
        apply_side_effects_on_completion: bool = True,
    ) -> None:
        response_text = ai_reply_regex_service.apply_rules_to_text(
            generated_reply.response_text
        )
        generated_reply.response_text = response_text

        if "summarize_channel" in last_tools:
            log.info("调用了总结工具, 使用图片发送并跳过恢复闭环。")
            await self._send_summary_image_response(
                message, response_text, task_id=task_id
            )
            return

        if (
            not self._is_unrestricted_channel(message.channel)
            and self._get_text_length_without_emojis(response_text)
            > MESSAGE_SETTINGS["DM_THRESHOLD"]
        ):
            await self._send_overflow_dm_response(
                message, response_text, task_id=task_id
            )
            return

        if not await reply_recovery_manager.store_response_text(
            task_id,
            attempt_token,
            response_text,
            side_effect_payload=ReplySideEffectPayload(
                user_name=generated_reply.user_name,
                user_content=generated_reply.user_content_for_memory,
                ai_response=response_text,
            ),
        ):
            return

        await self._send_recoverable_text_response(
            message,
            response_text,
            task_id=task_id,
            attempt_token=attempt_token,
            apply_side_effects_on_completion=apply_side_effects_on_completion,
        )

    async def _generate_and_deliver_reply(
        self,
        message: discord.Message,
        processed_data: dict,
        *,
        task_id: int,
        reason: str,
        use_typing: bool,
    ) -> None:
        try:
            attempt_token = await reply_recovery_manager.start_attempt(task_id)
            if attempt_token is None:
                return

            guild_name, location_name = self._build_location_context(message)

            async def _run_generation() -> Optional[GeneratedReply]:
                return await chat_service.generate_reply_for_message(
                    message,
                    processed_data,
                    guild_name,
                    location_name,
                )

            async def _run_generation_and_delivery() -> None:
                generated_reply = await _run_generation()
                if generated_reply is None:
                    await reply_recovery_manager.drop_task(
                        task_id, reason=f"{reason}_no_reply"
                    )
                    return

                last_tools = list(getattr(gemini_service, "last_called_tools", []))
                await self._deliver_generated_reply(
                    message,
                    generated_reply,
                    task_id=task_id,
                    attempt_token=attempt_token,
                    last_tools=last_tools,
                    apply_side_effects_on_completion=not use_typing,
                )

            if use_typing:
                async with self._best_effort_typing(message.channel):
                    await _run_generation_and_delivery()
                await reply_recovery_manager.apply_side_effects_if_needed(task_id)
            else:
                await _run_generation_and_delivery()
        except Exception as exc:
            log.error(
                "处理回复任务失败 | task_id=%s | reason=%s | error=%s",
                task_id,
                reason,
                exc,
                exc_info=True,
            )
            await reply_recovery_manager.drop_task(task_id, reason=f"{reason}_exception")

    async def _fetch_source_message(
        self, task: ReplyRecoveryTask
    ) -> Optional[discord.Message]:
        channel = self.bot.get_channel(task.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(task.channel_id)
            except Exception as exc:
                log.warning(
                    "恢复任务时获取频道失败 | task_id=%s | channel_id=%s | error=%s",
                    task.message_id,
                    task.channel_id,
                    exc,
                )
                return None

        fetch_message = getattr(channel, "fetch_message", None)
        if fetch_message is None:
            return None

        try:
            return await fetch_message(task.message_id)
        except discord.NotFound:
            return None
        except Exception as exc:
            log.warning(
                "恢复任务时获取原消息失败 | task_id=%s | error=%s",
                task.message_id,
                exc,
                exc_info=True,
            )
            return None

    async def resume_reply_task(self, task_id: int, reason: str) -> None:
        snapshot = await reply_recovery_manager.get_task_snapshot(task_id)
        if snapshot is None or snapshot.task_state in {"completed", "dropped"}:
            return

        message = await self._fetch_source_message(snapshot)
        if message is None:
            await reply_recovery_manager.drop_task(
                task_id, reason=f"{reason}_source_message_missing"
            )
            return

        if snapshot.response_text:
            attempt_token = await reply_recovery_manager.start_attempt(task_id)
            if attempt_token is None:
                return
            await self._send_recoverable_text_response(
                message,
                snapshot.response_text,
                task_id=task_id,
                attempt_token=attempt_token,
            )
            return

        processed_data = await message_processor.process_message(message, self.bot)
        if processed_data is None:
            await reply_recovery_manager.drop_task(
                task_id, reason=f"{reason}_message_unavailable"
            )
            return

        await self._generate_and_deliver_reply(
            message,
            processed_data,
            task_id=task_id,
            reason=reason,
            use_typing=False,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        priority_marked = False
        if not CHAT_ENABLED:
            return

        if message.author.bot:
            return

        processed_data = await message_processor.process_message(message, self.bot)
        if processed_data is None:
            return

        is_dm = message.guild is None
        is_mentioned = self.bot.user in message.mentions
        if not is_dm and not is_mentioned:
            return

        if not is_dm and is_mentioned and isinstance(
            message.channel, (discord.TextChannel, discord.Thread)
        ):
            try:
                get_context_service().mark_channel_high_priority(message.channel.id)
                priority_marked = True
            except RuntimeError:
                log.warning(
                    "[主动聊天缓存] ContextServiceTest 尚未初始化，无法标记高优先级频道。"
                )
            except Exception as exc:
                log.warning(
                    "[主动聊天缓存] 标记频道 %s 为高优先级失败: %s",
                    message.channel.id,
                    exc,
                    exc_info=True,
                )

        try:
            if is_dm:
                dm_enabled = await chat_settings_service.is_global_dm_enabled()
                if (
                    not dm_enabled
                    and message.author.id not in config.DEVELOPER_USER_IDS
                ):
                    log.info(
                        "私信总开关已关闭，用户 %s 非开发者，跳过私信处理。",
                        message.author.id,
                    )
                    return

            if await chat_db_manager.is_user_globally_blacklisted(message.author.id):
                log.info(f"用户 {message.author.id} 在全局黑名单中，已跳过。")
                return

            if not await chat_service.should_process_message(message):
                return

            task_id = await reply_recovery_manager.register_message_task(message)
            await self._generate_and_deliver_reply(
                message,
                processed_data,
                task_id=task_id,
                reason="on_message",
                use_typing=True,
            )
        finally:
            if priority_marked:
                try:
                    get_context_service().clear_channel_high_priority(message.channel.id)
                except Exception:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(AIChatCog(bot))
