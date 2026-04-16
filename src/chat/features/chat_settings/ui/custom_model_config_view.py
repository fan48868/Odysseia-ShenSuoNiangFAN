# -*- coding: utf-8 -*-

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from discord import ButtonStyle, Interaction, SelectOption
from discord.ui import Button, View
from dotenv import load_dotenv, set_key

from src import config
from src.chat.features.chat_settings.services.chat_settings_service import (
    ChatSettingsService,
)
from src.chat.features.chat_settings.services.custom_model_preset_store import (
    CustomModelPreset,
    CustomModelPresetNotFoundError,
    CustomModelPresetStore,
)
from src.chat.features.chat_settings.ui.custom_model_config_modal import (
    CustomModelConfigModal,
    CustomModelPresetNameModal,
)
from src.chat.features.chat_settings.ui.components import PaginatedSelect
from src.chat.services.gemini_service import gemini_service
from src.chat.services.openai_models import CustomModelClient
from src.chat.utils.custom_model_api_keys import (
    ResolvedCustomModelApiKeys,
    get_custom_model_api_key_raw_value,
    resolve_custom_model_api_keys,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CustomModelSettings:
    custom_model_url: str
    custom_model_api_key: str
    custom_model_name: str
    custom_model_enable_vision: str
    custom_model_enable_video_input: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "custom_model_url": self.custom_model_url,
            "custom_model_api_key": self.custom_model_api_key,
            "custom_model_name": self.custom_model_name,
            "custom_model_enable_vision": self.custom_model_enable_vision,
            "custom_model_enable_video_input": self.custom_model_enable_video_input,
        }

    @classmethod
    def from_preset(cls, preset: CustomModelPreset) -> "CustomModelSettings":
        return cls(
            custom_model_url=preset.custom_model_url,
            custom_model_api_key=preset.custom_model_api_key,
            custom_model_name=preset.custom_model_name,
            custom_model_enable_vision=preset.custom_model_enable_vision,
            custom_model_enable_video_input=preset.custom_model_enable_video_input,
        )


@dataclass(frozen=True)
class CustomModelApplyResult:
    settings: CustomModelSettings
    resolved_key_config: ResolvedCustomModelApiKeys
    persisted: bool


class _OwnedView(View):
    def __init__(self, *, opener_user_id: int, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.opener_user_id = opener_user_id

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user and interaction.user.id == self.opener_user_id:
            return True
        await interaction.response.send_message(
            "❌ 只有发起者可以操作该按钮。",
            ephemeral=True,
        )
        return False


class CustomModelConfigRuntime:
    @staticmethod
    def _get_env_path() -> str:
        return os.path.join(config.BASE_DIR, ".env")

    @staticmethod
    def _normalize_bool_value(raw: str) -> Optional[str]:
        lowered = (raw or "").strip().lower()
        if lowered in {"true", "1", "yes"}:
            return "true"
        if lowered in {"false", "0", "no"}:
            return "false"
        return None

    @staticmethod
    def _mask_key(key: str) -> str:
        key = (key or "").strip()
        if len(key) <= 8:
            return "*" * len(key) if key else ""
        return f"{key[:4]}****{key[-4:]}"

    @staticmethod
    def _truncate_text(value: str, *, limit: int = 80) -> str:
        normalized_value = str(value or "").strip()
        if len(normalized_value) <= limit:
            return normalized_value
        return f"{normalized_value[: limit - 3]}..."

    @classmethod
    def read_current_settings(cls) -> CustomModelSettings:
        def _pick(key: str) -> str:
            return str(os.environ.get(key, "") or "").strip()

        return CustomModelSettings(
            custom_model_url=_pick("CUSTOM_MODEL_URL"),
            custom_model_api_key=get_custom_model_api_key_raw_value(),
            custom_model_name=_pick("CUSTOM_MODEL_NAME"),
            custom_model_enable_vision=(
                cls._normalize_bool_value(_pick("CUSTOM_MODEL_ENABLE_VISION"))
                or "false"
            ),
            custom_model_enable_video_input=(
                cls._normalize_bool_value(_pick("CUSTOM_MODEL_ENABLE_VIDEO_INPUT"))
                or "false"
            ),
        )

    @staticmethod
    def build_modal_api_key_state(settings: CustomModelSettings) -> Tuple[str, bool]:
        api_key_default_value = settings.custom_model_api_key
        api_key_omitted = False
        if len(api_key_default_value) > CustomModelConfigModal.API_KEY_INPUT_MAX_LENGTH:
            api_key_default_value = ""
            api_key_omitted = True
        return api_key_default_value, api_key_omitted

    @staticmethod
    def _build_api_key_notes(
        resolved_key_config: ResolvedCustomModelApiKeys,
    ) -> Tuple[str, Optional[str]]:
        if resolved_key_config.source_type == "file":
            source_note = (
                f"文件 `{resolved_key_config.file_path}`"
                f"（从文件读取到 {resolved_key_config.key_count} 个 key）"
            )
            return source_note, None

        source_note = f"inline（解析到 {resolved_key_config.key_count} 个 key）"
        first_key = (
            resolved_key_config.api_keys[0] if resolved_key_config.api_keys else ""
        )
        preview_note = (
            f"- API Key 预览: `{CustomModelConfigRuntime._mask_key(first_key)}`"
            if first_key
            else None
        )
        return source_note, preview_note

    @classmethod
    def _build_message_body(
        cls,
        settings: CustomModelSettings,
        *,
        resolved_key_config: Optional[ResolvedCustomModelApiKeys] = None,
        persisted: Optional[bool] = None,
        allow_unresolved_api_key: bool = False,
    ) -> str:
        api_key_source_note: str
        api_key_preview_line: Optional[str]

        if resolved_key_config is None:
            try:
                resolved_key_config = resolve_custom_model_api_keys(
                    settings.custom_model_api_key
                )
            except ValueError:
                if not allow_unresolved_api_key:
                    raise
                truncated_api_key = cls._truncate_text(settings.custom_model_api_key)
                api_key_source_note = "原值当前无法解析"
                api_key_preview_line = (
                    f"- API Key 原值: `{truncated_api_key}`"
                    if truncated_api_key
                    else None
                )
            else:
                api_key_source_note, api_key_preview_line = cls._build_api_key_notes(
                    resolved_key_config
                )
        else:
            api_key_source_note, api_key_preview_line = cls._build_api_key_notes(
                resolved_key_config
            )

        vision_note = (
            "✅ 开启" if settings.custom_model_enable_vision == "true" else "❌ 关闭"
        )
        video_input_note = (
            "✅ 开启"
            if settings.custom_model_enable_video_input == "true"
            else "❌ 关闭"
        )

        lines = [
            f"- URL: `{settings.custom_model_url}`",
            f"- Model: `{settings.custom_model_name}`",
            f"- API Key 来源: {api_key_source_note}",
        ]
        if api_key_preview_line:
            lines.append(api_key_preview_line)
        lines.extend(
            [
                f"- 识图工具: {vision_note}",
                f"- 视频输入: {video_input_note}",
            ]
        )
        if persisted is not None:
            persist_note = (
                "✅ 已写入 .env 并立即生效"
                if persisted
                else "⚠️ 写入 .env 失败，已仅对当前进程生效（重启后可能失效）"
            )
            lines.append(f"- 持久化: {persist_note}")
        return "\n".join(lines)

    @classmethod
    def build_result_message(
        cls,
        result: CustomModelApplyResult,
        *,
        heading: str = "✅ 已更新 custom 模型配置并生效。",
    ) -> str:
        body = cls._build_message_body(
            result.settings,
            resolved_key_config=result.resolved_key_config,
            persisted=result.persisted,
        )
        return f"{heading}\n{body}"

    @classmethod
    def build_preview_message(
        cls,
        settings: CustomModelSettings,
        *,
        heading: str,
    ) -> str:
        body = cls._build_message_body(
            settings,
            allow_unresolved_api_key=True,
        )
        return f"{heading}\n{body}"

    @classmethod
    def _persist_custom_model_env(cls, settings: CustomModelSettings) -> bool:
        env_path = cls._get_env_path()
        try:
            os.makedirs(os.path.dirname(env_path), exist_ok=True)
            set_key(
                env_path,
                "CUSTOM_MODEL_URL",
                settings.custom_model_url,
                quote_mode="always",
                encoding="utf-8",
            )
            set_key(
                env_path,
                "CUSTOM_MODEL_API_KEY",
                settings.custom_model_api_key,
                quote_mode="always",
                encoding="utf-8",
            )
            set_key(
                env_path,
                "CUSTOM_MODEL_NAME",
                settings.custom_model_name,
                quote_mode="always",
                encoding="utf-8",
            )
            set_key(
                env_path,
                "CUSTOM_MODEL_ENABLE_VISION",
                settings.custom_model_enable_vision,
                quote_mode="always",
                encoding="utf-8",
            )
            set_key(
                env_path,
                "CUSTOM_MODEL_ENABLE_VIDEO_INPUT",
                settings.custom_model_enable_video_input,
                quote_mode="always",
                encoding="utf-8",
            )
            load_dotenv(env_path, override=True, encoding="utf-8")
            return True
        except Exception as exc:
            log.warning("写回 .env 失败（将仅本次进程生效）: %s", exc, exc_info=True)
            return False

    @classmethod
    def validate_settings(
        cls,
        settings: Dict[str, Any],
        *,
        fallback_api_key: str = "",
    ) -> Tuple[CustomModelSettings, ResolvedCustomModelApiKeys]:
        new_url = str(settings.get("custom_model_url", "")).strip()
        submitted_key = str(settings.get("custom_model_api_key", "")).strip()
        effective_key = submitted_key or str(fallback_api_key or "").strip()
        new_name = str(settings.get("custom_model_name", "")).strip()
        vision_raw = str(settings.get("custom_model_enable_vision", "")).strip()
        video_input_raw = str(settings.get("custom_model_enable_video_input", "")).strip()

        new_enable_vision = cls._normalize_bool_value(vision_raw)
        if new_enable_vision is None:
            raise ValueError("“开启识图工具” 只能填写 true 或 false。")

        new_enable_video_input = cls._normalize_bool_value(video_input_raw)
        if new_enable_video_input is None:
            raise ValueError("“开启视频输入” 只能填写 true 或 false。")

        if new_enable_vision == "true" and new_enable_video_input == "true":
            raise ValueError("仅当“开启识图工具”为 false 时，才允许开启视频输入。")

        if not new_url or not effective_key or not new_name:
            raise ValueError(
                "CUSTOM_MODEL_URL / CUSTOM_MODEL_API_KEY / CUSTOM_MODEL_NAME 不能为空。"
            )

        resolved_key_config = resolve_custom_model_api_keys(effective_key)
        return (
            CustomModelSettings(
                custom_model_url=new_url,
                custom_model_api_key=effective_key,
                custom_model_name=new_name,
                custom_model_enable_vision=new_enable_vision,
                custom_model_enable_video_input=new_enable_video_input,
            ),
            resolved_key_config,
        )

    @classmethod
    def apply_settings(
        cls,
        settings: CustomModelSettings,
        *,
        resolved_key_config: Optional[ResolvedCustomModelApiKeys] = None,
    ) -> CustomModelApplyResult:
        resolved = resolved_key_config or resolve_custom_model_api_keys(
            settings.custom_model_api_key
        )

        os.environ["CUSTOM_MODEL_URL"] = settings.custom_model_url
        os.environ["CUSTOM_MODEL_API_KEY"] = settings.custom_model_api_key
        os.environ["CUSTOM_MODEL_NAME"] = settings.custom_model_name
        os.environ["CUSTOM_MODEL_ENABLE_VISION"] = settings.custom_model_enable_vision
        os.environ["CUSTOM_MODEL_ENABLE_VIDEO_INPUT"] = (
            settings.custom_model_enable_video_input
        )
        if "CUSTON_MODEL_API_KEY" in os.environ:
            os.environ["CUSTON_MODEL_API_KEY"] = settings.custom_model_api_key

        persisted = cls._persist_custom_model_env(settings)

        try:
            gemini_service.openai_service.custom_model_client = CustomModelClient()
        except Exception as exc:
            log.warning("刷新 CustomModelClient 失败: %s", exc, exc_info=True)

        return CustomModelApplyResult(
            settings=settings,
            resolved_key_config=resolved,
            persisted=persisted,
        )

    @classmethod
    def apply_settings_from_mapping(
        cls,
        settings: Dict[str, Any],
        *,
        fallback_api_key: str = "",
    ) -> CustomModelApplyResult:
        normalized_settings, resolved_key_config = cls.validate_settings(
            settings,
            fallback_api_key=fallback_api_key,
        )
        return cls.apply_settings(
            normalized_settings,
            resolved_key_config=resolved_key_config,
        )


class CustomModelConfigView(_OwnedView):
    """当用户选择 custom 模型时，引导其打开二次配置窗口。"""

    def __init__(
        self,
        *,
        opener_user_id: int,
        settings_service: ChatSettingsService,
        preset_store: Optional[CustomModelPresetStore] = None,
        timeout: int = 180,
    ):
        super().__init__(opener_user_id=opener_user_id, timeout=timeout)
        self.settings_service = settings_service
        self.preset_store = preset_store or CustomModelPresetStore()
        self.preset_paginator: Optional[PaginatedSelect] = None
        self._create_preset_paginator()
        self._rebuild_items()

    def _create_preset_paginator(self) -> None:
        presets = self.preset_store.list_presets()
        options = [
            SelectOption(
                label=preset.name,
                value=preset.preset_id,
                description=CustomModelConfigRuntime._truncate_text(
                    preset.custom_model_name or preset.custom_model_url,
                    limit=100,
                )
                or "点击查看该预设详情",
            )
            for preset in presets[:25]
        ]

        self.preset_paginator = PaginatedSelect(
            placeholder="选择已保存的预设...",
            custom_id_prefix="custom_model_preset_select",
            options=options,
            on_select_callback=self.on_preset_select,
            label_prefix="预设",
        )

    def _rebuild_items(self) -> None:
        self.clear_items()
        self._create_preset_paginator()

        open_btn = Button(
            label="(可选) 配置 custom 模型参数",
            style=ButtonStyle.green,
            custom_id="custom_model_config_open",
            row=0,
        )
        open_btn.callback = self.on_open
        self.add_item(open_btn)

        refresh_btn = Button(
            label="刷新",
            style=ButtonStyle.primary,
            custom_id="custom_model_preset_refresh",
            row=0,
        )
        refresh_btn.callback = self.on_refresh
        self.add_item(refresh_btn)

        if self.preset_paginator and self.preset_paginator.options:
            preset_select = self.preset_paginator.create_select(row=1)
            preset_select.max_values = 1
            preset_select.min_values = 1
            self.add_item(preset_select)

    async def on_refresh(self, interaction: Interaction) -> None:
        self._rebuild_items()
        await interaction.response.edit_message(view=self)

    async def on_preset_select(self, interaction: Interaction) -> None:
        if not interaction.data or "values" not in interaction.data:
            await interaction.response.defer()
            return

        values = interaction.data["values"]
        if not values or values[0] == "disabled":
            await interaction.response.defer()
            return

        preset_id = values[0]
        try:
            preset = self.preset_store.get_preset(preset_id)
        except CustomModelPresetNotFoundError as exc:
            await interaction.response.send_message(
                f"❌ {exc}",
                view=self._build_main_view(),
                ephemeral=True,
            )
            return

        settings = CustomModelSettings.from_preset(preset)
        await interaction.response.send_message(
            CustomModelConfigRuntime.build_preview_message(
                settings,
                heading=f"📦 预设预览：`{preset.name}`",
            ),
            view=CustomModelPresetPreviewView(
                opener_user_id=self.opener_user_id,
                settings_service=self.settings_service,
                preset_store=self.preset_store,
                preset_id=preset.preset_id,
                timeout=self.timeout or 180,
            ),
            ephemeral=True,
        )

    def _build_config_modal(
        self,
        *,
        title: str,
        initial_settings: CustomModelSettings,
        on_submit_callback,
    ) -> CustomModelConfigModal:
        api_key_default_value, api_key_omitted = (
            CustomModelConfigRuntime.build_modal_api_key_state(initial_settings)
        )
        return CustomModelConfigModal(
            title=title,
            current_url=initial_settings.custom_model_url,
            current_api_key=api_key_default_value,
            current_api_key_omitted=api_key_omitted,
            current_model_name=initial_settings.custom_model_name,
            current_enable_vision=initial_settings.custom_model_enable_vision,
            current_enable_video_input=initial_settings.custom_model_enable_video_input,
            on_submit_callback=on_submit_callback,
        )

    def _build_main_view(self) -> "CustomModelConfigView":
        return CustomModelConfigView(
            opener_user_id=self.opener_user_id,
            settings_service=self.settings_service,
            preset_store=self.preset_store,
            timeout=self.timeout or 180,
        )

    def _build_save_view(
        self, settings: CustomModelSettings
    ) -> "CustomModelConfigSuccessView":
        return CustomModelConfigSuccessView(
            opener_user_id=self.opener_user_id,
            settings_service=self.settings_service,
            preset_store=self.preset_store,
            saved_settings=settings,
            timeout=self.timeout or 180,
        )

    async def on_open(self, interaction: Interaction) -> None:
        current_settings = CustomModelConfigRuntime.read_current_settings()

        async def _on_submit(modal_interaction: Interaction, settings: Dict[str, Any]):
            try:
                result = CustomModelConfigRuntime.apply_settings_from_mapping(
                    settings,
                    fallback_api_key=current_settings.custom_model_api_key,
                )
            except ValueError as exc:
                await modal_interaction.response.send_message(
                    f"❌ {exc}",
                    ephemeral=True,
                )
                return

            await modal_interaction.response.send_message(
                CustomModelConfigRuntime.build_result_message(result),
                view=self._build_save_view(result.settings),
                ephemeral=True,
            )

        await interaction.response.send_modal(
            self._build_config_modal(
                title="配置 Custom 模型",
                initial_settings=current_settings,
                on_submit_callback=_on_submit,
            )
        )


class CustomModelConfigSuccessView(_OwnedView):
    def __init__(
        self,
        *,
        opener_user_id: int,
        settings_service: ChatSettingsService,
        preset_store: CustomModelPresetStore,
        saved_settings: CustomModelSettings,
        timeout: int = 180,
    ):
        super().__init__(opener_user_id=opener_user_id, timeout=timeout)
        self.settings_service = settings_service
        self.preset_store = preset_store
        self.saved_settings = saved_settings

        save_btn = Button(
            label="保存为预设",
            style=ButtonStyle.primary,
            custom_id="custom_model_save_preset",
            row=0,
        )
        save_btn.callback = self.on_save_preset
        self.add_item(save_btn)

    async def on_save_preset(self, interaction: Interaction) -> None:
        async def _on_submit(modal_interaction: Interaction, preset_name: str):
            try:
                preset = self.preset_store.create_preset(
                    name=preset_name,
                    settings=self.saved_settings.to_dict(),
                )
            except ValueError as exc:
                await modal_interaction.response.send_message(
                    f"❌ {exc}",
                    ephemeral=True,
                )
                return

            await modal_interaction.response.send_message(
                f"✅ 已保存为预设 `{preset.name}`。",
                ephemeral=True,
            )

        await interaction.response.send_modal(
            CustomModelPresetNameModal(
                title="保存为 custom 预设",
                current_name="",
                on_submit_callback=_on_submit,
            )
        )


class CustomModelPresetPreviewView(_OwnedView):
    def __init__(
        self,
        *,
        opener_user_id: int,
        settings_service: ChatSettingsService,
        preset_store: CustomModelPresetStore,
        preset_id: str,
        timeout: int = 180,
    ):
        super().__init__(opener_user_id=opener_user_id, timeout=timeout)
        self.settings_service = settings_service
        self.preset_store = preset_store
        self.preset_id = preset_id

        apply_btn = Button(
            label="应用此预设",
            style=ButtonStyle.success,
            custom_id=f"custom_model_preset_apply_{preset_id}",
            row=0,
        )
        apply_btn.callback = self.on_apply
        self.add_item(apply_btn)

        edit_btn = Button(
            label="配置预设",
            style=ButtonStyle.primary,
            custom_id=f"custom_model_preset_configure_{preset_id}",
            row=0,
        )
        edit_btn.callback = self.on_configure
        self.add_item(edit_btn)

        rename_btn = Button(
            label="改名",
            style=ButtonStyle.secondary,
            custom_id=f"custom_model_preset_rename_{preset_id}",
            row=0,
        )
        rename_btn.callback = self.on_rename
        self.add_item(rename_btn)

        delete_btn = Button(
            label="删除此预设",
            style=ButtonStyle.danger,
            custom_id=f"custom_model_preset_delete_{preset_id}",
            row=0,
        )
        delete_btn.callback = self.on_delete
        self.add_item(delete_btn)

    def _build_main_view(self) -> CustomModelConfigView:
        return CustomModelConfigView(
            opener_user_id=self.opener_user_id,
            settings_service=self.settings_service,
            preset_store=self.preset_store,
            timeout=self.timeout or 180,
        )

    def _build_preview_content(self, preset: CustomModelPreset, *, heading: str) -> str:
        return CustomModelConfigRuntime.build_preview_message(
            CustomModelSettings.from_preset(preset),
            heading=heading,
        )

    def _build_preview_view(self, *, preset_id: str) -> "CustomModelPresetPreviewView":
        return CustomModelPresetPreviewView(
            opener_user_id=self.opener_user_id,
            settings_service=self.settings_service,
            preset_store=self.preset_store,
            preset_id=preset_id,
            timeout=self.timeout or 180,
        )

    def _build_save_view(
        self, settings: CustomModelSettings
    ) -> CustomModelConfigSuccessView:
        return CustomModelConfigSuccessView(
            opener_user_id=self.opener_user_id,
            settings_service=self.settings_service,
            preset_store=self.preset_store,
            saved_settings=settings,
            timeout=self.timeout or 180,
        )

    def _get_current_preset(self) -> CustomModelPreset:
        return self.preset_store.get_preset(self.preset_id)

    def _build_config_modal(
        self,
        *,
        title: str,
        initial_settings: CustomModelSettings,
        on_submit_callback,
    ) -> CustomModelConfigModal:
        api_key_default_value, api_key_omitted = (
            CustomModelConfigRuntime.build_modal_api_key_state(initial_settings)
        )
        return CustomModelConfigModal(
            title=title,
            current_url=initial_settings.custom_model_url,
            current_api_key=api_key_default_value,
            current_api_key_omitted=api_key_omitted,
            current_model_name=initial_settings.custom_model_name,
            current_enable_vision=initial_settings.custom_model_enable_vision,
            current_enable_video_input=initial_settings.custom_model_enable_video_input,
            on_submit_callback=on_submit_callback,
        )

    async def on_apply(self, interaction: Interaction) -> None:
        try:
            preset = self._get_current_preset()
            result = CustomModelConfigRuntime.apply_settings_from_mapping(
                CustomModelSettings.from_preset(preset).to_dict()
            )
        except ValueError as exc:
            await interaction.response.edit_message(
                content=f"❌ {exc}",
                view=self._build_main_view(),
            )
            return

        await interaction.response.edit_message(
            content=CustomModelConfigRuntime.build_result_message(result),
            view=self._build_save_view(result.settings),
        )

    async def on_configure(self, interaction: Interaction) -> None:
        try:
            preset = self._get_current_preset()
        except CustomModelPresetNotFoundError as exc:
            await interaction.response.edit_message(
                content=f"❌ {exc}",
                view=self._build_main_view(),
            )
            return

        current_settings = CustomModelSettings.from_preset(preset)

        async def _on_submit(modal_interaction: Interaction, settings: Dict[str, Any]):
            try:
                normalized_settings, _ = CustomModelConfigRuntime.validate_settings(
                    settings,
                    fallback_api_key=current_settings.custom_model_api_key,
                )
                updated_preset = self.preset_store.update_preset_settings(
                    self.preset_id,
                    settings=normalized_settings.to_dict(),
                )
            except ValueError as exc:
                await modal_interaction.response.send_message(
                    f"❌ {exc}",
                    ephemeral=True,
                )
                return

            await modal_interaction.response.send_message(
                self._build_preview_content(
                    updated_preset,
                    heading=f"✅ 已更新预设 `{updated_preset.name}`。",
                ),
                view=self._build_preview_view(preset_id=updated_preset.preset_id),
                ephemeral=True,
            )

        await interaction.response.send_modal(
            self._build_config_modal(
                title=f"配置预设：{preset.name}",
                initial_settings=current_settings,
                on_submit_callback=_on_submit,
            )
        )

    async def on_rename(self, interaction: Interaction) -> None:
        try:
            preset = self._get_current_preset()
        except CustomModelPresetNotFoundError as exc:
            await interaction.response.edit_message(
                content=f"❌ {exc}",
                view=self._build_main_view(),
            )
            return

        async def _on_submit(modal_interaction: Interaction, preset_name: str):
            try:
                updated_preset = self.preset_store.rename_preset(
                    self.preset_id,
                    name=preset_name,
                )
            except ValueError as exc:
                await modal_interaction.response.send_message(
                    f"❌ {exc}",
                    ephemeral=True,
                )
                return

            await modal_interaction.response.send_message(
                self._build_preview_content(
                    updated_preset,
                    heading=f"✅ 已更新预设 `{updated_preset.name}`。",
                ),
                view=self._build_preview_view(preset_id=updated_preset.preset_id),
                ephemeral=True,
            )

        await interaction.response.send_modal(
            CustomModelPresetNameModal(
                title=f"修改预设名称：{preset.name}",
                current_name=preset.name,
                on_submit_callback=_on_submit,
            )
        )

    async def on_delete(self, interaction: Interaction) -> None:
        try:
            preset = self._get_current_preset()
        except CustomModelPresetNotFoundError as exc:
            await interaction.response.edit_message(
                content=f"❌ {exc}",
                view=self._build_main_view(),
            )
            return

        await interaction.response.edit_message(
            content=f"⚠️ 确认删除预设 `{preset.name}`？此操作无法撤销。",
            view=CustomModelPresetDeleteConfirmView(
                opener_user_id=self.opener_user_id,
                settings_service=self.settings_service,
                preset_store=self.preset_store,
                preset_id=preset.preset_id,
                timeout=self.timeout or 180,
            ),
        )


class CustomModelPresetDeleteConfirmView(_OwnedView):
    def __init__(
        self,
        *,
        opener_user_id: int,
        settings_service: ChatSettingsService,
        preset_store: CustomModelPresetStore,
        preset_id: str,
        timeout: int = 180,
    ):
        super().__init__(opener_user_id=opener_user_id, timeout=timeout)
        self.settings_service = settings_service
        self.preset_store = preset_store
        self.preset_id = preset_id

        confirm_btn = Button(
            label="确认删除",
            style=ButtonStyle.danger,
            custom_id=f"custom_model_preset_delete_confirm_{preset_id}",
            row=0,
        )
        confirm_btn.callback = self.on_confirm_delete
        self.add_item(confirm_btn)

        cancel_btn = Button(
            label="取消",
            style=ButtonStyle.secondary,
            custom_id=f"custom_model_preset_delete_cancel_{preset_id}",
            row=0,
        )
        cancel_btn.callback = self.on_cancel_delete
        self.add_item(cancel_btn)

    def _build_main_view(self) -> CustomModelConfigView:
        return CustomModelConfigView(
            opener_user_id=self.opener_user_id,
            settings_service=self.settings_service,
            preset_store=self.preset_store,
            timeout=self.timeout or 180,
        )

    def _build_preview_view(self, *, preset_id: str) -> CustomModelPresetPreviewView:
        return CustomModelPresetPreviewView(
            opener_user_id=self.opener_user_id,
            settings_service=self.settings_service,
            preset_store=self.preset_store,
            preset_id=preset_id,
            timeout=self.timeout or 180,
        )

    async def on_confirm_delete(self, interaction: Interaction) -> None:
        preset_name = "该预设"
        try:
            preset = self.preset_store.get_preset(self.preset_id)
            preset_name = preset.name
            self.preset_store.delete_preset(self.preset_id)
        except CustomModelPresetNotFoundError as exc:
            await interaction.response.edit_message(
                content=f"❌ {exc}",
                view=self._build_main_view(),
            )
            return

        await interaction.response.edit_message(
            content=f"✅ 已删除预设 `{preset_name}`。",
            view=None,
        )

    async def on_cancel_delete(self, interaction: Interaction) -> None:
        try:
            preset = self.preset_store.get_preset(self.preset_id)
        except CustomModelPresetNotFoundError as exc:
            await interaction.response.edit_message(
                content=f"❌ {exc}",
                view=self._build_main_view(),
            )
            return

        await interaction.response.edit_message(
            content=CustomModelConfigRuntime.build_preview_message(
                CustomModelSettings.from_preset(preset),
                heading=f"📦 预设预览：`{preset.name}`",
            ),
            view=self._build_preview_view(preset_id=preset.preset_id),
        )
