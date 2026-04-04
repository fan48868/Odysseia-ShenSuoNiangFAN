# # -*- coding: utf-8 -*-

# import logging
# from typing import Optional

# from pydantic import BaseModel, Field
# from src.chat.features.tools.tool_metadata import tool_metadata

# log = logging.getLogger(__name__)


# class TriggerPersonalMemorySummaryParams(BaseModel):
#     limit: int = Field(
#         2,
#         description="用于本次总结的最近对话轮数（user+AI 视为 1 轮）。默认 2，最高 20。根据频道历史进行设置。确保覆盖要总结的内容。",
#     )
#     reason: Optional[str] = Field(
#         None,
#         description="为什么要现在总结/想记住的关键点（可选，简短即可）。",
#     )


# @tool_metadata(
#     name="个人记忆总结（手动触发）",
#     description="提前触发个人记忆总结，不必等到固定阈值；可指定本次用于总结的最近对话轮数。",
#     emoji="🧠",
#     category="记忆",
# )
# async def trigger_personal_memory_summary(
#     params: TriggerPersonalMemorySummaryParams,
#     **kwargs,
# ) -> str:
#     """
#     [工具说明]
#     - 作用：登记一次“强制总结请求”，在本轮对话写入个人历史后立刻触发个人记忆总结。
#     - 适用场景：用户明确说“记住/写进记忆”、出现长期偏好/约定/身份信息/长期计划等关键点。当用户明确提及“记住这个/记住了！”等时候，调用此工具。
#     - 不建议滥用：闲聊、一次性信息、可从上下文自然推断的信息不需要触发。

#     [参数说明]
#     - limit：用于本次总结的最近对话轮数（user+AI 视为 1 轮）。默认 1，最高 20。根据频道历史进行设置。确保覆盖要总结的内容。
#     - reason：记录触发原因，便于日志排查。
#     """

#     user_id = kwargs.get("user_id")
#     channel = kwargs.get("channel")
#     if not user_id:
#         return "错误：无法获取当前用户ID，无法触发个人记忆总结。"

#     if not isinstance(params, TriggerPersonalMemorySummaryParams):
#         try:
#             clean_dict = {k.strip().strip('"'): v for k, v in (params or {}).items()}
#             params = TriggerPersonalMemorySummaryParams(**clean_dict)
#         except Exception as e:
#             log.error(f"创建 TriggerPersonalMemorySummaryParams 失败: {e}")
#             return f"参数解析失败: {e}"

#     try:
#         user_id_int = int(str(user_id).strip())
#     except Exception:
#         return f"错误：无效的 user_id: {user_id}"

#     # Lazy import to avoid circular import during tool loading.
#     from src.chat.features.personal_memory.services.personal_memory_service import (
#         personal_memory_service,
#     )

#     limit = params.limit
#     try:
#         limit_int = int(limit)
#     except Exception:
#         limit_int = 1
#     if limit_int < 0:
#         limit_int = 1
#     if limit_int > 20:
#         limit_int = 20

#     reason = (params.reason or "").strip() or None

#     await personal_memory_service.request_force_summary(
#         user_id=user_id_int,
#         reason=reason,
#         limit=limit_int,
#     )

#     if channel and hasattr(channel, "send"):
#         try:
#             reason_text = " ".join(reason.split()) if reason else ""
#             confirmation_message = "✅我记住啦"
#             if reason_text:
#                 confirmation_message = f"{reason_text}，{confirmation_message}"
#             await channel.send(confirmation_message)
#         except Exception as e:
#             log.warning(f"发送个人记忆确认消息失败: {e}")

#     return (
#         f"已标记：本轮对话结束后会立刻进行一次个人记忆总结（最近 {limit_int} 轮）。"
#     )
