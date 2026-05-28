# -*- coding: utf-8 -*-
"""
提示词配置 UI 组件

提供在聊天设置中修改默认人设和越狱方式的界面。
支持人设预设的保存、加载和删除。
所有修改存储在数据库 global_settings 中，不会修改源码文件。
"""

import json
import logging
import re
import discord
from discord import ButtonStyle, Interaction
from typing import Optional, Dict, Any, List

from src.chat.features.chat_settings.services.chat_settings_service import (
    chat_settings_service,
)
from src.chat.config.prompts import PROMPT_CONFIG

log = logging.getLogger(__name__)

# 数据库 key 前缀
_PROMPT_OVERRIDE_PREFIX = "prompt_override_default_"
_PRESET_DB_KEY = "persona_presets"  # JSON 格式存储所有预设
_ACTIVE_PRESET_KEY = "active_persona_preset_name"


def _db_key(prompt_name: str) -> str:
    return f"{_PROMPT_OVERRIDE_PREFIX}{prompt_name}"


async def _get_override(prompt_name: str) -> Optional[str]:
    """从数据库读取提示词覆盖值。"""
    raw = await chat_settings_service.db_manager.get_global_setting(
        _db_key(prompt_name)
    )
    if raw is not None and raw.strip():
        return raw
    return None


async def _set_override(prompt_name: str, value: str) -> None:
    """将提示词覆盖值写入数据库。"""
    await chat_settings_service.db_manager.set_global_setting(
        _db_key(prompt_name), value
    )


async def _clear_override(prompt_name: str) -> None:
    """清除数据库中的提示词覆盖值（恢复为文件默认值）。"""
    await chat_settings_service.db_manager.set_global_setting(
        _db_key(prompt_name), ""
    )


def _get_file_default(prompt_name: str) -> str:
    """从 prompts.py 文件中获取默认值。"""
    return (PROMPT_CONFIG.get("default", {}).get(prompt_name) or "").strip()


# --- <character> 标签提取/替换 ---


def _extract_character_content(text: str) -> str:
    """从 SYSTEM_PROMPT 中提取 <character>...</character> 内部内容。"""
    if not text:
        return ""
    match = re.search(r"<character>(.*?)</character>", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _build_system_prompt_with_character(character_content: str) -> str:
    """用新的 <character> 内容重建完整的 SYSTEM_PROMPT（基于文件默认值的非 character 部分）。"""
    default_prompt = _get_file_default("SYSTEM_PROMPT")
    if not default_prompt:
        return f"<character>\n{character_content}\n</character>"

    # 提取默认值中的非 character 部分（<character>之前和之后的内容）
    match = re.search(r"<character>.*?</character>", default_prompt, re.DOTALL)
    if not match:
        # 默认值没有 <character> 标签，直接追加
        return f"{default_prompt}\n<character>\n{character_content}\n</character>"

    before = default_prompt[:match.start()].rstrip()
    after = default_prompt[match.end():].lstrip()
    parts = [before, f"<character>\n{character_content}\n</character>"]
    if after:
        parts.append(after)
    return "\n\n".join(parts)


# --- 预设管理 ---


async def _get_presets() -> Dict[str, str]:
    """从数据库读取所有保存的预设。格式: {"预设名": "<character>内容", ...}"""
    raw = await chat_settings_service.db_manager.get_global_setting(_PRESET_DB_KEY)
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


async def _save_presets(presets: Dict[str, str]) -> None:
    """将所有预设保存到数据库。"""
    await chat_settings_service.db_manager.set_global_setting(
        _PRESET_DB_KEY, json.dumps(presets, ensure_ascii=False)
    )


async def _get_active_preset_name() -> Optional[str]:
    """获取当前激活的预设名称。"""
    raw = await chat_settings_service.db_manager.get_global_setting(_ACTIVE_PRESET_KEY)
    return raw if raw and raw.strip() else None


async def _set_active_preset_name(name: Optional[str]) -> None:
    """设置当前激活的预设名称。"""
    await chat_settings_service.db_manager.set_global_setting(
        _ACTIVE_PRESET_KEY, name or ""
    )


# --- Modals ---


class CharacterEditModal(discord.ui.Modal, title="修改人设 (<character>)"):
    """编辑 SYSTEM_PROMPT 中 <character>...</character> 的内容。"""

    def __init__(self, current_character_content: str):
        super().__init__(timeout=300)
        self.character_input = discord.ui.TextInput(
            label="<character> 标签内的内容",
            placeholder="输入人设内容（<core_identity>、<behavioral_guidelines> 等）...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
            default=current_character_content[:4000] if current_character_content else "",
        )
        self.add_item(self.character_input)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        new_character = self.character_input.value.strip()
        if not new_character:
            await interaction.followup.send("❌ 内容不能为空。", ephemeral=True)
            return

        # 用新的 character 内容重建完整 SYSTEM_PROMPT
        full_prompt = _build_system_prompt_with_character(new_character)
        await _set_override("SYSTEM_PROMPT", full_prompt)
        await interaction.followup.send(
            "✅ **人设 (character)** 已更新。\n"
            "下次对话时将使用新的人设。如需恢复默认，点击「恢复默认」按钮。",
            ephemeral=True,
        )


class PresetSaveModal(discord.ui.Modal, title="保存当前人设为预设"):
    """输入预设名称保存当前人设。"""

    def __init__(self, current_character_content: str):
        super().__init__(timeout=300)
        self.character_content = current_character_content
        self.name_input = discord.ui.TextInput(
            label="预设名称",
            placeholder="例如：温柔版、傲娇版、病娇版...",
            style=discord.TextStyle.short,
            required=True,
            max_length=50,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        name = self.name_input.value.strip()
        if not name:
            await interaction.followup.send("❌ 预设名称不能为空。", ephemeral=True)
            return

        presets = await _get_presets()
        presets[name] = self.character_content
        await _save_presets(presets)
        await _set_active_preset_name(name)
        await interaction.followup.send(
            f"✅ 人设预设 **「{name}」** 已保存！\n"
            f"可以随时切换回此预设。",
            ephemeral=True,
        )


class JailbreakModal(discord.ui.Modal, title="修改越狱方式"):
    """编辑越狱方式的模态框。"""

    def __init__(
        self,
        current_user_prompt: str,
        current_model_response: str,
        current_final_instruction: str,
    ):
        super().__init__(timeout=300)
        self.user_prompt_input = discord.ui.TextInput(
            label="JAILBREAK_USER_PROMPT（越狱用户提示）",
            placeholder="输入越狱用户提示...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
            default=current_user_prompt[:4000] if current_user_prompt else "",
        )
        self.model_response_input = discord.ui.TextInput(
            label="JAILBREAK_MODEL_RESPONSE（越狱模型回复）",
            placeholder="输入越狱模型回复...",
            style=discord.TextStyle.short,
            required=True,
            max_length=2000,
            default=current_model_response[:2000] if current_model_response else "",
        )
        self.final_instruction_input = discord.ui.TextInput(
            label="JAILBREAK_FINAL_INSTRUCTION（最终指令）",
            placeholder="输入最终指令...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
            default=current_final_instruction[:4000] if current_final_instruction else "",
        )
        self.add_item(self.user_prompt_input)
        self.add_item(self.model_response_input)
        self.add_item(self.final_instruction_input)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        user_prompt = self.user_prompt_input.value.strip()
        model_response = self.model_response_input.value.strip()
        final_instruction = self.final_instruction_input.value.strip()

        if not user_prompt or not model_response or not final_instruction:
            await interaction.followup.send("❌ 所有字段都不能为空。", ephemeral=True)
            return

        await _set_override("JAILBREAK_USER_PROMPT", user_prompt)
        await _set_override("JAILBREAK_MODEL_RESPONSE", model_response)
        await _set_override("JAILBREAK_FINAL_INSTRUCTION", final_instruction)
        await interaction.followup.send(
            "✅ **越狱方式** 已更新。\n"
            "- JAILBREAK_USER_PROMPT ✅\n"
            "- JAILBREAK_MODEL_RESPONSE ✅\n"
            "- JAILBREAK_FINAL_INSTRUCTION ✅\n"
            "下次对话时将使用新的越狱方式。如需恢复默认，点击「恢复默认」按钮。",
            ephemeral=True,
        )


# --- Views ---


class PromptConfigView(discord.ui.View):
    """提示词配置主面板。"""

    def __init__(self, opener_user_id: int):
        super().__init__(timeout=300)
        self.opener_user_id = opener_user_id

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.opener_user_id:
            await interaction.response.send_message(
                "这不是你的设置面板。", ephemeral=True
            )
            return False
        return True

    def _render_content(
        self,
        has_system_override: bool,
        has_jb_override: bool,
        active_preset_name: Optional[str] = None,
        preset_count: int = 0,
    ) -> str:
        lines = [
            "📝 **提示词配置**\n",
            f"**1. 人设 (character)**: {'🟡 已自定义' if has_system_override else '🟢 使用文件默认值'}",
            f"**2. 越狱方式 (JAILBREAK)**: {'🟡 已自定义' if has_jb_override else '🟢 使用文件默认值'}",
            f"**当前预设**: {'🏷️ ' + active_preset_name if active_preset_name else '无'}  |  已保存预设: {preset_count}个",
            "",
            "人设修改只影响 `<character>...</character>` 内容，不影响其他部分。",
            "修改会保存到数据库，不会修改源码文件。重启后依然生效。",
        ]
        return "\n".join(lines)

    async def build_and_send(self, interaction: Interaction):
        """构建并发送提示词配置面板。"""
        sys_override = await _get_override("SYSTEM_PROMPT")
        jb_override = await _get_override("JAILBREAK_USER_PROMPT")
        has_sys = sys_override is not None
        has_jb = jb_override is not None
        presets = await _get_presets()
        active_name = await _get_active_preset_name()

        content = self._render_content(has_sys, has_jb, active_name, len(presets))
        self._rebuild_buttons(has_sys, has_jb, presets, active_name)
        await interaction.response.edit_message(content=content, view=self)

    def _rebuild_buttons(
        self,
        has_system_override: bool,
        has_jb_override: bool,
        presets: Optional[Dict[str, str]] = None,
        active_preset_name: Optional[str] = None,
    ):
        self.clear_items()
        presets = presets or {}

        # Row 0: 编辑按钮
        edit_sys_btn = discord.ui.Button(
            label="修改人设",
            style=ButtonStyle.primary,
            emoji="🎭",
            row=0,
        )
        edit_sys_btn.callback = self._on_edit_system_prompt
        self.add_item(edit_sys_btn)

        edit_jb_btn = discord.ui.Button(
            label="修改越狱方式",
            style=ButtonStyle.primary,
            emoji="🔓",
            row=0,
        )
        edit_jb_btn.callback = self._on_edit_jailbreak
        self.add_item(edit_jb_btn)

        # Row 1: 预设按钮
        save_btn = discord.ui.Button(
            label="保存当前为预设",
            style=ButtonStyle.success,
            emoji="💾",
            row=1,
        )
        save_btn.callback = self._on_save_preset
        self.add_item(save_btn)

        # 预设选择下拉菜单
        if presets:
            preset_options = [
                discord.SelectOption(
                    label=f"{'✅ ' if name == active_preset_name else ''}{name}",
                    value=name,
                    default=(name == active_preset_name),
                )
                for name in list(presets.keys())[:25]
            ]
            preset_select = discord.ui.Select(
                placeholder="切换预设...",
                options=preset_options,
                custom_id="preset_select",
                row=2,
            )
            preset_select.callback = self._on_load_preset
            self.add_item(preset_select)

            # 删除预设下拉菜单
            delete_options = [
                discord.SelectOption(label=f"🗑️ {name}", value=name)
                for name in list(presets.keys())[:25]
            ]
            delete_select = discord.ui.Select(
                placeholder="删除预设...",
                options=delete_options,
                custom_id="preset_delete_select",
                row=3,
            )
            delete_select.callback = self._on_delete_preset
            self.add_item(delete_select)

        # Row 4: 恢复默认
        if has_system_override or has_jb_override:
            reset_btn = discord.ui.Button(
                label="恢复全部默认",
                style=ButtonStyle.danger,
                emoji="🔄",
                row=4,
            )
            reset_btn.callback = self._on_reset_all
            self.add_item(reset_btn)

        if has_system_override:
            reset_sys_btn = discord.ui.Button(
                label="恢复人设默认",
                style=ButtonStyle.secondary,
                emoji="↩️",
                row=4,
            )
            reset_sys_btn.callback = self._on_reset_system
            self.add_item(reset_sys_btn)

        if has_jb_override:
            reset_jb_btn = discord.ui.Button(
                label="恢复越狱默认",
                style=ButtonStyle.secondary,
                emoji="↩️",
                row=4,
            )
            reset_jb_btn.callback = self._on_reset_jailbreak
            self.add_item(reset_jb_btn)

    async def _on_edit_system_prompt(self, interaction: Interaction):
        """打开修改人设的模态框（只编辑 <character> 内容）。"""
        current = await _get_override("SYSTEM_PROMPT")
        if current is None:
            current = _get_file_default("SYSTEM_PROMPT")

        character_content = _extract_character_content(current)
        modal = CharacterEditModal(current_character_content=character_content)
        await interaction.response.send_modal(modal)

        try:
            await modal.wait()
            await self._refresh_panel()
        except Exception:
            pass

    async def _on_save_preset(self, interaction: Interaction):
        """保存当前人设为预设。"""
        current = await _get_override("SYSTEM_PROMPT")
        if current is None:
            current = _get_file_default("SYSTEM_PROMPT")
        character_content = _extract_character_content(current)

        modal = PresetSaveModal(current_character_content=character_content)
        await interaction.response.send_modal(modal)

        try:
            await modal.wait()
            await self._refresh_panel()
        except Exception:
            pass

    async def _on_load_preset(self, interaction: Interaction):
        """加载选中的预设。"""
        if not interaction.data or "values" not in interaction.data:
            await interaction.response.defer()
            return

        preset_name = interaction.data["values"][0]
        presets = await _get_presets()
        character_content = presets.get(preset_name)
        if not character_content:
            await interaction.response.send_message(
                f"❌ 预设「{preset_name}」不存在。", ephemeral=True
            )
            return

        full_prompt = _build_system_prompt_with_character(character_content)
        await _set_override("SYSTEM_PROMPT", full_prompt)
        await _set_active_preset_name(preset_name)

        # 刷新面板
        sys_override = await _get_override("SYSTEM_PROMPT")
        jb_override = await _get_override("JAILBREAK_USER_PROMPT")
        has_sys = sys_override is not None
        has_jb = jb_override is not None
        updated_presets = await _get_presets()
        content = self._render_content(has_sys, has_jb, preset_name, len(updated_presets))
        self._rebuild_buttons(has_sys, has_jb, updated_presets, preset_name)
        await interaction.response.edit_message(content=content, view=self)

    async def _on_delete_preset(self, interaction: Interaction):
        """删除选中的预设。"""
        if not interaction.data or "values" not in interaction.data:
            await interaction.response.defer()
            return

        preset_name = interaction.data["values"][0]
        presets = await _get_presets()
        if preset_name not in presets:
            await interaction.response.send_message(
                f"❌ 预设「{preset_name}」不存在。", ephemeral=True
            )
            return

        del presets[preset_name]
        await _save_presets(presets)

        # 如果删除的是当前激活的预设，清除激活状态
        active_name = await _get_active_preset_name()
        if active_name == preset_name:
            await _set_active_preset_name(None)
            active_name = None

        # 刷新面板
        sys_override = await _get_override("SYSTEM_PROMPT")
        jb_override = await _get_override("JAILBREAK_USER_PROMPT")
        has_sys = sys_override is not None
        has_jb = jb_override is not None
        content = self._render_content(has_sys, has_jb, active_name, len(presets))
        self._rebuild_buttons(has_sys, has_jb, presets, active_name)
        await interaction.response.edit_message(content=content, view=self)

    async def _refresh_panel(self):
        """刷新面板内容。"""
        sys_override = await _get_override("SYSTEM_PROMPT")
        jb_override = await _get_override("JAILBREAK_USER_PROMPT")
        has_sys = sys_override is not None
        has_jb = jb_override is not None
        presets = await _get_presets()
        active_name = await _get_active_preset_name()
        content = self._render_content(has_sys, has_jb, active_name, len(presets))
        self._rebuild_buttons(has_sys, has_jb, presets, active_name)
        if self.message:
            await self.message.edit(content=content, view=self)

    async def _on_edit_jailbreak(self, interaction: Interaction):
        """打开修改越狱方式的模态框。"""
        current_user = await _get_override("JAILBREAK_USER_PROMPT")
        if current_user is None:
            current_user = _get_file_default("JAILBREAK_USER_PROMPT")

        current_model = await _get_override("JAILBREAK_MODEL_RESPONSE")
        if current_model is None:
            current_model = _get_file_default("JAILBREAK_MODEL_RESPONSE")

        current_final = await _get_override("JAILBREAK_FINAL_INSTRUCTION")
        if current_final is None:
            current_final = _get_file_default("JAILBREAK_FINAL_INSTRUCTION")

        modal = JailbreakModal(
            current_user_prompt=current_user,
            current_model_response=current_model,
            current_final_instruction=current_final,
        )
        await interaction.response.send_modal(modal)

        try:
            await modal.wait()
            await self._refresh_panel()
        except Exception:
            pass

    async def _on_reset_all(self, interaction: Interaction):
        """恢复全部默认。"""
        await interaction.response.defer(ephemeral=True)
        await _clear_override("SYSTEM_PROMPT")
        await _clear_override("JAILBREAK_USER_PROMPT")
        await _clear_override("JAILBREAK_MODEL_RESPONSE")
        await _clear_override("JAILBREAK_FINAL_INSTRUCTION")
        await _set_active_preset_name(None)

        await self._refresh_panel()
        await interaction.followup.send("✅ 已恢复全部默认提示词。", ephemeral=True)

    async def _on_reset_system(self, interaction: Interaction):
        """恢复人设默认。"""
        await interaction.response.defer(ephemeral=True)
        await _clear_override("SYSTEM_PROMPT")
        await _set_active_preset_name(None)

        await self._refresh_panel()
        await interaction.followup.send("✅ 已恢复默认人设。", ephemeral=True)

    async def _on_reset_jailbreak(self, interaction: Interaction):
        """恢复越狱默认。"""
        await interaction.response.defer(ephemeral=True)
        await _clear_override("JAILBREAK_USER_PROMPT")
        await _clear_override("JAILBREAK_MODEL_RESPONSE")
        await _clear_override("JAILBREAK_FINAL_INSTRUCTION")

        await self._refresh_panel()
        await interaction.followup.send("✅ 已恢复默认越狱方式。", ephemeral=True)
