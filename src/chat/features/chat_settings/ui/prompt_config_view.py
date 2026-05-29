# -*- coding: utf-8 -*-
"""
提示词配置 UI 组件

提供在聊天设置中修改默认人设和越狱方式的界面。
支持人设预设的保存、加载和删除。
所有修改存储在数据库 global_settings 中，不会修改源码文件。

人设拆分为三个编辑框：
  1. 核心人设  → <core_identity>...</core_identity>
  2. 互动规范  → <behavioral_guidelines> 到 </style_guide>（含 acting_guide, abilities, style_guide）
  3. 越狱方式  → JAILBREAK_USER_PROMPT / MODEL_RESPONSE / FINAL_INSTRUCTION
"""

import json
import logging
import re
import discord
from discord import ButtonStyle, Interaction
from typing import Optional, Dict

from src.chat.features.chat_settings.services.chat_settings_service import (
    chat_settings_service,
)
from src.chat.config.prompts import PROMPT_CONFIG

log = logging.getLogger(__name__)

# 数据库 key 前缀
_PROMPT_OVERRIDE_PREFIX = "prompt_override_default_"
_PRESET_DB_KEY = "persona_presets"  # JSON 格式存储所有预设
_ACTIVE_PRESET_KEY = "active_persona_preset_name"


# ============================================================
# 基础读写
# ============================================================


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


async def _get_current_system_prompt() -> str:
    """获取当前生效的 SYSTEM_PROMPT（优先 DB 覆盖，否则文件默认）。"""
    override = await _get_override("SYSTEM_PROMPT")
    return override if override is not None else _get_file_default("SYSTEM_PROMPT")


# ============================================================
# 标签提取 / 替换
# ============================================================


def _extract_character_content(text: str) -> str:
    """从 SYSTEM_PROMPT 中提取 <character>...</character> 内部内容。"""
    if not text:
        return ""
    match = re.search(r"<character>(.*?)</character>", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_tag_content(text: str, tag_name: str) -> str:
    """提取指定标签的内容（不含标签本身）。"""
    if not text:
        return ""
    match = re.search(f"<{tag_name}>(.*?)</{tag_name}>", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_tag_range(text: str, start_tag: str, end_tag: str) -> str:
    """提取从 <start_tag> 到 </end_tag> 的完整范围（含标签本身）。"""
    if not text:
        return ""
    match = re.search(f"(<{start_tag}>.*?</{end_tag}>)", text, re.DOTALL)
    return match.group(0).strip() if match else ""


def _replace_tag_content(text: str, tag_name: str, new_inner: str) -> str:
    """替换指定标签内的内容，保留标签本身。"""
    return re.sub(
        f"(<{tag_name}>)(.*?)(</{tag_name}>)",
        lambda m: f"{m.group(1)}\n{new_inner}\n{m.group(3)}",
        text,
        count=1,
        flags=re.DOTALL,
    )


def _replace_tag_range(text: str, start_tag: str, end_tag: str, new_block: str) -> str:
    """替换从 <start_tag> 到 </end_tag> 的完整范围（含标签）。"""
    return re.sub(
        f"<{start_tag}>.*?</{end_tag}>",
        new_block,
        text,
        count=1,
        flags=re.DOTALL,
    )


def _build_system_prompt_with_character(character_content: str) -> str:
    """用新的 <character> 内容重建完整的 SYSTEM_PROMPT（基于文件默认值的非 character 部分）。"""
    default_prompt = _get_file_default("SYSTEM_PROMPT")
    if not default_prompt:
        return f"<character>\n{character_content}\n</character>"

    match = re.search(r"<character>.*?</character>", default_prompt, re.DOTALL)
    if not match:
        return f"{default_prompt}\n<character>\n{character_content}\n</character>"

    before = default_prompt[:match.start()].rstrip()
    after = default_prompt[match.end():].lstrip()
    parts = [before, f"<character>\n{character_content}\n</character>"]
    if after:
        parts.append(after)
    return "\n\n".join(parts)


# ============================================================
# 预设管理
# ============================================================


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


# ============================================================
# Modals
# ============================================================


class CoreIdentityModal(discord.ui.Modal, title="修改核心人设 (core_identity)"):
    """编辑 <core_identity>...</core_identity> 内容。"""

    def __init__(self, current_content: str):
        super().__init__(timeout=300)
        self.content_input = discord.ui.TextInput(
            label="<core_identity> 标签内的内容",
            placeholder="输入核心人设内容（名称、性格、喜好等）...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
            default=current_content[:4000] if current_content else "",
        )
        self.add_item(self.content_input)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        new_content = self.content_input.value.strip()
        if not new_content:
            await interaction.followup.send("❌ 内容不能为空。", ephemeral=True)
            return

        # 在当前 SYSTEM_PROMPT 中替换 <core_identity> 内容
        current_prompt = await _get_current_system_prompt()
        updated = _replace_tag_content(current_prompt, "core_identity", new_content)
        await _set_override("SYSTEM_PROMPT", updated)
        await interaction.followup.send(
            "✅ **核心人设 (core_identity)** 已更新。\n"
            "下次对话时将使用新的人设。如需恢复默认，点击「恢复默认」按钮。",
            ephemeral=True,
        )


class BehavioralGuidelineModal(discord.ui.Modal, title="修改互动规范 (behavioral~style)"):
    """编辑 <behavioral_guidelines> 到 </style_guide> 范围（含 acting_guide, abilities, style_guide）。"""

    def __init__(self, current_range_content: str):
        super().__init__(timeout=300)
        self.range_input = discord.ui.TextInput(
            label="behavioral_guidelines ~ style_guide 范围",
            placeholder="编辑互动规范、扮演指导、能力定义、对话风格等...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
            default=current_range_content[:4000] if current_range_content else "",
        )
        self.add_item(self.range_input)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        new_range = self.range_input.value.strip()
        if not new_range:
            await interaction.followup.send("❌ 内容不能为空。", ephemeral=True)
            return

        # 在当前 SYSTEM_PROMPT 中替换 behavioral_guidelines ~ style_guide 范围
        current_prompt = await _get_current_system_prompt()
        updated = _replace_tag_range(
            current_prompt, "behavioral_guidelines", "style_guide", new_range
        )
        await _set_override("SYSTEM_PROMPT", updated)
        await interaction.followup.send(
            "✅ **互动规范 (behavioral_guidelines ~ style_guide)** 已更新。\n"
            "下次对话时将使用新的互动规范。如需恢复默认，点击「恢复默认」按钮。",
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


# ============================================================
# Views
# ============================================================


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
        core_customized: bool,
        behav_customized: bool,
        has_jb_override: bool,
        active_preset_name: Optional[str] = None,
        preset_count: int = 0,
    ) -> str:
        lines = [
            "📝 **提示词配置**\n",
            f"**1. 核心人设 (core_identity)**: {'🟡 已自定义' if core_customized else '🟢 默认'}",
            f"**2. 互动规范 (behavioral~style)**: {'🟡 已自定义' if behav_customized else '🟢 默认'}",
            f"**3. 越狱方式 (JAILBREAK)**: {'🟡 已自定义' if has_jb_override else '🟢 使用文件默认值'}",
            f"**当前预设**: {'🏷️ ' + active_preset_name if active_preset_name else '无'}  |  已保存预设: {preset_count}个",
            "",
            "修改只影响对应标签内容，不影响其他部分。",
            "修改会保存到数据库，不会修改源码文件。重启后依然生效。",
        ]
        return "\n".join(lines)

    async def _check_customization(self):
        """检查核心人设和互动规范是否与文件默认值不同。"""
        current = await _get_current_system_prompt()
        default = _get_file_default("SYSTEM_PROMPT")
        core_custom = _extract_tag_content(current, "core_identity") != _extract_tag_content(default, "core_identity")
        behav_custom = _extract_tag_range(current, "behavioral_guidelines", "style_guide") != _extract_tag_range(default, "behavioral_guidelines", "style_guide")
        return core_custom, behav_custom

    async def build_and_send(self, interaction: Interaction):
        """构建并发送提示词配置面板。"""
        core_custom, behav_custom = await self._check_customization()
        jb_override = await _get_override("JAILBREAK_USER_PROMPT")
        has_jb = jb_override is not None
        presets = await _get_presets()
        active_name = await _get_active_preset_name()

        content = self._render_content(core_custom, behav_custom, has_jb, active_name, len(presets))
        self._rebuild_buttons(core_custom or behav_custom, has_jb, presets, active_name)
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

        # Row 0: 三个编辑按钮
        btn_core = discord.ui.Button(
            label="修改核心人设",
            style=ButtonStyle.primary,
            emoji="🎭",
            row=0,
        )
        btn_core.callback = self._on_edit_core_identity
        self.add_item(btn_core)

        btn_behav = discord.ui.Button(
            label="修改互动规范",
            style=ButtonStyle.primary,
            emoji="📋",
            row=0,
        )
        btn_behav.callback = self._on_edit_behavioral
        self.add_item(btn_behav)

        btn_jb = discord.ui.Button(
            label="修改越狱方式",
            style=ButtonStyle.primary,
            emoji="🔓",
            row=0,
        )
        btn_jb.callback = self._on_edit_jailbreak
        self.add_item(btn_jb)

        # Row 1: 预设保存按钮
        save_btn = discord.ui.Button(
            label="保存当前为预设",
            style=ButtonStyle.success,
            emoji="💾",
            row=1,
        )
        save_btn.callback = self._on_save_preset
        self.add_item(save_btn)

        # Row 2: 预设选择下拉菜单
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

            # Row 3: 删除预设下拉菜单
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

        # Row 4: 恢复默认按钮
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

    # --- 编辑回调 ---

    async def _on_edit_core_identity(self, interaction: Interaction):
        """打开核心人设编辑模态框（只编辑 <core_identity> 内容）。"""
        current_prompt = await _get_current_system_prompt()
        content = _extract_tag_content(current_prompt, "core_identity")
        if not content:
            content = _extract_tag_content(
                _get_file_default("SYSTEM_PROMPT"), "core_identity"
            )

        modal = CoreIdentityModal(current_content=content)
        await interaction.response.send_modal(modal)

        try:
            await modal.wait()
            await self._refresh_panel()
        except Exception:
            pass

    async def _on_edit_behavioral(self, interaction: Interaction):
        """打开互动规范编辑模态框（编辑 behavioral_guidelines ~ style_guide 范围）。"""
        current_prompt = await _get_current_system_prompt()
        range_content = _extract_tag_range(
            current_prompt, "behavioral_guidelines", "style_guide"
        )
        if not range_content:
            range_content = _extract_tag_range(
                _get_file_default("SYSTEM_PROMPT"),
                "behavioral_guidelines",
                "style_guide",
            )

        modal = BehavioralGuidelineModal(current_range_content=range_content)
        await interaction.response.send_modal(modal)

        try:
            await modal.wait()
            await self._refresh_panel()
        except Exception:
            pass

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
        jb_override = await _get_override("JAILBREAK_USER_PROMPT")
        has_jb = jb_override is not None
        updated_presets = await _get_presets()

        # 检查加载后各部分是否与默认值不同
        default_prompt = _get_file_default("SYSTEM_PROMPT")
        core_custom = _extract_tag_content(full_prompt, "core_identity") != _extract_tag_content(default_prompt, "core_identity")
        behav_custom = _extract_tag_range(full_prompt, "behavioral_guidelines", "style_guide") != _extract_tag_range(default_prompt, "behavioral_guidelines", "style_guide")

        content = self._render_content(core_custom, behav_custom, has_jb, preset_name, len(updated_presets))
        self._rebuild_buttons(core_custom or behav_custom, has_jb, updated_presets, preset_name)
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
        core_custom, behav_custom = await self._check_customization()
        jb_override = await _get_override("JAILBREAK_USER_PROMPT")
        has_jb = jb_override is not None
        content = self._render_content(core_custom, behav_custom, has_jb, active_name, len(presets))
        self._rebuild_buttons(core_custom or behav_custom, has_jb, presets, active_name)
        await interaction.response.edit_message(content=content, view=self)

    async def _refresh_panel(self):
        """刷新面板内容。"""
        core_custom, behav_custom = await self._check_customization()
        jb_override = await _get_override("JAILBREAK_USER_PROMPT")
        has_jb = jb_override is not None
        presets = await _get_presets()
        active_name = await _get_active_preset_name()
        content = self._render_content(core_custom, behav_custom, has_jb, active_name, len(presets))
        self._rebuild_buttons(core_custom or behav_custom, has_jb, presets, active_name)
        if self.message:
            await self.message.edit(content=content, view=self)

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