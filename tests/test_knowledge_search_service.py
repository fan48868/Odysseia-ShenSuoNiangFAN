import importlib
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def knowledge_search_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))

    sqlalchemy_stub = types.ModuleType("sqlalchemy")
    sqlalchemy_stub.text = lambda value: value
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy_stub)

    database_stub = types.ModuleType("src.database.database")
    database_stub.AsyncSessionLocal = object()
    monkeypatch.setitem(sys.modules, "src.database.database", database_stub)

    gemini_stub_module = types.ModuleType("src.chat.services.gemini_service")

    class _GeminiStub:
        async def generate_embedding(self, *args, **kwargs):
            return []

    gemini_stub_module.gemini_service = _GeminiStub()
    monkeypatch.setitem(
        sys.modules,
        "src.chat.services.gemini_service",
        gemini_stub_module,
    )

    module_name = "src.chat.features.world_book.services.knowledge_search_service"
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _raw_result(source_table: str, document_id: int, rrf_score: float, chunk_text: str):
    return {
        "source_table": source_table,
        "document_id": document_id,
        "chunk_text": chunk_text,
        "rrf_score": rrf_score,
    }


def test_select_final_results_keeps_user_card_on_top_and_caps_members(
    knowledge_search_module,
):
    service = knowledge_search_module.KnowledgeSearchService()

    user_card_chunk = {
        "parent_id": 100,
        "chunk_text": "发起者名片",
    }
    search_results = [
        _raw_result("community", 100, 0.99, "发起者名片的重复块"),
        _raw_result("community", 200, 0.98, "其他成员名片"),
        _raw_result("community", 201, 0.97, "不该进入最终结果的第三张名片"),
        _raw_result("general_knowledge", 300, 0.96, "知识 1"),
        _raw_result("general_knowledge", 301, 0.95, "知识 2"),
        _raw_result("general_knowledge", 302, 0.94, "知识 3"),
    ]

    results = service._select_final_results(search_results, user_card_chunk)

    assert len(results) == 5
    assert results[0]["id"] == 100
    assert results[0]["metadata"]["is_user_card"] is True
    assert [item["metadata"]["source_table"] for item in results].count("community") == 2
    assert [item["id"] for item in results] == [100, 200, 300, 301, 302]


def test_select_final_results_dedupes_same_entry_and_backfills_with_knowledge(
    knowledge_search_module,
):
    service = knowledge_search_module.KnowledgeSearchService()

    search_results = [
        _raw_result("community", 200, 0.99, "成员 200 的第一个块"),
        _raw_result("community", 200, 0.98, "成员 200 的第二个块"),
        _raw_result("community", 201, 0.97, "成员 201"),
        _raw_result("general_knowledge", 300, 0.96, "知识 1"),
        _raw_result("general_knowledge", 301, 0.95, "知识 2"),
        _raw_result("general_knowledge", 302, 0.94, "知识 3"),
        _raw_result("general_knowledge", 303, 0.93, "知识 4"),
    ]

    results = service._select_final_results(search_results)

    assert len(results) == 5
    assert [item["id"] for item in results] == [200, 201, 300, 301, 302]
    assert [item["metadata"]["source_table"] for item in results].count("community") == 2
    assert [item["metadata"]["source_table"] for item in results].count("general_knowledge") == 3
