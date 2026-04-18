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
            get_formatted_channel_history_new=AsyncMock(
                return_value=[
                    {
                        "role": "user",
                        "parts": ["这是本频道最近的对话记录:\n\n[路人甲]: 早上好"],
                    },
                    {"role": "model", "parts": ["我已了解频道的历史对话"]},
                ]
            )
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
async def test_generate_reply_for_message_retries_with_continue_message(
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
        "replied_content": "> [Alice]:\n> 前文\n\n",
        "image_data_list": [{"mime_type": "image/png", "data": b"img"}],
        "video_data_list": [{"mime_type": "video/mp4", "data": b"vid"}],
    }
    chat_service_module.gemini_service.generate_response.side_effect = [
        "",
        "继续补上的回复",
    ]

    result = await chat_service_module.chat_service.generate_reply_for_message(
        message,
        processed_data,
        guild_name="测试服务器",
        location_name="测试频道",
    )

    assert result is not None
    assert result.response_text == "继续补上的回复"
    assert result.user_name == "测试用户"
    assert result.user_content_for_memory == "你好"
    assert chat_service_module.gemini_service.generate_response.await_count == 2

    first_call = chat_service_module.gemini_service.generate_response.await_args_list[0]
    second_call = chat_service_module.gemini_service.generate_response.await_args_list[1]

    assert first_call.kwargs["message"] == "你好"
    assert first_call.kwargs["replied_message"] == "> [Alice]:\n> 前文\n\n"
    assert first_call.kwargs["images"] == processed_data["image_data_list"]
    assert first_call.kwargs["videos"] == processed_data["video_data_list"]
    assert second_call.kwargs["message"] == "@神所娘：继续回复"
    assert second_call.kwargs["replied_message"] is None
    assert second_call.kwargs["images"] is None
    assert second_call.kwargs["videos"] is None
    assert second_call.kwargs["world_book_entries"] == first_call.kwargs["world_book_entries"]
    assert (
        second_call.kwargs["retrieval_query_text"]
        == first_call.kwargs["retrieval_query_text"]
    )
    assert (
        second_call.kwargs["retrieval_query_embedding"]
        == first_call.kwargs["retrieval_query_embedding"]
    )
    assert len(second_call.kwargs["channel_context"]) == len(first_call.kwargs["channel_context"])
    assert second_call.kwargs["channel_context"][-1]["parts"][0] == "我已了解频道的历史对话"
    synthetic_history = second_call.kwargs["channel_context"][0]["parts"][0]
    assert "AI服务未返回回复" in synthetic_history
    assert "> [Alice]:" in synthetic_history
    assert "你好" in synthetic_history
    assert "以下是刚刚发生但未成功送达频道的续写上下文" not in synthetic_history
    assert "我知道了，我会基于这轮中断前的内容继续回复" not in synthetic_history


@pytest.mark.asyncio
async def test_generate_reply_for_message_returns_fallback_when_retry_is_also_empty(
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
    chat_service_module.gemini_service.generate_response.side_effect = [
        "",
        "   ",
    ]

    result = await chat_service_module.chat_service.generate_reply_for_message(
        message,
        processed_data,
        guild_name="测试服务器",
        location_name="测试频道",
    )

    assert result is not None
    assert result.response_text == chat_service_module.EMPTY_AI_RESPONSE_FALLBACK
    assert chat_service_module.gemini_service.generate_response.await_count == 2


@pytest.mark.asyncio
async def test_generate_reply_for_message_does_not_retry_when_first_response_has_content(
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
        "replied_content": "> [Alice]:\n> 前文\n\n",
        "image_data_list": [{"mime_type": "image/png", "data": b"img"}],
        "video_data_list": [{"mime_type": "video/mp4", "data": b"vid"}],
    }
    chat_service_module.gemini_service.generate_response.return_value = "正常回复"

    result = await chat_service_module.chat_service.generate_reply_for_message(
        message,
        processed_data,
        guild_name="测试服务器",
        location_name="测试频道",
    )

    assert result is not None
    assert result.response_text == "正常回复"
    assert chat_service_module.gemini_service.generate_response.await_count == 1

    call = chat_service_module.gemini_service.generate_response.await_args_list[0]
    assert call.kwargs["message"] == "你好"
    assert call.kwargs["replied_message"] == "> [Alice]:\n> 前文\n\n"
    assert call.kwargs["images"] == processed_data["image_data_list"]
    assert call.kwargs["videos"] == processed_data["video_data_list"]
