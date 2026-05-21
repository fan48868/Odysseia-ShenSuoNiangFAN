import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.chat.features.tools.functions.web_search_tool import (
    _extract_sources_from_response,
    search_web,
)


@pytest.mark.asyncio
async def test_search_web_returns_clear_error_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)

    result = await search_web("上网搜一下 ds2api 是什么")

    assert result["search_executed"] is False
    assert "SEARCH_API_KEY" in result["error"]


def test_extract_sources_from_response_dedupes_multiple_source_shapes():
    data = {
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "sources": [
                        {"title": "Example A", "url": "https://example.com/a"},
                        {"title": "Example A", "url": "https://example.com/a"},
                    ]
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "done",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "title": "Example B",
                                "url": "https://example.com/b",
                            }
                        ],
                    }
                ],
            },
        ]
    }

    sources = _extract_sources_from_response(data)

    assert sources == [
        {"title": "Example A", "url": "https://example.com/a"},
        {"title": "Example B", "url": "https://example.com/b"},
    ]
