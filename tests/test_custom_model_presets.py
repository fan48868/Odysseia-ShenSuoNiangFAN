# -*- coding: utf-8 -*-

import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.chat.features.chat_settings.services.custom_model_preset_store import (
    MAX_CUSTOM_MODEL_PRESETS,
    CustomModelPresetDuplicateNameError,
    CustomModelPresetLimitError,
    CustomModelPresetStore,
)


def _build_settings(
    *,
    url: str = "https://example.com/v1",
    api_key: str = "sk-live-alpha",
    model_name: str = "gpt-4o-mini",
    enable_vision: str = "false",
    enable_video_input: str = "false",
):
    return {
        "custom_model_url": url,
        "custom_model_api_key": api_key,
        "custom_model_name": model_name,
        "custom_model_enable_vision": enable_vision,
        "custom_model_enable_video_input": enable_video_input,
    }


@pytest.fixture
def custom_model_view_module(monkeypatch: pytest.MonkeyPatch):
    module_name = "src.chat.features.chat_settings.ui.custom_model_config_view"
    sys.modules.pop(module_name, None)

    fake_chat_settings_service_module = SimpleNamespace(
        ChatSettingsService=type("ChatSettingsService", (), {})
    )
    fake_gemini_service_module = SimpleNamespace(
        gemini_service=SimpleNamespace(
            openai_service=SimpleNamespace(custom_model_client=None)
        )
    )
    fake_openai_models_module = SimpleNamespace(
        CustomModelClient=type("CustomModelClient", (), {})
    )

    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.chat_settings.services.chat_settings_service",
        fake_chat_settings_service_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.gemini_service",
        fake_gemini_service_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.openai_models",
        fake_openai_models_module,
    )

    module = importlib.import_module(module_name)
    yield module
    sys.modules.pop(module_name, None)


def test_preset_store_crud_lifecycle(tmp_path: Path):
    store = CustomModelPresetStore(base_dir=str(tmp_path))
    created = store.create_preset(name="我的预设", settings=_build_settings())

    listed = store.list_presets()
    assert [preset.name for preset in listed] == ["我的预设"]

    loaded = store.get_preset(created.preset_id)
    assert loaded.custom_model_url == "https://example.com/v1"

    store.delete_preset(created.preset_id)
    assert store.list_presets() == []


def test_preset_store_can_rename_without_changing_content(tmp_path: Path):
    store = CustomModelPresetStore(base_dir=str(tmp_path))
    created = store.create_preset(name="旧名字", settings=_build_settings())

    renamed = store.rename_preset(created.preset_id, name="新名字")

    assert renamed.name == "新名字"
    assert renamed.custom_model_url == created.custom_model_url
    assert renamed.custom_model_api_key == created.custom_model_api_key
    assert renamed.custom_model_name == created.custom_model_name


def test_preset_store_can_update_settings_without_changing_name(tmp_path: Path):
    store = CustomModelPresetStore(base_dir=str(tmp_path))
    created = store.create_preset(name="原预设", settings=_build_settings())

    updated = store.update_preset_settings(
        created.preset_id,
        settings=_build_settings(
            url="https://updated.example.com/v1",
            api_key="sk-updated",
            model_name="updated-model",
            enable_vision="true",
            enable_video_input="false",
        ),
    )

    assert updated.name == created.name
    assert updated.custom_model_url == "https://updated.example.com/v1"
    assert updated.custom_model_api_key == "sk-updated"
    assert updated.custom_model_name == "updated-model"
    assert updated.custom_model_enable_vision == "true"


def test_preset_store_rejects_duplicate_names(tmp_path: Path):
    store = CustomModelPresetStore(base_dir=str(tmp_path))
    store.create_preset(name="重复名", settings=_build_settings())

    with pytest.raises(CustomModelPresetDuplicateNameError):
        store.create_preset(name="  重复名  ", settings=_build_settings(model_name="x"))

    another = store.create_preset(name="另一个名字", settings=_build_settings(model_name="y"))
    with pytest.raises(CustomModelPresetDuplicateNameError):
        store.rename_preset(another.preset_id, name="重复名")


def test_preset_store_rejects_more_than_four_presets(tmp_path: Path):
    store = CustomModelPresetStore(base_dir=str(tmp_path))
    for index in range(MAX_CUSTOM_MODEL_PRESETS):
        store.create_preset(
            name=f"预设{index}",
            settings=_build_settings(model_name=f"model-{index}"),
        )

    with pytest.raises(CustomModelPresetLimitError):
        store.create_preset(name="超出上限", settings=_build_settings())


@pytest.mark.asyncio
async def test_custom_model_view_builds_buttons_from_presets(
    tmp_path: Path, custom_model_view_module
):
    store = CustomModelPresetStore(base_dir=str(tmp_path))
    store.create_preset(name="预设A", settings=_build_settings(model_name="a"))
    store.create_preset(name="预设B", settings=_build_settings(model_name="b"))

    empty_view = custom_model_view_module.CustomModelConfigView(
        opener_user_id=1,
        settings_service=MagicMock(),
        preset_store=CustomModelPresetStore(base_dir=str(tmp_path / "empty")),
    )
    assert [item.label for item in empty_view.children] == [
        "(可选) 配置 custom 模型参数",
        "刷新",
    ]

    filled_view = custom_model_view_module.CustomModelConfigView(
        opener_user_id=1,
        settings_service=MagicMock(),
        preset_store=store,
    )
    assert len(filled_view.children) == 3
    assert filled_view.children[0].label == "(可选) 配置 custom 模型参数"
    assert filled_view.children[1].label == "刷新"
    assert [option.label for option in filled_view.children[2].options] == ["预设A", "预设B"]


@pytest.mark.asyncio
async def test_custom_model_view_caps_preset_options_at_twenty_five(
    tmp_path: Path, custom_model_view_module
):
    store = CustomModelPresetStore(base_dir=str(tmp_path))
    for index in range(MAX_CUSTOM_MODEL_PRESETS):
        store.create_preset(
            name=f"预设{index}",
            settings=_build_settings(model_name=f"model-{index}"),
        )

    os.makedirs(store.presets_dir, exist_ok=True)
    extra_file = Path(store.presets_dir) / "manual-extra.json"
    extra_file.write_text(
        json.dumps(
            {
                "preset_id": "manual-extra",
                "name": "额外预设",
                "custom_model_url": "https://example.com/v1",
                "custom_model_api_key": "extra-key",
                "custom_model_name": "extra-model",
                "custom_model_enable_vision": "false",
                "custom_model_enable_video_input": "false",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    view = custom_model_view_module.CustomModelConfigView(
        opener_user_id=1,
        settings_service=MagicMock(),
        preset_store=store,
    )

    assert len(view.children) == 3
    assert len(view.children[2].options) == MAX_CUSTOM_MODEL_PRESETS


def test_apply_settings_updates_env_and_refreshes_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, custom_model_view_module
):
    set_key_mock = MagicMock()
    load_dotenv_mock = MagicMock(return_value=True)
    client_ctor = MagicMock(return_value=object())

    fake_openai_service = SimpleNamespace(custom_model_client=None)
    fake_gemini_service = SimpleNamespace(openai_service=fake_openai_service)

    monkeypatch.setattr(
        custom_model_view_module,
        "set_key",
        set_key_mock,
    )
    monkeypatch.setattr(
        custom_model_view_module,
        "load_dotenv",
        load_dotenv_mock,
    )
    monkeypatch.setattr(
        custom_model_view_module,
        "CustomModelClient",
        client_ctor,
    )
    monkeypatch.setattr(
        custom_model_view_module,
        "gemini_service",
        fake_gemini_service,
    )
    monkeypatch.setattr(
        custom_model_view_module.CustomModelConfigRuntime,
        "_get_env_path",
        lambda: str(tmp_path / ".env"),
    )

    result = custom_model_view_module.CustomModelConfigRuntime.apply_settings_from_mapping(
        _build_settings()
    )

    assert os.environ["CUSTOM_MODEL_URL"] == "https://example.com/v1"
    assert os.environ["CUSTOM_MODEL_API_KEY"] == "sk-live-alpha"
    assert os.environ["CUSTOM_MODEL_NAME"] == "gpt-4o-mini"
    assert os.environ["CUSTOM_MODEL_ENABLE_VISION"] == "false"
    assert os.environ["CUSTOM_MODEL_ENABLE_VIDEO_INPUT"] == "false"
    assert result.persisted is True
    assert fake_openai_service.custom_model_client is client_ctor.return_value
    assert set_key_mock.call_count == 5
    load_dotenv_mock.assert_called_once()


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (
            _build_settings(enable_vision="maybe"),
            "“开启识图工具” 只能填写 true 或 false。",
        ),
        (
            _build_settings(enable_video_input="maybe"),
            "“开启视频输入” 只能填写 true 或 false。",
        ),
        (
            _build_settings(enable_vision="true", enable_video_input="true"),
            "仅当“开启识图工具”为 false 时，才允许开启视频输入。",
        ),
        (
            _build_settings(url=""),
            "CUSTOM_MODEL_URL / CUSTOM_MODEL_API_KEY / CUSTOM_MODEL_NAME 不能为空。",
        ),
    ],
)
def test_apply_settings_validation_errors(
    settings, message, custom_model_view_module
):
    with pytest.raises(ValueError, match=message):
        custom_model_view_module.CustomModelConfigRuntime.apply_settings_from_mapping(
            settings
        )


def test_apply_settings_supports_data_json_file_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, custom_model_view_module
):
    app_data_dir = tmp_path / "app" / "data"
    app_data_dir.mkdir(parents=True, exist_ok=True)
    api_key_file = app_data_dir / "custom-preset-key.json"
    api_key_file.write_text(
        json.dumps({"api_keys": ["vck_alpha", "vck_beta"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    set_key_mock = MagicMock()
    load_dotenv_mock = MagicMock(return_value=True)
    client_ctor = MagicMock(return_value=object())
    fake_openai_service = SimpleNamespace(custom_model_client=None)
    fake_gemini_service = SimpleNamespace(openai_service=fake_openai_service)

    monkeypatch.setattr(
        "src.chat.utils.custom_model_api_keys._validate_custom_model_api_key_file_path",
        lambda raw_value: str(api_key_file) if raw_value == "/data/custom-preset-key.json" else raw_value,
    )
    monkeypatch.setattr(
        custom_model_view_module,
        "set_key",
        set_key_mock,
    )
    monkeypatch.setattr(
        custom_model_view_module,
        "load_dotenv",
        load_dotenv_mock,
    )
    monkeypatch.setattr(
        custom_model_view_module,
        "CustomModelClient",
        client_ctor,
    )
    monkeypatch.setattr(
        custom_model_view_module,
        "gemini_service",
        fake_gemini_service,
    )
    monkeypatch.setattr(
        custom_model_view_module.CustomModelConfigRuntime,
        "_get_env_path",
        lambda: str(tmp_path / ".env"),
    )

    result = custom_model_view_module.CustomModelConfigRuntime.apply_settings_from_mapping(
        _build_settings(api_key="/data/custom-preset-key.json")
    )

    assert result.resolved_key_config.source_type == "file"
    assert result.resolved_key_config.key_count == 2
    assert os.environ["CUSTOM_MODEL_API_KEY"] == "/data/custom-preset-key.json"


@pytest.mark.asyncio
async def test_configure_preset_modal_updates_preset_settings(
    tmp_path: Path, custom_model_view_module
):
    store = CustomModelPresetStore(base_dir=str(tmp_path))
    created = store.create_preset(name="旧预设", settings=_build_settings())

    preview_view = custom_model_view_module.CustomModelPresetPreviewView(
        opener_user_id=1,
        settings_service=MagicMock(),
        preset_store=store,
        preset_id=created.preset_id,
    )

    interaction = SimpleNamespace(response=SimpleNamespace(send_modal=AsyncMock()))
    await preview_view.on_configure(interaction)

    sent_modal = interaction.response.send_modal.await_args.args[0]
    assert sent_modal.url_input.default == created.custom_model_url
    assert sent_modal.model_name_input.default == created.custom_model_name

    submit_interaction = SimpleNamespace(
        response=SimpleNamespace(send_message=AsyncMock())
    )
    sent_modal.url_input = SimpleNamespace(value="https://updated.example.com/v1")
    sent_modal.api_key_input = SimpleNamespace(value="sk-updated")
    sent_modal.model_name_input = SimpleNamespace(value="updated-model")
    sent_modal.enable_vision_input = SimpleNamespace(value="true")
    sent_modal.enable_video_input = SimpleNamespace(value="false")

    await sent_modal.on_submit(submit_interaction)

    updated = store.get_preset(created.preset_id)
    assert updated.name == created.name
    assert updated.custom_model_url == "https://updated.example.com/v1"
    assert updated.custom_model_api_key == "sk-updated"
    assert updated.custom_model_name == "updated-model"
    submit_interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_preset_modal_only_updates_name(
    tmp_path: Path, custom_model_view_module
):
    store = CustomModelPresetStore(base_dir=str(tmp_path))
    created = store.create_preset(name="旧预设", settings=_build_settings())

    preview_view = custom_model_view_module.CustomModelPresetPreviewView(
        opener_user_id=1,
        settings_service=MagicMock(),
        preset_store=store,
        preset_id=created.preset_id,
    )

    interaction = SimpleNamespace(response=SimpleNamespace(send_modal=AsyncMock()))
    await preview_view.on_rename(interaction)

    sent_modal = interaction.response.send_modal.await_args.args[0]
    assert sent_modal.name_input.default == "旧预设"
    assert len(sent_modal.children) == 1

    submit_interaction = SimpleNamespace(
        response=SimpleNamespace(send_message=AsyncMock())
    )
    sent_modal.name_input = SimpleNamespace(value="新预设名")

    await sent_modal.on_submit(submit_interaction)

    updated = store.get_preset(created.preset_id)
    assert updated.name == "新预设名"
    assert updated.custom_model_url == created.custom_model_url
    assert updated.custom_model_api_key == created.custom_model_api_key
    assert updated.custom_model_name == created.custom_model_name
    submit_interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_preset_success_message_has_no_buttons(
    tmp_path: Path, custom_model_view_module
):
    success_view = custom_model_view_module.CustomModelConfigSuccessView(
        opener_user_id=1,
        settings_service=MagicMock(),
        preset_store=CustomModelPresetStore(base_dir=str(tmp_path)),
        saved_settings=custom_model_view_module.CustomModelSettings(**_build_settings()),
    )

    interaction = SimpleNamespace(response=SimpleNamespace(send_modal=AsyncMock()))
    await success_view.on_save_preset(interaction)

    modal = interaction.response.send_modal.await_args.args[0]
    submit_interaction = SimpleNamespace(
        response=SimpleNamespace(send_message=AsyncMock())
    )
    modal.name_input = SimpleNamespace(value="新预设")

    await modal.on_submit(submit_interaction)

    kwargs = submit_interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert "view" not in kwargs


@pytest.mark.asyncio
async def test_delete_preset_success_message_clears_buttons(
    tmp_path: Path, custom_model_view_module
):
    store = CustomModelPresetStore(base_dir=str(tmp_path))
    created = store.create_preset(name="待删除", settings=_build_settings())

    confirm_view = custom_model_view_module.CustomModelPresetDeleteConfirmView(
        opener_user_id=1,
        settings_service=MagicMock(),
        preset_store=store,
        preset_id=created.preset_id,
    )

    interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
    await confirm_view.on_confirm_delete(interaction)

    kwargs = interaction.response.edit_message.await_args.kwargs
    assert kwargs["view"] is None
