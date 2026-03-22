import discord
from discord import Interaction
from typing import Optional, Callable, Awaitable, Dict, Any


def _parse_optional_boolean_text(value: str) -> Optional[bool]:
    normalized = (value or "").strip().lower()
    if normalized in ["true", "1", "yes"]:
        return True
    if normalized in ["false", "0", "no"]:
        return False
    if normalized == "":
        return None
    raise ValueError("无效的布尔值")


class ChatSettingsModal(discord.ui.Modal):
    """一个通用的模态框，用于设置聊天CD，支持回调。"""

    def __init__(
        self,
        *,
        title: str,
        current_config: Dict[str, Any],
        on_submit_callback: Callable[[Interaction, Dict[str, Any]], Awaitable[None]],
        include_enable_option: bool = True,
        entity_name: Optional[str] = None,
        include_active_chat_cache_option: bool = False,
    ):
        super().__init__(title=title)
        self.on_submit_callback = on_submit_callback

        # --- 解析当前配置 ---
        cooldown_sec_str = str(v) if (v := current_config.get("cooldown_seconds")) is not None else ""
        duration_str = str(v) if (v := current_config.get("cooldown_duration")) is not None else ""
        limit_str = str(v) if (v := current_config.get("cooldown_limit")) is not None else ""
        
        # --- 输入字段定义 ---
        if include_enable_option and entity_name:
            enabled_str = str(v).lower() if (v := current_config.get("is_chat_enabled")) is not None else ""
            self.enabled_input = discord.ui.TextInput(
                label=f"[{entity_name}] 是否开启聊天",
                placeholder="true / false / 留空以继承",
                default=enabled_str,
                required=False,
                row=0
            )
            self.add_item(self.enabled_input)
        else:
            self.enabled_input = None

        if include_active_chat_cache_option:
            active_cache_enabled_str = (
                str(v).lower()
                if (v := current_config.get("active_chat_cache_enabled")) is not None
                else ""
            )
            self.active_chat_cache_enabled_input = discord.ui.TextInput(
                label="主动聊天缓存",
                placeholder="true / false / 留空",
                default=active_cache_enabled_str,
                required=False,
                row=1,
            )
            self.add_item(self.active_chat_cache_enabled_input)
        else:
            self.active_chat_cache_enabled_input = None

        cooldown_row_start = 2 if self.active_chat_cache_enabled_input else 1

        self.cooldown_seconds_input = discord.ui.TextInput(
            label="模式一: 固定冷却",
            placeholder="每条消息冷却X秒。例如: 30",
            default=cooldown_sec_str,
            required=False,
            row=cooldown_row_start
        )
        self.duration_input = discord.ui.TextInput(
            label="模式二: 频率限制 - 时间窗口(秒)",
            placeholder="在X秒内... (例如: 60)",
            default=duration_str,
            required=False,
            row=cooldown_row_start + 1
        )
        self.limit_input = discord.ui.TextInput(
            label="模式二: 频率限制 - 次数上限",
            placeholder="...最多允许发送Y条消息。例如: 2",
            default=limit_str,
            required=False,
            row=cooldown_row_start + 2
        )

        self.add_item(self.cooldown_seconds_input)
        self.add_item(self.duration_input)
        self.add_item(self.limit_input)

    async def on_submit(self, interaction: Interaction):
        settings = {}
        try:
            # 解析布尔值
            if self.enabled_input:
                settings['is_chat_enabled'] = _parse_optional_boolean_text(
                    self.enabled_input.value
                )

            if self.active_chat_cache_enabled_input:
                settings["active_chat_cache_enabled"] = _parse_optional_boolean_text(
                    self.active_chat_cache_enabled_input.value
                )
            
            # 解析整数值
            settings['cooldown_seconds'] = int(v) if (v := self.cooldown_seconds_input.value.strip()) else None
            settings['cooldown_duration'] = int(v) if (v := self.duration_input.value.strip()) else None
            settings['cooldown_limit'] = int(v) if (v := self.limit_input.value.strip()) else None

            # 调用回调函数
            await self.on_submit_callback(interaction, settings)

        except (ValueError, TypeError):
            await interaction.response.send_message(
                "输入的值无效，请检查布尔值和数字格式。",
                ephemeral=True,
            )
