from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
