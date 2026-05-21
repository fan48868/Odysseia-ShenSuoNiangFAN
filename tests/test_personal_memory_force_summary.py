#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

import pytest

# 添加项目根目录到 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.chat.features.personal_memory.services.personal_memory_service import (
    PersonalMemoryService,
)


def _build_history(pair_count: int) -> list[dict]:
    history: list[dict] = []
    for i in range(pair_count):
        history.append({"role": "user", "parts": [f"user-{i}"]})
        history.append({"role": "model", "parts": [f"model-{i}"]})
    return history


def test_clamp_force_summary_limit_default_and_max():
    service = PersonalMemoryService()
    assert service._clamp_force_summary_limit(None) == 5
    assert service._clamp_force_summary_limit(0) == 5
    assert service._clamp_force_summary_limit(-10) == 5
    assert service._clamp_force_summary_limit(1) == 1
    assert service._clamp_force_summary_limit(5) == 5
    assert service._clamp_force_summary_limit(20) == 20
    assert service._clamp_force_summary_limit(999) == 20


def test_split_history_for_forced_summary_basic():
    history = _build_history(10)  # 10 pairs, 20 turns
    to_sum, remaining, pairs = PersonalMemoryService._split_history_for_forced_summary(
        history, limit=5
    )
    assert pairs == 5
    assert len(to_sum) == 10
    assert len(remaining) == 10
    assert remaining == history[:10]
    assert to_sum == history[-10:]


def test_split_history_for_forced_summary_limit_exceeds_available():
    history = _build_history(3)  # 3 pairs, 6 turns
    to_sum, remaining, pairs = PersonalMemoryService._split_history_for_forced_summary(
        history, limit=20
    )
    assert pairs == 3
    assert len(to_sum) == 6
    assert remaining == []


def test_split_history_for_forced_summary_odd_length_keeps_dangling_turn():
    history = _build_history(3)
    dangling = {"role": "user", "parts": ["dangling-user"]}
    history.append(dangling)  # odd length

    to_sum, remaining, pairs = PersonalMemoryService._split_history_for_forced_summary(
        history, limit=2
    )

    assert pairs == 2
    assert len(to_sum) == 4  # 2 pairs
    assert dangling not in to_sum
    assert remaining[-1] == dangling


@pytest.mark.asyncio
async def test_force_summary_request_consumption_last_write_wins():
    service = PersonalMemoryService()
    await service.request_force_summary(user_id=123, reason="a", limit=5)
    await service.request_force_summary(user_id=123, reason="b", limit=7)

    req = await service._consume_force_summary_request(123)
    assert req is not None
    assert req["reason"] == "b"
    assert req["limit"] == 7

    req2 = await service._consume_force_summary_request(123)
    assert req2 is None

