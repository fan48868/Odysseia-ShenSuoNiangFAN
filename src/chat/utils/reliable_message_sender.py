import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Literal, Optional

import discord

from src.chat.services.reply_recovery_manager import reply_recovery_manager

log = logging.getLogger(__name__)

VERIFY_DELAY_SECONDS = 2.0
_QUEUE_ATTR = "_pending_text_deliveries"
_QUEUE_LOCK_ATTR = "_pending_text_deliveries_lock"
_FLUSH_LOCK_ATTR = "_pending_text_deliveries_flush_lock"
_FLUSH_TASK_ATTR = "_pending_text_deliveries_flush_task"
_WAKE_EVENT_ATTR = "_pending_text_deliveries_wake_event"

DeliveryStatus = Literal["confirmed", "missing", "uncertain"]


@dataclass
class PendingTextDelivery:
    channel_id: int
    content: str
    reply_to_message_id: Optional[int] = None
    mention_author: bool = False
    task_id: Optional[int] = None
    chunk_index: int = 0
    chunk_total: int = 1
    attempt_token: Optional[str] = None
    sent_message_id: Optional[int] = None
    attempt_count: int = 0
    retry_cycle_count: int = 0
    last_error: Optional[str] = None


def initialize_reliable_delivery_state(bot: discord.Client) -> None:
    if not hasattr(bot, _QUEUE_ATTR):
        setattr(bot, _QUEUE_ATTR, [])
    if not hasattr(bot, _QUEUE_LOCK_ATTR):
        setattr(bot, _QUEUE_LOCK_ATTR, asyncio.Lock())
    if not hasattr(bot, _FLUSH_LOCK_ATTR):
        setattr(bot, _FLUSH_LOCK_ATTR, asyncio.Lock())
    if not hasattr(bot, _FLUSH_TASK_ATTR):
        setattr(bot, _FLUSH_TASK_ATTR, None)
    if not hasattr(bot, _WAKE_EVENT_ATTR):
        setattr(bot, _WAKE_EVENT_ATTR, asyncio.Event())


def _get_queue(bot: discord.Client) -> list[PendingTextDelivery]:
    initialize_reliable_delivery_state(bot)
    return getattr(bot, _QUEUE_ATTR)


def _get_queue_lock(bot: discord.Client) -> asyncio.Lock:
    initialize_reliable_delivery_state(bot)
    return getattr(bot, _QUEUE_LOCK_ATTR)


def _get_flush_lock(bot: discord.Client) -> asyncio.Lock:
    initialize_reliable_delivery_state(bot)
    return getattr(bot, _FLUSH_LOCK_ATTR)


def _get_flush_task(bot: discord.Client) -> Optional[asyncio.Task]:
    initialize_reliable_delivery_state(bot)
    return getattr(bot, _FLUSH_TASK_ATTR)


def _set_flush_task(bot: discord.Client, task: Optional[asyncio.Task]) -> None:
    initialize_reliable_delivery_state(bot)
    setattr(bot, _FLUSH_TASK_ATTR, task)


def _get_wake_event(bot: discord.Client) -> asyncio.Event:
    initialize_reliable_delivery_state(bot)
    return getattr(bot, _WAKE_EVENT_ATTR)


def _get_retry_delay_seconds(delivery: PendingTextDelivery) -> float:
    if delivery.retry_cycle_count < 3:
        return 5.0
    return 10.0


def _is_transient_send_error(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, OSError, ConnectionError)):
        return True

    status = getattr(exc, "status", None)
    if status in {429, 500, 502, 503, 504}:
        return True

    message = str(exc).lower()
    transient_markers = (
        "cannot connect to host",
        "server disconnected",
        "connection reset",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "network is unreachable",
    )
    return any(marker in message for marker in transient_markers)


def _find_cached_message(
    bot: discord.Client, message_id: int
) -> Optional[discord.Message]:
    for cached_message in getattr(bot, "cached_messages", []):
        if getattr(cached_message, "id", None) == message_id:
            return cached_message
    return None


def _build_delivery_nonce() -> str:
    # Discord 要求 nonce 长度不超过 25 个字符。
    return uuid.uuid4().hex[:24]


async def _resolve_channel(
    bot: discord.Client, channel_id: int
) -> Optional[discord.abc.Messageable]:
    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel

    try:
        return await bot.fetch_channel(channel_id)
    except Exception as exc:
        log.warning("获取待补发频道失败 | channel_id=%s | error=%s", channel_id, exc)
        return None


async def verify_sent_message(
    bot: discord.Client,
    channel: discord.abc.Messageable,
    message_id: int,
    *,
    delay_seconds: float = VERIFY_DELAY_SECONDS,
) -> DeliveryStatus:
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    cached_message = _find_cached_message(bot, message_id)
    bot_user_id = getattr(getattr(bot, "user", None), "id", None)
    if (
        cached_message is not None
        and getattr(getattr(cached_message, "author", None), "id", None) == bot_user_id
    ):
        return "confirmed"

    fetch_message = getattr(channel, "fetch_message", None)
    if fetch_message is None:
        return "confirmed"

    try:
        fetched_message = await fetch_message(message_id)
    except discord.NotFound:
        return "missing"
    except Exception as exc:
        if _is_transient_send_error(exc):
            log.warning(
                "校验已发送消息时遇到瞬时错误 | channel_id=%s | message_id=%s | error=%s",
                getattr(channel, "id", "<unknown>"),
                message_id,
                exc,
            )
            return "uncertain"
        raise

    if getattr(getattr(fetched_message, "author", None), "id", None) == bot_user_id:
        return "confirmed"
    return "missing"


async def _dispatch_text_delivery(
    channel: discord.abc.Messageable, delivery: PendingTextDelivery
) -> discord.Message:
    nonce = _build_delivery_nonce()

    if delivery.reply_to_message_id is not None:
        if not hasattr(channel, "get_partial_message"):
            raise RuntimeError(
                f"频道 {getattr(channel, 'id', '<unknown>')} 不支持 get_partial_message"
            )
        reply_target = channel.get_partial_message(delivery.reply_to_message_id)
        return await reply_target.reply(
            delivery.content,
            mention_author=delivery.mention_author,
            nonce=nonce,
        )

    return await channel.send(delivery.content, nonce=nonce)


async def _attempt_delivery(
    bot: discord.Client, delivery: PendingTextDelivery
) -> DeliveryStatus:
    if not await reply_recovery_manager.is_attempt_current(
        delivery.task_id, delivery.attempt_token
    ):
        log.info(
            "检测到过期回复投递任务，已跳过发送 | task_id=%s | chunk_index=%s",
            delivery.task_id,
            delivery.chunk_index,
        )
        return "missing"

    channel = await _resolve_channel(bot, delivery.channel_id)
    if channel is None:
        delivery.last_error = "channel_unavailable"
        return "uncertain"

    try:
        sent_message = await _dispatch_text_delivery(channel, delivery)
    except Exception as exc:
        delivery.last_error = str(exc)
        if _is_transient_send_error(exc):
            log.warning(
                "发送频道回复时遇到瞬时错误，暂挂待补发 | channel_id=%s | reply_to=%s | error=%s",
                delivery.channel_id,
                delivery.reply_to_message_id,
                exc,
            )
            return "uncertain"
        raise

    delivery.sent_message_id = sent_message.id
    delivery.attempt_count += 1
    delivery.last_error = None

    status = await verify_sent_message(bot, channel, sent_message.id)
    if status == "confirmed":
        await reply_recovery_manager.mark_chunk_confirmed(
            delivery.task_id,
            chunk_index=delivery.chunk_index,
            message_id=sent_message.id,
            chunk_total=delivery.chunk_total,
            attempt_token=delivery.attempt_token,
        )
    return status


async def enqueue_pending_text_delivery(
    bot: discord.Client, delivery: PendingTextDelivery
) -> None:
    queue_lock = _get_queue_lock(bot)
    async with queue_lock:
        _get_queue(bot).append(delivery)

    log.warning(
        "频道回复已加入待补发队列 | channel_id=%s | reply_to=%s | attempt_count=%s | sent_message_id=%s",
        delivery.channel_id,
        delivery.reply_to_message_id,
        delivery.attempt_count,
        delivery.sent_message_id,
    )
    schedule_pending_text_delivery_flush(bot, reason="enqueue")


async def _requeue_pending_delivery(
    bot: discord.Client, delivery: PendingTextDelivery
) -> None:
    delivery.retry_cycle_count += 1
    queue_lock = _get_queue_lock(bot)
    async with queue_lock:
        _get_queue(bot).append(delivery)


async def send_text_reply_with_recovery(
    bot: discord.Client,
    message: discord.Message,
    content: str,
    *,
    mention_author: bool = True,
) -> bool:
    delivery = PendingTextDelivery(
        channel_id=message.channel.id,
        content=content,
        reply_to_message_id=message.id,
        mention_author=mention_author,
        task_id=getattr(message, "id", None),
    )
    return await send_pending_text_delivery_with_recovery(bot, delivery)


async def send_channel_text_with_recovery(
    bot: discord.Client,
    channel: discord.abc.Messageable,
    content: str,
) -> bool:
    delivery = PendingTextDelivery(
        channel_id=channel.id,
        content=content,
    )
    return await send_pending_text_delivery_with_recovery(bot, delivery)


async def send_pending_text_delivery_with_recovery(
    bot: discord.Client, delivery: PendingTextDelivery
) -> bool:
    status = await _attempt_delivery(bot, delivery)
    if status == "confirmed":
        return True

    if status != "confirmed":
        log.warning(
            "发送未确认成功，立即补发重试一次 | channel_id=%s | reply_to=%s | status=%s",
            delivery.channel_id,
            delivery.reply_to_message_id,
            status,
        )
        status = await _attempt_delivery(bot, delivery)
        if status == "confirmed":
            return True

    await enqueue_pending_text_delivery(bot, delivery)
    return False


async def flush_pending_text_deliveries(bot: discord.Client) -> None:
    initialize_reliable_delivery_state(bot)
    flush_lock = _get_flush_lock(bot)
    queue_lock = _get_queue_lock(bot)

    if flush_lock.locked():
        return

    async with flush_lock:
        while True:
            async with queue_lock:
                queue = _get_queue(bot)
                if not queue:
                    return
                delivery = queue.pop(0)

            try:
                if delivery.sent_message_id is not None:
                    if not await reply_recovery_manager.is_attempt_current(
                        delivery.task_id, delivery.attempt_token
                    ):
                        log.info(
                            "待补发队列中的过期回复已丢弃 | task_id=%s | chunk_index=%s",
                            delivery.task_id,
                            delivery.chunk_index,
                        )
                        continue

                    channel = await _resolve_channel(bot, delivery.channel_id)
                    if channel is None:
                        await _requeue_pending_delivery(bot, delivery)
                        continue

                    status = await verify_sent_message(
                        bot,
                        channel,
                        delivery.sent_message_id,
                        delay_seconds=0,
                    )
                    if status == "confirmed":
                        log.info(
                            "待补发频道回复已确认存在，无需重发 | channel_id=%s | message_id=%s",
                            delivery.channel_id,
                            delivery.sent_message_id,
                        )
                        await reply_recovery_manager.mark_chunk_confirmed(
                            delivery.task_id,
                            chunk_index=delivery.chunk_index,
                            message_id=delivery.sent_message_id,
                            chunk_total=delivery.chunk_total,
                            attempt_token=delivery.attempt_token,
                        )
                        continue
                    if status == "uncertain":
                        await _requeue_pending_delivery(bot, delivery)
                        continue

                status = await _attempt_delivery(bot, delivery)
                if status == "confirmed":
                    log.info(
                        "待补发频道回复发送成功 | channel_id=%s | reply_to=%s | attempt_count=%s",
                        delivery.channel_id,
                        delivery.reply_to_message_id,
                        delivery.attempt_count,
                    )
                    continue

                await _requeue_pending_delivery(bot, delivery)
                continue
            except Exception as exc:
                if _is_transient_send_error(exc):
                    await _requeue_pending_delivery(bot, delivery)
                    continue

                log.error(
                    "待补发频道回复出现永久性错误，已放弃该条消息 | channel_id=%s | reply_to=%s | error=%s",
                    delivery.channel_id,
                    delivery.reply_to_message_id,
                    exc,
                    exc_info=True,
                )


def schedule_pending_text_delivery_flush(
    bot: discord.Client, *, reason: str, immediate: bool = False
) -> None:
    initialize_reliable_delivery_state(bot)
    wake_event = _get_wake_event(bot)
    if immediate:
        wake_event.set()

    existing_task = _get_flush_task(bot)
    if existing_task is not None and not existing_task.done():
        return

    async def _runner() -> None:
        should_wait_before_retry = not immediate
        try:
            while True:
                queue_lock = _get_queue_lock(bot)
                async with queue_lock:
                    queue = list(_get_queue(bot))

                if not queue:
                    return

                if should_wait_before_retry:
                    next_delay = _get_retry_delay_seconds(queue[0])
                    log.info(
                        "待补发频道回复仍有 %s 条，%.1f 秒后继续尝试 | first_retry_cycle=%s",
                        len(queue),
                        next_delay,
                        queue[0].retry_cycle_count,
                    )

                    try:
                        await asyncio.wait_for(wake_event.wait(), timeout=next_delay)
                    except asyncio.TimeoutError:
                        pass
                    finally:
                        wake_event.clear()

                should_wait_before_retry = True
                await flush_pending_text_deliveries(bot)
        finally:
            _set_flush_task(bot, None)

    task = asyncio.create_task(_runner(), name=f"pending-text-delivery-flush:{reason}")
    _set_flush_task(bot, task)
