import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import src.chat.features.chat_settings.services.chat_settings_service as chat_settings_service_module
from src.chat.config import chat_config
from src.chat.features.chat_settings.ui.ai_model_settings_modal import (
    AIModelSettingsModal,
)

try:
    import src.chat.features.personal_memory.services.personal_memory_service as personal_memory_service_module
except ModuleNotFoundError as exc:
    if exc.name == "sqlalchemy":
        personal_memory_service_module = None
    else:
        raise


def test_get_summary_model_reads_runtime_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SUMMARY_MODEL", raising=False)
    assert chat_config.get_summary_model() == chat_config.SUMMARY_MODEL

    monkeypatch.setenv("SUMMARY_MODEL", "deepseek-chat")
    assert chat_config.get_summary_model() == "deepseek-chat"

    monkeypatch.setenv("SUMMARY_MODEL", "")
    assert chat_config.get_summary_model() == chat_config.SUMMARY_MODEL


def test_set_summary_model_updates_env_and_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    service = chat_settings_service_module.ChatSettingsService()
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")

    set_key_mock = MagicMock()
    load_dotenv_mock = MagicMock(return_value=True)

    monkeypatch.setattr(service, "_get_env_path", lambda: str(env_path))
    monkeypatch.setattr(chat_settings_service_module, "set_key", set_key_mock)
    monkeypatch.setattr(chat_settings_service_module, "load_dotenv", load_dotenv_mock)
    monkeypatch.delenv("SUMMARY_MODEL", raising=False)

    persisted = asyncio.run(service.set_summary_model("kimi-k2.5"))

    assert persisted is True
    assert os.environ["SUMMARY_MODEL"] == "kimi-k2.5"
    set_key_mock.assert_called_once_with(
        str(env_path),
        "SUMMARY_MODEL",
        "kimi-k2.5",
        quote_mode="always",
        encoding="utf-8",
    )
    load_dotenv_mock.assert_called_once_with(
        str(env_path), override=True, encoding="utf-8"
    )


def test_ai_model_settings_modal_submits_main_and_summary_models():
    async def _run():
        callback = AsyncMock()
        modal = AIModelSettingsModal(
            title="切换全局AI模型",
            current_model="gemini-2.5-flash",
            current_summary_model="custom",
            available_models=["gemini-2.5-flash", "custom"],
            on_submit_callback=callback,
        )
        interaction = AsyncMock()

        modal.model_input = SimpleNamespace(value="deepseek-chat")
        modal.summary_model_input = SimpleNamespace(value="kimi-k2.5")

        await modal.on_submit(interaction)

        callback.assert_awaited_once_with(
            interaction,
            {
                "ai_model": "deepseek-chat",
                "summary_model": "kimi-k2.5",
            },
        )

    asyncio.run(_run())


class _FakeScalarResult:
    def __init__(self, summary: str):
        self._summary = summary

    def scalars(self):
        return self

    def first(self):
        return self._summary


class _FakeSession:
    def __init__(self, summary: str = ""):
        self._summary = summary

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        return _FakeScalarResult(self._summary)


def test_personal_memory_summary_uses_latest_summary_model(
    monkeypatch: pytest.MonkeyPatch,
):
    if personal_memory_service_module is None:
        pytest.skip("sqlalchemy is not installed in the current environment.")

    service = personal_memory_service_module.PersonalMemoryService()
    response_text = "<new_long_memory>\n- 用户喜欢猫\n</new_long_memory>\n<recent_dynamics>\n- 用户刚提到宠物\n</recent_dynamics>"
    generate_simple_response = AsyncMock(return_value=response_text)

    monkeypatch.setenv("SUMMARY_MODEL", "deepseek-chat")
    monkeypatch.setattr(
        personal_memory_service_module, "AsyncSessionLocal", lambda: _FakeSession("")
    )
    monkeypatch.setattr(
        personal_memory_service_module.gemini_service,
        "generate_simple_response",
        generate_simple_response,
    )
    monkeypatch.setattr(
        service,
        "_filter_new_long_memory_lines",
        AsyncMock(return_value=["- 用户喜欢猫"]),
    )
    monkeypatch.setattr(service, "update_summary_manually", AsyncMock())

    asyncio.run(
        service._summarize_memory(
            user_id=123,
            conversation_history=[
                {"role": "user", "parts": ["请记住我喜欢猫"]},
                {"role": "model", "parts": ["好，我会记住"]},
            ],
        )
    )

    assert generate_simple_response.await_count == 1
    assert generate_simple_response.await_args.kwargs["model_name"] == "deepseek-chat"
