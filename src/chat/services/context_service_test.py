# -*- coding: utf-8 -*-

import json
import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
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


class ContextServiceTest:
    """上下文管理服务测试版本，用于对比新的上下文处理逻辑"""

    @staticmethod
    def _is_publicly_visible_message(message: discord.Message) -> bool:
        """
        判断消息是否为“公开可见”。

        目前主要用于过滤 Discord 的 ephemeral（仅对触发者可见）交互消息，避免进入上下文缓存。
        """
        # Discord MessageFlags: EPHEMERAL = 1 << 6
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

            # 兼容：某些消息对象可能直接带有 ephemeral 属性
            if getattr(message, "ephemeral", False):
                return False
        except Exception:
            # 出错时按“公开可见”处理，避免误杀普通消息
            return True

        return True

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 按频道缓存被引用消息（LRU），用于避免重复拉取
        self.message_cache: Dict[int, OrderedDict[int, CachedReplyMessage]] = {}
        self.loaded_channel_caches: Set[int] = set()
        self.MAX_CACHE_SIZE = max(
            1,
            int(
                chat_config.CHANNEL_MEMORY_CONFIG.get("formatted_history_limit", 20)
            ),
        )
        self.cache_dir = os.path.join(config.DATA_DIR, "reply_message_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        if bot:
            log.info("ContextServiceTest 已通过构造函数设置 bot 实例。")
        else:
            log.warning("ContextServiceTest 初始化时未收到有效的 bot 实例。")

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
                    if not cached:
                        continue
                    if cached.message_id in channel_cache:
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

    def _upsert_reference_message(
        self, channel_id: int, message: discord.Message
    ) -> None:
        channel_cache = self._get_channel_cache(channel_id)
        author_name = message.author.display_name if message.author else "未知用户"
        entry = CachedReplyMessage(
            message_id=message.id, author_display_name=author_name
        )
        if message.id in channel_cache:
            channel_cache.pop(message.id, None)
        channel_cache[message.id] = entry
        while len(channel_cache) > self.MAX_CACHE_SIZE:
            removed_item = channel_cache.popitem(last=False)
            log.info(
                f"[单条消息缓存] 清理频道 {channel_id} 缓存已满，移除最旧的消息 {removed_item[0]}。"
            )

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
        if not self.bot:
            log.error("ContextServiceTest 的 bot 实例未设置，无法获取频道消息历史。")
            return []

        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                # 作为备用方案，尝试通过API获取，这可以找到公开帖子
                channel = await self.bot.fetch_channel(channel_id)
                if isinstance(channel, (discord.abc.GuildChannel, discord.Thread)):
                    log.info(
                        f"通过 fetch_channel 成功获取到频道/帖子: {channel.name} (ID: {channel_id})"
                    )
                else:
                    log.info(f"通过 fetch_channel 成功获取到频道 (ID: {channel_id})")
            except (discord.NotFound, discord.Forbidden):
                log.warning(
                    f"无法通过 get_channel 或 fetch_channel 找到 ID 为 {channel_id} 的频道或帖子。"
                )
                return []

        # 检查是否是支持消息历史的类型
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            log.warning(
                f"频道 ID {channel_id} 的类型为 {type(channel)}，不支持读取消息历史。"
            )
            return []

        # 读取该频道的本地缓存（如有）
        self._ensure_channel_cache_loaded(channel_id)
        channel_cache = self._get_channel_cache(channel_id)
        cached_reference_map: Dict[int, CachedReplyMessage] = dict(channel_cache)

        history_parts = []
        try:
            history_messages = []

            # --- 混合读取逻辑 ---
            cached_msgs = []
            api_messages = []

            # 1. 从缓存中获取所有可用的当前频道消息
            if self.bot and self.bot.cached_messages:
                cached_msgs_all = [
                    m
                    for m in self.bot.cached_messages
                    if getattr(getattr(m, "channel", None), "id", None) == channel_id
                ]
                cached_msgs = [
                    m
                    for m in cached_msgs_all
                    if self._is_publicly_visible_message(m)
                ]
                ignored_count = len(cached_msgs_all) - len(cached_msgs)
                if ignored_count > 0:
                    log.debug(
                        f"[上下文服务-Test] 已从缓存中过滤 {ignored_count} 条非公开可见消息（ephemeral）。"
                    )
                # 确保缓存消息按时间从旧到新排序
                cached_msgs.sort(key=lambda m: m.created_at)

            # 2. 判断是否需要调用 API
            if len(cached_msgs) >= limit:
                # 缓存完全满足需求
                history_messages = cached_msgs[-limit:]
                log.info(
                    f"[上下文服务-Test] 缓存命中。从缓存中获取 {len(history_messages)} 条消息。API 调用: 0。"
                )
            else:
                # 缓存不足或为空，需要 API 补充
                remaining_limit = limit - len(cached_msgs)
                before_message = None
                if cached_msgs:
                    # 如果缓存中有消息，就从最老的一条消息之前开始获取
                    before_message = discord.Object(id=cached_msgs[0].id)

                log.info(
                    f"[上下文服务-Test] 缓存找到 {len(cached_msgs)} 条，需要从 API 获取 {remaining_limit} 条。"
                )

                # 从 API 获取缺失的消息
                api_messages = [
                    msg
                    async for msg in channel.history(
                        limit=remaining_limit, before=before_message
                    )
                ]
                # API 返回的是从新到旧，需要反转以匹配时间顺序
                api_messages.reverse()

                # 合并两部分消息
                history_messages = api_messages + cached_msgs
                log.info(
                    f"[上下文服务-Test] 本次获取: 缓存 {len(cached_msgs)} 条, API {len(api_messages)} 条。总计 {len(history_messages)} 条。"
                )

            # --- 新增：并发获取所有缺失的被引用消息 ---
            # 1. 收集所有在缓存中不存在的、有效的被引用消息ID
            ids_to_fetch = {
                msg.reference.message_id
                for msg in history_messages
                if msg.reference
                and msg.reference.message_id
                and msg.reference.message_id not in cached_reference_map
            }

            # 2. 如果有需要获取的消息，则逐一获取
            if ids_to_fetch:
                log.info(
                    f"[单条消息缓存] 发现 {len(ids_to_fetch)} 条缺失的引用消息，开始逐一获取..."
                )
                successful_fetches = 0
                for msg_id in ids_to_fetch:
                    # 检查缓存，避免重复获取
                    if msg_id in cached_reference_map:
                        continue
                    try:
                        message = await channel.fetch_message(msg_id)
                        if message:
                            author_name = (
                                message.author.display_name
                                if message.author
                                else "未知用户"
                            )
                            cached_reference_map[message.id] = CachedReplyMessage(
                                message_id=message.id,
                                author_display_name=author_name,
                            )
                            successful_fetches += 1
                    except discord.NotFound:
                        log.warning(
                            f"[单条消息缓存] 找不到消息 {msg_id}，可能已被删除。"
                        )
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

            # 3. 每次对话仅保留本轮上下文引用，覆盖旧缓存
            refreshed_cache: OrderedDict[int, CachedReplyMessage] = OrderedDict()
            for msg in history_messages:
                if msg.reference and msg.reference.message_id:
                    ref_msg = cached_reference_map.get(msg.reference.message_id)
                    if not ref_msg:
                        continue
                    if ref_msg.message_id in refreshed_cache:
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

            # --- 处理历史消息 ---
            # 此时所有需要的被引用消息都应该在缓存中了
            for msg in history_messages:
                is_irrelevant_type = msg.type not in (
                    discord.MessageType.default,
                    discord.MessageType.reply,
                )
                if is_irrelevant_type or msg.id == exclude_message_id:
                    continue

                clean_content = self.clean_message_content(msg.content, msg.guild)
                if not clean_content and not msg.attachments:
                    continue

                reply_info = ""
                if msg.reference and msg.reference.message_id:
                    # 直接从缓存中获取，.get() 方法可以安全地处理获取失败的情况
                    ref_msg = channel_cache.get(msg.reference.message_id)
                    if ref_msg:
                        reply_info = f"[回复 {ref_msg.author_display_name}]"

                # 强制在元信息（用户名和回复）后添加冒号，清晰地分割内容
                user_meta = f"[{msg.author.display_name}]{reply_info}"
                final_part = f"{user_meta}: {clean_content}"
                history_parts.append(final_part)

            # 构建最终的上下文列表
            final_context = []

            # 1. 将所有历史记录打包成一个 user 消息作为背景
            if history_parts:
                background_prompt = "这是本频道最近的对话记录:\n\n" + "\n\n".join(
                    history_parts
                )
                final_context.append({"role": "user", "parts": [background_prompt]})

            # 2. 添加一个确认收到历史背景的 model 回复，以维持对话轮次
            final_context.append({"role": "model", "parts": ["我已了解频道的历史对话"]})

            return final_context
        except discord.Forbidden:
            log.error(f"机器人没有权限读取频道 {channel_id} 的消息历史。")
            return []
        except Exception as e:
            log.error(f"获取并格式化频道 {channel_id} 消息历史时出错: {e}")
            return []

    def clean_message_content(
        self, content: str, guild: Optional[discord.Guild]
    ) -> str:
        """
        净化消息内容，移除或替换不适合模型处理的元素。
        """
        # 还原 Discord 为了 Markdown 显示而自动添加的转义
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


# 全局实例
# 修改：不再立即创建实例，而是等待 bot 对象被创建后再初始化
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
