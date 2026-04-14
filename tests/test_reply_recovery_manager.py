import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.chat.services.reply_recovery_manager import (
    ReplySideEffectPayload,
    reply_recovery_manager,
)


@pytest.mark.asyncio
async def test_reply_recovery_manager_completes_and_runs_side_effects_once(
    monkeypatch: pytest.MonkeyPatch,
):
    await reply_recovery_manager.clear_for_tests()

    affection_mock = AsyncMock(return_value=None)
    reward_mock = AsyncMock(return_value=True)
    memory_mock = AsyncMock(return_value=None)

    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.affection.service.affection_service",
        SimpleNamespace(
            affection_service=SimpleNamespace(
                increase_affection_on_message=affection_mock
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.odysseia_coin.service.coin_service",
        SimpleNamespace(
            coin_service=SimpleNamespace(grant_daily_message_reward=reward_mock)
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.personal_memory.services.personal_memory_service",
        SimpleNamespace(
            personal_memory_service=SimpleNamespace(
                update_and_conditionally_summarize_memory=memory_mock
            )
        ),
    )

    message = SimpleNamespace(
        id=123,
        channel=SimpleNamespace(id=456),
        guild=SimpleNamespace(id=789),
        author=SimpleNamespace(id=999),
    )

    task_id = await reply_recovery_manager.register_message_task(message)
    attempt_token = await reply_recovery_manager.start_attempt(task_id)
    assert attempt_token

    stored = await reply_recovery_manager.store_response_text(
        task_id,
        attempt_token,
        "你好",
        side_effect_payload=ReplySideEffectPayload(
            user_name="Tester",
            user_content="今天天气不错",
            ai_response="你好",
        ),
    )
    assert stored is True

    assert (
        await reply_recovery_manager.begin_delivery(
            task_id, attempt_token, chunk_total=1
        )
        is True
    )

    await reply_recovery_manager.mark_chunk_confirmed(
        task_id,
        chunk_index=0,
        message_id=555,
        chunk_total=1,
        attempt_token=attempt_token,
    )

    snapshot = await reply_recovery_manager.get_task_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.task_state == "completed"
    assert snapshot.confirmed_message_ids == [555]
    assert snapshot.side_effect_flags == {
        "affection": True,
        "daily_reward": True,
        "personal_memory": True,
    }

    affection_mock.assert_awaited_once_with(999)
    reward_mock.assert_awaited_once_with(999)
    memory_mock.assert_awaited_once_with(
        user_id=999,
        user_name="Tester",
        user_content="今天天气不错",
        ai_response="你好",
    )

    await reply_recovery_manager.mark_chunk_confirmed(
        task_id,
        chunk_index=0,
        message_id=555,
        chunk_total=1,
        attempt_token=attempt_token,
    )

    affection_mock.assert_awaited_once()
    reward_mock.assert_awaited_once()
    memory_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_reply_recovery_manager_rejects_stale_attempt_tokens():
    await reply_recovery_manager.clear_for_tests()

    message = SimpleNamespace(
        id=321,
        channel=SimpleNamespace(id=654),
        guild=SimpleNamespace(id=987),
        author=SimpleNamespace(id=111),
    )

    task_id = await reply_recovery_manager.register_message_task(message)
    stale_token = await reply_recovery_manager.start_attempt(task_id)
    current_token = await reply_recovery_manager.start_attempt(task_id)

    assert stale_token
    assert current_token
    assert stale_token != current_token
    assert (
        await reply_recovery_manager.store_response_text(
            task_id,
            stale_token,
            "过期回复",
        )
        is False
    )
    assert (
        await reply_recovery_manager.begin_delivery(
            task_id, stale_token, chunk_total=1
        )
        is False
    )
    assert await reply_recovery_manager.is_attempt_current(task_id, stale_token) is False
    assert await reply_recovery_manager.is_attempt_current(task_id, current_token) is True
