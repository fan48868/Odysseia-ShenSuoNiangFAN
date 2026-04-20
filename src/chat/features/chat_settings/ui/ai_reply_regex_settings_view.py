# -*- coding: utf-8 -*-

from typing import Any, Awaitable, Callable, Dict, Optional

import discord
from discord import ButtonStyle, Interaction, SelectOption
from discord.ui import Button, Select, TextInput, View

from src.chat.features.chat_settings.services.ai_reply_regex_service import (
    AIReplyRegexService,
    RegexRule,
    RegexRuleNotFoundError,
    RegexRuleStoreError,
    ai_reply_regex_service,
)
from src.chat.features.chat_settings.ui.components import PaginatedSelect


class _OwnedView(View):
    def __init__(self, *, opener_user_id: int, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.opener_user_id = opener_user_id

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user and interaction.user.id == self.opener_user_id:
            return True
        await interaction.response.send_message(
            "❌ 只有发起者可以操作该界面。",
            ephemeral=True,
        )
        return False


class RegexRuleEditModal(discord.ui.Modal):
    """新建或编辑 AI 回复正则规则。"""

    def __init__(
        self,
        *,
        title: str,
        current_rule: Optional[RegexRule],
        on_submit_callback: Callable[[Interaction, Dict[str, Any]], Awaitable[None]],
    ):
        super().__init__(title=title)
        self.on_submit_callback = on_submit_callback

        self.name_input = TextInput(
            label="名称",
            placeholder="例如：敏感词替换",
            default=current_rule.name if current_rule else "",
            required=True,
            max_length=100,
            custom_id="regex_rule_name",
        )
        self.order_input = TextInput(
            label="顺序",
            placeholder="例如：10",
            default=str(current_rule.order) if current_rule else "10",
            required=True,
            max_length=10,
            custom_id="regex_rule_order",
        )
        self.enabled_input = TextInput(
            label="是否启用",
            placeholder="true / false / 开 / 关",
            default="true" if current_rule is None or current_rule.enabled else "false",
            required=True,
            max_length=10,
            custom_id="regex_rule_enabled",
        )
        self.pattern_input = TextInput(
            label="查找正则表达式",
            placeholder="例如：(foo)+",
            default=current_rule.pattern if current_rule else "",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=4000,
            custom_id="regex_rule_pattern",
        )
        self.replacement_input = TextInput(
            label="替换为",
            placeholder="例如：bar",
            default=current_rule.replacement if current_rule else "",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=4000,
            custom_id="regex_rule_replacement",
        )

        self.add_item(self.name_input)
        self.add_item(self.order_input)
        self.add_item(self.enabled_input)
        self.add_item(self.pattern_input)
        self.add_item(self.replacement_input)

    async def on_submit(self, interaction: Interaction):
        await self.on_submit_callback(
            interaction,
            {
                "name": (self.name_input.value or "").strip(),
                "order": (self.order_input.value or "").strip(),
                "enabled": (self.enabled_input.value or "").strip(),
                "pattern": self.pattern_input.value or "",
                "replacement": self.replacement_input.value or "",
            },
        )


class RegexRuleDeleteConfirmView(_OwnedView):
    def __init__(
        self,
        *,
        opener_user_id: int,
        manager_view: "AIReplyRegexSettingsView",
        rule: RegexRule,
    ):
        super().__init__(opener_user_id=opener_user_id, timeout=120)
        self.manager_view = manager_view
        self.rule = rule

        confirm_btn = Button(
            label="确认删除",
            style=ButtonStyle.danger,
            custom_id="confirm_delete_regex_rule",
        )
        confirm_btn.callback = self.on_confirm
        self.add_item(confirm_btn)

        cancel_btn = Button(
            label="取消",
            style=ButtonStyle.secondary,
            custom_id="cancel_delete_regex_rule",
        )
        cancel_btn.callback = self.on_cancel
        self.add_item(cancel_btn)

    async def on_confirm(self, interaction: Interaction):
        try:
            self.manager_view.regex_service.delete_rule(self.rule.rule_id)
        except RegexRuleNotFoundError as exc:
            await interaction.response.edit_message(
                content=f"❌ 删除失败：{exc}",
                view=None,
            )
            return

        self.manager_view.selected_rule_id = None
        await self.manager_view.refresh_message()
        await interaction.response.edit_message(
            content=f"✅ 已删除正则：**{self.rule.name}**",
            view=None,
        )

    async def on_cancel(self, interaction: Interaction):
        await interaction.response.edit_message(content="已取消删除。", view=None)


class AIReplyRegexSettingsView(_OwnedView):
    """管理全局 AI 回复正则规则的私有视图。"""

    def __init__(
        self,
        *,
        opener_user_id: int,
        regex_service: Optional[AIReplyRegexService] = None,
        timeout: int = 300,
    ):
        super().__init__(opener_user_id=opener_user_id, timeout=timeout)
        self.regex_service = regex_service or ai_reply_regex_service
        self.rules: list[RegexRule] = []
        self.selected_rule_id: Optional[str] = None
        self.message: Optional[discord.Message] = None
        self.rule_paginator: Optional[PaginatedSelect] = None

        self.reload()

    def reload(self) -> None:
        self.rules = self.regex_service.list_rules()
        if self.selected_rule_id and any(
            rule.rule_id == self.selected_rule_id for rule in self.rules
        ):
            pass
        else:
            self.selected_rule_id = self.rules[0].rule_id if self.rules else None
        self._create_paginator()
        self._create_view_items()

    def _create_paginator(self) -> None:
        options = []
        for rule in self.rules:
            status = "启用" if rule.enabled else "禁用"
            description = self._truncate_text(rule.pattern.replace("\n", " "), limit=90)
            options.append(
                SelectOption(
                    label=self._truncate_text(
                        f"{rule.order}. [{status}] {rule.name}", limit=100
                    ),
                    value=rule.rule_id,
                    description=description or "无描述",
                    default=rule.rule_id == self.selected_rule_id,
                )
            )

        self.rule_paginator = PaginatedSelect(
            placeholder="选择已保存的正则...",
            custom_id_prefix="regex_rule_select",
            options=options,
            on_select_callback=self.on_rule_select,
            label_prefix="正则",
        )

        if not options:
            return

        selected_index = next(
            (
                index
                for index, rule in enumerate(self.rules)
                if rule.rule_id == self.selected_rule_id
            ),
            0,
        )
        self.rule_paginator.current_page = selected_index // 25

    def _create_view_items(self) -> None:
        self.clear_items()

        if self.rule_paginator:
            self.add_item(self.rule_paginator.create_select(row=0))
            for btn in self.rule_paginator.get_buttons(row=1):
                self.add_item(btn)

        has_current_rule = self.current_rule is not None

        self.add_item(
            Button(
                label="新建正则",
                style=ButtonStyle.secondary,
                custom_id="create_regex_rule",
                row=2,
            )
        )
        self.add_item(
            Button(
                label="编辑当前",
                style=ButtonStyle.secondary,
                custom_id="edit_regex_rule",
                disabled=not has_current_rule,
                row=2,
            )
        )
        self.add_item(
            Button(
                label="删除当前",
                style=ButtonStyle.danger,
                custom_id="delete_regex_rule",
                disabled=not has_current_rule,
                row=2,
            )
        )

    @property
    def current_rule(self) -> Optional[RegexRule]:
        if not self.selected_rule_id:
            return None
        for rule in self.rules:
            if rule.rule_id == self.selected_rule_id:
                return rule
        return None

    def build_content(self) -> str:
        if not self.rules:
            return (
                "### 全局 AI 回复正则设置\n"
                "当前还没有已保存的正则规则。\n\n"
                "新建后，机器人会在发送 AI 回复前，按顺序依次应用启用中的规则。"
            )

        rule = self.current_rule
        if rule is None:
            return "### 全局 AI 回复正则设置\n当前规则列表已刷新，请重新选择。"

        status = "启用" if rule.enabled else "禁用"
        return (
            "### 全局 AI 回复正则设置\n"
            f"当前共 **{len(self.rules)}** 条规则，按顺序应用。\n\n"
            f"**当前选中**：{rule.name}\n"
            f"**顺序**：{rule.order}\n"
            f"**状态**：{status}\n"
            f"**查找**：`{self._truncate_text(rule.pattern, limit=120)}`\n"
            f"**替换为**：`{self._truncate_text(rule.replacement, limit=120)}`"
        )

    async def refresh_message(self) -> None:
        self.reload()
        if self.message is not None:
            await self.message.edit(content=self.build_content(), view=self)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False

        custom_id = interaction.data.get("custom_id") if interaction.data else None
        if (
            self.rule_paginator
            and custom_id
            and self.rule_paginator.handle_pagination(custom_id)
        ):
            self._create_view_items()
            await interaction.response.edit_message(
                content=self.build_content(),
                view=self,
            )
            return False

        if custom_id == "create_regex_rule":
            await self.on_create_rule(interaction)
            return False
        if custom_id == "edit_regex_rule":
            await self.on_edit_rule(interaction)
            return False
        if custom_id == "delete_regex_rule":
            await self.on_delete_rule(interaction)
            return False

        return True

    async def on_rule_select(self, interaction: Interaction):
        if not interaction.data or "values" not in interaction.data:
            await interaction.response.defer()
            return

        values = interaction.data["values"]
        if not values or values[0] == "disabled":
            await interaction.response.defer()
            return

        self.selected_rule_id = values[0]
        self.reload()
        await interaction.response.edit_message(content=self.build_content(), view=self)

    async def on_create_rule(self, interaction: Interaction):
        async def modal_callback(modal_interaction: Interaction, settings: Dict[str, Any]):
            try:
                created_rule = self.regex_service.create_rule_from_mapping(settings)
            except RegexRuleStoreError as exc:
                await modal_interaction.response.send_message(
                    f"❌ 保存失败：{exc}",
                    ephemeral=True,
                )
                return

            self.selected_rule_id = created_rule.rule_id
            await self.refresh_message()
            await modal_interaction.response.send_message(
                f"✅ 已创建正则：**{created_rule.name}**",
                ephemeral=True,
            )

        await interaction.response.send_modal(
            RegexRuleEditModal(
                title="新建正则",
                current_rule=None,
                on_submit_callback=modal_callback,
            )
        )

    async def on_edit_rule(self, interaction: Interaction):
        rule = self.current_rule
        if rule is None:
            await interaction.response.send_message("当前没有可编辑的正则。", ephemeral=True)
            return

        async def modal_callback(modal_interaction: Interaction, settings: Dict[str, Any]):
            try:
                updated_rule = self.regex_service.update_rule_from_mapping(
                    rule.rule_id,
                    settings,
                )
            except (RegexRuleStoreError, RegexRuleNotFoundError) as exc:
                await modal_interaction.response.send_message(
                    f"❌ 保存失败：{exc}",
                    ephemeral=True,
                )
                return

            self.selected_rule_id = updated_rule.rule_id
            await self.refresh_message()
            await modal_interaction.response.send_message(
                f"✅ 已更新正则：**{updated_rule.name}**",
                ephemeral=True,
            )

        await interaction.response.send_modal(
            RegexRuleEditModal(
                title=f"编辑正则：{rule.name}",
                current_rule=rule,
                on_submit_callback=modal_callback,
            )
        )

    async def on_delete_rule(self, interaction: Interaction):
        rule = self.current_rule
        if rule is None:
            await interaction.response.send_message("当前没有可删除的正则。", ephemeral=True)
            return

        confirm_view = RegexRuleDeleteConfirmView(
            opener_user_id=self.opener_user_id,
            manager_view=self,
            rule=rule,
        )
        await interaction.response.send_message(
            content=f"确定删除正则 **{rule.name}** 吗？此操作不可撤销。",
            view=confirm_view,
            ephemeral=True,
        )

    @staticmethod
    def _truncate_text(value: str, *, limit: int) -> str:
        normalized = str(value or "").replace("`", "'").strip()
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 3]}..."
