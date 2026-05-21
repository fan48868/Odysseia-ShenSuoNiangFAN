# -*- coding: utf-8 -*-

import json
import logging
import os
from typing import Any, Dict, List, Tuple

import httpx

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

SEARCH_API_BASE_URL = "https://ai-gateway.vercel.sh/v1"
SEARCH_API_URL = f"{SEARCH_API_BASE_URL}/responses"
SEARCH_MODEL_NAME = "openai/gpt-5.4-mini"
SEARCH_TIMEOUT_SECONDS = 180.0
SEARCH_INCLUDE_FIELDS = ["web_search_call.action.sources"]
SEARCH_TOOL_INSTRUCTIONS = """
你是一个联网检索助手。

要求：
1. 必须先使用 web_search 工具，再回答用户问题。
2. 回答语言使用简体中文。
3. 输出必须整理清楚，按以下结构组织：
   - 摘要
   - 关键信息
   - 参考来源
4. 如果搜索结果存在时效性或来源冲突，要明确说明。
5. 不要编造来源；只引用实际搜索到的网址。
""".strip()


def _dedupe_sources(sources: List[Tuple[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    deduped: List[Dict[str, str]] = []
    for title, url in sources:
        clean_title = str(title or "").strip()
        clean_url = str(url or "").strip()
        key = (clean_title, clean_url)
        if not clean_url or key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "title": clean_title or clean_url,
                "url": clean_url,
            }
        )
    return deduped


def _extract_text_from_output_item(item: Dict[str, Any]) -> str:
    if item.get("type") != "message":
        return ""

    chunks: List[str] = []
    for content_item in item.get("content", []) or []:
        if not isinstance(content_item, dict):
            continue
        if content_item.get("type") == "output_text":
            text = str(content_item.get("text") or "").strip()
            if text:
                chunks.append(text)

    return "\n".join(chunks).strip()


def _extract_sources_from_annotations(
    annotations: List[Dict[str, Any]],
) -> List[Tuple[str, str]]:
    extracted: List[Tuple[str, str]] = []
    for annotation in annotations or []:
        if not isinstance(annotation, dict):
            continue
        if annotation.get("type") != "url_citation":
            continue
        url = str(annotation.get("url") or "").strip()
        title = str(annotation.get("title") or url or "Untitled").strip()
        if url:
            extracted.append((title, url))
    return extracted


def _extract_sources_from_response(data: Dict[str, Any]) -> List[Dict[str, str]]:
    sources: List[Tuple[str, str]] = []

    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue

        if item.get("type") == "web_search_call":
            action = item.get("action")
            if isinstance(action, dict):
                for source in action.get("sources", []) or []:
                    if not isinstance(source, dict):
                        continue
                    url = str(source.get("url") or "").strip()
                    title = str(source.get("title") or url or "Untitled").strip()
                    if url:
                        sources.append((title, url))

        for content_item in item.get("content", []) or []:
            if not isinstance(content_item, dict):
                continue
            sources.extend(
                _extract_sources_from_annotations(content_item.get("annotations", []) or [])
            )

    return _dedupe_sources(sources)


def _extract_answer_text(data: Dict[str, Any]) -> str:
    output_text = str(data.get("output_text") or "").strip()
    if output_text:
        return output_text

    chunks: List[str] = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        text = _extract_text_from_output_item(item)
        if text:
            chunks.append(text)

    return "\n\n".join(chunks).strip()


def _has_web_search_call(data: Dict[str, Any]) -> bool:
    for item in data.get("output", []) or []:
        if isinstance(item, dict) and item.get("type") == "web_search_call":
            return True
    return False


@tool_metadata(
    name="联网搜索",
    description="当用户明确要求上网搜、联网搜、搜索最新网页信息时，使用此工具联网检索并整理结果。",
    emoji="🌐",
    category="工具",
)
async def search_web(question: str, **kwargs) -> Dict[str, Any]:
    """
    [工具说明]
    这是一个专门用于联网搜索网页信息的工具。

    [调用规则 - 高优先级]
    - 当用户明确提到“上网搜”、“联网搜”、“网上查”、“去网上搜一下”、“帮我搜最新信息”、“看一下网页/链接内容”时，你必须调用此工具。
    - 当问题明显依赖最新网页信息时，应优先考虑此工具，而不是直接凭记忆回答。
    - 如果没有调用此工具，就不要假装自己已经上网搜索过。
    - 传入参数时，只需要把“要搜索的问题”本身传给 `question`，不要夹带多余解释。

    [结果使用规则]
    - 工具返回的 `answer` 是已经整理好的搜索结果，可以直接基于它进行回复。
    - 工具返回的 `sources` 是实际搜索到的网址列表；如果需要引用来源，应优先使用这些链接。
    - 如果工具返回 `search_executed=false` 或 `error`，要如实告诉用户本次联网搜索失败，不要编造结果。

    Args:
        question (str): 需要联网搜索的问题，例如“上网搜一下 ds2api 项目是做什么的”里真正要搜索的那部分问题。

    Returns:
        一个包含搜索是否执行、整理后的答案、来源列表和错误信息的字典。
    """
    del kwargs

    clean_question = str(question or "").strip()
    if not clean_question:
        return {
            "search_executed": False,
            "error": "搜索问题不能为空。",
        }

    api_key = str(os.getenv("SEARCH_API_KEY") or "").strip()
    if not api_key:
        log.warning("[WebSearchTool] SEARCH_API_KEY 未配置。")
        return {
            "search_executed": False,
            "error": "未配置 SEARCH_API_KEY，无法执行联网搜索。",
        }

    payload: Dict[str, Any] = {
        "model": SEARCH_MODEL_NAME,
        "instructions": SEARCH_TOOL_INSTRUCTIONS,
        "input": clean_question,
        "tools": [
            {
                "type": "web_search",
                "external_web_access": True,
            }
        ],
        "tool_choice": "required",
        "include": list(SEARCH_INCLUDE_FIELDS),
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SECONDS) as client:
            response = await client.post(
                SEARCH_API_URL,
                headers=headers,
                json=payload,
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body_preview = exc.response.text[:2000] if exc.response is not None else ""
        log.error(
            "[WebSearchTool] HTTP 请求失败 | status=%s | body=%s",
            exc.response.status_code if exc.response is not None else "N/A",
            body_preview,
        )
        return {
            "search_executed": False,
            "error": f"联网搜索请求失败：HTTP {exc.response.status_code if exc.response is not None else 'N/A'}。",
            "detail": body_preview or str(exc),
        }
    except httpx.RequestError as exc:
        log.error("[WebSearchTool] 网络请求异常: %s", exc, exc_info=True)
        return {
            "search_executed": False,
            "error": f"联网搜索网络异常：{type(exc).__name__}。",
            "detail": str(exc),
        }

    try:
        data = response.json()
    except json.JSONDecodeError:
        body_preview = response.text[:2000] if response.text else "<empty>"
        log.error("[WebSearchTool] 响应不是 JSON: %s", body_preview)
        return {
            "search_executed": False,
            "error": "联网搜索返回了非 JSON 响应。",
            "detail": body_preview,
        }

    answer_text = _extract_answer_text(data)
    sources = _extract_sources_from_response(data)
    search_executed = _has_web_search_call(data)

    result: Dict[str, Any] = {
        "search_executed": search_executed,
        "query": clean_question,
        "model": SEARCH_MODEL_NAME,
        "answer": answer_text,
        "sources": sources,
    }

    if not search_executed:
        result["error"] = "模型本次响应中未实际触发 web_search 工具。"
    elif not answer_text:
        result["error"] = "联网搜索已执行，但未解析出整理后的正文。"

    return result
