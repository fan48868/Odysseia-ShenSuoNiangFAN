import asyncio
import json
import pathlib
import sys
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.chat.services.openai_models.custom_model import CustomModelClient
from src.chat.services.vercel_gateway_provider_selector import (
    VercelGatewayProviderSelectorService,
)


class _DelayedSSEStream(httpx.AsyncByteStream):
    def __init__(self, chunks: List[bytes], delay_seconds: float) -> None:
        self._chunks = chunks
        self._delay_seconds = delay_seconds

    async def __aiter__(self) -> AsyncGenerator[bytes, None]:
        if not self._chunks:
            return

        yield self._chunks[0]
        for chunk in self._chunks[1:]:
            await asyncio.sleep(self._delay_seconds)
            yield chunk

    async def aclose(self) -> None:
        return None


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
        "gateway_provider_timeout_ms": 6000,
        "gateway_provider_name": "",
        "stream_idle_timeout_seconds": 20.0,
        "accept_encoding": "identity",
    }


def _build_sse_chunk(content: str, model_name: str = "moonshotai/kimi-k2.5") -> str:
    chunk = {
        "id": "resp-1",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content},
                "finish_reason": None,
            }
        ],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


@pytest.mark.asyncio
async def test_proactive_timeout_triggers_for_slow_provider():
    client = CustomModelClient()
    selector = VercelGatewayProviderSelectorService(
        "moonshotai/kimi-k2.5",
        rng=_DeterministicRandom(random_values=[0.0]),  # always pick first provider
    )
    # Ensure warmup is complete to get predictable selection
    await selector.report_success(
        await selector.select_provider(), elapsed_seconds=0.1, output_units=10
    )
    await selector.report_success(
        await selector.select_provider(), elapsed_seconds=0.1, output_units=10
    )
    await selector.report_success(
        await selector.select_provider(), elapsed_seconds=0.1, output_units=10
    )
    await selector.report_success(
        await selector.select_provider(), elapsed_seconds=0.1, output_units=10
    )
    await selector.report_success(
        await selector.select_provider(), elapsed_seconds=0.1, output_units=10
    )

    client._gateway_selectors["moonshotai/kimi-k2.5"] = selector

    def slow_transport_handler(request: httpx.Request) -> httpx.Response:
        chunks = [_build_sse_chunk("hello").encode("utf-8")] + [
            _build_sse_chunk(".").encode("utf-8") for _ in range(16)
        ]
        return httpx.Response(
            200,
            stream=_DelayedSSEStream(chunks, delay_seconds=1.0),
            headers={"Content-Type": "text/event-stream"},
            request=request,
        )

    # Ensure warmup is complete to get predictable selection
    await selector.report_success(
        await selector.select_provider(), elapsed_seconds=0.1, output_units=10
    )
    await selector.report_success(
        await selector.select_provider(), elapsed_seconds=0.1, output_units=10
    )
    await selector.report_success(
        await selector.select_provider(), elapsed_seconds=0.1, output_units=10
    )
    await selector.report_success(
        await selector.select_provider(), elapsed_seconds=0.1, output_units=10
    )
    await selector.report_success(
        await selector.select_provider(), elapsed_seconds=0.1, output_units=10
    )

    client._gateway_selectors["moonshotai/kimi-k2.5"] = selector

    def slow_transport_handler(request: httpx.Request) -> httpx.Response:
        chunks = [_build_sse_chunk("hello").encode("utf-8")] + [
            _build_sse_chunk(".").encode("utf-8") for _ in range(16)
        ]
        return httpx.Response(
            200,
            stream=_DelayedSSEStream(chunks, delay_seconds=1.0),
            headers={"Content-Type": "text/event-stream"},
            request=request,
        )

    runtime_config = _build_runtime_config()
    # Override idle timeout to be longer than proactive timeout
    runtime_config["stream_idle_timeout_seconds"] = 30.0

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(slow_transport_handler)
    ) as http_client:
        with pytest.raises(httpx.ReadTimeout) as exc_info:
            await client.send(
                http_client=http_client,
                payload={"model": "moonshotai/kimi-k2.5", "messages": []},
                runtime_config=runtime_config,
            )

    assert "Proactive timeout" in str(exc_info.value)
    assert exc_info.value.request.method == "POST"
    assert (
        str(exc_info.value.request.url)
        == "https://ai-gateway.vercel.sh/v1/chat/completions"
    )

    statuses = await selector.get_provider_statuses()
    # The first provider is 'novita'
    slow_provider_status = next(s for s in statuses if s["provider_name"] == "novita")
    # The provider should have a failure penalty because the timeout is a network error
    assert slow_provider_status["failure_penalty"] > 0


async def main():
    try:
        await test_proactive_timeout_triggers_for_slow_provider()
        print("Test passed")
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
