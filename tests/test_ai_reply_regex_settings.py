# -*- coding: utf-8 -*-

import os
import sys
import importlib
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.chat.features.chat_settings.services.ai_reply_regex_service import (
    AIReplyRegexService,
    RegexRuleNotFoundError,
    RegexRuleStore,
    RegexRuleStoreError,
)
from src.chat.features.chat_settings.ui.ai_reply_regex_settings_view import (
    AIReplyRegexSettingsView,
)


def _build_rule_payload(
    *,
    name: str = "规则A",
    order: str = "10",
    enabled: str = "true",
    pattern: str = "foo",
    replacement: str = "bar",
):
    return {
        "name": name,
        "order": order,
        "enabled": enabled,
        "pattern": pattern,
        "replacement": replacement,
    }


@pytest.fixture
def ai_chat_cog_module(monkeypatch: pytest.MonkeyPatch):
    module_name = "src.chat.cogs.ai_chat_cog"
    sys.modules.pop(module_name, None)

    @dataclass
    class FakeGeneratedReply:
        response_text: str
        user_name: str
        user_content_for_memory: str

    @dataclass
    class FakePendingTextDelivery:
        channel_id: int
        content: str
        reply_to_message_id: int | None = None
        mention_author: bool = True
        task_id: int | None = None
        chunk_index: int = 0
        chunk_total: int = 1
        attempt_token: str | None = None
        retry_cycle_count: int = 0

    @dataclass
    class FakeReplySideEffectPayload:
        user_name: str
        user_content: str
        ai_response: str

    monkeypatch.setitem(
        sys.modules,
        "src.chat.config.chat_config",
        SimpleNamespace(
            CHAT_ENABLED=True,
            MESSAGE_SETTINGS={"DM_THRESHOLD": 1000},
            UNRESTRICTED_CHANNEL_IDS=set(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.chat_settings.services.chat_settings_service",
        SimpleNamespace(chat_settings_service=SimpleNamespace()),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.chat_settings.services.ai_reply_regex_service",
        SimpleNamespace(
            ai_reply_regex_service=SimpleNamespace(apply_rules_to_text=lambda text: text)
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.tools.functions.summarize_channel",
        SimpleNamespace(text_to_summary_image=lambda text: b"image"),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.chat_service",
        SimpleNamespace(
            GeneratedReply=FakeGeneratedReply,
            chat_service=SimpleNamespace(generate_reply_for_message=AsyncMock()),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.context_service_test",
        SimpleNamespace(get_context_service=MagicMock()),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.gemini_service",
        SimpleNamespace(gemini_service=SimpleNamespace(last_called_tools=[])),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.message_processor",
        SimpleNamespace(message_processor=SimpleNamespace(process_message=AsyncMock())),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.reply_recovery_manager",
        SimpleNamespace(
            ReplyRecoveryTask=type("ReplyRecoveryTask", (), {}),
            ReplySideEffectPayload=FakeReplySideEffectPayload,
            reply_recovery_manager=SimpleNamespace(
                drop_task=AsyncMock(),
                store_response_text=AsyncMock(return_value=True),
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.utils.database",
        SimpleNamespace(chat_db_manager=SimpleNamespace()),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.utils.reliable_message_sender",
        SimpleNamespace(
            PendingTextDelivery=FakePendingTextDelivery,
            enqueue_pending_text_delivery=AsyncMock(),
            send_pending_text_delivery_with_recovery=AsyncMock(return_value=True),
        ),
    )

    module = importlib.import_module(module_name)
    yield module
    sys.modules.pop(module_name, None)


def test_regex_rule_service_returns_empty_list_for_empty_dir(tmp_path: Path):
    service = AIReplyRegexService(RegexRuleStore(base_dir=str(tmp_path)))

    assert service.list_rules() == []


def test_regex_rule_service_creates_and_reads_rules(tmp_path: Path):
    service = AIReplyRegexService(RegexRuleStore(base_dir=str(tmp_path)))

    created = service.create_rule_from_mapping(_build_rule_payload(name="替换词"))
    rules = service.list_rules()

    assert len(rules) == 1
    assert rules[0].rule_id == created.rule_id
    assert rules[0].name == "替换词"
    assert (tmp_path / "chat_regex_rules" / f"{created.rule_id}.json").exists()


def test_regex_rule_service_rejects_invalid_regex(tmp_path: Path):
    service = AIReplyRegexService(RegexRuleStore(base_dir=str(tmp_path)))

    with pytest.raises(RegexRuleStoreError, match="查找正则表达式无效"):
        service.create_rule_from_mapping(_build_rule_payload(pattern="("))


def test_regex_rule_service_orders_by_order_then_created_at_then_id(tmp_path: Path):
    service = AIReplyRegexService(RegexRuleStore(base_dir=str(tmp_path)))
    service.create_rule_from_mapping(_build_rule_payload(name="晚创建", order="20"))
    service.create_rule_from_mapping(_build_rule_payload(name="早创建", order="10"))
    same_order_first = service.create_rule_from_mapping(
        _build_rule_payload(name="同序1", order="30")
    )
    same_order_second = service.create_rule_from_mapping(
        _build_rule_payload(name="同序2", order="30")
    )

    rules = service.list_rules()

    assert [rule.name for rule in rules] == [
        "早创建",
        "晚创建",
        "同序1",
        "同序2",
    ]
    assert same_order_first.created_at <= same_order_second.created_at


def test_regex_rule_service_delete_missing_rule_raises(tmp_path: Path):
    service = AIReplyRegexService(RegexRuleStore(base_dir=str(tmp_path)))

    with pytest.raises(RegexRuleNotFoundError):
        service.delete_rule("missing-rule-id")


def test_regex_rule_service_applies_rules_in_order(tmp_path: Path):
    service = AIReplyRegexService(RegexRuleStore(base_dir=str(tmp_path)))
    service.create_rule_from_mapping(
        _build_rule_payload(name="先改 foo", order="10", pattern="foo", replacement="bar")
    )
    service.create_rule_from_mapping(
        _build_rule_payload(name="再改 bar", order="20", pattern="bar", replacement="baz")
    )

    assert service.apply_rules_to_text("foo") == "baz"


def test_regex_rule_service_skips_disabled_rules(tmp_path: Path):
    service = AIReplyRegexService(RegexRuleStore(base_dir=str(tmp_path)))
    service.create_rule_from_mapping(
        _build_rule_payload(
            name="禁用规则",
            enabled="false",
            pattern="foo",
            replacement="bar",
        )
    )

    assert service.apply_rules_to_text("foo") == "foo"


def test_regex_rule_service_handles_runtime_invalid_rule_file(tmp_path: Path):
    service = AIReplyRegexService(RegexRuleStore(base_dir=str(tmp_path)))
    rules_dir = tmp_path / "chat_regex_rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "broken.json").write_text(
        """
        {
          "rule_id": "broken",
          "name": "损坏规则",
          "order": 1,
          "enabled": true,
          "pattern": "(",
          "replacement": "oops",
          "created_at": "2026-04-16T00:00:00+00:00",
          "updated_at": "2026-04-16T00:00:00+00:00"
        }
        """.strip(),
        encoding="utf-8",
    )
    service.create_rule_from_mapping(
        _build_rule_payload(name="正常规则", order="2", pattern="foo", replacement="bar")
    )

    assert service.apply_rules_to_text("foo") == "bar"


@pytest.mark.asyncio
async def test_regex_rule_manager_view_disables_edit_and_delete_without_rules(tmp_path: Path):
    service = AIReplyRegexService(RegexRuleStore(base_dir=str(tmp_path)))

    view = AIReplyRegexSettingsView(opener_user_id=1, regex_service=service)

    labels = [getattr(item, "label", None) for item in view.children]
    assert "新建正则" in labels
    edit_button = next(item for item in view.children if getattr(item, "label", "") == "编辑当前")
    delete_button = next(item for item in view.children if getattr(item, "label", "") == "删除当前")
    assert edit_button.disabled is True
    assert delete_button.disabled is True


@pytest.mark.asyncio
async def test_regex_rule_manager_view_enables_edit_and_delete_with_rules(tmp_path: Path):
    service = AIReplyRegexService(RegexRuleStore(base_dir=str(tmp_path)))
    service.create_rule_from_mapping(_build_rule_payload())

    view = AIReplyRegexSettingsView(opener_user_id=1, regex_service=service)

    edit_button = next(item for item in view.children if getattr(item, "label", "") == "编辑当前")
    delete_button = next(item for item in view.children if getattr(item, "label", "") == "删除当前")
    assert edit_button.disabled is False
    assert delete_button.disabled is False
    assert "当前共 **1** 条规则" in view.build_content()


@pytest.mark.asyncio
async def test_deliver_generated_reply_applies_regex_before_store(
    monkeypatch: pytest.MonkeyPatch,
    ai_chat_cog_module,
):
    bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog = ai_chat_cog_module.AIChatCog(bot)
    message = SimpleNamespace(channel=SimpleNamespace(id=123))
    generated_reply = ai_chat_cog_module.GeneratedReply(
        response_text="foo",
        user_name="测试用户",
        user_content_for_memory="hello",
    )

    apply_mock = MagicMock(return_value="bar")
    store_response_mock = AsyncMock(return_value=True)
    send_mock = AsyncMock()

    monkeypatch.setattr(
        "src.chat.cogs.ai_chat_cog.ai_reply_regex_service.apply_rules_to_text",
        apply_mock,
    )
    monkeypatch.setattr(
        "src.chat.cogs.ai_chat_cog.reply_recovery_manager.store_response_text",
        store_response_mock,
    )
    monkeypatch.setattr(cog, "_send_recoverable_text_response", send_mock)
    monkeypatch.setattr(cog, "_is_unrestricted_channel", lambda channel: True)

    await cog._deliver_generated_reply(
        message,
        generated_reply,
        task_id=1,
        attempt_token="token-1",
        last_tools=[],
    )

    apply_mock.assert_called_once_with("foo")
    assert generated_reply.response_text == "bar"
    assert store_response_mock.await_args.args[2] == "bar"
    assert store_response_mock.await_args.kwargs["side_effect_payload"].ai_response == "bar"
    send_mock.assert_awaited_once()
    assert send_mock.await_args.args[1] == "bar"


@pytest.mark.asyncio
async def test_deliver_generated_reply_uses_transformed_text_for_summary_image(
    monkeypatch: pytest.MonkeyPatch,
    ai_chat_cog_module,
):
    bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog = ai_chat_cog_module.AIChatCog(bot)
    message = SimpleNamespace(channel=SimpleNamespace(id=123))
    generated_reply = ai_chat_cog_module.GeneratedReply(
        response_text="foo",
        user_name="测试用户",
        user_content_for_memory="hello",
    )
    summary_mock = AsyncMock()

    monkeypatch.setattr(
        "src.chat.cogs.ai_chat_cog.ai_reply_regex_service.apply_rules_to_text",
        MagicMock(return_value="summary-bar"),
    )
    monkeypatch.setattr(cog, "_send_summary_image_response", summary_mock)

    await cog._deliver_generated_reply(
        message,
        generated_reply,
        task_id=1,
        attempt_token="token-1",
        last_tools=["summarize_channel"],
    )

    summary_mock.assert_awaited_once()
    assert summary_mock.await_args.args[1] == "summary-bar"


@pytest.mark.asyncio
async def test_deliver_generated_reply_uses_transformed_text_for_overflow_dm(
    monkeypatch: pytest.MonkeyPatch,
    ai_chat_cog_module,
):
    bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog = ai_chat_cog_module.AIChatCog(bot)
    message = SimpleNamespace(
        channel=SimpleNamespace(id=123, mention="#general"),
        author=SimpleNamespace(send=AsyncMock()),
    )
    generated_reply = ai_chat_cog_module.GeneratedReply(
        response_text="short",
        user_name="测试用户",
        user_content_for_memory="hello",
    )
    overflow_mock = AsyncMock()

    monkeypatch.setattr(
        "src.chat.cogs.ai_chat_cog.ai_reply_regex_service.apply_rules_to_text",
        MagicMock(return_value="x" * 5000),
    )
    monkeypatch.setattr(cog, "_send_overflow_dm_response", overflow_mock)
    monkeypatch.setattr(cog, "_is_unrestricted_channel", lambda channel: False)
    monkeypatch.setattr(cog, "_get_text_length_without_emojis", lambda text: len(text))

    await cog._deliver_generated_reply(
        message,
        generated_reply,
        task_id=1,
        attempt_token="token-1",
        last_tools=[],
    )

    overflow_mock.assert_awaited_once()
    assert overflow_mock.await_args.args[1] == "x" * 5000
