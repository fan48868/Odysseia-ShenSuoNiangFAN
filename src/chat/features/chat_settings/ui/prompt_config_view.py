# -*- coding: utf-8 -*-
"""
提示词配置 UI 组件

提供在聊天设置中修改默认人设和越狱方式的界面。
支持人设预设的保存、加载和删除。
所有修改存储在数据库 global_settings 中，不会修改源码文件。

人设拆分为四个编辑框：
  1. 核心人设  → <core_identity>...</core_identity>
  2. 互动规范  → <core_vows> 到 </acting_guide>（含 core_vows, community_firewall, acting_guide）
  3. 语言风格  → <style_guide>...</style_guide>
  4. 越狱方式  → JAILBREAK_USER_PROMPT / MODEL_RESPONSE / FINAL_INSTRUCTION

预设存储全部四部分内容，加载时完整恢复所有配置。
"""

import json
import logging
import re
from typing import Optional, Dict

import discord
from discord import ButtonStyle, Interaction

from src.chat.features.chat_settings.services.chat_settings_service import (
    chat_settings_service,
)
from src.chat.config.prompts import PROMPT_CONFIG
from src.chat.services.prompt_service import prompt_service

log = logging.getLogger(__name__)

# 数据库 key 前缀
_PROMPT_OVERRIDE_PREFIX = "prompt_override_default_"
_PRESET_DB_KEY = "persona_presets"  # JSON 格式存储所有预设
_ACTIVE_PRESET_KEY = "active_persona_preset_name"

# 预设数据格式:
# {
#   "core_identity": str,
#   "interaction_norms": str,
#   "style_guide": str,
#   "jailbreak_user_prompt": str,
#   "jailbreak_model_response": str,
#   "jailbreak_final_instruction": str
# }
# 兼容旧格式: 纯字符串 → 视为 core_identity，其余取默认值


# ============================================================
# 基础读写
# ============================================================


def _db_key(prompt_name: str, user_id: int) -> str:
    """生成用户专属的数据库 key：prompt_override_default_<user_id>_<prompt_name>"""
    return f"{_PROMPT_OVERRIDE_PREFIX}{user_id}_{prompt_name}"


async def _get_override(prompt_name: str, user_id: int) -> Optional[str]:
    """从数据库读取指定用户的提示词覆盖值。"""
    raw = await chat_settings_service.db_manager.get_global_setting(
        _db_key(prompt_name, user_id)
    )
    if raw is not None and raw.strip():
        return raw
    return None


async def _set_override(prompt_name: str, value: str, user_id: int) -> None:
    """将指定用户的提示词覆盖值写入数据库。"""
    try:
        await chat_settings_service.db_manager.set_global_setting(
            _db_key(prompt_name, user_id), value
        )
    except Exception as e:
        log.error(f"[prompt_config] 写入覆盖值失败 key={_db_key(prompt_name, user_id)}: {e}")
        raise


async def _clear_override(prompt_name: str, user_id: int) -> None:
    """清除数据库中指定用户的提示词覆盖值（恢复为文件默认值）。"""
    try:
        await chat_settings_service.db_manager.set_global_setting(
            _db_key(prompt_name, user_id), ""
        )
    except Exception as e:
        log.error(f"[prompt_config] 清除覆盖值失败 key={_db_key(prompt_name, user_id)}: {e}")
        raise


def _get_file_default(prompt_name: str) -> str:
    """从 prompts.py 文件中获取默认值。"""
    return (PROMPT_CONFIG.get("default", {}).get(prompt_name) or "").strip()


async def _get_current_system_prompt(user_id: int) -> str:
    """获取指定用户当前生效的 SYSTEM_PROMPT（优先 DB 覆盖，否则文件默认）。"""
    override = await _get_override("SYSTEM_PROMPT", user_id)
    return override if override is not None else _get_file_default("SYSTEM_PROMPT")


def _invalidate_prompt_cache(user_id: int) -> None:
    """使指定用户的提示词缓存失效。"""
    prompt_service._overrides_loaded.discard(user_id)
    prompt_service._prompt_overrides_cache.pop(user_id, None)


# ============================================================
# 标签提取 / 替换
# ============================================================


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
    """替换从 <start_tag> 到 </end_tag> 的完整范围（含标签）。
    使用 lambda 避免 re.sub 将 new_block 中的反斜杠解释为正则回溯引用。"""
    return re.sub(
        f"<{start_tag}>.*?</{end_tag}>",
        lambda _: new_block,
        text,
        count=1,
        flags=re.DOTALL,
    )


def _build_system_prompt_from_parts(
    core_identity: str,
    interaction_norms: str,
    style_guide: str,
) -> str:
    """用给定的 core_identity、interaction_norms、style_guide 内容重建 SYSTEM_PROMPT。
    基于文件默认值结构，替换对应标签范围为传入的新内容。"""
    default_prompt = _get_file_default("SYSTEM_PROMPT")

    # 提取文件默认值中 <character> 外部文本
    match = re.search(r"<character>(.*?)</character>", default_prompt, re.DOTALL)
    if not match:
        # 没有 character 标签，构造一个
        char_content = (
            f"<core_identity>\n{core_identity}\n</core_identity>\n\n"
            f"{interaction_norms}\n\n"
            f"<style_guide>\n{style_guide}\n</style_guide>"
        )
        return f"{default_prompt}\n<character>\n{char_content}\n</character>"

    before = default_prompt[:match.start()].rstrip()
    after = default_prompt[match.end():].lstrip()

    # 从文件默认值提取 character 内部结构，替换三个部分
    char_default = match.group(1).strip()
    char_updated = _replace_tag_content(char_default, "core_identity", core_identity)
    char_updated = _replace_tag_range(char_updated, "core_vows", "acting_guide", interaction_norms)
    char_updated = _replace_tag_content(char_updated, "style_guide", style_guide)

    parts = [before, f"<character>\n{char_updated}\n</character>"]
    if after:
        parts.append(after)
    return "\n\n".join(parts)


# ============================================================
# 预设管理
# ============================================================


async def _get_presets() -> Dict[str, dict]:
    """从数据库读取所有保存的预设。
    新格式: {"预设名": {"core_identity": str, "interaction_norms": str, "style_guide": str, ...}}
    兼容旧格式: 纯字符串 → 自动转换为新格式，只填充 core_identity
    """
    raw = await chat_settings_service.db_manager.get_global_setting(_PRESET_DB_KEY)
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}

    default_prompt = _get_file_default("SYSTEM_PROMPT")
    default_core = _extract_tag_content(default_prompt, "core_identity")
    default_norms = _extract_tag_range(default_prompt, "core_vows", "acting_guide")
    default_style = _extract_tag_content(default_prompt, "style_guide")
    default_jb_user = _get_file_default("JAILBREAK_USER_PROMPT")
    default_jb_model = _get_file_default("JAILBREAK_MODEL_RESPONSE")
    default_jb_final = _get_file_default("JAILBREAK_FINAL_INSTRUCTION")

    converted = {}
    needs_save = False
    for name, value in data.items():
        if isinstance(value, str):
            # 旧格式：仅字符串，视为 core_identity
            converted[name] = {
                "core_identity": value,
                "interaction_norms": default_norms,
                "style_guide": default_style,
                "jailbreak_user_prompt": default_jb_user,
                "jailbreak_model_response": default_jb_model,
                "jailbreak_final_instruction": default_jb_final,
            }
            needs_save = True
        elif isinstance(value, dict):
            converted[name] = {
                "core_identity": value.get("core_identity", default_core),
                "interaction_norms": value.get("interaction_norms", default_norms),
                "style_guide": value.get("style_guide", default_style),
                "jailbreak_user_prompt": value.get("jailbreak_user_prompt", default_jb_user),
                "jailbreak_model_response": value.get("jailbreak_model_response", default_jb_model),
                "jailbreak_final_instruction": value.get("jailbreak_final_instruction", default_jb_final),
            }
        else:
            continue

    if needs_save:
        await _save_presets(converted)

    return converted


async def _save_presets(presets: Dict[str, dict]) -> None:
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

    def __init__(self, current_content: str, user_id: int):
        super().__init__(timeout=300)
        self._user_id = user_id
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

        current_prompt = await _get_current_system_prompt(self._user_id)
        updated = _replace_tag_content(current_prompt, "core_identity", new_content)
        await _set_override("SYSTEM_PROMPT", updated, self._user_id)
        _invalidate_prompt_cache(self._user_id)
        await interaction.followup.send(
            "✅ **核心人设 (core_identity)** 已更新。\n"
            "下次对话时将使用新的人设。如需恢复默认，点击「恢复默认」按钮。",
            ephemeral=True,
        )


class InteractionNormModal(discord.ui.Modal, title="修改互动规范 (core_vows~acting_guide)"):
    """编辑 <core_vows> 到 </acting_guide> 范围（含 core_vows, community_firewall, acting_guide）。"""

    def __init__(self, current_range_content: str, user_id: int):
        super().__init__(timeout=300)
        self._user_id = user_id
        self.range_input = discord.ui.TextInput(
            label="core_vows ~ acting_guide 范围",
            placeholder="编辑核心誓约、社区防护墙、扮演指导...",
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

        current_prompt = await _get_current_system_prompt(self._user_id)
        updated = _replace_tag_range(
            current_prompt, "core_vows", "acting_guide", new_range
        )
        await _set_override("SYSTEM_PROMPT", updated, self._user_id)
        _invalidate_prompt_cache(self._user_id)
        await interaction.followup.send(
            "✅ **互动规范 (core_vows ~ acting_guide)** 已更新。\n"
            "下次对话时将使用新的互动规范。如需恢复默认，点击「恢复默认」按钮。",
            ephemeral=True,
        )


class StyleGuideModal(discord.ui.Modal, title="修改语言风格 (style_guide)"):
    """编辑 <style_guide>...</style_guide> 内容。"""

    def __init__(self, current_content: str, user_id: int):
        super().__init__(timeout=300)
        self._user_id = user_id
        self.content_input = discord.ui.TextInput(
            label="<style_guide> 标签内的内容",
            placeholder="编辑对话风格、格式化规则、表情使用等...",
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

        current_prompt = await _get_current_system_prompt(self._user_id)
        updated = _replace_tag_content(current_prompt, "style_guide", new_content)
        await _set_override("SYSTEM_PROMPT", updated, self._user_id)
        _invalidate_prompt_cache(self._user_id)
        await interaction.followup.send(
            "✅ **语言风格 (style_guide)** 已更新。\n"
            "下次对话时将使用新的语言风格。如需恢复默认，点击「恢复默认」按钮。",
            ephemeral=True,
        )


class PresetSaveModal(discord.ui.Modal, title="保存全部提示词为预设"):
    """输入预设名称保存当前全部提示词配置（核心人设 + 互动规范 + 语言风格 + 越狱方式）。"""

    def __init__(self, preset_data: dict):
        super().__init__(timeout=300)
        self.preset_data = preset_data
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
        presets[name] = self.preset_data
        await _save_presets(presets)
        await _set_active_preset_name(name)
        await interaction.followup.send(
            f"✅ 提示词预设 **「{name}」** 已保存！（含核心人设、互动规范、语言风格、越狱方式）\n"
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
        user_id: int,
    ):
        super().__init__(timeout=300)
        self._user_id = user_id
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
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
            default=current_model_response[:4000] if current_model_response else "",
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

        try:
            await _set_override("JAILBREAK_USER_PROMPT", user_prompt, self._user_id)
            await _set_override("JAILBREAK_MODEL_RESPONSE", model_response, self._user_id)
            await _set_override("JAILBREAK_FINAL_INSTRUCTION", final_instruction, self._user_id)
        except Exception as e:
            log.error(f"[prompt_config] 越狱方式写入失败 user_id={self._user_id}: {e}")
            await interaction.followup.send(
                f"❌ 越狱方式保存失败：{e}", ephemeral=True
            )
            return

        _invalidate_prompt_cache(self._user_id)
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
        norms_customized: bool,
        style_customized: bool,
        has_jb_override: bool,
        active_preset_name: Optional[str] = None,
        preset_count: int = 0,
    ) -> str:
        lines = [
            "📝 **提示词配置**\n",
            f"**1. 核心人设 (core_identity)**: {'🟡 已自定义' if core_customized else '🟢 默认'}",
            f"**2. 互动规范 (core_vows~acting)**: {'🟡 已自定义' if norms_customized else '🟢 默认'}",
            f"**3. 语言风格 (style_guide)**: {'🟡 已自定义' if style_customized else '🟢 默认'}",
            f"**4. 越狱方式 (JAILBREAK)**: {'🟡 已自定义' if has_jb_override else '🟢 使用文件默认值'}",
            f"**当前预设**: {'🏷️ ' + active_preset_name if active_preset_name else '无'}  |  已保存预设: {preset_count}个",
            "",
            "修改只影响对应标签内容，不影响其他部分。",
            "修改会保存到数据库，不会修改源码文件。重启后依然生效。",
            "保存预设会一并保存四部分全部内容。",
        ]
        return "\n".join(lines)

    async def _check_customization(self, user_id: int) -> tuple:
        """检查各部分是否与文件默认值不同。
        返回 (core_custom, norms_custom, style_custom)。
        """
        current = await _get_current_system_prompt(user_id)
        default = _get_file_default("SYSTEM_PROMPT")
        core_custom = _extract_tag_content(current, "core_identity") != _extract_tag_content(default, "core_identity")
        norms_custom = _extract_tag_range(current, "core_vows", "acting_guide") != _extract_tag_range(default, "core_vows", "acting_guide")
        style_custom = _extract_tag_content(current, "style_guide") != _extract_tag_content(default, "style_guide")
        return core_custom, norms_custom, style_custom

    async def _collect_current_preset_data(self) -> dict:
        """收集当前全部四部分的配置数据，用于预设保存。"""
        current_prompt = await _get_current_system_prompt(self.opener_user_id)
        default_prompt = _get_file_default("SYSTEM_PROMPT")

        core_identity = _extract_tag_content(current_prompt, "core_identity")
        if not core_identity:
            core_identity = _extract_tag_content(default_prompt, "core_identity")

        interaction_norms = _extract_tag_range(current_prompt, "core_vows", "acting_guide")
        if not interaction_norms:
            interaction_norms = _extract_tag_range(default_prompt, "core_vows", "acting_guide")

        style_guide = _extract_tag_content(current_prompt, "style_guide")
        if not style_guide:
            style_guide = _extract_tag_content(default_prompt, "style_guide")

        jb_user = await _get_override("JAILBREAK_USER_PROMPT", self.opener_user_id)
        if jb_user is None:
            jb_user = _get_file_default("JAILBREAK_USER_PROMPT")

        jb_model = await _get_override("JAILBREAK_MODEL_RESPONSE", self.opener_user_id)
        if jb_model is None:
            jb_model = _get_file_default("JAILBREAK_MODEL_RESPONSE")

        jb_final = await _get_override("JAILBREAK_FINAL_INSTRUCTION", self.opener_user_id)
        if jb_final is None:
            jb_final = _get_file_default("JAILBREAK_FINAL_INSTRUCTION")

        return {
            "core_identity": core_identity,
            "interaction_norms": interaction_norms,
            "style_guide": style_guide,
            "jailbreak_user_prompt": jb_user,
            "jailbreak_model_response": jb_model,
            "jailbreak_final_instruction": jb_final,
        }

    async def build_and_send(self, interaction: Interaction):
        """构建并发送提示词配置面板。"""
        core_custom, norms_custom, style_custom = await self._check_customization(self.opener_user_id)
        jb_override = await _get_override("JAILBREAK_USER_PROMPT", self.opener_user_id)
        has_jb = jb_override is not None
        presets = await _get_presets()
        active_name = await _get_active_preset_name()

        has_any_system = core_custom or norms_custom or style_custom
        content = self._render_content(core_custom, norms_custom, style_custom, has_jb, active_name, len(presets))
        self._rebuild_buttons(has_any_system, has_jb, presets, active_name)
        await interaction.response.edit_message(content=content, view=self)

    def _rebuild_buttons(
        self,
        has_system_override: bool,
        has_jb_override: bool,
        presets: Optional[Dict[str, dict]] = None,
        active_preset_name: Optional[str] = None,
    ):
        self.clear_items()
        presets = presets or {}

        # Row 0: 核心人设 + 互动规范
        btn_core = discord.ui.Button(
            label="修改核心人设",
            style=ButtonStyle.primary,
            emoji="🎭",
            row=0,
        )
        btn_core.callback = self._on_edit_core_identity
        self.add_item(btn_core)

        btn_norms = discord.ui.Button(
            label="修改互动规范",
            style=ButtonStyle.primary,
            emoji="📋",
            row=0,
        )
        btn_norms.callback = self._on_edit_interaction_norms
        self.add_item(btn_norms)

        # Row 1: 语言风格 + 越狱方式
        btn_style = discord.ui.Button(
            label="修改语言风格",
            style=ButtonStyle.primary,
            emoji="💬",
            row=1,
        )
        btn_style.callback = self._on_edit_style_guide
        self.add_item(btn_style)

        btn_jb = discord.ui.Button(
            label="修改越狱方式",
            style=ButtonStyle.primary,
            emoji="🔓",
            row=1,
        )
        btn_jb.callback = self._on_edit_jailbreak
        self.add_item(btn_jb)

        # Row 2: 保存预设 + 恢复全部默认（合并到一行节省空间）
        save_btn = discord.ui.Button(
            label="保存当前为预设",
            style=ButtonStyle.success,
            emoji="💾",
            row=2,
        )
        save_btn.callback = self._on_save_preset
        self.add_item(save_btn)

        if has_system_override or has_jb_override:
            reset_btn = discord.ui.Button(
                label="恢复全部默认",
                style=ButtonStyle.danger,
                emoji="🔄",
                row=2,
            )
            reset_btn.callback = self._on_reset_all
            self.add_item(reset_btn)

        # Row 3: 预设选择下拉菜单
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
                row=3,
            )
            preset_select.callback = self._on_load_preset
            self.add_item(preset_select)

            # Row 4: 删除预设下拉菜单
            delete_options = [
                discord.SelectOption(label=f"🗑️ {name}", value=name)
                for name in list(presets.keys())[:25]
            ]
            delete_select = discord.ui.Select(
                placeholder="删除预设...",
                options=delete_options,
                custom_id="preset_delete_select",
                row=4,
            )
            delete_select.callback = self._on_delete_preset
            self.add_item(delete_select)

    # --- 编辑回调 ---

    async def _on_edit_core_identity(self, interaction: Interaction):
        """打开核心人设编辑模态框（只编辑 <core_identity> 内容）。"""
        current_prompt = await _get_current_system_prompt(self.opener_user_id)
        content = _extract_tag_content(current_prompt, "core_identity")
        if not content:
            content = _extract_tag_content(
                _get_file_default("SYSTEM_PROMPT"), "core_identity"
            )

        modal = CoreIdentityModal(current_content=content, user_id=self.opener_user_id)
        await interaction.response.send_modal(modal)

        try:
            await modal.wait()
            await self._refresh_panel()
        except Exception:
            pass

    async def _on_edit_interaction_norms(self, interaction: Interaction):
        """打开互动规范编辑模态框（编辑 core_vows ~ acting_guide 范围）。"""
        current_prompt = await _get_current_system_prompt(self.opener_user_id)
        range_content = _extract_tag_range(
            current_prompt, "core_vows", "acting_guide"
        )
        if not range_content:
            range_content = _extract_tag_range(
                _get_file_default("SYSTEM_PROMPT"),
                "core_vows",
                "acting_guide",
            )

        modal = InteractionNormModal(current_range_content=range_content, user_id=self.opener_user_id)
        await interaction.response.send_modal(modal)

        try:
            await modal.wait()
            await self._refresh_panel()
        except Exception:
            pass

    async def _on_edit_style_guide(self, interaction: Interaction):
        """打开语言风格编辑模态框（编辑 <style_guide> 内容）。"""
        current_prompt = await _get_current_system_prompt(self.opener_user_id)
        content = _extract_tag_content(current_prompt, "style_guide")
        if not content:
            content = _extract_tag_content(
                _get_file_default("SYSTEM_PROMPT"), "style_guide"
            )

        modal = StyleGuideModal(current_content=content, user_id=self.opener_user_id)
        await interaction.response.send_modal(modal)

        try:
            await modal.wait()
            await self._refresh_panel()
        except Exception:
            pass

    async def _on_edit_jailbreak(self, interaction: Interaction):
        """打开修改越狱方式的模态框。"""
        current_user = await _get_override("JAILBREAK_USER_PROMPT", self.opener_user_id)
        if current_user is None:
            current_user = _get_file_default("JAILBREAK_USER_PROMPT")

        current_model = await _get_override("JAILBREAK_MODEL_RESPONSE", self.opener_user_id)
        if current_model is None:
            current_model = _get_file_default("JAILBREAK_MODEL_RESPONSE")

        current_final = await _get_override("JAILBREAK_FINAL_INSTRUCTION", self.opener_user_id)
        if current_final is None:
            current_final = _get_file_default("JAILBREAK_FINAL_INSTRUCTION")

        modal = JailbreakModal(
            current_user_prompt=current_user,
            current_model_response=current_model,
            current_final_instruction=current_final,
            user_id=self.opener_user_id,
        )
        await interaction.response.send_modal(modal)

        try:
            await modal.wait()
            await self._refresh_panel()
        except Exception:
            pass

    async def _on_save_preset(self, interaction: Interaction):
        """保存当前全部提示词配置为预设（核心人设 + 互动规范 + 语言风格 + 越狱方式）。"""
        preset_data = await self._collect_current_preset_data()

        modal = PresetSaveModal(preset_data=preset_data)
        await interaction.response.send_modal(modal)

        try:
            await modal.wait()
            await self._refresh_panel()
        except Exception:
            pass

    async def _on_load_preset(self, interaction: Interaction):
        """加载选中的预设，应用全部四部分配置。"""
        if not interaction.data or "values" not in interaction.data:
            await interaction.response.defer()
            return

        preset_name = interaction.data["values"][0]
        presets = await _get_presets()
        preset_data = presets.get(preset_name)
        if not preset_data:
            await interaction.response.send_message(
                f"❌ 预设「{preset_name}」不存在。", ephemeral=True
            )
            return

        core_identity = preset_data.get("core_identity", "")
        interaction_norms = preset_data.get("interaction_norms", "")
        style_guide = preset_data.get("style_guide", "")
        jb_user = preset_data.get("jailbreak_user_prompt", "")
        jb_model = preset_data.get("jailbreak_model_response", "")
        jb_final = preset_data.get("jailbreak_final_instruction", "")

        if not core_identity:
            await interaction.response.send_message(
                f"❌ 预设「{preset_name}」核心人设内容为空。", ephemeral=True
            )
            return

        # 重建 SYSTEM_PROMPT 并保存
        full_prompt = _build_system_prompt_from_parts(core_identity, interaction_norms, style_guide)
        await _set_override("SYSTEM_PROMPT", full_prompt, self.opener_user_id)

        # 应用越狱方式
        if jb_user:
            await _set_override("JAILBREAK_USER_PROMPT", jb_user, self.opener_user_id)
        if jb_model:
            await _set_override("JAILBREAK_MODEL_RESPONSE", jb_model, self.opener_user_id)
        if jb_final:
            await _set_override("JAILBREAK_FINAL_INSTRUCTION", jb_final, self.opener_user_id)

        await _set_active_preset_name(preset_name)
        _invalidate_prompt_cache(self.opener_user_id)

        # 刷新面板
        jb_override = await _get_override("JAILBREAK_USER_PROMPT", self.opener_user_id)
        has_jb = jb_override is not None
        updated_presets = await _get_presets()

        default_prompt = _get_file_default("SYSTEM_PROMPT")
        core_custom = _extract_tag_content(full_prompt, "core_identity") != _extract_tag_content(default_prompt, "core_identity")
        norms_custom = _extract_tag_range(full_prompt, "core_vows", "acting_guide") != _extract_tag_range(default_prompt, "core_vows", "acting_guide")
        style_custom = _extract_tag_content(full_prompt, "style_guide") != _extract_tag_content(default_prompt, "style_guide")
        has_any_system = core_custom or norms_custom or style_custom

        content = self._render_content(core_custom, norms_custom, style_custom, has_jb, preset_name, len(updated_presets))
        self._rebuild_buttons(has_any_system, has_jb, updated_presets, preset_name)
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
        core_custom, norms_custom, style_custom = await self._check_customization(self.opener_user_id)
        jb_override = await _get_override("JAILBREAK_USER_PROMPT", self.opener_user_id)
        has_jb = jb_override is not None
        has_any_system = core_custom or norms_custom or style_custom
        content = self._render_content(core_custom, norms_custom, style_custom, has_jb, active_name, len(presets))
        self._rebuild_buttons(has_any_system, has_jb, presets, active_name)
        await interaction.response.edit_message(content=content, view=self)

    async def _refresh_panel(self):
        """刷新面板内容。"""
        core_custom, norms_custom, style_custom = await self._check_customization(self.opener_user_id)
        jb_override = await _get_override("JAILBREAK_USER_PROMPT", self.opener_user_id)
        has_jb = jb_override is not None
        presets = await _get_presets()
        active_name = await _get_active_preset_name()
        has_any_system = core_custom or norms_custom or style_custom
        content = self._render_content(core_custom, norms_custom, style_custom, has_jb, active_name, len(presets))
        self._rebuild_buttons(has_any_system, has_jb, presets, active_name)
        if self.message:
            await self.message.edit(content=content, view=self)

    async def _on_reset_all(self, interaction: Interaction):
        """恢复全部默认。"""
        await interaction.response.defer(ephemeral=True)
        await _clear_override("SYSTEM_PROMPT", self.opener_user_id)
        await _clear_override("JAILBREAK_USER_PROMPT", self.opener_user_id)
        await _clear_override("JAILBREAK_MODEL_RESPONSE", self.opener_user_id)
        await _clear_override("JAILBREAK_FINAL_INSTRUCTION", self.opener_user_id)
        await _set_active_preset_name(None)
        _invalidate_prompt_cache(self.opener_user_id)

        await self._refresh_panel()
        await interaction.followup.send("✅ 已恢复全部默认提示词。", ephemeral=True)

    async def _on_reset_system(self, interaction: Interaction):
        """恢复人设默认（核心人设 + 互动规范 + 语言风格）。"""
        await interaction.response.defer(ephemeral=True)
        await _clear_override("SYSTEM_PROMPT", self.opener_user_id)
        await _set_active_preset_name(None)
        _invalidate_prompt_cache(self.opener_user_id)

        await self._refresh_panel()
        await interaction.followup.send("✅ 已恢复默认人设。", ephemeral=True)

    async def _on_reset_jailbreak(self, interaction: Interaction):
        """恢复越狱默认。"""
        await interaction.response.defer(ephemeral=True)
        await _clear_override("JAILBREAK_USER_PROMPT", self.opener_user_id)
        await _clear_override("JAILBREAK_MODEL_RESPONSE", self.opener_user_id)
        await _clear_override("JAILBREAK_FINAL_INSTRUCTION", self.opener_user_id)
        _invalidate_prompt_cache(self.opener_user_id)

        await self._refresh_panel()
        await interaction.followup.send("✅ 已恢复默认越狱方式。", ephemeral=True)