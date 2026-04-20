import asyncio
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import discord

log = logging.getLogger(__name__)

TaskState = Literal[
    "registered",
    "generating",
    "response_ready",
    "delivering",
    "completed",
    "dropped",
]


@dataclass
class ReplyRecoveryTask:
    channel_id: int
    guild_id: int
    author_id: int
    message_id: int
    task_state: TaskState
    attempt_token: Optional[str]
    response_text: Optional[str]
    delivery_state: dict[str, Any]
    confirmed_message_ids: list[int]
    side_effect_flags: dict[str, bool]
    created_at: str
    updated_at: str
    last_drop_reason: Optional[str] = None


@dataclass
class ReplySideEffectPayload:
    user_name: str
    user_content: str
    ai_response: str


class ReplyRecoveryManager:
    def __init__(self) -> None:
        self._tasks: "OrderedDict[int, ReplyRecoveryTask]" = OrderedDict()
        self._side_effect_payloads: dict[int, ReplySideEffectPayload] = {}
        self._lock = asyncio.Lock()
        self._resume_task_refs: dict[int, asyncio.Task] = {}

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_side_effect_flags(
        self, existing: Optional[dict[str, bool]] = None
    ) -> dict[str, bool]:
        flags = dict(existing or {})
        flags.setdefault("affection", False)
        flags.setdefault("daily_reward", False)
        flags.setdefault("personal_memory", False)
        return flags

    async def clear_for_tests(self) -> None:
        async with self._lock:
            self._tasks.clear()
            self._side_effect_payloads.clear()
            self._resume_task_refs.clear()

    async def register_message_task(self, message: discord.Message) -> int:
        now = self._now_iso()
        async with self._lock:
            task = self._tasks.get(message.id)
            if task is None:
                task = ReplyRecoveryTask(
                    channel_id=message.channel.id,
                    guild_id=message.guild.id if message.guild else 0,
                    author_id=message.author.id,
                    message_id=message.id,
                    task_state="registered",
                    attempt_token=None,
                    response_text=None,
                    delivery_state={},
                    confirmed_message_ids=[],
                    side_effect_flags=self._ensure_side_effect_flags(),
                    created_at=now,
                    updated_at=now,
                )
            else:
                task.channel_id = message.channel.id
                task.guild_id = message.guild.id if message.guild else 0
                task.author_id = message.author.id
                task.updated_at = now
                if task.task_state in {"completed", "dropped"}:
                    task.task_state = "registered"
                    task.attempt_token = None
                    task.response_text = None
                    task.delivery_state = {}
                    task.confirmed_message_ids = []
                    task.side_effect_flags = self._ensure_side_effect_flags()
                    task.last_drop_reason = None

            self._tasks[message.id] = task
            self._tasks.move_to_end(message.id, last=False)

        return message.id

    async def start_attempt(self, task_id: int) -> Optional[str]:
        token = uuid.uuid4().hex
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.task_state in {"completed", "dropped"}:
                return None

            task.attempt_token = token
            if task.response_text:
                task.task_state = "response_ready"
            else:
                task.task_state = "generating"
            task.updated_at = self._now_iso()
            self._tasks.move_to_end(task_id, last=False)
        return token

    async def is_attempt_current(
        self, task_id: Optional[int], attempt_token: Optional[str]
    ) -> bool:
        if task_id is None or not attempt_token:
            return True

        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            return (
                task.attempt_token == attempt_token
                and task.task_state not in {"completed", "dropped"}
            )

    async def store_response_text(
        self,
        task_id: int,
        attempt_token: str,
        response_text: str,
        *,
        side_effect_payload: Optional[ReplySideEffectPayload] = None,
    ) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.attempt_token != attempt_token:
                return False

            task.response_text = response_text
            task.task_state = "response_ready"
            task.updated_at = self._now_iso()
            if side_effect_payload is not None:
                self._side_effect_payloads[task_id] = side_effect_payload
            self._tasks.move_to_end(task_id, last=False)
            return True

    async def begin_delivery(
        self,
        task_id: int,
        attempt_token: str,
        *,
        chunk_total: int,
    ) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.attempt_token != attempt_token:
                return False

            confirmed_chunks = list(task.delivery_state.get("confirmed_chunks", []))
            task.task_state = "delivering"
            task.delivery_state = {
                "chunk_total": max(int(chunk_total), 1),
                "confirmed_chunks": confirmed_chunks,
            }
            task.updated_at = self._now_iso()
            self._tasks.move_to_end(task_id, last=False)
            return True

    async def get_task_snapshot(self, task_id: int) -> Optional[ReplyRecoveryTask]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            return ReplyRecoveryTask(
                channel_id=task.channel_id,
                guild_id=task.guild_id,
                author_id=task.author_id,
                message_id=task.message_id,
                task_state=task.task_state,
                attempt_token=task.attempt_token,
                response_text=task.response_text,
                delivery_state=dict(task.delivery_state),
                confirmed_message_ids=list(task.confirmed_message_ids),
                side_effect_flags=dict(task.side_effect_flags),
                created_at=task.created_at,
                updated_at=task.updated_at,
                last_drop_reason=task.last_drop_reason,
            )

    async def mark_side_effect_applied(self, task_id: int, flag_name: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.side_effect_flags = self._ensure_side_effect_flags(task.side_effect_flags)
            task.side_effect_flags[flag_name] = True
            task.updated_at = self._now_iso()

    async def mark_chunk_confirmed(
        self,
        task_id: Optional[int],
        *,
        chunk_index: int,
        message_id: int,
        chunk_total: int,
        attempt_token: Optional[str] = None,
        apply_side_effects_on_completion: bool = True,
    ) -> None:
        if task_id is None:
            return

        should_apply_side_effects = False

        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if attempt_token and task.attempt_token != attempt_token:
                return

            confirmed_chunks = set(task.delivery_state.get("confirmed_chunks", []))
            confirmed_chunks.add(int(chunk_index))
            task.delivery_state = {
                "chunk_total": max(int(chunk_total), 1),
                "confirmed_chunks": sorted(confirmed_chunks),
            }
            if message_id not in task.confirmed_message_ids:
                task.confirmed_message_ids.append(message_id)

            if len(confirmed_chunks) >= task.delivery_state["chunk_total"]:
                task.task_state = "completed"
                should_apply_side_effects = apply_side_effects_on_completion
            else:
                task.task_state = "delivering"

            task.updated_at = self._now_iso()

        if should_apply_side_effects:
            await self._apply_side_effects(task_id)

    async def apply_side_effects_if_needed(self, task_id: int) -> None:
        snapshot = await self.get_task_snapshot(task_id)
        if snapshot is None or snapshot.task_state != "completed":
            return
        await self._apply_side_effects(task_id)

    async def drop_task(self, task_id: int, reason: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.task_state = "dropped"
            task.last_drop_reason = reason
            task.updated_at = self._now_iso()
            self._side_effect_payloads.pop(task_id, None)

    async def _apply_side_effects(self, task_id: int) -> None:
        snapshot = await self.get_task_snapshot(task_id)
        if snapshot is None:
            return

        payload = self._side_effect_payloads.get(task_id)
        if payload is None:
            log.info("回复任务 %s 已完成，但没有待执行的副作用负载。", task_id)
            return

        from src.chat.features.affection.service.affection_service import (
            affection_service,
        )
        from src.chat.features.odysseia_coin.service.coin_service import coin_service
        from src.chat.features.personal_memory.services.personal_memory_service import (
            personal_memory_service,
        )

        if not snapshot.side_effect_flags.get("affection"):
            try:
                await affection_service.increase_affection_on_message(snapshot.author_id)
            except Exception as exc:
                log.error("执行好感度副作用失败 | task_id=%s | error=%s", task_id, exc, exc_info=True)
            else:
                await self.mark_side_effect_applied(task_id, "affection")

        if not snapshot.side_effect_flags.get("daily_reward"):
            try:
                reward_granted = await coin_service.grant_daily_message_reward(
                    snapshot.author_id
                )
                if reward_granted:
                    log.info("回复任务 %s 已为用户 %s 发放每日首次对话奖励。", task_id, snapshot.author_id)
            except Exception as exc:
                log.error("执行每日奖励副作用失败 | task_id=%s | error=%s", task_id, exc, exc_info=True)
            else:
                await self.mark_side_effect_applied(task_id, "daily_reward")

        if not snapshot.side_effect_flags.get("personal_memory"):
            try:
                await personal_memory_service.update_and_conditionally_summarize_memory(
                    user_id=snapshot.author_id,
                    user_name=payload.user_name,
                    user_content=payload.user_content,
                    ai_response=payload.ai_response,
                )
            except Exception as exc:
                log.error("执行个人记忆副作用失败 | task_id=%s | error=%s", task_id, exc, exc_info=True)
            else:
                await self.mark_side_effect_applied(task_id, "personal_memory")

    async def _get_resume_candidates(self) -> list[ReplyRecoveryTask]:
        async with self._lock:
            candidates: list[ReplyRecoveryTask] = []
            for task in self._tasks.values():
                if task.task_state in {"completed", "dropped"}:
                    continue
                candidates.append(
                    ReplyRecoveryTask(
                        channel_id=task.channel_id,
                        guild_id=task.guild_id,
                        author_id=task.author_id,
                        message_id=task.message_id,
                        task_state=task.task_state,
                        attempt_token=task.attempt_token,
                        response_text=task.response_text,
                        delivery_state=dict(task.delivery_state),
                        confirmed_message_ids=list(task.confirmed_message_ids),
                        side_effect_flags=dict(task.side_effect_flags),
                        created_at=task.created_at,
                        updated_at=task.updated_at,
                        last_drop_reason=task.last_drop_reason,
                    )
                )
            return candidates

    async def resume_unfinished_tasks(self, bot: discord.Client, reason: str) -> None:
        cog = bot.get_cog("AIChatCog")
        if cog is None:
            log.warning("AIChatCog 尚未加载，暂时无法恢复未完成回复。")
            return

        candidates = await self._get_resume_candidates()
        if not candidates:
            return

        for task in candidates:
            existing_task = self._resume_task_refs.get(task.message_id)
            if existing_task is not None and not existing_task.done():
                continue

            async def _runner(task_id: int = task.message_id) -> None:
                try:
                    await cog.resume_reply_task(task_id=task_id, reason=reason)
                except Exception as exc:
                    log.error(
                        "恢复未完成回复失败 | task_id=%s | reason=%s | error=%s",
                        task_id,
                        reason,
                        exc,
                        exc_info=True,
                    )
                finally:
                    self._resume_task_refs.pop(task_id, None)

            resume_task = asyncio.create_task(
                _runner(), name=f"reply-recovery-resume:{task.message_id}:{reason}"
            )
            self._resume_task_refs[task.message_id] = resume_task


reply_recovery_manager = ReplyRecoveryManager()
