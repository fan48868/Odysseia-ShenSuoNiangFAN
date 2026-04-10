"""
工具设置服务

负责管理全局工具启用设置。
"""

import json
import logging
from typing import Any, Dict, Optional, Set

from src.chat.utils.database import chat_db_manager

log = logging.getLogger(__name__)

GLOBAL_ENABLED_TOOLS_KEY = "global_enabled_tools"


class UserToolSettingsService:
    """工具设置服务。保留旧类名，内部改为管理全局工具开关。"""

    async def get_global_tool_settings(self) -> Optional[Dict[str, Any]]:
        """
        获取全局工具设置。

        Returns:
            如果已有全局设置记录，返回设置字典；否则返回 None。
        """
        raw_value = await chat_db_manager.get_global_setting(GLOBAL_ENABLED_TOOLS_KEY)
        if not raw_value:
            return None

        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as e:
            log.error("解析全局工具设置失败: %s", e, exc_info=True)
            return None

        if isinstance(parsed, dict):
            return parsed

        log.warning("全局工具设置格式无效，预期为 dict，实际为 %s", type(parsed).__name__)
        return None

    async def save_global_tool_settings(self, settings: Dict[str, Any]) -> bool:
        """
        保存全局工具设置。

        Args:
            settings: 工具设置字典。

        Returns:
            保存是否成功。
        """
        try:
            await chat_db_manager.set_global_setting(
                GLOBAL_ENABLED_TOOLS_KEY,
                json.dumps(settings, ensure_ascii=False),
            )
            log.info("成功保存全局工具设置: %s", settings)
            return True
        except Exception as e:
            log.error("保存全局工具设置时出错: %s", e, exc_info=True)
            return False

    async def get_globally_enabled_tool_names(self) -> Optional[Set[str]]:
        """
        获取当前全局启用的工具名集合。

        Returns:
            - `None`: 未配置全局开关，表示默认启用全部工具。
            - `set()`: 已显式关闭全部可控工具。
            - `{"tool_a", "tool_b"}`: 仅这些工具启用。
        """
        settings = await self.get_global_tool_settings()
        if not settings:
            return None

        enabled_tools = settings.get("enabled_tools")
        if enabled_tools is None:
            return None

        if not isinstance(enabled_tools, list):
            log.warning(
                "全局工具设置中的 enabled_tools 格式无效，预期为 list，实际为 %s",
                type(enabled_tools).__name__,
            )
            return None

        return {str(tool_name) for tool_name in enabled_tools}

    # 兼容旧调用方，统一映射到全局设置。
    async def get_user_tool_settings(self, user_id: str) -> Optional[Dict[str, Any]]:
        return await self.get_global_tool_settings()

    async def save_user_tool_settings(
        self, user_id: str, enabled_tools: Dict[str, Any]
    ) -> bool:
        return await self.save_global_tool_settings(enabled_tools)

    async def delete_user_tool_settings(self, user_id: str) -> bool:
        return await self.save_global_tool_settings({"enabled_tools": None})


# 全局服务实例
user_tool_settings_service = UserToolSettingsService()
