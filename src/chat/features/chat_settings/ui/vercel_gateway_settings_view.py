# -*- coding: utf-8 -*-

import asyncio
from typing import Any, Dict, List, Optional, Set

import discord
from discord import ButtonStyle, Interaction, SelectOption
from discord.ui import Button, Select, View

from src.chat.features.chat_settings.services.chat_settings_service import (
    chat_settings_service,
)
from src.chat.services.vercel_gateway_provider_selector import (
    VercelGatewayProviderSelectorService,
)


class VercelGatewayProvidersView(View):
    """供应商设置面板：模型选择、状态展示、禁用管理与手动测速。"""

    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.selected_model: str = ""
        self.selector: Optional[VercelGatewayProviderSelectorService] = None
        self.statuses: List[Dict[str, Any]] = []
        self.disabled_providers: Set[str] = set()
        self.selected_test_providers: Set[str] = set()
        self.show_manual_test_controls = False
        self.message: Optional[discord.Message] = None
        self._manual_test_poll_task: Optional[asyncio.Task] = None

        self.model_select = self._build_model_select()
        self.provider_select = Select(
            placeholder="选择要启用的供应商（取消勾选即禁用）",
            options=[SelectOption(label="暂无供应商数据", value="placeholder")],
            custom_id="provider_select",
            min_values=0,
            max_values=1,
            disabled=True,
            row=1,
        )
        self.provider_select.callback = self.on_provider_select

        self.manual_test_provider_select = Select(
            placeholder="选择要测速的供应商",
            options=[SelectOption(label="暂无可测试供应商", value="placeholder")],
            custom_id="manual_test_provider_select",
            min_values=0,
            max_values=1,
            disabled=True,
            row=2,
        )
        self.manual_test_provider_select.callback = self.on_manual_test_provider_select

        self.back_button = Button(
            label="返回",
            style=ButtonStyle.secondary,
            custom_id="back_to_models",
            row=0,
        )
        self.back_button.callback = self.on_back

        self.refresh_button = Button(
            label="刷新状态",
            style=ButtonStyle.secondary,
            custom_id="refresh_status",
            row=0,
        )
        self.refresh_button.callback = self.on_refresh

        self.manual_test_button = Button(
            label="手动测速",
            style=ButtonStyle.primary,
            custom_id="toggle_manual_test",
            row=0,
        )
        self.manual_test_button.callback = self.on_manual_test_toggle

        self.manual_test_start_button = Button(
            label="开始测速",
            style=ButtonStyle.success,
            custom_id="start_manual_test",
            row=3,
        )
        self.manual_test_start_button.callback = self.on_manual_test_start

        self._sync_components()

    def _build_model_select(self) -> Select:
        models = VercelGatewayProviderSelectorService.get_available_models()
        if not models:
            model_select = Select(
                placeholder="没有可用的模型，请检查配置",
                options=[SelectOption(label="无", value="none", default=True)],
                custom_id="model_select",
                disabled=True,
                row=0,
            )
        else:
            model_select = Select(
                placeholder="选择要管理的模型",
                options=[
                    SelectOption(label=model_name, value=model_name)
                    for model_name in models
                ],
                custom_id="model_select",
                row=0,
            )
        model_select.callback = self.on_model_select
        return model_select

    async def on_model_select(self, interaction: Interaction):
        if not self.model_select.values:
            await interaction.response.defer()
            return

        self.message = interaction.message
        self.selected_model = self.model_select.values[0]
        self.selector = await VercelGatewayProviderSelectorService.get_or_create_instance(
            self.selected_model
        )
        self.disabled_providers = (
            await VercelGatewayProviderSelectorService.load_disabled_providers(
                self.selected_model
            )
        )
        await self.selector.update_disabled_providers(self.disabled_providers)
        self.selected_test_providers.clear()
        self.show_manual_test_controls = False
        await self._refresh_statuses()
        await self._render(interaction=interaction)
        await self._ensure_manual_test_polling()

    async def on_provider_select(self, interaction: Interaction):
        self.message = interaction.message
        selected_providers = (
            set(self.provider_select.values) if self.provider_select.values else set()
        )
        all_providers = {status["provider_name"] for status in self.statuses}
        new_disabled = all_providers - selected_providers

        self.disabled_providers = new_disabled
        key = VercelGatewayProviderSelectorService.get_disabled_providers_setting_key(
            self.selected_model
        )
        import json

        await chat_settings_service.db_manager.set_global_setting(
            key, json.dumps(sorted(new_disabled))
        )

        if self.selector is not None:
            await self.selector.update_disabled_providers(new_disabled)

        await self._refresh_statuses()
        await self._render(interaction=interaction)
        await interaction.followup.send("✅ 已保存供应商启用/禁用设置", ephemeral=True)

    async def on_refresh(self, interaction: Interaction):
        self.message = interaction.message
        if self.selector is None:
            await interaction.response.send_message("选择器未激活，无法刷新。", ephemeral=True)
            return
        await self._refresh_statuses()
        await self._render(interaction=interaction)

    async def on_back(self, interaction: Interaction):
        self.message = interaction.message
        await self._stop_manual_test_polling()
        if self.show_manual_test_controls:
            self.selected_test_providers = set()
            self.show_manual_test_controls = False
        else:
            self._reset_to_model_selection()
        await self._render(interaction=interaction)

    async def on_manual_test_toggle(self, interaction: Interaction):
        self.message = interaction.message
        if self.selector is None:
            await interaction.response.send_message("请先选择一个模型。", ephemeral=True)
            return

        self.show_manual_test_controls = True
        await self._refresh_statuses()
        await self._render(interaction=interaction)

    async def on_manual_test_provider_select(self, interaction: Interaction):
        self.message = interaction.message
        self.selected_test_providers = set(self.manual_test_provider_select.values or [])
        await self._render(interaction=interaction)

    async def on_manual_test_start(self, interaction: Interaction):
        self.message = interaction.message
        if self.selector is None:
            await interaction.response.send_message("请选择模型后再开始测速。", ephemeral=True)
            return

        selected_providers = list(self.selected_test_providers)
        if not selected_providers:
            await interaction.response.send_message("请先选择至少一个供应商。", ephemeral=True)
            return

        try:
            await self.selector.start_manual_test(selected_providers)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.show_manual_test_controls = True
        await self._refresh_statuses()
        await self._render(interaction=interaction)
        await self._ensure_manual_test_polling()

    async def _refresh_statuses(self):
        if self.selector is None:
            self.statuses = []
            return
        self.statuses = await self.selector.get_provider_statuses()

    def _reset_to_model_selection(self) -> None:
        self.selected_model = ""
        self.selector = None
        self.statuses = []
        self.disabled_providers = set()
        self.selected_test_providers = set()
        self.show_manual_test_controls = False

    def _sync_components(self) -> None:
        self.clear_items()
        if not self.selected_model:
            self.add_item(self.model_select)
            return

        self._update_provider_select_options()
        self._update_manual_test_select_options()

        self.add_item(self.back_button)
        self.add_item(self.refresh_button)
        self.add_item(self.manual_test_button)
        if not self.show_manual_test_controls:
            self.add_item(self.provider_select)

        if self.show_manual_test_controls:
            self.add_item(self.manual_test_provider_select)
            self.add_item(self.manual_test_start_button)

    def _update_provider_select_options(self) -> None:
        if not self.statuses:
            self.provider_select.options = [
                SelectOption(label="暂无供应商数据", value="placeholder")
            ]
            self.provider_select.placeholder = "暂无供应商数据"
            self.provider_select.disabled = True
            self.provider_select.max_values = 1
            return

        options = []
        for status in self.statuses:
            provider_name = status["provider_name"]
            options.append(
                SelectOption(
                    label=provider_name,
                    value=provider_name,
                    default=provider_name not in self.disabled_providers,
                )
            )

        self.provider_select.options = options
        self.provider_select.placeholder = "选择要启用的供应商（取消勾选即禁用）"
        self.provider_select.disabled = False
        self.provider_select.max_values = len(options)

    def _update_manual_test_select_options(self) -> None:
        provider_names = [status["provider_name"] for status in self.statuses]
        if not provider_names:
            self.manual_test_provider_select.options = [
                SelectOption(label="暂无可测试供应商", value="placeholder")
            ]
            self.manual_test_provider_select.placeholder = "暂无可测试供应商"
            self.manual_test_provider_select.disabled = True
            self.manual_test_provider_select.max_values = 1
            self.manual_test_start_button.disabled = True
            return

        self.manual_test_provider_select.options = [
            SelectOption(
                label=provider_name,
                value=provider_name,
                default=provider_name in self.selected_test_providers,
            )
            for provider_name in provider_names
        ]
        self.manual_test_provider_select.placeholder = "选择要测速的供应商（支持多选）"
        self.manual_test_provider_select.disabled = False
        self.manual_test_provider_select.max_values = len(provider_names)

    async def _render(self, interaction: Optional[Interaction] = None) -> None:
        manual_status = await self._get_manual_status()
        self._sync_components()
        content = self._build_content(manual_status)

        if not self.selected_model:
            if interaction is not None:
                await interaction.response.edit_message(content=content, embed=None, view=self)
            elif self.message is not None:
                await self.message.edit(content=content, embed=None, view=self)
            return

        embed = self._build_embed(manual_status)
        self.manual_test_button.disabled = manual_status["status"] == "running"
        self.manual_test_start_button.disabled = (
            manual_status["status"] == "running"
            or not self.selected_test_providers
            or self.manual_test_provider_select.disabled
        )
        self._sync_components()

        if interaction is not None:
            await interaction.response.edit_message(content=content, embed=embed, view=self)
        elif self.message is not None:
            await self.message.edit(content=content, embed=embed, view=self)

    def _build_content(self, manual_status: Dict[str, Any]) -> str:
        if not self.selected_model:
            return "⚙️ Vercel Gateway 供应商设置"
        if self.show_manual_test_controls and manual_status["status"] == "running":
            return "⏱️ 手动测速进行中，请在聊天中进行调用"
        if self.show_manual_test_controls and manual_status["status"] == "completed":
            return "✅ 手动测速完成"
        return "⚙️ Vercel Gateway 供应商设置"

    def _build_embed(self, manual_status: Dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(
            title="🔌 Vercel Gateway 供应商状态",
            description=f"模型：**{self.selected_model}**",
            color=discord.Color.blue(),
        )
        if not self.statuses:
            embed.add_field(
                name="暂无数据",
                value="尚未收到任何测速数据，请稍后刷新。",
                inline=False,
            )
        else:
            for status in self.statuses:
                provider_name = status["provider_name"]
                enabled = provider_name not in self.disabled_providers
                status_icon = "✅" if enabled else "❌"
                cooling = status["cooling_seconds"]
                cooling_str = f"{cooling:.0f}秒" if cooling > 0 else "无"
                value = (
                    f"综合分: `{status['effective_score']:.6f}`\n"
                    f"每字耗时: `{status['unit_cost']:.6f}`\n"
                    f"惩罚: `{status['failure_penalty']:.2f}` | 样本: {status['sample_count']}\n"
                    f"冷却: {cooling_str}"
                )
                embed.add_field(
                    name=f"{status_icon} {provider_name}",
                    value=value,
                    inline=True,
                )

        manual_summary = self._build_manual_test_summary(manual_status)
        if manual_summary:
            embed.add_field(name="🧪 手动测速", value=manual_summary, inline=False)
        return embed

    def _build_manual_test_summary(self, manual_status: Dict[str, Any]) -> str:
        if not self.show_manual_test_controls:
            return ""

        status = manual_status["status"]
        if status != "running":
            return ""

        lines: List[str] = []
        pending_providers = list(manual_status["queued_providers"])
        pending_providers.extend(manual_status["pending_request_bindings"].values())
        remaining_text = "、".join(pending_providers) if pending_providers else "无"
        lines.append("状态: 进行中")
        lines.append(f"剩余供应商: {remaining_text}")

        return "\n".join(lines)

    async def _get_manual_status(self) -> Dict[str, Any]:
        if self.selector is None:
            return {
                "run_id": None,
                "status": "idle",
                "selected_providers": [],
                "queued_providers": [],
                "pending_request_bindings": {},
                "results": [],
            }
        return await self.selector.get_manual_test_status()

    async def _ensure_manual_test_polling(self) -> None:
        if self.selector is None:
            return
        manual_status = await self.selector.get_manual_test_status()
        if manual_status["status"] != "running":
            return
        if self._manual_test_poll_task and not self._manual_test_poll_task.done():
            return
        self._manual_test_poll_task = asyncio.create_task(self._manual_test_poll_loop())

    async def _manual_test_poll_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(2)
                if self.selector is None or self.message is None or not self.selected_model:
                    return

                await self._refresh_statuses()
                manual_status = await self.selector.get_manual_test_status()
                await self._render()
                if manual_status["status"] != "running":
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _stop_manual_test_polling(self) -> None:
        if self._manual_test_poll_task is None:
            return
        self._manual_test_poll_task.cancel()
        try:
            await self._manual_test_poll_task
        except asyncio.CancelledError:
            pass
        self._manual_test_poll_task = None

    async def on_timeout(self) -> None:
        await self._stop_manual_test_polling()
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
