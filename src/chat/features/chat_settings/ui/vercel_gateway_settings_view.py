# -*- coding: utf-8 -*-

import discord
from discord.ui import View, Select, Button
from discord import ButtonStyle, SelectOption, Interaction
from typing import List, Dict, Any, Set

from src.chat.services.vercel_gateway_provider_selector import VercelGatewayProviderSelectorService
from src.chat.features.chat_settings.services.chat_settings_service import chat_settings_service

_DISABLED_PROVIDERS_KEY_TEMPLATE = "vercel_gateway_disabled_providers:{}"


class VercelGatewayProvidersView(View):
    """供应商设置面板：模型选择、状态展示、禁用管理。"""

    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.selected_model: str = ""
        self.selector: VercelGatewayProviderSelectorService | None = None
        self.statuses: List[Dict[str, Any]] = []
        self.disabled_providers: Set[str] = set()

        # 构建模型选择下拉框
        models = VercelGatewayProviderSelectorService.get_available_models()
        if not models:
            self.model_select = Select(
                placeholder="没有可用的模型，请检查配置",
                options=[SelectOption(label="无", value="none", default=True)],
                custom_id="model_select",
                disabled=True,
            )
        else:
            options = [
                SelectOption(label=model, value=model, default=False)
                for model in models
            ]
            self.model_select = Select(
                placeholder="选择要管理的模型",
                options=options,
                custom_id="model_select",
            )
        self.model_select.callback = self.on_model_select
        self.add_item(self.model_select)

        # 供应商相关组件（初始不添加，等模型激活后再动态添加）
        self.provider_select: Select | None = None
        self.refresh_button: Button | None = None

        self.message: discord.Message | None = None

    async def on_model_select(self, interaction: Interaction):
        """用户选择模型后，加载该模型的供应商状态和禁用列表。"""
        if not self.model_select.values:
            await interaction.response.defer()
            return
        self.selected_model = self.model_select.values[0]

        # 获取选择器实例（必须存在，否则提示先调用模型）
        self.selector = await VercelGatewayProviderSelectorService.get_instance(self.selected_model)
        if self.selector is None:
            # 模型未激活，提示用户先使用模型
            await interaction.response.edit_message(
                content=f"⚠️ 模型 `{self.selected_model}` 尚未激活。\n请先在聊天中使用该模型发送一条消息，然后再回来配置供应商。",
                view=self,
            )
            # 确保供应商相关组件未创建（如果之前意外创建了，删除它们）
            if self.provider_select is not None:
                self.remove_item(self.provider_select)
                self.provider_select = None
            if self.refresh_button is not None:
                self.remove_item(self.refresh_button)
                self.refresh_button = None
            return

        # 动态创建供应商多选下拉框和刷新按钮（如果尚未创建）
        if self.provider_select is None:
            self.provider_select = Select(
                placeholder="正在加载供应商数据...",
                options=[SelectOption(label="加载中", value="loading", default=False)],
                custom_id="provider_select",
                min_values=0,
                max_values=1,
                disabled=True,
            )
            self.provider_select.callback = self.on_provider_select
            self.add_item(self.provider_select)

        if self.refresh_button is None:
            self.refresh_button = Button(
                label="刷新状态",
                style=ButtonStyle.secondary,
                custom_id="refresh_status",
                disabled=True,
            )
            self.refresh_button.callback = self.on_refresh
            self.add_item(self.refresh_button)

        # 加载当前全局禁用列表（从数据库）
        key = _DISABLED_PROVIDERS_KEY_TEMPLATE.format(self.selected_model)
        raw = await chat_settings_service.db_manager.get_global_setting(key)
        if raw:
            try:
                import json
                self.disabled_providers = set(json.loads(raw))
            except:
                self.disabled_providers = set()
        else:
            self.disabled_providers = set()

        # 同步禁用列表到 selector
        await self.selector.update_disabled_providers(self.disabled_providers)

        # 获取供应商状态
        await self._refresh_statuses()

        # 移除模型选择下拉框（隐藏）
        self.remove_item(self.model_select)

        # 更新界面（此时 provider_select 和 refresh_button 会显示并启用）
        await self._update_ui(interaction, edit=True)

    async def _refresh_statuses(self):
        """从 selector 获取最新供应商状态（仅在 selector 存在时有效）。"""
        if self.selector is None:
            # 如果没有 selector，保持现有 statuses 不变（已由 on_model_select 构建）
            return
        self.statuses = await self.selector.get_provider_statuses()

    async def _update_ui(self, interaction: Interaction, edit: bool = True):
        """根据当前状态更新 embed 和多选下拉框。"""
        if not self.selector:
            # 没有选择器，说明还未激活
            content = "请先选择一个模型。"
            if edit:
                await interaction.response.edit_message(content=content, view=self)
            else:
                await interaction.followup.send(content, ephemeral=True)
            return

        # 确保供应商组件已创建（防御性代码，理论上已在 on_model_select 中创建）
        if self.provider_select is None or self.refresh_button is None:
            # 如果组件不存在，尝试创建（但这种情况不应发生）
            if self.provider_select is None:
                self.provider_select = Select(
                    placeholder="正在加载供应商数据...",
                    options=[SelectOption(label="加载中", value="loading", default=False)],
                    custom_id="provider_select",
                    min_values=0,
                    max_values=1,
                    disabled=True,
                )
                self.provider_select.callback = self.on_provider_select
                self.add_item(self.provider_select)
            if self.refresh_button is None:
                self.refresh_button = Button(
                    label="刷新状态",
                    style=ButtonStyle.secondary,
                    custom_id="refresh_status",
                    disabled=True,
                )
                self.refresh_button.callback = self.on_refresh
                self.add_item(self.refresh_button)

        # 构建 embed 展示供应商详情
        embed = discord.Embed(
            title=f"🔌 Vercel Gateway 供应商状态",
            description=f"模型：**{self.selected_model}**",
            color=discord.Color.blue(),
        )
        if not self.statuses:
            embed.add_field(name="暂无数据", value="尚未收到任何测速数据，请稍后刷新。", inline=False)
        else:
            for status in self.statuses:
                provider = status["provider_name"]
                enabled = provider not in self.disabled_providers  # 根据禁用列表实时计算启用状态
                score = status["effective_score"]
                unit_cost = status["unit_cost"]
                penalty = status["failure_penalty"]
                samples = status["sample_count"]
                cooling = status["cooling_seconds"]
                status_icon = "✅" if enabled else "❌"
                cooling_str = f"{cooling:.0f}秒" if cooling > 0 else "无"
                value = (
                    f"综合分: `{score:.6f}`\n"
                    f"每字耗时: `{unit_cost:.6f}`\n"
                    f"惩罚: `{penalty:.2f}` | 样本: {samples}\n"
                    f"冷却: {cooling_str}"
                )
                embed.add_field(name=f"{status_icon} {provider}", value=value, inline=True)

        # 更新多选下拉框的选项（默认全选，取消勾选表示禁用）
        if not self.statuses:
            # 无数据时，显示占位选项并禁用
            self.provider_select.options = [SelectOption(label="暂无供应商数据", value="placeholder", default=False)]
            self.provider_select.placeholder = "暂无供应商数据"
            self.provider_select.disabled = True
            self.provider_select.max_values = 1
        else:
            options = []
            for status in self.statuses:
                label = status["provider_name"]
                # 默认全选：即不在禁用列表中的供应商默认选中
                default = label not in self.disabled_providers
                options.append(SelectOption(label=label, value=label, default=default))
            self.provider_select.options = options
            self.provider_select.placeholder = "选择要启用的供应商（取消勾选即禁用）"
            self.provider_select.disabled = False
            # max_values 设为选项数量，允许全选/全不选
            self.provider_select.max_values = len(options)
        self.refresh_button.disabled = False

        if edit:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)

    async def on_provider_select(self, interaction: Interaction):
        """用户在多选下拉框中修改了选择，立即保存并更新禁用列表。"""
        # 获取当前选中的供应商（即用户想要启用的供应商）
        selected_providers = set(self.provider_select.values) if self.provider_select.values else set()
        # 所有供应商名称
        all_providers = {status["provider_name"] for status in self.statuses}
        # 禁用列表 = 所有供应商 - 选中的供应商
        new_disabled = all_providers - selected_providers

        # 更新内存中的禁用列表
        self.disabled_providers = new_disabled

        # 保存到数据库
        key = _DISABLED_PROVIDERS_KEY_TEMPLATE.format(self.selected_model)
        import json
        value = json.dumps(list(new_disabled))
        await chat_settings_service.db_manager.set_global_setting(key, value)

        # 如果 selector 存在，同步禁用列表
        if self.selector is not None:
            await self.selector.update_disabled_providers(new_disabled)

        # 刷新界面（更新 embed 中的启用状态）
        await self._update_ui(interaction, edit=True)
        await interaction.followup.send("✅ 已保存供应商启用/禁用设置", ephemeral=True)

    async def on_refresh(self, interaction: Interaction):
        """刷新供应商状态（从 selector 重新获取）。"""
        if self.selector is None:
            await interaction.response.send_message("选择器未激活，无法刷新。", ephemeral=True)
            return
        await self._refresh_statuses()
        await self._update_ui(interaction, edit=True)

