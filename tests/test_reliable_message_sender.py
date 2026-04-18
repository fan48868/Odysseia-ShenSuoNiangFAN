from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.chat.services.reply_recovery_manager import reply_recovery_manager
from src.chat.utils import reliable_message_sender as sender


class FakeBot:
    def __init__(self):
        self.user = SimpleNamespace(id=999)
        self.cached_messages = []
        self._channels = {}

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        return self._channels.get(channel_id)


class FakeChannel:
    def __init__(self, channel_id=123):
        self.id = channel_id
        self.fetch_message = AsyncMock()

    def get_partial_message(self, message_id):
        return SimpleNamespace(reply=self.reply)

    async def reply(self, content, mention_author=True, nonce=None):
        return SimpleNamespace(id=456)

    async def send(self, content, nonce=None):
        return SimpleNamespace(id=456)


def test_build_delivery_nonce_length():
    nonce = sender._build_delivery_nonce()

    assert isinstance(nonce, str)
    assert len(nonce) <= 25
    assert len(nonce) == 24


def test_retry_delay_schedule():
    assert sender._get_retry_delay_seconds(sender.PendingTextDelivery(1, "x")) == 5.0
    assert (
        sender._get_retry_delay_seconds(
            sender.PendingTextDelivery(1, "x", retry_cycle_count=2)
        )
        == 5.0
    )
    assert (
        sender._get_retry_delay_seconds(
            sender.PendingTextDelivery(1, "x", retry_cycle_count=3)
        )
        == 10.0
    )


@pytest.mark.asyncio
async def test_verify_sent_message_prefers_cache(monkeypatch: pytest.MonkeyPatch):
    bot = FakeBot()
    channel = FakeChannel()
    bot.cached_messages = [
        SimpleNamespace(id=456, author=SimpleNamespace(id=999)),
    ]

    monkeypatch.setattr(sender.asyncio, "sleep", AsyncMock())

    status = await sender.verify_sent_message(bot, channel, 456)

    assert status == "confirmed"
    channel.fetch_message.assert_not_called()


@pytest.mark.asyncio
async def test_send_text_reply_with_recovery_enqueues_on_uncertain_error(
    monkeypatch: pytest.MonkeyPatch,
):
    bot = FakeBot()
    channel = FakeChannel()
    bot._channels[channel.id] = channel
    message = SimpleNamespace(id=321, channel=channel)

    async def failing_reply(content, mention_author=True, nonce=None):
        raise OSError("Cannot connect to host discord.com:443 ssl:default [None]")

    channel.reply = failing_reply

    delivered = await sender.send_text_reply_with_recovery(bot, message, "hello")

    assert delivered is False
    queue = getattr(bot, "_pending_text_deliveries")
    assert len(queue) == 1
    assert queue[0].reply_to_message_id == 321
    assert queue[0].content == "hello"


@pytest.mark.asyncio
async def test_send_text_reply_with_recovery_retries_once_before_enqueue():
    bot = FakeBot()
    channel = FakeChannel()
    bot._channels[channel.id] = channel
    message = SimpleNamespace(id=321, channel=channel)
    attempt_counter = {"count": 0}

    async def failing_reply(content, mention_author=True, nonce=None):
        attempt_counter["count"] += 1
        raise OSError("Cannot connect to host discord.com:443 ssl:default [None]")

    channel.reply = failing_reply

    delivered = await sender.send_text_reply_with_recovery(bot, message, "hello")

    assert delivered is False
    assert attempt_counter["count"] == 2
    queue = getattr(bot, "_pending_text_deliveries")
    assert len(queue) == 1
    assert queue[0].retry_cycle_count == 0


@pytest.mark.asyncio
async def test_send_pending_text_delivery_with_recovery_confirms_immediately_without_verify(
    monkeypatch: pytest.MonkeyPatch,
):
    await reply_recovery_manager.clear_for_tests()

    bot = FakeBot()
    channel = FakeChannel()
    bot._channels[channel.id] = channel
    message = SimpleNamespace(
        id=321,
        channel=channel,
        guild=SimpleNamespace(id=654),
        author=SimpleNamespace(id=987),
    )

    task_id = await reply_recovery_manager.register_message_task(message)
    attempt_token = await reply_recovery_manager.start_attempt(task_id)
    assert attempt_token is not None
    assert (
        await reply_recovery_manager.begin_delivery(
            task_id,
            attempt_token,
            chunk_total=1,
        )
        is True
    )

    verify_mock = AsyncMock(return_value="confirmed")
    monkeypatch.setattr(sender, "verify_sent_message", verify_mock)

    delivered = await sender.send_pending_text_delivery_with_recovery(
        bot,
        sender.PendingTextDelivery(
            channel_id=channel.id,
            content="hello",
            reply_to_message_id=message.id,
            mention_author=True,
            task_id=task_id,
            chunk_index=0,
            chunk_total=1,
            attempt_token=attempt_token,
        ),
        apply_side_effects_on_completion=False,
    )

    assert delivered is True
    verify_mock.assert_not_awaited()

    snapshot = await reply_recovery_manager.get_task_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.task_state == "completed"
    assert snapshot.confirmed_message_ids == [456]


@pytest.mark.asyncio
async def test_send_pending_text_delivery_with_recovery_requests_immediate_flush_on_enqueue(
    monkeypatch: pytest.MonkeyPatch,
):
    bot = FakeBot()
    schedule_calls = []

    async def fake_attempt_delivery(*args, **kwargs):
        return "uncertain"

    async def fake_enqueue(*args, **kwargs):
        return None

    def fake_schedule_pending_text_delivery_flush(bot_arg, *, reason, immediate=False):
        schedule_calls.append(
            {
                "bot": bot_arg,
                "reason": reason,
                "immediate": immediate,
            }
        )

    monkeypatch.setattr(sender, "_attempt_delivery", fake_attempt_delivery)
    monkeypatch.setattr(sender, "enqueue_pending_text_delivery", fake_enqueue)
    monkeypatch.setattr(
        sender,
        "schedule_pending_text_delivery_flush",
        fake_schedule_pending_text_delivery_flush,
    )

    delivered = await sender.send_pending_text_delivery_with_recovery(
        bot,
        sender.PendingTextDelivery(channel_id=123, content="hello"),
    )

    assert delivered is False
    assert any(call["immediate"] is True for call in schedule_calls)


@pytest.mark.asyncio
async def test_flush_pending_text_deliveries_skips_existing_message(
    monkeypatch: pytest.MonkeyPatch,
):
    bot = FakeBot()
    channel = FakeChannel()
    bot._channels[channel.id] = channel
    bot.cached_messages = [
        SimpleNamespace(id=777, author=SimpleNamespace(id=999)),
    ]

    monkeypatch.setattr(sender.asyncio, "sleep", AsyncMock())

    await sender.enqueue_pending_text_delivery(
        bot,
        sender.PendingTextDelivery(
            channel_id=channel.id,
            content="hello",
            reply_to_message_id=321,
            sent_message_id=777,
        ),
    )

    await sender.flush_pending_text_deliveries(bot)

    queue = getattr(bot, "_pending_text_deliveries")
    assert queue == []
    channel.fetch_message.assert_not_called()
