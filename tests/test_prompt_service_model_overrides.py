# -*- coding: utf-8 -*-

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import src.chat.services.prompt_service as prompt_service_module
from src.chat.services.prompt_service import PromptService


MINIMAL_DEFAULT_CONFIG = {
    "SYSTEM_PROMPT": """
<character>
<core_identity>default-core</core_identity>
<behavioral_guidelines>default-behavior</behavioral_guidelines>
<abilities>default-abilities</abilities>
<style_guide>default-style</style_guide>
<word>default-word</word>
</character>
""",
    "JAILBREAK_USER_PROMPT": "default-jailbreak-user",
    "JAILBREAK_MODEL_RESPONSE": "default-jailbreak-model",
    "JAILBREAK_FINAL_INSTRUCTION": "default-final-instruction",
}


@pytest.fixture
def prompt_service(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        prompt_service_module.event_service,
        "get_prompt_overrides",
        lambda: None,
    )
    monkeypatch.setattr(
        prompt_service_module.event_service,
        "get_active_event",
        lambda: None,
    )
    monkeypatch.setattr(
        prompt_service_module.event_service,
        "get_system_prompt_faction_pack_content",
        lambda: None,
    )
    return PromptService()


def test_system_prompt_fragment_only_replaces_target_tag(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
            "kimi-k2.5": {
                "SYSTEM_PROMPT": """
<behavioral_guidelines>kimi-behavior</behavioral_guidelines>
"""
            },
        },
    )

    result = prompt_service.get_prompt("SYSTEM_PROMPT", model_name="kimi-k2.5")

    assert "default-core" in result
    assert "kimi-behavior" in result
    assert "default-abilities" in result
    assert "default-style" in result
    assert "default-behavior" not in result


def test_group_model_override_supports_comma_separated_keys_and_exact_override(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
            "deepseek-chat, deepseek-reasoner, custom": {
                "JAILBREAK_USER_PROMPT": "shared-jailbreak-user",
            },
            "custom": {
                "JAILBREAK_USER_PROMPT": "custom-only-jailbreak-user",
            },
        },
    )

    deepseek_prompt = prompt_service.get_prompt(
        "JAILBREAK_USER_PROMPT", model_name="deepseek-chat"
    )
    custom_prompt = prompt_service.get_prompt(
        "JAILBREAK_USER_PROMPT", model_name="custom"
    )
    fallback_prompt = prompt_service.get_prompt(
        "JAILBREAK_USER_PROMPT", model_name="kimi-k2.5"
    )

    assert deepseek_prompt == "shared-jailbreak-user"
    assert custom_prompt == "custom-only-jailbreak-user"
    assert fallback_prompt == "default-jailbreak-user"


def test_system_prompt_applies_group_then_exact_then_faction_pack(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
            ("deepseek-chat", "deepseek-reasoner", "kimi-k2.5"): {
                "SYSTEM_PROMPT": """
<abilities>group-abilities</abilities>
"""
            },
            "kimi-k2.5": {
                "SYSTEM_PROMPT": """
<behavioral_guidelines>kimi-behavior</behavioral_guidelines>
"""
            },
        },
    )
    monkeypatch.setattr(
        prompt_service_module.event_service,
        "get_system_prompt_faction_pack_content",
        lambda: """
<core_identity>faction-core</core_identity>
""",
    )

    result = prompt_service.get_prompt("SYSTEM_PROMPT", model_name="kimi-k2.5")

    assert "faction-core" in result
    assert "kimi-behavior" in result
    assert "group-abilities" in result
    assert "default-core" not in result
    assert "default-behavior" not in result
    assert "default-abilities" not in result


def test_system_prompt_fragment_appends_missing_tag_to_final_prompt(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
            "custom-deepseek-expert-reasoner": {
                "SYSTEM_PROMPT": """
<think_guide>
先思考，再回答。
</think_guide>
"""
            },
        },
    )

    result = prompt_service.get_prompt(
        "SYSTEM_PROMPT", model_name="custom-deepseek-expert-reasoner"
    )

    assert "default-core" in result
    assert "<think_guide>" in result
    assert "先思考，再回答。" in result


def test_full_character_system_prompt_still_replaces_entire_prompt(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    full_override = """
<character>
<core_identity>override-core</core_identity>
<behavioral_guidelines>override-behavior</behavioral_guidelines>
这里是完整覆盖才会保留的额外文本
</character>
"""
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
            "custom": {
                "SYSTEM_PROMPT": full_override,
            },
        },
    )

    result = prompt_service.get_prompt("SYSTEM_PROMPT", model_name="custom")

    assert result == full_override


def test_custom_variant_prompt_can_override_generic_custom(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    monkeypatch.setenv("CUSTOM_MODEL_NAME", "deepseek-expert-reasoner")
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
            "custom": {
                "JAILBREAK_USER_PROMPT": "generic-custom-user",
                "SYSTEM_PROMPT": """
<behavioral_guidelines>generic-custom-behavior</behavioral_guidelines>
""",
            },
            "custom-deepseek-expert-reasoner": {
                "JAILBREAK_USER_PROMPT": "specific-custom-user",
                "SYSTEM_PROMPT": """
<behavioral_guidelines>specific-custom-behavior</behavioral_guidelines>
""",
            },
        },
    )

    jailbreak_prompt = prompt_service.get_prompt(
        "JAILBREAK_USER_PROMPT", model_name="custom"
    )
    system_prompt = prompt_service.get_prompt("SYSTEM_PROMPT", model_name="custom")

    assert jailbreak_prompt == "specific-custom-user"
    assert "specific-custom-behavior" in system_prompt
    assert "generic-custom-behavior" not in system_prompt
    assert "default-core" in system_prompt


def test_custom_variant_does_not_inherit_generic_custom_when_variant_exists(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    monkeypatch.setenv("CUSTOM_MODEL_NAME", "deepseek-expert-reasoner")
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
            "custom": {
                "SYSTEM_PROMPT": """
<behavioral_guidelines>generic-custom-behavior</behavioral_guidelines>
""",
                "JAILBREAK_USER_PROMPT": "generic-custom-user",
            },
            "custom-deepseek-expert-reasoner": {
                "JAILBREAK_USER_PROMPT": "specific-custom-user",
            },
        },
    )

    jailbreak_prompt = prompt_service.get_prompt(
        "JAILBREAK_USER_PROMPT", model_name="custom"
    )
    system_prompt = prompt_service.get_prompt("SYSTEM_PROMPT", model_name="custom")

    assert jailbreak_prompt == "specific-custom-user"
    assert "default-behavior" in system_prompt
    assert "generic-custom-behavior" not in system_prompt


def test_custom_variant_falls_back_to_generic_custom_when_missing(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    monkeypatch.setenv("CUSTOM_MODEL_NAME", "not-configured-model")
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
            "custom": {
                "JAILBREAK_USER_PROMPT": "generic-custom-user",
            },
        },
    )

    jailbreak_prompt = prompt_service.get_prompt(
        "JAILBREAK_USER_PROMPT", model_name="custom"
    )

    assert jailbreak_prompt == "generic-custom-user"


def test_custom_variant_reads_latest_value_from_project_dotenv(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    monkeypatch.setenv("CUSTOM_MODEL_NAME", "stale-model")

    def _fake_load_project_dotenv(_file_path: str, *, parents: int):
        assert parents == 3
        os.environ["CUSTOM_MODEL_NAME"] = "deepseek-expert-reasoner"
        return "fake-dotenv-path"

    monkeypatch.setattr(
        prompt_service_module,
        "load_project_dotenv",
        _fake_load_project_dotenv,
    )
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
            "custom": {
                "JAILBREAK_USER_PROMPT": "generic-custom-user",
            },
            "custom-deepseek-expert-reasoner": {
                "JAILBREAK_USER_PROMPT": "specific-custom-user",
            },
        },
    )

    jailbreak_prompt = prompt_service.get_prompt(
        "JAILBREAK_USER_PROMPT", model_name="custom"
    )

    assert jailbreak_prompt == "specific-custom-user"


@pytest.mark.asyncio
async def test_build_chat_prompt_appends_think_guide_from_final_system_prompt(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
            "custom-deepseek-expert-reasoner": {
                "SYSTEM_PROMPT": """
<think_guide>
先进行完整推理，再给出结论。
</think_guide>
""",
            },
        },
    )

    result = await prompt_service.build_chat_prompt(
        user_name="测试用户",
        message="你好",
        replied_message=None,
        images=None,
        channel_context=None,
        world_book_entries=None,
        affection_status=None,
        guild_name="测试服务器",
        location_name="测试地点",
        model_name="custom-deepseek-expert-reasoner",
    )

    assert result[-3]["role"] == "user"
    assert result[-3]["parts"] == ["[测试用户]: 你好"]
    assert result[-2] == {"role": "model", "parts": ["我已了解用户输入"]}
    assert result[-1] == {
        "role": "user",
        "parts": ["先进行完整推理，再给出结论。"],
    }


@pytest.mark.asyncio
async def test_build_chat_prompt_keeps_current_user_input_unchanged_without_think_guide(
    monkeypatch: pytest.MonkeyPatch, prompt_service: PromptService
):
    monkeypatch.setattr(
        prompt_service_module,
        "PROMPT_CONFIG",
        {
            "default": dict(MINIMAL_DEFAULT_CONFIG),
        },
    )

    result = await prompt_service.build_chat_prompt(
        user_name="测试用户",
        message="你好",
        replied_message=None,
        images=None,
        channel_context=None,
        world_book_entries=None,
        affection_status=None,
        guild_name="测试服务器",
        location_name="测试地点",
        model_name="kimi-k2.5",
    )

    assert result[-1] == {"role": "user", "parts": ["[测试用户]: 你好"]}
    assert all(turn.get("parts") != ["我已了解用户输入"] for turn in result)
