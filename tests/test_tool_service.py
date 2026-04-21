import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.chat.features.tools.services.tool_service import ToolService


def _build_service(tool_map):
    service = ToolService(bot=None, tool_map=tool_map, tool_declarations=[])

    async def _always_enabled(_tool_name: str) -> bool:
        return True

    service.is_tool_globally_enabled = _always_enabled
    return service


@pytest.mark.asyncio
async def test_execute_tool_call_blocks_immediate_duplicate_after_success():
    call_count = 0

    async def sample_tool(**kwargs):
        nonlocal call_count
        call_count += 1
        return {"echo": kwargs["value"]}

    service = _build_service({"sample_tool": sample_tool})
    tool_call = SimpleNamespace(name="sample_tool", args={"value": 42})

    first_result = await service.execute_tool_call(tool_call)
    second_result = await service.execute_tool_call(tool_call)

    assert first_result.function_response.response["result"] == {"echo": 42}
    assert (
        second_result.function_response.response["result"]
        == "你已调用过这个工具了，请根据结果生成回复"
    )
    assert call_count == 1


@pytest.mark.asyncio
async def test_execute_tool_call_allows_retry_after_duplicate_rejection():
    call_count = 0

    async def sample_tool(**kwargs):
        nonlocal call_count
        call_count += 1
        return {"echo": kwargs["value"]}

    service = _build_service({"sample_tool": sample_tool})
    tool_call = SimpleNamespace(name="sample_tool", args={"value": 7})

    await service.execute_tool_call(tool_call)
    blocked_result = await service.execute_tool_call(tool_call)
    retry_result = await service.execute_tool_call(tool_call)

    assert (
        blocked_result.function_response.response["result"]
        == "你已调用过这个工具了，请根据结果生成回复"
    )
    assert retry_result.function_response.response["result"] == {"echo": 7}
    assert call_count == 2


@pytest.mark.asyncio
async def test_execute_tool_call_does_not_block_after_previous_failure():
    call_count = 0

    async def flaky_tool(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        return {"echo": kwargs["value"]}

    service = _build_service({"flaky_tool": flaky_tool})
    tool_call = SimpleNamespace(name="flaky_tool", args={"value": "ok"})

    first_result = await service.execute_tool_call(tool_call)
    second_result = await service.execute_tool_call(tool_call)

    assert "An unexpected error occurred during execution" in (
        first_result.function_response.response["error"]
    )
    assert second_result.function_response.response["result"] == {"echo": "ok"}
    assert call_count == 2


@pytest.mark.asyncio
async def test_execute_tool_call_treats_reordered_dict_args_as_same_call():
    call_count = 0

    async def nested_tool(**kwargs):
        nonlocal call_count
        call_count += 1
        return {"payload": kwargs["payload"]}

    service = _build_service({"nested_tool": nested_tool})
    first_call = SimpleNamespace(
        name="nested_tool",
        args={"payload": {"b": 2, "a": 1}, "list_value": [3, 2, 1]},
    )
    second_call = SimpleNamespace(
        name="nested_tool",
        args={"list_value": [3, 2, 1], "payload": {"a": 1, "b": 2}},
    )

    await service.execute_tool_call(first_call)
    duplicate_result = await service.execute_tool_call(second_call)

    assert (
        duplicate_result.function_response.response["result"]
        == "你已调用过这个工具了，请根据结果生成回复"
    )
    assert call_count == 1


@pytest.mark.asyncio
async def test_execute_tool_call_isolated_by_user_scope():
    call_count = 0

    async def sample_tool(**kwargs):
        nonlocal call_count
        call_count += 1
        return {"echo": kwargs["value"]}

    service = _build_service({"sample_tool": sample_tool})
    tool_call = SimpleNamespace(name="sample_tool", args={"value": 99})

    first_user_result = await service.execute_tool_call(tool_call, user_id=1)
    second_user_result = await service.execute_tool_call(tool_call, user_id=2)

    assert first_user_result.function_response.response["result"] == {"echo": 99}
    assert second_user_result.function_response.response["result"] == {"echo": 99}
    assert call_count == 2
