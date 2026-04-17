# -*- coding: utf-8 -*-

import importlib
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))


@pytest.fixture
def chat_service_module(monkeypatch: pytest.MonkeyPatch):
    module_name = "src.chat.services.chat_service"
    sys.modules.pop(module_name, None)

    gemini_service = SimpleNamespace(
        generate_response=AsyncMock(return_value=""),
        generate_embedding=AsyncMock(return_value=[]),
        is_available=MagicMock(return_value=False),
        last_called_tools=[],
    )
    get_context_service = MagicMock(
        return_value=SimpleNamespace(
            get_formatted_channel_history_new=AsyncMock(return_value="")
        )
    )
    world_book_service = SimpleNamespace(
        get_profile_by_discord_id=AsyncMock(return_value=None),
        find_entries=AsyncMock(return_value=[]),
    )
    affection_service = SimpleNamespace(
        get_affection_status=AsyncMock(return_value=None)
    )
    chat_db_manager = SimpleNamespace(is_user_blacklisted=AsyncMock(return_value=False))
    chat_settings_service = SimpleNamespace(
        is_chat_globally_enabled=AsyncMock(return_value=True),
        get_effective_channel_config=AsyncMock(return_value={"is_chat_enabled": True}),
        get_current_ai_model=AsyncMock(return_value="test-model"),
    )
    regex_service = SimpleNamespace(clean_user_input=lambda text: text)

    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.gemini_service",
        SimpleNamespace(gemini_service=gemini_service),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.context_service_test",
        SimpleNamespace(get_context_service=get_context_service),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.world_book.services.world_book_service",
        SimpleNamespace(world_book_service=world_book_service),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.affection.service.affection_service",
        SimpleNamespace(affection_service=affection_service),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.utils.database",
        SimpleNamespace(chat_db_manager=chat_db_manager),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.config.chat_config",
        SimpleNamespace(TUTORIAL_SEARCH_SUFFIX=""),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.chat_settings.services.chat_settings_service",
        SimpleNamespace(chat_settings_service=chat_settings_service),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.regex_service",
        SimpleNamespace(regex_service=regex_service),
    )

    module = importlib.import_module(module_name)
    yield module
    sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_generate_reply_for_message_returns_fallback_when_ai_response_is_empty(
    chat_service_module,
):
    message = SimpleNamespace(
        id=456,
        author=SimpleNamespace(id=123, display_name="测试用户"),
        guild=SimpleNamespace(id=789),
        channel=SimpleNamespace(id=321),
    )
    processed_data = {
        "user_content": "你好",
        "replied_content": "",
        "image_data_list": [],
        "video_data_list": [],
    }

    result = await chat_service_module.chat_service.generate_reply_for_message(
        message,
        processed_data,
        guild_name="测试服务器",
        location_name="测试频道",
    )

    assert result is not None
    assert result.response_text == chat_service_module.EMPTY_AI_RESPONSE_FALLBACK
    assert result.user_name == "测试用户"
    assert result.user_content_for_memory == "你好"
