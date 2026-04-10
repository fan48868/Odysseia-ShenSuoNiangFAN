# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger(__name__)

_DISABLED_PROVIDERS_KEY_TEMPLATE = "vercel_gateway_disabled_providers:{}"


@dataclass(frozen=True)
class ProviderSelection:
    provider_name: str
    stage: str
    effective_score: float
    selection_token: str
    logical_request_id: Optional[str] = None
    manual_test_run_id: Optional[str] = None
    manual_test_sequence_index: Optional[int] = None


@dataclass
class _ProviderState:
    provider_name: str
    current_unit_cost: Optional[float] = None
    warmup_total_unit_cost: float = 0.0
    warmup_sample_count: int = 0
    success_sample_count: int = 0
    failure_penalty_seconds: float = 0.0
    explored: bool = False
    warmup_reserved: bool = False
    in_flight_count: int = 0
    last_output_units: int = 0
    last_elapsed_seconds: float = 0.0
    cooldown_until_monotonic: float = 0.0


@dataclass
class _ManualTestState:
    run_id: Optional[str] = None
    status: str = "idle"
    selected_providers: List[str] = field(default_factory=list)
    queued_providers: List[str] = field(default_factory=list)
    pending_request_bindings: Dict[str, ProviderSelection] = field(default_factory=dict)
    results: List[Dict[str, Any]] = field(default_factory=list)


class VercelGatewayProviderSelectorService:
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

    _global_instances: Dict[str, "VercelGatewayProviderSelectorService"] = {}
    _global_instances_lock = asyncio.Lock()

    @classmethod
    async def get_instance(
        cls, model_name: str
    ) -> Optional["VercelGatewayProviderSelectorService"]:
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
    async def register_instance(
        cls, model_name: str, instance: "VercelGatewayProviderSelectorService"
    ) -> None:
        async with cls._global_instances_lock:
            cls._global_instances[model_name] = instance

    @classmethod
    async def unregister_instance(cls, model_name: str) -> None:
        async with cls._global_instances_lock:
            cls._global_instances.pop(model_name, None)

    @classmethod
    def get_available_models(cls) -> List[str]:
        return list(cls.MODEL_PROVIDER_NAMES.keys())

    @classmethod
    def get_disabled_providers_setting_key(cls, model_name: str) -> str:
        return _DISABLED_PROVIDERS_KEY_TEMPLATE.format(model_name)

    @classmethod
    async def load_disabled_providers(cls, model_name: str) -> Set[str]:
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
        self._active_selections: Dict[str, ProviderSelection] = {}
        self._manual_test_state = _ManualTestState()

        asyncio.create_task(self._register_async())

    async def _register_async(self) -> None:
        await self.register_instance(self._model_name, self)

    async def update_disabled_providers(self, disabled_set: Set[str]) -> None:
        async with self._lock:
            self._disabled_providers = set(disabled_set)
            if self._is_warmup_complete_locked() and not self._warmup_results_logged:
                self._log_warmup_results_if_ready_locked()

    async def start_manual_test(self, providers: List[str]) -> Dict[str, Any]:
        async with self._lock:
            if self._manual_test_state.status == "running":
                raise ValueError("已有手动测速进行中。")

            allowed_providers = [
                provider_name
                for provider_name in self.provider_names
                if provider_name in set(providers)
            ]
            if not allowed_providers:
                raise ValueError("没有可用于手动测速的供应商。")

            self._manual_test_state = _ManualTestState(
                run_id=uuid.uuid4().hex,
                status="running",
                selected_providers=list(allowed_providers),
                queued_providers=list(allowed_providers),
                pending_request_bindings={},
                results=[],
            )
            return self._build_manual_test_status_locked()

    async def get_manual_test_status(self) -> Dict[str, Any]:
        async with self._lock:
            return self._build_manual_test_status_locked()

    async def get_provider_statuses(self) -> List[Dict[str, Any]]:
        async with self._lock:
            now = self._time_fn()
            self._refresh_cooldowns_locked(now)
            statuses = []
            for provider_name in self.provider_names:
                state = self._provider_states[provider_name]
                cooling_seconds = max(state.cooldown_until_monotonic - now, 0.0)
                statuses.append(
                    {
                        "provider_name": provider_name,
                        "enabled": provider_name not in self._disabled_providers,
                        "effective_score": self._get_effective_score_locked(state),
                        "unit_cost": state.current_unit_cost or 0.0,
                        "failure_penalty": state.failure_penalty_seconds,
                        "sample_count": state.success_sample_count,
                        "cooling_seconds": cooling_seconds,
                        "last_elapsed_seconds": state.last_elapsed_seconds,
                        "last_output_units": state.last_output_units,
                        "explored": state.explored,
                    }
                )
            return statuses

    async def select_provider(
        self, logical_request_id: Optional[str] = None
    ) -> ProviderSelection:
        async with self._lock:
            now = self._time_fn()
            self._refresh_cooldowns_locked(now)

            manual_selection = self._select_manual_test_provider_locked(logical_request_id)
            if manual_selection is not None:
                return manual_selection

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
            selection = ProviderSelection(
                provider_name=selected_state.provider_name,
                stage=stage,
                effective_score=self._get_effective_score_locked(selected_state),
                selection_token=uuid.uuid4().hex,
                logical_request_id=logical_request_id,
            )
            self._active_selections[selection.selection_token] = selection
            return selection

    async def report_success(
        self,
        selection: ProviderSelection,
        *,
        elapsed_seconds: float,
        output_units: int,
    ) -> None:
        async with self._lock:
            active_selection = self._active_selections.pop(
                selection.selection_token, None
            )
            if active_selection is None:
                return

            state = self._provider_states.get(active_selection.provider_name)
            if state is None:
                return

            was_warmup_complete = self._is_warmup_complete_locked()
            self._finish_selection_locked(active_selection, state)

            state.explored = True
            state.failure_penalty_seconds = 0.0
            state.last_elapsed_seconds = max(float(elapsed_seconds), 0.0)
            state.last_output_units = max(int(output_units), 1)
            state.success_sample_count += 1

            unit_cost = state.last_elapsed_seconds / state.last_output_units
            if unit_cost > self.SLOW_PROVIDER_UNIT_COST_THRESHOLD:
                state.cooldown_until_monotonic = (
                    self._time_fn() + self.SLOW_PROVIDER_COOLDOWN_SECONDS
                )
                log.warning(
                    "[Gateway] 供应商测速过慢，进入冷却 | 模型=%s | 供应商=%s | 每字耗时=%.6f | 阈值=%.3f | 冷却分钟=%s",
                    self._model_name,
                    active_selection.provider_name,
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
            else:
                state.current_unit_cost = unit_cost

            self._record_manual_test_result_locked(
                active_selection,
                status="success",
                elapsed_seconds=state.last_elapsed_seconds,
                output_units=state.last_output_units,
                unit_cost=unit_cost,
            )
            self._log_warmup_results_if_ready_locked()

    async def report_failure(
        self,
        selection: ProviderSelection,
        *,
        error: Optional[BaseException] = None,
        network_error: bool = False,
        apply_penalty: bool = True,
    ) -> None:
        async with self._lock:
            active_selection = self._active_selections.pop(
                selection.selection_token, None
            )
            if active_selection is None:
                return

            state = self._provider_states.get(active_selection.provider_name)
            if state is None:
                return

            self._finish_selection_locked(active_selection, state)

            if apply_penalty:
                state.explored = True
                state.failure_penalty_seconds += self.FAILURE_PENALTY_SECONDS
                if state.current_unit_cost is None:
                    state.current_unit_cost = 0.0

            self._record_manual_test_result_locked(
                active_selection,
                status="network_error" if network_error else "failure",
                error=error,
            )
            if apply_penalty:
                self._log_warmup_results_if_ready_locked()

    async def release_provider(
        self,
        selection: ProviderSelection,
        *,
        manual_result_status: Optional[str] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        async with self._lock:
            active_selection = self._active_selections.pop(
                selection.selection_token, None
            )
            if active_selection is None:
                return

            state = self._provider_states.get(active_selection.provider_name)
            if state is None:
                return

            self._finish_selection_locked(active_selection, state)
            if manual_result_status:
                self._record_manual_test_result_locked(
                    active_selection,
                    status=manual_result_status,
                    error=error,
                )

    def _select_manual_test_provider_locked(
        self, logical_request_id: Optional[str]
    ) -> Optional[ProviderSelection]:
        state = self._manual_test_state
        if state.status != "running" or not logical_request_id:
            return None

        existing_selection = state.pending_request_bindings.get(logical_request_id)
        if existing_selection is not None:
            return existing_selection

        if not state.queued_providers:
            return None

        provider_name = state.queued_providers.pop(0)
        provider_state = self._provider_states[provider_name]
        provider_state.in_flight_count += 1
        selection = ProviderSelection(
            provider_name=provider_name,
            stage="manual_test",
            effective_score=self._get_effective_score_locked(provider_state),
            selection_token=uuid.uuid4().hex,
            logical_request_id=logical_request_id,
            manual_test_run_id=state.run_id,
            manual_test_sequence_index=(
                len(state.selected_providers)
                - len(state.queued_providers)
                - 1
            ),
        )
        state.pending_request_bindings[logical_request_id] = selection
        self._active_selections[selection.selection_token] = selection
        return selection

    def _finish_selection_locked(
        self, selection: ProviderSelection, state: _ProviderState
    ) -> None:
        state.warmup_reserved = False
        state.in_flight_count = max(0, state.in_flight_count - 1)
        self._remove_manual_binding_locked(selection)

    def _remove_manual_binding_locked(self, selection: ProviderSelection) -> None:
        logical_request_id = selection.logical_request_id
        if not logical_request_id:
            return

        bound_selection = self._manual_test_state.pending_request_bindings.get(
            logical_request_id
        )
        if bound_selection and bound_selection.selection_token == selection.selection_token:
            self._manual_test_state.pending_request_bindings.pop(logical_request_id, None)

    def _record_manual_test_result_locked(
        self,
        selection: ProviderSelection,
        *,
        status: str,
        elapsed_seconds: Optional[float] = None,
        output_units: Optional[int] = None,
        unit_cost: Optional[float] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        run_id = selection.manual_test_run_id
        if not run_id or self._manual_test_state.run_id != run_id:
            return

        result = {
            "provider_name": selection.provider_name,
            "status": status,
            "elapsed_seconds": elapsed_seconds,
            "output_units": output_units,
            "unit_cost": unit_cost,
            "error_type": type(error).__name__ if error is not None else None,
            "error_message": str(error) if error is not None else None,
            "sequence_index": selection.manual_test_sequence_index,
        }
        self._manual_test_state.results.append(result)
        self._manual_test_state.results.sort(
            key=lambda item: (
                item.get("sequence_index")
                if item.get("sequence_index") is not None
                else 10**9
            )
        )
        self._maybe_complete_manual_test_locked(run_id)

    def _maybe_complete_manual_test_locked(self, run_id: str) -> None:
        state = self._manual_test_state
        if state.run_id != run_id or state.status != "running":
            return
        if state.queued_providers or state.pending_request_bindings:
            return
        state.status = "completed"

    def _build_manual_test_status_locked(self) -> Dict[str, Any]:
        return {
            "run_id": self._manual_test_state.run_id,
            "status": self._manual_test_state.status,
            "selected_providers": list(self._manual_test_state.selected_providers),
            "queued_providers": list(self._manual_test_state.queued_providers),
            "pending_request_bindings": {
                logical_request_id: selection.provider_name
                for logical_request_id, selection in self._manual_test_state.pending_request_bindings.items()
            },
            "results": [dict(result) for result in self._manual_test_state.results],
        }

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

        if not remaining_states or self._rng.random() < self.PRIMARY_SELECTION_PROBABILITY:
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
        active_states = [
            state
            for name, state in self._provider_states.items()
            if name not in self._disabled_providers
        ]
        if not active_states:
            return True
        return all(state.explored for state in active_states)

    def _log_warmup_results_if_ready_locked(self) -> None:
        if self._warmup_results_logged or not self._is_warmup_complete_locked():
            return

        self._warmup_results_logged = True
        active_states = [
            state
            for name, state in self._provider_states.items()
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
                f",样本={state.success_sample_count}"
                f",冷却剩余秒={max(int(state.cooldown_until_monotonic - self._time_fn()), 0)}"
            )
            for state in ordered_states
        ]
