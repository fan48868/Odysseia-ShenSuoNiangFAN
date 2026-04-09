# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger(__name__)

_DISABLED_PROVIDERS_KEY_TEMPLATE = "vercel_gateway_disabled_providers:{}"


@dataclass(frozen=True)
class ProviderSelection:
    provider_name: str
    stage: str
    effective_score: float


@dataclass
class _ProviderState:
    provider_name: str
    current_unit_cost: Optional[float] = None
    warmup_total_unit_cost: float = 0.0
    warmup_sample_count: int = 0
    failure_penalty_seconds: float = 0.0
    explored: bool = False
    warmup_reserved: bool = False
    in_flight_count: int = 0
    last_output_units: int = 0
    last_elapsed_seconds: float = 0.0
    cooldown_until_monotonic: float = 0.0


class VercelGatewayProviderSelectorService:
    # 按模型名配置的供应商列表，方便以后扩展其他模型
    MODEL_PROVIDER_NAMES: Dict[str, List[str]] = {
        "moonshotai/kimi-k2.5": [
            "novita",
            "bedrock",
            "moonshotai",
            "fireworks",
            "togetherai",
        ],
    }

    PRIMARY_SELECTION_PROBABILITY = 0.7
    FAST_PROVIDER_LOCK_UNIT_COST_THRESHOLD = 0.06
    FAILURE_PENALTY_SECONDS = 2.0
    SLOW_PROVIDER_UNIT_COST_THRESHOLD = 0.2
    SLOW_PROVIDER_COOLDOWN_SECONDS = 20 * 60

    # 全局实例注册表
    _global_instances: Dict[str, "VercelGatewayProviderSelectorService"] = {}
    _global_instances_lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls, model_name: str) -> Optional["VercelGatewayProviderSelectorService"]:
        """获取已注册的选择器实例（如果尚未注册则返回 None，UI 应通过 CustomModelClient 创建后注册）"""
        async with cls._global_instances_lock:
            return cls._global_instances.get(model_name)

    @classmethod
    async def get_or_create_instance(
        cls,
        model_name: str,
        *,
        rng: Optional[random.Random] = None,
        time_fn: Optional[Callable[[], float]] = None,
    ) -> "VercelGatewayProviderSelectorService":
        """获取全局选择器实例，如不存在则按数据库配置创建。"""
        existing_instance = await cls.get_instance(model_name)
        if existing_instance is not None:
            return existing_instance

        created_instance = await cls.create_for_model(
            model_name,
            rng=rng,
            time_fn=time_fn,
        )
        async with cls._global_instances_lock:
            existing_instance = cls._global_instances.get(model_name)
            if existing_instance is not None:
                return existing_instance
            cls._global_instances[model_name] = created_instance
            return created_instance

    @classmethod
    async def register_instance(cls, model_name: str, instance: "VercelGatewayProviderSelectorService") -> None:
        async with cls._global_instances_lock:
            cls._global_instances[model_name] = instance

    @classmethod
    async def unregister_instance(cls, model_name: str) -> None:
        async with cls._global_instances_lock:
            cls._global_instances.pop(model_name, None)

    @classmethod
    def get_available_models(cls) -> List[str]:
        """获取所有已配置供应商的模型列表（供 UI 使用）"""
        return list(cls.MODEL_PROVIDER_NAMES.keys())

    @classmethod
    def get_disabled_providers_setting_key(cls, model_name: str) -> str:
        return _DISABLED_PROVIDERS_KEY_TEMPLATE.format(model_name)

    @classmethod
    async def load_disabled_providers(cls, model_name: str) -> Set[str]:
        """从全局设置中加载指定模型的已禁用供应商列表。"""
        from src.chat.features.chat_settings.services.chat_settings_service import (
            chat_settings_service,
        )

        raw = await chat_settings_service.db_manager.get_global_setting(
            cls.get_disabled_providers_setting_key(model_name)
        )
        if not raw:
            return set()

        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            log.warning(
                "[Gateway] 禁用供应商配置解析失败，已忽略 | 模型=%s | 原始值=%r",
                model_name,
                raw,
            )
            return set()

        if not isinstance(parsed, (list, tuple, set)):
            log.warning(
                "[Gateway] 禁用供应商配置格式无效，已忽略 | 模型=%s | 类型=%s",
                model_name,
                type(parsed).__name__,
            )
            return set()

        configured_providers = set(cls.MODEL_PROVIDER_NAMES.get(model_name, []))
        disabled_providers = {str(provider_name) for provider_name in parsed}
        if configured_providers:
            disabled_providers &= configured_providers
        return disabled_providers

    @classmethod
    async def create_for_model(
        cls,
        model_name: str,
        *,
        rng: Optional[random.Random] = None,
        time_fn: Optional[Callable[[], float]] = None,
    ) -> "VercelGatewayProviderSelectorService":
        """创建选择器时自动恢复数据库中的禁用供应商配置。"""
        disabled_providers = await cls.load_disabled_providers(model_name)
        return cls(
            model_name=model_name,
            rng=rng,
            time_fn=time_fn,
            disabled_providers=disabled_providers,
        )

    def __init__(
        self,
        model_name: str,
        *,
        rng: Optional[random.Random] = None,
        time_fn: Optional[Callable[[], float]] = None,
        disabled_providers: Optional[Set[str]] = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._rng = rng or random.Random()
        self._time_fn = time_fn or time.monotonic
        self._model_name = model_name

        provider_names = self.MODEL_PROVIDER_NAMES.get(model_name)
        if not provider_names:
            raise ValueError(
                f"No provider names configured for model '{model_name}'. "
                f"Available models: {list(self.MODEL_PROVIDER_NAMES.keys())}"
            )

        self.provider_names = provider_names
        self._disabled_providers: Set[str] = set(disabled_providers or [])
        self._warmup_results_logged = False
        self._provider_states: Dict[str, _ProviderState] = {
            provider_name: _ProviderState(provider_name=provider_name)
            for provider_name in self.provider_names
        }

        # 注册到全局表
        asyncio.create_task(self._register_async())

    async def _register_async(self) -> None:
        await self.register_instance(self._model_name, self)

    async def update_disabled_providers(self, disabled_set: Set[str]) -> None:
        """更新被禁用的供应商列表（UI 调用）"""
        async with self._lock:
            self._disabled_providers = set(disabled_set)
            # 如果禁用列表发生变化，重新检查预热是否完成
            if self._is_warmup_complete_locked() and not self._warmup_results_logged:
                self._log_warmup_results_if_ready_locked()

    async def get_provider_statuses(self) -> List[Dict[str, Any]]:
        """获取当前所有供应商的状态（供 UI 展示）"""
        async with self._lock:
            now = self._time_fn()
            self._refresh_cooldowns_locked(now)
            statuses = []
            for provider_name in self.provider_names:
                state = self._provider_states[provider_name]
                cooling_seconds = max(state.cooldown_until_monotonic - now, 0.0)
                statuses.append({
                    "provider_name": provider_name,
                    "enabled": provider_name not in self._disabled_providers,
                    "effective_score": self._get_effective_score_locked(state),
                    "unit_cost": state.current_unit_cost or 0.0,
                    "failure_penalty": state.failure_penalty_seconds,
                    "sample_count": state.warmup_sample_count,
                    "cooling_seconds": cooling_seconds,
                    "last_elapsed_seconds": state.last_elapsed_seconds,
                    "last_output_units": state.last_output_units,
                    "explored": state.explored,
                })
            return statuses

    async def select_provider(self) -> ProviderSelection:
        async with self._lock:
            now = self._time_fn()
            self._refresh_cooldowns_locked(now)
            warmup_candidates = [
                self._provider_states[provider_name]
                for provider_name in self.provider_names
                if not self._provider_states[provider_name].explored
                and not self._provider_states[provider_name].warmup_reserved
                and not self._is_provider_cooling_locked(
                    self._provider_states[provider_name], now
                )
                and provider_name not in self._disabled_providers
            ]

            if warmup_candidates:
                selected_state = warmup_candidates[0]
                selected_state.warmup_reserved = True
            else:
                selected_state = self._select_steady_provider_locked(now)

            selected_state.in_flight_count += 1
            stage = "warmup" if not self._is_warmup_complete_locked() else "steady"
            return ProviderSelection(
                provider_name=selected_state.provider_name,
                stage=stage,
                effective_score=self._get_effective_score_locked(selected_state),
            )

    async def report_success(
        self, provider_name: str, *, elapsed_seconds: float, output_units: int
    ) -> None:
        async with self._lock:
            state = self._provider_states.get(provider_name)
            if state is None:
                return

            was_warmup_complete = self._is_warmup_complete_locked()
            state.explored = True
            state.warmup_reserved = False
            state.in_flight_count = max(0, state.in_flight_count - 1)
            state.failure_penalty_seconds = 0.0
            state.last_elapsed_seconds = max(float(elapsed_seconds), 0.0)
            state.last_output_units = max(int(output_units), 1)
            unit_cost = state.last_elapsed_seconds / state.last_output_units
            if unit_cost > self.SLOW_PROVIDER_UNIT_COST_THRESHOLD:
                state.cooldown_until_monotonic = (
                    self._time_fn() + self.SLOW_PROVIDER_COOLDOWN_SECONDS
                )
                log.warning(
                    "[Gateway] 供应商测速过慢，进入冷却 | 模型=%s | 供应商=%s | 每字耗时=%.6f | 阈值=%.3f | 冷却分钟=%s",
                    self._model_name,
                    provider_name,
                    unit_cost,
                    self.SLOW_PROVIDER_UNIT_COST_THRESHOLD,
                    self.SLOW_PROVIDER_COOLDOWN_SECONDS // 60,
                )
            else:
                state.cooldown_until_monotonic = 0.0

            if not was_warmup_complete:
                state.warmup_total_unit_cost += unit_cost
                state.warmup_sample_count += 1
                state.current_unit_cost = (
                    state.warmup_total_unit_cost / state.warmup_sample_count
                )
                self._log_warmup_results_if_ready_locked()
                return

            state.current_unit_cost = unit_cost

    async def report_failure(self, provider_name: str) -> None:
        async with self._lock:
            state = self._provider_states.get(provider_name)
            if state is None:
                return

            state.explored = True
            state.warmup_reserved = False
            state.in_flight_count = max(0, state.in_flight_count - 1)
            state.failure_penalty_seconds += self.FAILURE_PENALTY_SECONDS
            if state.current_unit_cost is None:
                state.current_unit_cost = 0.0
            self._log_warmup_results_if_ready_locked()

    async def release_provider(self, provider_name: str) -> None:
        async with self._lock:
            state = self._provider_states.get(provider_name)
            if state is None:
                return

            state.warmup_reserved = False
            state.in_flight_count = max(0, state.in_flight_count - 1)

    def _select_steady_provider_locked(self, now: float) -> _ProviderState:
        ordered_states = self._get_selectable_provider_states_locked(now)
        if not ordered_states:
            log.warning(
                "[Gateway] 所有供应商都在冷却中或被禁用，本次临时忽略冷却继续选路 | 模型=%s",
                self._model_name,
            )
            ordered_states = [
                self._provider_states[provider_name]
                for provider_name in self.provider_names
                if provider_name not in self._disabled_providers
            ] or [
                self._provider_states[provider_name]
                for provider_name in self.provider_names
            ]
        ordered_states.sort(
            key=lambda state: (
                self._get_effective_score_locked(state),
                self.provider_names.index(state.provider_name),
            )
        )

        primary_state = ordered_states[0]
        remaining_states = ordered_states[1:]

        if self._should_lock_primary_provider_locked(primary_state):
            return primary_state

        if (
            not remaining_states
            or self._rng.random() < self.PRIMARY_SELECTION_PROBABILITY
        ):
            return primary_state

        return self._rng.choice(remaining_states)

    def _get_selectable_provider_states_locked(self, now: float) -> List[_ProviderState]:
        return [
            self._provider_states[provider_name]
            for provider_name in self.provider_names
            if not self._is_provider_cooling_locked(
                self._provider_states[provider_name], now
            )
            and provider_name not in self._disabled_providers
        ]

    @staticmethod
    def _is_provider_cooling_locked(state: _ProviderState, now: float) -> bool:
        return state.cooldown_until_monotonic > now

    def _refresh_cooldowns_locked(self, now: float) -> None:
        for state in self._provider_states.values():
            if 0.0 < state.cooldown_until_monotonic <= now:
                state.cooldown_until_monotonic = 0.0
                log.info(
                    "[Gateway] 供应商冷却结束，重新参与选路 | 模型=%s | 供应商=%s",
                    self._model_name,
                    state.provider_name,
                )

    def _get_effective_score_locked(self, state: _ProviderState) -> float:
        return max(state.current_unit_cost or 0.0, 0.0) + max(
            state.failure_penalty_seconds, 0.0
        )

    def _should_lock_primary_provider_locked(self, state: _ProviderState) -> bool:
        if state.current_unit_cost is None:
            return False
        if state.last_output_units <= 0:
            return False
        if state.failure_penalty_seconds > 0:
            return False
        return state.current_unit_cost <= self.FAST_PROVIDER_LOCK_UNIT_COST_THRESHOLD

    def _is_warmup_complete_locked(self) -> bool:
        """预热完成条件：所有未被禁用的供应商都已探索（explored=True）。"""
        active_states = [
            state for name, state in self._provider_states.items()
            if name not in self._disabled_providers
        ]
        if not active_states:
            # 如果没有启用的供应商，预热视为已完成（避免卡死）
            return True
        return all(state.explored for state in active_states)

    def _log_warmup_results_if_ready_locked(self) -> None:
        if self._warmup_results_logged or not self._is_warmup_complete_locked():
            return

        self._warmup_results_logged = True
        # 只记录未禁用的供应商成绩
        active_states = [
            state for name, state in self._provider_states.items()
            if name not in self._disabled_providers
        ]
        if not active_states:
            log.info("[Gateway] 没有启用的供应商，跳过预热记录。模型=%s", self._model_name)
            return
        scoreboard = " | ".join(self._build_scoreboard_entries_locked())
        log.info("[Gateway] 供应商预热完成，模型=%s，当前成绩榜：%s", self._model_name, scoreboard)

    def _build_scoreboard_entries_locked(self) -> List[str]:
        ordered_states = [
            self._provider_states[provider_name] for provider_name in self.provider_names
        ]
        ordered_states.sort(
            key=lambda state: (
                self._get_effective_score_locked(state),
                self.provider_names.index(state.provider_name),
            )
        )
        return [
            (
                f"{state.provider_name}:综合分={self._get_effective_score_locked(state):.6f}"
                f",每字耗时={float(state.current_unit_cost or 0.0):.6f}"
                f",惩罚={state.failure_penalty_seconds:.2f}"
                f",样本={state.warmup_sample_count}"
                f",冷却剩余秒={max(int(state.cooldown_until_monotonic - self._time_fn()), 0)}"
            )
            for state in ordered_states
        ]
