# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import discord
from discord.ext import commands

from src import config
from src.chat.config import chat_config
from src.chat.services.regex_service import regex_service

log = logging.getLogger(__name__)


@dataclass
class CachedReplyMessage:
    message_id: int
    author_display_name: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["CachedReplyMessage"]:
        if not isinstance(data, dict):
            return None

        message_id = data.get("message_id")
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            return None

        author_display_name = data.get("author_display_name")
        if not isinstance(author_display_name, str) or not author_display_name:
            author_display_name = "未知用户"

        return cls(message_id=message_id, author_display_name=author_display_name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "author_display_name": self.author_display_name,
        }


@dataclass
class ActiveCachedMessage:
    message_id: int
    author_display_name: str
    content: str
    created_at_ts: float
    is_bot: bool = False
    reply_to_message_id: Optional[int] = None
    reply_to_author_display_name: Optional[str] = None
    has_attachments: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["ActiveCachedMessage"]:
        if not isinstance(data, dict):
            return None

        message_id = data.get("message_id")
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            return None

        created_at_ts_raw = data.get("created_at_ts", 0.0)
        try:
            created_at_ts = float(created_at_ts_raw)
        except (TypeError, ValueError):
            created_at_ts = 0.0

        author_display_name = data.get("author_display_name")
        if not isinstance(author_display_name, str) or not author_display_name:
            author_display_name = "未知用户"

        content = data.get("content")
        if not isinstance(content, str):
            content = ""

        reply_to_message_id = data.get("reply_to_message_id")
        try:
            reply_to_message_id = (
                int(reply_to_message_id)
                if reply_to_message_id is not None
                else None
            )
        except (TypeError, ValueError):
            reply_to_message_id = None

        reply_to_author_display_name = data.get("reply_to_author_display_name")
        if not isinstance(reply_to_author_display_name, str):
            reply_to_author_display_name = None

        return cls(
            message_id=message_id,
            author_display_name=author_display_name,
            content=content,
            created_at_ts=created_at_ts,
            is_bot=bool(data.get("is_bot", False)),
            reply_to_message_id=reply_to_message_id,
            reply_to_author_display_name=reply_to_author_display_name,
            has_attachments=bool(data.get("has_attachments", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "author_display_name": self.author_display_name,
            "content": self.content,
            "created_at_ts": self.created_at_ts,
            "is_bot": self.is_bot,
            "reply_to_message_id": self.reply_to_message_id,
            "reply_to_author_display_name": self.reply_to_author_display_name,
            "has_attachments": self.has_attachments,
        }


class ContextServiceTest:
    """上下文管理服务测试版本，用于对比新的上下文处理逻辑"""

    ACTIVE_CACHE_BATCH_SIZE = 10

    @staticmethod
    def _is_publicly_visible_message(message: discord.Message) -> bool:
        """
        判断消息是否为“公开可见”。

        目前主要用于过滤 Discord 的 ephemeral（仅对触发者可见）交互消息，避免进入上下文缓存。
        """
        EPHEMERAL_FLAG = 1 << 6
        try:
            flags = getattr(message, "flags", None)
            if flags is not None:
                ephemeral_attr = getattr(flags, "ephemeral", None)
                if ephemeral_attr is True:
                    return False

                flags_value = getattr(flags, "value", None)
                if isinstance(flags_value, int) and (flags_value & EPHEMERAL_FLAG):
                    return False
                if isinstance(flags, int) and (flags & EPHEMERAL_FLAG):
                    return False

            if getattr(message, "ephemeral", False):
                return False
        except Exception:
            return True

        return True

    @staticmethod
    def _is_history_channel(channel: Any) -> bool:
        return isinstance(channel, (discord.TextChannel, discord.Thread)) or (
            channel is not None and hasattr(channel, "history") and hasattr(channel, "id")
        )

    @staticmethod
    def _active_sort_key(entry: ActiveCachedMessage) -> tuple[float, int]:
        return (entry.created_at_ts, entry.message_id)

    @classmethod
    def _merge_active_entries(
        cls,
        existing: Optional[ActiveCachedMessage],
        incoming: ActiveCachedMessage,
    ) -> ActiveCachedMessage:
        if existing is None:
            return incoming

        return ActiveCachedMessage(
            message_id=incoming.message_id,
            author_display_name=incoming.author_display_name
            or existing.author_display_name,
            content=incoming.content or existing.content,
            created_at_ts=incoming.created_at_ts or existing.created_at_ts,
            is_bot=incoming.is_bot or existing.is_bot,
            reply_to_message_id=incoming.reply_to_message_id
            or existing.reply_to_message_id,
            reply_to_author_display_name=incoming.reply_to_author_display_name
            or existing.reply_to_author_display_name,
            has_attachments=incoming.has_attachments or existing.has_attachments,
        )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.message_cache: Dict[int, OrderedDict[int, CachedReplyMessage]] = {}
        self.loaded_channel_caches: Set[int] = set()
        self.cache_dir = os.path.join(config.DATA_DIR, "reply_message_cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        self.active_message_cache: Dict[int, OrderedDict[int, ActiveCachedMessage]] = {}
        self.pending_active_message_buffer: Dict[
            int, OrderedDict[int, ActiveCachedMessage]
        ] = {}
        self.loaded_active_channel_caches: Set[int] = set()
        self.active_cache_dir = os.path.join(config.DATA_DIR, "active_chat_cache")
        os.makedirs(self.active_cache_dir, exist_ok=True)
        self.active_flush_tasks: Dict[int, asyncio.Task] = {}
        self.high_priority_channels: Set[int] = set()

        self.MAX_CACHE_SIZE = max(
            1,
            int(chat_config.CHANNEL_MEMORY_CONFIG.get("formatted_history_limit", 20)),
        )

        if bot:
            log.info("ContextServiceTest 已通过构造函数设置 bot 实例。")
        else:
            log.warning("ContextServiceTest 初始化时未收到有效的 bot 实例。")

    # --- 引用消息缓存 ---

    def _get_cache_file_path(self, channel_id: int) -> str:
        return os.path.join(self.cache_dir, f"reply_cache_{channel_id}.json")

    def _get_channel_cache(
        self, channel_id: int
    ) -> OrderedDict[int, CachedReplyMessage]:
        if channel_id not in self.message_cache:
            self.message_cache[channel_id] = OrderedDict()
        return self.message_cache[channel_id]

    def _ensure_channel_cache_loaded(self, channel_id: int) -> None:
        if channel_id in self.loaded_channel_caches:
            return
        self._load_channel_cache(channel_id)
        self.loaded_channel_caches.add(channel_id)

    def _load_channel_cache(self, channel_id: int) -> None:
        channel_cache = self._get_channel_cache(channel_id)
        channel_cache.clear()
        cache_file_path = self._get_cache_file_path(channel_id)
        if not os.path.isfile(cache_file_path):
            return

        try:
            with open(cache_file_path, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                for entry in data:
                    cached = CachedReplyMessage.from_dict(entry)
                    if not cached or cached.message_id in channel_cache:
                        continue
                    channel_cache[cached.message_id] = cached
        except Exception as e:
            log.warning(f"[单条消息缓存] 读取频道 {channel_id} 缓存文件失败: {e}")
            channel_cache.clear()

        while len(channel_cache) > self.MAX_CACHE_SIZE:
            channel_cache.popitem(last=False)

        if channel_cache:
            log.info(
                f"[单条消息缓存] 已加载频道 {channel_id} 缓存: {len(channel_cache)}/{self.MAX_CACHE_SIZE}。"
            )

    def _save_channel_cache(self, channel_id: int) -> None:
        channel_cache = self._get_channel_cache(channel_id)
        cache_file_path = self._get_cache_file_path(channel_id)
        data = [entry.to_dict() for entry in channel_cache.values()]
        try:
            with open(cache_file_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False)
        except Exception as e:
            log.warning(f"[单条消息缓存] 写入频道 {channel_id} 缓存文件失败: {e}")

    # --- 主动聊天缓存 ---

    def _get_active_cache_file_path(self, channel_id: int) -> str:
        return os.path.join(self.active_cache_dir, f"active_cache_{channel_id}.json")

    def _get_active_channel_cache(
        self, channel_id: int
    ) -> OrderedDict[int, ActiveCachedMessage]:
        if channel_id not in self.active_message_cache:
            self.active_message_cache[channel_id] = OrderedDict()
        return self.active_message_cache[channel_id]

    def _get_pending_active_buffer(
        self, channel_id: int
    ) -> OrderedDict[int, ActiveCachedMessage]:
        if channel_id not in self.pending_active_message_buffer:
            self.pending_active_message_buffer[channel_id] = OrderedDict()
        return self.pending_active_message_buffer[channel_id]

    def _ensure_active_channel_cache_loaded(self, channel_id: int) -> None:
        if channel_id in self.loaded_active_channel_caches:
            return
        self._load_active_channel_cache(channel_id)
        self.loaded_active_channel_caches.add(channel_id)

    def _load_active_cache_file_sync(
        self, channel_id: int
    ) -> OrderedDict[int, ActiveCachedMessage]:
        channel_cache: OrderedDict[int, ActiveCachedMessage] = OrderedDict()
        cache_file_path = self._get_active_cache_file_path(channel_id)
        if not os.path.isfile(cache_file_path):
            return channel_cache

        try:
            with open(cache_file_path, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                entries = []
                for entry in data:
                    cached = ActiveCachedMessage.from_dict(entry)
                    if cached:
                        entries.append(cached)
                for cached in sorted(entries, key=self._active_sort_key):
                    channel_cache[cached.message_id] = cached
        except Exception as e:
            log.warning(f"[主动聊天缓存] 读取频道 {channel_id} 缓存文件失败: {e}")
            channel_cache.clear()

        while len(channel_cache) > self.MAX_CACHE_SIZE:
            channel_cache.popitem(last=False)

        return channel_cache

    def _load_active_channel_cache(self, channel_id: int) -> None:
        channel_cache = self._get_active_channel_cache(channel_id)
        channel_cache.clear()
        channel_cache.update(self._load_active_cache_file_sync(channel_id))

        if channel_cache:
            log.info(
                f"[主动聊天缓存] 已加载频道 {channel_id} 缓存: {len(channel_cache)}/{self.MAX_CACHE_SIZE}。"
            )

    def _replace_active_channel_cache(
        self, channel_id: int, entries: List[ActiveCachedMessage]
    ) -> None:
        channel_cache = self._get_active_channel_cache(channel_id)
        channel_cache.clear()
        for entry in sorted(entries, key=self._active_sort_key):
            channel_cache[entry.message_id] = entry

        while len(channel_cache) > self.MAX_CACHE_SIZE:
            channel_cache.popitem(last=False)

        self.loaded_active_channel_caches.add(channel_id)

    def _write_active_cache_sync(
        self, channel_id: int, entries: List[ActiveCachedMessage]
    ) -> None:
        cache_file_path = self._get_active_cache_file_path(channel_id)
        temp_file_path = f"{cache_file_path}.{os.getpid()}.tmp"
        data = [entry.to_dict() for entry in entries]

        with open(temp_file_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False)

        os.replace(temp_file_path, cache_file_path)

    def _persist_active_batch_sync(
        self, channel_id: int, batch_entries: List[ActiveCachedMessage]
    ) -> List[ActiveCachedMessage]:
        persisted_cache = self._load_active_cache_file_sync(channel_id)

        for entry in batch_entries:
            merged_entry = self._merge_active_entries(
                persisted_cache.get(entry.message_id), entry
            )
            persisted_cache.pop(entry.message_id, None)
            persisted_cache[entry.message_id] = merged_entry

        sorted_entries = sorted(persisted_cache.values(), key=self._active_sort_key)
        if len(sorted_entries) > self.MAX_CACHE_SIZE:
            sorted_entries = sorted_entries[-self.MAX_CACHE_SIZE :]

        self._write_active_cache_sync(channel_id, sorted_entries)
        return sorted_entries

    def _acknowledge_flushed_messages(
        self, channel_id: int, batch_entries: List[ActiveCachedMessage]
    ) -> None:
        pending_buffer = self._get_pending_active_buffer(channel_id)
        for entry in batch_entries:
            pending_buffer.pop(entry.message_id, None)

    def _maybe_schedule_active_flush(self, channel_id: int) -> None:
        if channel_id in self.high_priority_channels:
            return

        pending_buffer = self._get_pending_active_buffer(channel_id)
        if len(pending_buffer) < self.ACTIVE_CACHE_BATCH_SIZE:
            return

        current_task = self.active_flush_tasks.get(channel_id)
        if current_task and not current_task.done():
            return

        self.active_flush_tasks[channel_id] = asyncio.create_task(
            self._flush_active_messages(channel_id)
        )

    async def _flush_active_messages(self, channel_id: int) -> None:
        current_task = asyncio.current_task()

        try:
            while True:
                if channel_id in self.high_priority_channels:
                    return

                pending_buffer = self._get_pending_active_buffer(channel_id)
                batch_entries = list(pending_buffer.values())[
                    : self.ACTIVE_CACHE_BATCH_SIZE
                ]
                if len(batch_entries) < self.ACTIVE_CACHE_BATCH_SIZE:
                    return

                write_future = asyncio.create_task(
                    asyncio.to_thread(
                        self._persist_active_batch_sync, channel_id, batch_entries
                    )
                )

                try:
                    persisted_entries = await write_future
                except asyncio.CancelledError:
                    try:
                        persisted_entries = await write_future
                    except Exception as e:
                        log.warning(
                            f"[主动聊天缓存] 频道 {channel_id} 后台写入在取消后失败: {e}",
                            exc_info=True,
                        )
                    else:
                        self._acknowledge_flushed_messages(channel_id, batch_entries)
                        self._replace_active_channel_cache(
                            channel_id, persisted_entries
                        )
                    return

                self._acknowledge_flushed_messages(channel_id, batch_entries)
                self._replace_active_channel_cache(channel_id, persisted_entries)
                log.info(
                    f"[主动聊天缓存] 频道 {channel_id} 已异步落盘 {len(batch_entries)} 条消息。"
                )
        except Exception as e:
            log.warning(
                f"[主动聊天缓存] 频道 {channel_id} 后台 flush 失败: {e}",
                exc_info=True,
            )
        finally:
            stored_task = self.active_flush_tasks.get(channel_id)
            if stored_task is current_task:
                self.active_flush_tasks.pop(channel_id, None)

            if channel_id not in self.high_priority_channels:
                self._maybe_schedule_active_flush(channel_id)

    def mark_channel_high_priority(self, channel_id: int) -> None:
        self.high_priority_channels.add(channel_id)

        task = self.active_flush_tasks.get(channel_id)
        if task and not task.done():
            task.cancel()

    def clear_channel_high_priority(self, channel_id: int) -> None:
        self.high_priority_channels.discard(channel_id)
        self._maybe_schedule_active_flush(channel_id)

    def _build_active_cached_message_from_message(
        self, message: discord.Message
    ) -> ActiveCachedMessage:
        created_at = getattr(message, "created_at", None)
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            created_at_ts = created_at.timestamp()
        else:
            created_at_ts = 0.0

        reply_to_message_id = None
        reply_to_author_display_name = None
        reference = getattr(message, "reference", None)
        if reference and getattr(reference, "message_id", None):
            reply_to_message_id = int(reference.message_id)

            resolved = getattr(reference, "resolved", None)
            if resolved is None:
                resolved = getattr(reference, "cached_message", None)
            if resolved and getattr(resolved, "author", None):
                reply_to_author_display_name = resolved.author.display_name

        return ActiveCachedMessage(
            message_id=int(message.id),
            author_display_name=(
                message.author.display_name
                if getattr(message, "author", None)
                else "未知用户"
            ),
            content=self.clean_message_content(message.content or "", message.guild),
            created_at_ts=created_at_ts,
            is_bot=bool(getattr(getattr(message, "author", None), "bot", False)),
            reply_to_message_id=reply_to_message_id,
            reply_to_author_display_name=reply_to_author_display_name,
            has_attachments=bool(getattr(message, "attachments", [])),
        )

    def _is_cacheable_message(self, message: discord.Message) -> bool:
        if not self._is_history_channel(getattr(message, "channel", None)):
            return False

        if not self._is_publicly_visible_message(message):
            return False

        return message.type in (
            discord.MessageType.default,
            discord.MessageType.reply,
        )

    async def record_message_for_active_cache(self, message: discord.Message) -> None:
        if not self._is_cacheable_message(message):
            return

        snapshot = self._build_active_cached_message_from_message(message)
        if not snapshot.content and not snapshot.has_attachments:
            return

        channel_id = getattr(getattr(message, "channel", None), "id", None)
        if channel_id is None:
            return

        self._ensure_active_channel_cache_loaded(channel_id)
        pending_buffer = self._get_pending_active_buffer(channel_id)
        pending_buffer.pop(snapshot.message_id, None)
        pending_buffer[snapshot.message_id] = snapshot
        self._maybe_schedule_active_flush(channel_id)

    def _collect_live_cached_snapshots(
        self, channel_id: int
    ) -> List[ActiveCachedMessage]:
        snapshots: List[ActiveCachedMessage] = []
        if not self.bot or not getattr(self.bot, "cached_messages", None):
            return snapshots

        for message in self.bot.cached_messages:
            if getattr(getattr(message, "channel", None), "id", None) != channel_id:
                continue
            if not self._is_cacheable_message(message):
                continue

            snapshot = self._build_active_cached_message_from_message(message)
            if not snapshot.content and not snapshot.has_attachments:
                continue
            snapshots.append(snapshot)

        return snapshots

    def _combine_active_entries(
        self, entries: List[ActiveCachedMessage]
    ) -> OrderedDict[int, ActiveCachedMessage]:
        combined: Dict[int, ActiveCachedMessage] = {}
        for entry in entries:
            combined[entry.message_id] = self._merge_active_entries(
                combined.get(entry.message_id), entry
            )

        ordered = OrderedDict()
        for entry in sorted(combined.values(), key=self._active_sort_key):
            ordered[entry.message_id] = entry
        return ordered

    async def _build_reference_map(
        self,
        channel_id: int,
        channel: Any,
        history_entries: List[ActiveCachedMessage],
    ) -> Dict[int, CachedReplyMessage]:
        self._ensure_channel_cache_loaded(channel_id)
        channel_cache = self._get_channel_cache(channel_id)
        cached_reference_map: Dict[int, CachedReplyMessage] = dict(channel_cache)

        for entry in history_entries:
            cached_reference_map[entry.message_id] = CachedReplyMessage(
                message_id=entry.message_id,
                author_display_name=entry.author_display_name,
            )

        ids_to_fetch = {
            entry.reply_to_message_id
            for entry in history_entries
            if entry.reply_to_message_id
            and entry.reply_to_message_id not in cached_reference_map
        }

        if ids_to_fetch:
            log.info(
                f"[单条消息缓存] 发现 {len(ids_to_fetch)} 条缺失的引用消息，开始逐一获取..."
            )
            successful_fetches = 0
            for msg_id in ids_to_fetch:
                try:
                    message = await channel.fetch_message(msg_id)
                    if not message:
                        continue

                    author_name = (
                        message.author.display_name
                        if getattr(message, "author", None)
                        else "未知用户"
                    )
                    cached_reference_map[message.id] = CachedReplyMessage(
                        message_id=message.id,
                        author_display_name=author_name,
                    )
                    successful_fetches += 1
                except discord.NotFound:
                    log.warning(f"[单条消息缓存] 找不到消息 {msg_id}，可能已被删除。")
                except discord.Forbidden:
                    log.warning(f"[单条消息缓存] 没有权限获取消息 {msg_id}。")
                except Exception as e:
                    log.error(
                        f"[单条消息缓存] 获取消息 {msg_id} 时发生未知错误: {e}",
                        exc_info=True,
                    )

            if successful_fetches > 0:
                log.info(
                    f"[单条消息缓存] 获取完成: 共成功获取 {successful_fetches}/{len(ids_to_fetch)} 条消息。"
                )

        refreshed_cache: OrderedDict[int, CachedReplyMessage] = OrderedDict()
        for entry in history_entries:
            ref_id = entry.reply_to_message_id
            ref_msg = cached_reference_map.get(ref_id) if ref_id else None
            if not ref_msg or ref_msg.message_id in refreshed_cache:
                continue
            refreshed_cache[ref_msg.message_id] = ref_msg

        channel_cache.clear()
        channel_cache.update(refreshed_cache)
        self._save_channel_cache(channel_id)

        if refreshed_cache:
            log.info(
                f"[单条消息缓存] 频道 {channel_id} 本轮缓存已刷新: {len(channel_cache)}/{self.MAX_CACHE_SIZE}。"
            )
        else:
            log.info(f"[单条消息缓存] 频道 {channel_id} 本轮无引用缓存，已清空。")

        return cached_reference_map

    async def get_formatted_channel_history_new(
        self,
        channel_id: int,
        user_id: int,
        guild_id: int,
        limit: int = chat_config.CHANNEL_MEMORY_CONFIG["formatted_history_limit"],
        exclude_message_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取结构化的频道对话历史。
        此方法将历史消息与用户的最新消息分离，以引导模型只回复最新内容。
        """
        del user_id, guild_id

        if not self.bot:
            log.error("ContextServiceTest 的 bot 实例未设置，无法获取频道消息历史。")
            return []

        try:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                    channel_name = getattr(channel, "name", f"ID: {channel_id}")
                    log.info(
                        f"通过 fetch_channel 成功获取到频道/帖子: {channel_name} (ID: {channel_id})"
                    )
                except (discord.NotFound, discord.Forbidden):
                    log.warning(
                        f"无法通过 get_channel 或 fetch_channel 找到 ID 为 {channel_id} 的频道或帖子。"
                    )
                    return []

            if not self._is_history_channel(channel):
                log.warning(
                    f"频道 ID {channel_id} 的类型为 {type(channel)}，不支持读取消息历史。"
                )
                return []

            from src.chat.features.chat_settings.services.chat_settings_service import (
                chat_settings_service,
            )

            active_cache_enabled = False
            try:
                active_cache_enabled = (
                    await chat_settings_service.is_active_chat_cache_enabled(channel)
                )
            except Exception as e:
                log.warning(f"[主动聊天缓存] 读取频道 {channel_id} 开关失败: {e}")

            self._ensure_channel_cache_loaded(channel_id)

            live_cached_entries = self._collect_live_cached_snapshots(channel_id)
            persisted_active_entries: List[ActiveCachedMessage] = []
            pending_active_entries: List[ActiveCachedMessage] = []
            if active_cache_enabled:
                self._ensure_active_channel_cache_loaded(channel_id)
                persisted_active_entries = list(
                    self._get_active_channel_cache(channel_id).values()
                )
                pending_active_entries = list(
                    self._get_pending_active_buffer(channel_id).values()
                )

            combined_entries = self._combine_active_entries(
                persisted_active_entries + pending_active_entries + live_cached_entries
            )

            history_entries = list(combined_entries.values())[-limit:]
            if len(history_entries) >= limit:
                log.info(
                    f"[上下文服务-Test] 缓存命中。从主动缓存/内存缓存中获取 {len(history_entries)} 条消息。API 调用: 0。"
                )
            else:
                remaining_limit = limit - len(history_entries)
                before_message = None
                oldest_entry = next(iter(combined_entries.values()), None)
                if oldest_entry:
                    before_message = discord.Object(id=oldest_entry.message_id)

                log.info(
                    f"[上下文服务-Test] 缓存找到 {len(history_entries)} 条，需要从 API 获取 {remaining_limit} 条。"
                )

                api_messages = [
                    msg
                    async for msg in channel.history(
                        limit=remaining_limit, before=before_message
                    )
                ]
                api_messages.reverse()
                api_entries = [
                    self._build_active_cached_message_from_message(msg)
                    for msg in api_messages
                    if self._is_cacheable_message(msg)
                ]

                combined_entries = self._combine_active_entries(
                    list(combined_entries.values()) + api_entries
                )
                history_entries = list(combined_entries.values())[-limit:]
                log.info(
                    f"[上下文服务-Test] 本次获取: 缓存 {len(persisted_active_entries) + len(pending_active_entries) + len(live_cached_entries)} 条, API {len(api_entries)} 条。总计 {len(history_entries)} 条。"
                )

            reference_map = await self._build_reference_map(
                channel_id, channel, history_entries
            )

            history_parts = []
            for entry in history_entries:
                if entry.message_id == exclude_message_id:
                    continue

                rendered_content = entry.content or ("[附件]" if entry.has_attachments else "")
                if not rendered_content:
                    continue

                reply_author = entry.reply_to_author_display_name
                if not reply_author and entry.reply_to_message_id:
                    ref_msg = reference_map.get(entry.reply_to_message_id)
                    if ref_msg:
                        reply_author = ref_msg.author_display_name

                reply_info = f"[回复 {reply_author}]" if reply_author else ""
                user_meta = f"[{entry.author_display_name}]{reply_info}"
                history_parts.append(f"{user_meta}: {rendered_content}")

            final_context = []
            if history_parts:
                background_prompt = "这是本频道最近的对话记录:\n\n" + "\n\n".join(
                    history_parts
                )
                final_context.append({"role": "user", "parts": [background_prompt]})

            final_context.append({"role": "model", "parts": ["我已了解频道的历史对话"]})
            return final_context
        except discord.Forbidden:
            log.error(f"机器人没有权限读取频道 {channel_id} 的消息历史。")
            return []
        except Exception as e:
            log.error(f"获取并格式化频道 {channel_id} 消息历史时出错: {e}")
            return []
        finally:
            self.clear_channel_high_priority(channel_id)

    def clean_message_content(
        self, content: str, guild: Optional[discord.Guild]
    ) -> str:
        """
        净化消息内容，移除或替换不适合模型处理的元素。
        """
        content = content.replace("\\_", "_")
        content = re.sub(r"https?://cdn\.discordapp\.com\S+", "", content)

        if guild:

            def replace_mention(match):
                user_id = int(match.group(1))
                member = guild.get_member(user_id)
                return f"@{member.display_name}" if member else "@未知用户"

            content = re.sub(r"<@!?(\d+)>", replace_mention, content)

        content = re.sub(r"<a?:\w+:\d+>", "", content)
        content = regex_service.clean_user_input(content)
        return content.strip()


_context_service_test_instance: Optional["ContextServiceTest"] = None


def initialize_context_service_test(bot: commands.Bot):
    """
    全局初始化函数。必须在应用程序启动时调用一次。
    """
    global _context_service_test_instance
    if _context_service_test_instance is None:
        _context_service_test_instance = ContextServiceTest(bot)
        log.info("全局 ContextServiceTest 实例已成功初始化。")
    else:
        log.warning("尝试重复初始化 ContextServiceTest 实例，已跳过。")


def get_context_service() -> "ContextServiceTest":
    """
    获取 ContextServiceTest 的单例实例。
    如果实例尚未初始化，将引发 RuntimeError。
    """
    if _context_service_test_instance is None:
        raise RuntimeError(
            "ContextServiceTest 尚未初始化。请确保在应用启动时调用 initialize_context_service_test。"
        )
    return _context_service_test_instance
