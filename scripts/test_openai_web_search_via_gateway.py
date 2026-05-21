# -*- coding: utf-8 -*-

import argparse
import getpass
import json
from typing import Any, Dict, List, Optional, Tuple

import requests


DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_INCLUDE_FIELDS = ["web_search_call.action.sources"]
DEFAULT_INSTRUCTIONS = """
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
FORCED_SEARCH_PREFIX = (
    "你必须先调用 web_search 工具至少一次，再整理回答。"
    "如果没有实际调用 web_search，就不要输出最终答案，而是明确说明未执行搜索。"
)


def _prompt_if_missing(value: Optional[str], prompt_text: str, *, secret: bool = False) -> str:
    if value and str(value).strip():
        return str(value).strip()
    if secret:
        return getpass.getpass(prompt_text).strip()
    return input(prompt_text).strip()


def _normalize_responses_url(raw_url: str) -> str:
    normalized = str(raw_url or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("URL 不能为空。")

    if normalized.endswith("/responses"):
        return normalized

    if normalized.endswith("/chat/completions"):
        return normalized[: -len("/chat/completions")] + "/responses"

    return normalized + "/responses"


def _dedupe_sources(sources: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen = set()
    deduped: List[Tuple[str, str]] = []
    for title, url in sources:
        key = (title.strip(), url.strip())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
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


def _extract_sources_from_response(data: Dict[str, Any]) -> List[Tuple[str, str]]:
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


def _has_web_search_call(data: Dict[str, Any]) -> bool:
    for item in data.get("output", []) or []:
        if isinstance(item, dict) and item.get("type") == "web_search_call":
            return True
    return False


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


def _build_payload(
    *,
    model_name: str,
    question: str,
    instructions: str,
    external_web_access: bool,
    tool_choice: str,
    force_prompt_search: bool,
    reasoning_effort: Optional[str],
) -> Dict[str, Any]:
    tool_config: Dict[str, Any] = {"type": "web_search"}
    if external_web_access:
        tool_config["external_web_access"] = True

    final_input = question.strip()
    if force_prompt_search:
        final_input = f"{FORCED_SEARCH_PREFIX}\n\n用户问题：{final_input}"

    payload: Dict[str, Any] = {
        "model": model_name,
        "instructions": instructions,
        "input": final_input,
        "tools": [tool_config],
        "tool_choice": tool_choice,
        "include": list(DEFAULT_INCLUDE_FIELDS),
    }
    if reasoning_effort and reasoning_effort != "none":
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def _looks_like_vercel_gateway_url(raw_url: str) -> bool:
    normalized = str(raw_url or "").strip().lower()
    return "ai-gateway.vercel.sh" in normalized or "vercel" in normalized


def _print_model_hint(raw_url: str, model_name: str) -> None:
    if _looks_like_vercel_gateway_url(raw_url) and not model_name.startswith("openai/"):
        print("\n=== 提示 ===\n")
        print(
            "当前 URL 看起来是 Vercel AI Gateway，但模型名不是 openai/...。"
            "Vercel 文档里的 OpenAI 原生 web_search 仅适用于 OpenAI 模型。"
        )
        print(
            "如果你想给任意模型加搜索，更适合走 Perplexity Search 或 Parallel Search。"
        )


def _send_request(
    request_url: str,
    api_key: str,
    payload: Dict[str, Any],
    timeout_seconds: int,
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        request_url,
        headers=headers,
        json=payload,
        timeout=timeout_seconds,
    )

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body_preview = response.text[:3000] if response.text else "<empty>"
        raise SystemExit(
            f"请求失败: {exc}\nURL: {request_url}\n响应体预览:\n{body_preview}"
        ) from exc

    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"接口返回的不是 JSON。\nURL: {request_url}\n响应体预览:\n{response.text[:3000]}"
        ) from exc


def _print_result(answer_text: str, sources: List[Tuple[str, str]]) -> None:
    print("\n=== 整理后的结果 ===\n")
    print(answer_text or "<模型没有返回可解析的正文>")

    print("\n=== 参考来源 ===\n")
    if not sources:
        print("<未从响应中解析到来源列表>")
        return

    for index, (title, url) in enumerate(sources, start=1):
        print(f"{index}. {title}")
        print(f"   {url}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="测试通过 Vercel AI Gateway / OpenAI Responses API 调用 web_search 工具。"
    )
    parser.add_argument("--url", help="Base URL 或 /responses 完整地址")
    parser.add_argument("--api-key", help="API Key")
    parser.add_argument("--model", help="模型名，例如 openai/gpt-5")
    parser.add_argument("--question", help="要搜索的问题")
    parser.add_argument(
        "--instructions",
        default=DEFAULT_INSTRUCTIONS,
        help="覆盖默认整理提示词",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="请求超时时间（秒）",
    )
    parser.add_argument(
        "--save-json",
        help="把原始响应保存到指定文件路径",
    )
    parser.add_argument(
        "--disable-external-web-access",
        action="store_true",
        help="关闭 external_web_access 标记",
    )
    parser.add_argument(
        "--tool-choice",
        default="required",
        choices=["auto", "required", "none"],
        help="工具选择策略，默认 required，便于验证是否真的联网",
    )
    parser.add_argument(
        "--reasoning-effort",
        default="low",
        choices=["none", "minimal", "low", "medium", "high", "xhigh"],
        help="reasoning.effort，默认 low",
    )
    parser.add_argument(
        "--no-retry-if-no-search",
        action="store_true",
        help="若首轮未触发 web_search，则不自动重试",
    )
    args = parser.parse_args()

    raw_url = _prompt_if_missing(args.url, "请输入 URL（base url 或 /responses 地址）: ")
    api_key = _prompt_if_missing(args.api_key, "请输入 API Key: ", secret=True)
    model_name = _prompt_if_missing(args.model, "请输入模型名: ")
    question = _prompt_if_missing(args.question, "请输入问题: ")

    request_url = _normalize_responses_url(raw_url)
    _print_model_hint(raw_url, model_name)

    payload = _build_payload(
        model_name=model_name,
        question=question,
        instructions=str(args.instructions or DEFAULT_INSTRUCTIONS).strip(),
        external_web_access=not args.disable_external_web_access,
        tool_choice=args.tool_choice,
        force_prompt_search=False,
        reasoning_effort=args.reasoning_effort,
    )
    data = _send_request(request_url, api_key, payload, args.timeout)

    used_retry = False
    if not _has_web_search_call(data) and not args.no_retry_if_no_search:
        retry_payload = _build_payload(
            model_name=model_name,
            question=question,
            instructions=str(args.instructions or DEFAULT_INSTRUCTIONS).strip(),
            external_web_access=not args.disable_external_web_access,
            tool_choice="required",
            force_prompt_search=True,
            reasoning_effort=args.reasoning_effort,
        )
        retried_data = _send_request(request_url, api_key, retry_payload, args.timeout)
        if _has_web_search_call(retried_data):
            data = retried_data
            used_retry = True

    if args.save_json:
        with open(args.save_json, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    has_search_call = _has_web_search_call(data)
    answer_text = _extract_answer_text(data)
    sources = _extract_sources_from_response(data)

    print("\n=== 调试信息 ===\n")
    print(f"请求地址: {request_url}")
    print(f"模型: {model_name}")
    print(f"tool_choice: {'required(重试后)' if used_retry else args.tool_choice}")
    print(f"检测到 web_search_call: {'是' if has_search_call else '否'}")
    if not has_search_call:
        print("说明: 响应中没有真实的 web_search_call，模型大概率没有真的联网搜索。")

    _print_result(answer_text, sources)


if __name__ == "__main__":
    main()
