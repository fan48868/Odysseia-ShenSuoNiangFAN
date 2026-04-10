import json
import pathlib
import sys
from typing import Any, Dict, List, Optional

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.chat.features.chat_settings.ui.vercel_gateway_settings_view import (
    VercelGatewayProvidersView,
)
from src.chat.services.openai_models.custom_model import CustomModelClient
from src.chat.services.vercel_gateway_provider_selector import (
    VercelGatewayProviderSelectorService,
)


class _DeterministicRandom:
    def __init__(
        self,
        *,
        random_values: Optional[List[float]] = None,
        choice_indexes: Optional[List[int]] = None,
    ) -> None:
        self._random_values = list(random_values or [])
        self._choice_indexes = list(choice_indexes or [])

    def random(self) -> float:
        if self._random_values:
            return self._random_values.pop(0)
        return 0.0

    def choice(self, items: List[Any]) -> Any:
        if not items:
            raise IndexError("Cannot choose from an empty sequence.")
        index = self._choice_indexes.pop(0) if self._choice_indexes else 0
        return items[index % len(items)]


def _build_runtime_config(
    *,
    base_url: str = "https://ai-gateway.vercel.sh/v1",
    model_name: str = "moonshotai/kimi-k2.5",
    gateway_provider_name: str = "",
    gateway_provider_timeout_ms: int = 4000,
    accept_encoding: Optional[str] = "identity",
) -> Dict[str, Any]:
    return {
        "base_url": base_url,
        "api_key": "test-key",
        "api_keys": ["test-key"],
        "api_key_source_type": "inline",
        "api_key_source_value": "test-key",
        "api_key_file_path": None,
        "api_key_error": None,
        "model_name": model_name,
        "enable_vision": False,
        "enable_video_input": False,
        "gateway_provider_timeout_ms": gateway_provider_timeout_ms,
        "gateway_provider_name": gateway_provider_name,
        "stream_idle_timeout_seconds": 5.0,
        "accept_encoding": accept_encoding,
    }


def _build_sse_response(
    request: httpx.Request,
    *,
    model_name: str = "moonshotai/kimi-k2.5",
    content: str = "hello",
    completion_tokens: Optional[int] = 7,
) -> httpx.Response:
    chunks: List[Dict[str, Any]] = [
        {
            "id": "resp-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": None,
                }
            ],
        }
    ]
    final_chunk: Dict[str, Any] = {
        "id": "resp-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": model_name,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    if completion_tokens is not None:
        final_chunk["usage"] = {
            "prompt_tokens": 3,
            "completion_tokens": completion_tokens,
            "total_tokens": completion_tokens + 3,
        }
    chunks.append(final_chunk)

    body = "\n".join(
        [f"data: {json.dumps(chunk, ensure_ascii=False)}" for chunk in chunks]
        + ["data: [DONE]"]
    )
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=body,
        request=request,
    )


def _capture_request_payload(
    request: httpx.Request, captured_payloads: List[Dict[str, Any]]
) -> None:
    captured_payloads.append(json.loads(request.content.decode("utf-8")))


@pytest.mark.asyncio
async def test_selector_tracks_real_success_sample_counts_after_warmup():
    service = VercelGatewayProviderSelectorService(
        "moonshotai/kimi-k2.5",
        rng=_DeterministicRandom(random_values=[0.0]),
    )

    await service.report_success(
        await service.select_provider("req-1"),
        elapsed_seconds=0.2,
        output_units=2,
    )
    await service.report_success(
        await service.select_provider("req-2"),
        elapsed_seconds=0.3,
        output_units=3,
    )
    await service.report_success(
        await service.select_provider("req-3"),
        elapsed_seconds=0.4,
        output_units=4,
    )
    await service.report_success(
        await service.select_provider("req-4"),
        elapsed_seconds=0.5,
        output_units=5,
    )
    await service.report_success(
        await service.select_provider("req-5"),
        elapsed_seconds=0.6,
        output_units=6,
    )

    follow_up = await service.select_provider("req-6")
    assert follow_up.stage == "steady"
    selected_provider_name = follow_up.provider_name

    await service.report_success(
        follow_up,
        elapsed_seconds=1.5,
        output_units=5,
    )

    statuses = await service.get_provider_statuses()
    selected_status = next(
        status
        for status in statuses
        if status["provider_name"] == selected_provider_name
    )
    assert selected_status["sample_count"] == 2
    assert selected_status["unit_cost"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_selector_manual_test_reuses_same_binding_for_retry():
    service = VercelGatewayProviderSelectorService("moonshotai/kimi-k2.5")
    await service.start_manual_test(["bedrock", "fireworks"])

    first_selection = await service.select_provider("retry-req")
    retry_selection = await service.select_provider("retry-req")

    assert first_selection.provider_name == "bedrock"
    assert first_selection.selection_token == retry_selection.selection_token

    await service.report_success(
        retry_selection,
        elapsed_seconds=0.8,
        output_units=4,
    )

    second_selection = await service.select_provider("next-req")
    assert second_selection.provider_name == "fireworks"


@pytest.mark.asyncio
async def test_selector_manual_test_allows_disabled_providers():
    service = VercelGatewayProviderSelectorService("moonshotai/kimi-k2.5")
    await service.update_disabled_providers({"bedrock"})
    await service.start_manual_test(["bedrock"])

    selection = await service.select_provider("disabled-manual-test")

    assert selection.provider_name == "bedrock"
    assert selection.stage == "manual_test"


@pytest.mark.asyncio
async def test_selector_manual_test_records_network_error_without_incrementing_samples():
    service = VercelGatewayProviderSelectorService("moonshotai/kimi-k2.5")
    await service.start_manual_test(["bedrock"])

    selection = await service.select_provider("network-error-req")
    await service.report_failure(
        selection,
        error=httpx.ReadTimeout("simulated timeout"),
        network_error=True,
    )

    statuses = await service.get_provider_statuses()
    bedrock_status = next(
        status for status in statuses if status["provider_name"] == "bedrock"
    )
    assert bedrock_status["sample_count"] == 0

    manual_status = await service.get_manual_test_status()
    assert manual_status["status"] == "completed"
    assert manual_status["results"][0]["status"] == "network_error"
    assert manual_status["results"][0]["error_type"] == "ReadTimeout"


@pytest.mark.asyncio
async def test_custom_model_send_manual_test_retry_reuses_same_provider_and_preserves_queue():
    client = CustomModelClient()
    selector = VercelGatewayProviderSelectorService("moonshotai/kimi-k2.5")
    await selector.start_manual_test(["bedrock", "fireworks"])
    client._gateway_selectors["moonshotai/kimi-k2.5"] = selector

    runtime_config = _build_runtime_config()
    logical_request_id = "logical-manual-test"

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler)
    ) as timeout_client:
        with pytest.raises(httpx.ReadTimeout):
            await client.send(
                http_client=timeout_client,
                payload={"model": "moonshotai/kimi-k2.5", "messages": []},
                runtime_config=runtime_config,
                logical_request_id=logical_request_id,
                is_final_network_attempt=False,
            )

    running_status = await selector.get_manual_test_status()
    assert running_status["status"] == "running"
    assert running_status["queued_providers"] == ["fireworks"]
    assert logical_request_id in running_status["pending_request_bindings"]

    captured_payloads: List[Dict[str, Any]] = []

    def success_handler(request: httpx.Request) -> httpx.Response:
        _capture_request_payload(request, captured_payloads)
        return _build_sse_response(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(success_handler)
    ) as success_client:
        result = await client.send(
            http_client=success_client,
            payload={"model": "moonshotai/kimi-k2.5", "messages": []},
            runtime_config=runtime_config,
            logical_request_id=logical_request_id,
            is_final_network_attempt=True,
        )

    assert captured_payloads[0]["providerOptions"]["gateway"]["only"] == ["bedrock"]
    assert result["selected_gateway_provider"] == "bedrock"

    next_payloads: List[Dict[str, Any]] = []

    def second_success_handler(request: httpx.Request) -> httpx.Response:
        _capture_request_payload(request, next_payloads)
        return _build_sse_response(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(second_success_handler)
    ) as success_client:
        next_result = await client.send(
            http_client=success_client,
            payload={"model": "moonshotai/kimi-k2.5", "messages": []},
            runtime_config=runtime_config,
            logical_request_id="logical-manual-test-2",
            is_final_network_attempt=True,
        )

    assert next_payloads[0]["providerOptions"]["gateway"]["only"] == ["fireworks"]
    assert next_result["selected_gateway_provider"] == "fireworks"

    completed_status = await selector.get_manual_test_status()
    assert completed_status["status"] == "completed"
    assert [item["provider_name"] for item in completed_status["results"]] == [
        "bedrock",
        "fireworks",
    ]


@pytest.mark.asyncio
async def test_view_toggles_between_model_list_and_detail_components():
    view = VercelGatewayProvidersView(guild_id=1)

    assert {item.custom_id for item in view.children} == {"model_select"}

    view.selected_model = "moonshotai/kimi-k2.5"
    view.statuses = [
        {
            "provider_name": "novita",
            "enabled": True,
            "effective_score": 0.1,
            "unit_cost": 0.1,
            "failure_penalty": 0.0,
            "sample_count": 1,
            "cooling_seconds": 0.0,
            "last_elapsed_seconds": 0.1,
            "last_output_units": 1,
            "explored": True,
        }
    ]
    view._sync_components()

    assert {
        item.custom_id for item in view.children
    } == {
        "back_to_models",
        "refresh_status",
        "toggle_manual_test",
        "provider_select",
    }

    view.show_manual_test_controls = True
    view.selected_test_providers = {"novita"}
    view._sync_components()

    assert {
        item.custom_id for item in view.children
    } == {
        "back_to_models",
        "refresh_status",
        "toggle_manual_test",
        "manual_test_provider_select",
        "start_manual_test",
    }

    view.show_manual_test_controls = False
    view.selected_test_providers = set()
    view._sync_components()
    assert {
        item.custom_id for item in view.children
    } == {
        "back_to_models",
        "refresh_status",
        "toggle_manual_test",
        "provider_select",
    }

    assert (
        view._build_content(
            {
                "run_id": "done-run",
                "status": "completed",
                "selected_providers": ["novita"],
                "queued_providers": [],
                "pending_request_bindings": {},
                "results": [
                    {
                        "provider_name": "novita",
                        "status": "success",
                        "elapsed_seconds": 1.0,
                        "output_units": 5,
                        "unit_cost": 0.2,
                        "error_type": None,
                        "error_message": None,
                    }
                ],
            }
        )
        == "⚙️ Vercel Gateway 供应商设置"
    )
    assert (
        view._build_manual_test_summary(
            {
                "run_id": "done-run",
                "status": "completed",
                "selected_providers": ["novita"],
                "queued_providers": [],
                "pending_request_bindings": {},
                "results": [],
            }
        )
        == ""
    )

    view._reset_to_model_selection()
    view._sync_components()
    assert {item.custom_id for item in view.children} == {"model_select"}
