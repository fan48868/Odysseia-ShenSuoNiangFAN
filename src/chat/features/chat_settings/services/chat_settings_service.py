import discord
import logging
import os
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv, set_key
from src.chat.utils.database import chat_db_manager
from src.chat.services.event_service import event_service
from src.chat.config import chat_config as app_config
from src import config

log = logging.getLogger(__name__)


class ChatSettingsService:
    """封装聊天设置相关的所有业务逻辑。"""

    def __init__(self):
        self.db_manager = chat_db_manager

    async def set_entity_settings(
        self,
        guild_id: int,
        entity_id: int,
        entity_type: str,
        is_chat_enabled: Optional[bool],
        active_chat_cache_enabled: Optional[bool],
    ):
        """设置频道或分类的聊天配置。"""
        await self.db_manager.update_channel_config(
            guild_id=guild_id,
            entity_id=entity_id,
            entity_type=entity_type,
            is_chat_enabled=is_chat_enabled,
            active_chat_cache_enabled=active_chat_cache_enabled,
        )

    async def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        """获取一个服务器的完整聊天设置，包括全局和所有特定频道的配置。"""
        global_config_row = await self.db_manager.get_global_chat_config(guild_id)
        channel_configs_rows = await self.db_manager.get_all_channel_configs_for_guild(
            guild_id
        )
        settings = {
            "global": {
                "chat_enabled": global_config_row["chat_enabled"]
                if global_config_row
                else True,
            },
            "channels": {
                config["entity_id"]: {
                    "entity_type": config["entity_type"],
                    "is_chat_enabled": config["is_chat_enabled"],
                    "active_chat_cache_enabled": config["active_chat_cache_enabled"],
                }
                for config in channel_configs_rows
            },
        }
        return settings

    async def is_active_chat_cache_enabled(
        self, channel: discord.abc.GuildChannel | discord.Thread
    ) -> bool:
        """检查具体频道是否开启了主动聊天缓存。线程继承父文本频道的开关。"""
        guild = getattr(channel, "guild", None)
        if guild is None:
            return False

        target_entity_id = getattr(channel, "id", None)
        parent = getattr(channel, "parent", None)
        owner_id = getattr(channel, "owner_id", None)
        if parent is not None and owner_id is not None and getattr(parent, "id", None):
            target_entity_id = parent.id

        if target_entity_id is None:
            return False

        config_row = await self.db_manager.get_channel_config(guild.id, target_entity_id)
        if not config_row:
            return False

        value = config_row["active_chat_cache_enabled"]
        return bool(value) if value is not None else False

    async def is_chat_globally_enabled(self, guild_id: int) -> bool:
        """检查聊天功能是否在服务器内全局开启。"""
        config = await self.db_manager.get_global_chat_config(guild_id)
        return config["chat_enabled"] if config else True

    async def is_global_dm_enabled(self) -> bool:
        """检查机器人私信功能是否全局开启。"""
        value = await self.db_manager.get_global_setting("global_dm_enabled")
        return value.lower() == "true" if value is not None else True

    async def set_global_dm_enabled(self, enabled: bool) -> None:
        """设置机器人私信功能的全局开关。"""
        await self.db_manager.set_global_setting(
            "global_dm_enabled", "true" if enabled else "false"
        )

    async def get_effective_channel_config(
        self, channel: discord.abc.GuildChannel
    ) -> Dict[str, Any]:
        """
        获取频道的最终生效配置。
        优先级: 帖子特定设置 > 父频道设置 > 分类设置 > 全局默认
        """
        guild_id = channel.guild.id
        channel_id = channel.id

        parent_channel_id = None
        if isinstance(channel, discord.Thread):
            parent_channel_id = channel.parent_id
            channel_category_id = channel.parent.category_id if channel.parent else None
        else:
            channel_category_id = (
                channel.category_id if hasattr(channel, "category_id") else None
            )

        # 默认配置
        effective_config = {
            "is_chat_enabled": True,
        }

        # 1. 获取分类配置
        category_config = None
        if channel_category_id:
            category_config = await self.db_manager.get_channel_config(
                guild_id, channel_category_id
            )

        if category_config:
            if category_config["is_chat_enabled"] is not None:
                effective_config["is_chat_enabled"] = category_config["is_chat_enabled"]

        # 2. 如果是帖子，先获取父频道配置，并覆盖分类配置
        if parent_channel_id is not None:
            parent_channel_config = await self.db_manager.get_channel_config(
                guild_id, parent_channel_id
            )
            if parent_channel_config:
                if parent_channel_config["is_chat_enabled"] is not None:
                    effective_config["is_chat_enabled"] = parent_channel_config[
                        "is_chat_enabled"
                    ]

        # 3. 获取当前频道/帖子特定配置，并覆盖前面的继承结果
        channel_config = await self.db_manager.get_channel_config(guild_id, channel_id)
        if channel_config:
            if channel_config["is_chat_enabled"] is not None:
                effective_config["is_chat_enabled"] = channel_config["is_chat_enabled"]

        return effective_config

    # --- Event Faction Settings ---

    def get_event_factions(self) -> Optional[List[Dict[str, Any]]]:
        """获取当前活动的所有派系。"""
        return event_service.get_event_factions()

    def set_winning_faction(self, faction_id: Optional[str]):
        """设置当前活动的获胜派系。"""
        event_service.set_winning_faction(faction_id)

    def get_winning_faction(self) -> Optional[str]:
        """获取当前活动的获胜派系。"""
        return event_service.get_winning_faction()

    # --- AI Model Settings ---

    def get_available_ai_models(self) -> List[str]:
        """获取所有可用的AI模型。"""
        return config.AVAILABLE_AI_MODELS

    @staticmethod
    def _get_env_path() -> str:
        return os.path.join(config.BASE_DIR, ".env")

    async def get_current_ai_model(self) -> str:
        """获取当前设置的全局AI模型。"""
        model = await self.db_manager.get_global_setting("ai_model")
        return model if model else config.AVAILABLE_AI_MODELS[0]

    async def set_ai_model(self, model: str) -> None:
        """设置全局AI模型。"""
        await self.db_manager.set_global_setting("ai_model", model)

    async def get_current_summary_model(self) -> str:
        """Get the current personal-memory summary model."""
        return app_config.get_summary_model()

    async def set_summary_model(self, model: str) -> bool:
        """Persist the summary model to the current process and the root .env file."""
        normalized_model = str(model or "").strip()
        if not normalized_model:
            raise ValueError("SUMMARY_MODEL cannot be empty.")

        os.environ["SUMMARY_MODEL"] = normalized_model

        env_path = self._get_env_path()
        try:
            os.makedirs(os.path.dirname(env_path), exist_ok=True)
            set_key(
                env_path,
                "SUMMARY_MODEL",
                normalized_model,
                quote_mode="always",
                encoding="utf-8",
            )
            load_dotenv(env_path, override=True, encoding="utf-8")
            return True
        except Exception as e:
            log.warning("写回 .env 失败（将仅本次进程生效）: %s", e, exc_info=True)
            return False

    # --- AI Model Usage ---

    async def increment_model_usage(self, model_name: str) -> None:
        """记录一次模型使用。"""
        if model_name:
            await self.db_manager.increment_model_usage(model_name)

    async def get_model_usage_counts(self) -> Dict[str, int]:
        """获取所有模型的使用计数。"""
        rows = await self.db_manager.get_model_usage_counts()
        return {row["model_name"]: row["usage_count"] for row in rows}


# 单例实例
chat_settings_service = ChatSettingsService()
