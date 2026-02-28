# -*- coding: utf-8 -*-

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

KIMI_TEMP_COOLDOWN_SECONDS = 120


class NoAvailableKimiKeyError(Exception):
    """当 Kimi Key 池中没有可用 Key 时抛出。"""

    pass


@dataclass
class KimiKeySlot:
    slot_id: str
    label: str  # custom / moonshot
    base_url: str
    api_key: str
    had_429_once: bool = False
    cooldown_until: float = 0.0
    daily_disabled_until: float = 0.0

    @property
    def key_tail(self) -> str:
        return self.api_key[-4:] if self.api_key else "????"

    def is_available(self, now_ts: float) -> bool:
        return now_ts >= self.cooldown_until and now_ts >= self.daily_disabled_until


class KimiKeyRotationService:
    """
    Kimi Key 路由服务（纯内存，不持久化）：
    - 公益站（custom）优先，且默认粘滞；
    - 公益站 key 出错后，临时冷却并尝试下一个 custom key；
    - 官方站（moonshot）按可用 key 轮询；
    - 官方 key 保留 429 两阶段机制：
        1) 首次 429：冷却 120 秒；
        2) 2 分钟后再次 429：封禁至次日 00:00（Asia/Shanghai）；
    - 重启进程后状态重置，全部 key 会重新尝试。
    """

    def __init__(
        self,
        custom_base_url: Optional[str],
        custom_api_key: Optional[str],
        fallback_base_url: Optional[str],
        fallback_api_key: Optional[str],
    ):
        self._lock = asyncio.Lock()
        self._slots: List[KimiKeySlot] = []
        self._slot_map: Dict[str, KimiKeySlot] = {}
        self._active_slot_id: Optional[str] = None
        self._rr_index_by_label: Dict[str, int] = {}

        self._append_slots_if_valid("custom", custom_base_url, custom_api_key)
        self._append_slots_if_valid("moonshot", fallback_base_url, fallback_api_key)

        if self._slots:
            self._active_slot_id = self._slots[0].slot_id
            slot_labels = [s.label for s in self._slots]
            log.info(f"[KimiKeyRotation] 已初始化 {len(self._slots)} 个 key 槽位，顺序: {slot_labels}")
        else:
            log.warning("[KimiKeyRotation] 未检测到任何可用 Kimi Key 配置。")

    @property
    def has_configured_keys(self) -> bool:
        return bool(self._slots)

    @property
    def has_custom_slots(self) -> bool:
        return any(slot.label == "custom" for slot in self._slots)

    @property
    def has_moonshot_slots(self) -> bool:
        return any(slot.label == "moonshot" for slot in self._slots)

    @staticmethod
    def _split_api_keys(raw_api_keys: Optional[str]) -> List[str]:
        """
        支持以下格式：
        - 单个 key: "sk-xxx"
        - 逗号分隔: "sk-1,sk-2"
        - 中文逗号分隔: "sk-1，sk-2"
        - 多行分隔
        """
        raw = (raw_api_keys or "").strip()
        if not raw:
            return []

        parts = re.split(r"[,\n\r，]+", raw)
        normalized: List[str] = []
        seen = set()

        for part in parts:
            key = part.strip().strip('"').strip("'").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(key)

        return normalized

    def _append_slots_if_valid(
        self, label: str, base_url: Optional[str], api_keys: Optional[str]
    ) -> None:
        url = (base_url or "").strip().rstrip("/")
        keys = self._split_api_keys(api_keys)
        if not (url and keys):
            return

        appended_count = 0
        for key in keys:
            slot_id = hashlib.sha256(f"{label}|{url}|{key}".encode("utf-8")).hexdigest()[:16]
            if slot_id in self._slot_map:
                continue

            slot = KimiKeySlot(
                slot_id=slot_id,
                label=label,
                base_url=url,
                api_key=key,
            )
            self._slots.append(slot)
            self._slot_map[slot_id] = slot
            appended_count += 1

        if appended_count > 1:
            log.info(f"[KimiKeyRotation] 来源 {label} 已加载 {appended_count} 个 key。")

    def _refresh_expired_states_locked(self) -> None:
        now_ts = time.time()
        for slot in self._slots:
            if slot.cooldown_until > 0 and now_ts >= slot.cooldown_until:
                slot.cooldown_until = 0.0

            if slot.daily_disabled_until > 0 and now_ts >= slot.daily_disabled_until:
                slot.daily_disabled_until = 0.0
                slot.had_429_once = False

    def _find_slot_index(self, slot_id: Optional[str]) -> int:
        if not slot_id:
            return -1
        for idx, slot in enumerate(self._slots):
            if slot.slot_id == slot_id:
                return idx
        return -1

    def _pick_next_available_after_locked(
        self, slot_id: Optional[str], preferred_labels: Optional[List[str]] = None
    ) -> Optional[KimiKeySlot]:
        if not self._slots:
            return None

        now_ts = time.time()
        start_idx = self._find_slot_index(slot_id)
        if start_idx < 0:
            start_idx = -1

        preferred_set = set(preferred_labels or [])

        for step in range(1, len(self._slots) + 1):
            idx = (start_idx + step) % len(self._slots)
            candidate = self._slots[idx]

            if preferred_set and candidate.label not in preferred_set:
                continue

            if candidate.is_available(now_ts):
                return candidate
        return None

    def _pick_custom_sticky_locked(self) -> Optional[KimiKeySlot]:
        """
        custom 策略：优先粘滞当前 custom key；
        当前不可用则切换到下一个可用 custom key。
        """
        now_ts = time.time()
        active = self._slot_map.get(self._active_slot_id) if self._active_slot_id else None

        if active and active.label == "custom" and active.is_available(now_ts):
            return active

        next_custom = self._pick_next_available_after_locked(
            self._active_slot_id, preferred_labels=["custom"]
        )
        if next_custom:
            self._active_slot_id = next_custom.slot_id
            return next_custom

        return None

    def _pick_round_robin_available_by_label_locked(
        self, label: str
    ) -> Optional[KimiKeySlot]:
        """
        moonshot 策略：同一来源按请求轮询（交替使用）。
        """
        now_ts = time.time()
        candidates = [
            slot for slot in self._slots if slot.label == label and slot.is_available(now_ts)
        ]
        if not candidates:
            return None

        next_idx = (self._rr_index_by_label.get(label, -1) + 1) % len(candidates)
        self._rr_index_by_label[label] = next_idx
        return candidates[next_idx]

    def _pick_active_or_next_locked(self) -> Optional[KimiKeySlot]:
        if not self._slots:
            return None

        # 1) 公益站 custom：优先
        custom_slot = self._pick_custom_sticky_locked()
        if custom_slot:
            return custom_slot

        # 2) 官方 moonshot：当 custom 当前都不可用时回落到官方轮询
        moonshot_slot = self._pick_round_robin_available_by_label_locked("moonshot")
        if moonshot_slot:
            self._active_slot_id = moonshot_slot.slot_id
            return moonshot_slot

        return None

    def _next_beijing_midnight_ts(self) -> float:
        tz = ZoneInfo("Asia/Shanghai")
        now = datetime.now(tz)
        tomorrow = (now + timedelta(days=1)).date()
        next_midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=tz)
        return next_midnight.timestamp()

    async def acquire_active_slot(self) -> KimiKeySlot:
        async with self._lock:
            if not self._slots:
                raise NoAvailableKimiKeyError("Kimi Key 未配置。")

            self._refresh_expired_states_locked()
            slot = self._pick_active_or_next_locked()
            if not slot:
                raise NoAvailableKimiKeyError("所有 Kimi Key 当前不可用。")
            return slot

    async def report_success(self, slot_id: str) -> None:
        async with self._lock:
            slot = self._slot_map.get(slot_id)
            if not slot:
                return

            # 成功后清理 429 标记，避免下一次 429 直接进次日封禁
            if slot.had_429_once:
                slot.had_429_once = False

            if self._active_slot_id != slot_id:
                self._active_slot_id = slot_id

    async def report_custom_error(
        self,
        slot_id: str,
        *,
        daily_disabled: bool = False,
        cooldown_seconds: int = KIMI_TEMP_COOLDOWN_SECONDS,
    ) -> Tuple[str, float]:
        """
        公益 key 出错后标记不可用：
        - daily_disabled=True: 封禁到次日（适合“额度耗尽”）
        - 否则：短时冷却（适合未知错误/临时错误）
        """
        async with self._lock:
            slot = self._slot_map.get(slot_id)
            if not slot:
                return "custom_cooldown", 0.0

            now_ts = time.time()
            if daily_disabled:
                slot.had_429_once = False
                slot.cooldown_until = 0.0
                slot.daily_disabled_until = self._next_beijing_midnight_ts()
                stage = "custom_daily_disabled"
                until_ts = slot.daily_disabled_until
            else:
                slot.had_429_once = False
                slot.cooldown_until = now_ts + max(1, int(cooldown_seconds))
                stage = "custom_cooldown"
                until_ts = slot.cooldown_until

            next_slot = self._pick_active_or_next_locked()
            self._active_slot_id = next_slot.slot_id if next_slot else None

            return stage, until_ts

    async def report_429(self, slot_id: str) -> Tuple[str, float]:
        """
        官方 key 的 429 双阶段惩罚：
        返回:
            (stage, until_ts)
            stage:
              - "temporary_cooldown"
              - "daily_disabled"
        """
        async with self._lock:
            slot = self._slot_map.get(slot_id)
            if not slot:
                return "temporary_cooldown", 0.0

            now_ts = time.time()
            if not slot.had_429_once:
                slot.had_429_once = True
                slot.cooldown_until = now_ts + KIMI_TEMP_COOLDOWN_SECONDS
                stage = "temporary_cooldown"
                until_ts = slot.cooldown_until
                log.warning(
                    f"[KimiKeyRotation] Key ...{slot.key_tail} 首次触发 429，冷却 {KIMI_TEMP_COOLDOWN_SECONDS} 秒。"
                )
            else:
                slot.had_429_once = False
                slot.cooldown_until = 0.0
                slot.daily_disabled_until = self._next_beijing_midnight_ts()
                stage = "daily_disabled"
                until_ts = slot.daily_disabled_until
                reset_dt = datetime.fromtimestamp(until_ts, ZoneInfo("Asia/Shanghai"))
                log.error(
                    f"[KimiKeyRotation] Key ...{slot.key_tail} 再次触发 429，已封禁至次日重置: "
                    f"{reset_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}。"
                )

            next_slot = self._pick_active_or_next_locked()
            self._active_slot_id = next_slot.slot_id if next_slot else None

            return stage, until_ts

    async def reset_all_penalties(self) -> None:
        """重置全部 Kimi key 的惩罚状态（冷却、次日封禁、429标记）。"""
        async with self._lock:
            for slot in self._slots:
                slot.had_429_once = False
                slot.cooldown_until = 0.0
                slot.daily_disabled_until = 0.0

            self._rr_index_by_label.clear()
            self._active_slot_id = self._slots[0].slot_id if self._slots else None
            log.warning("[KimiKeyRotation] 已手动重置全部 key 惩罚状态。")
