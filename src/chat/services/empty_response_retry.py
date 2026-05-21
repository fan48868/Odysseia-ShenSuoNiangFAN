# -*- coding: utf-8 -*-

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DEFAULT_RETRY_MESSAGE = "@神所娘：继续回复"
DEFAULT_ASSISTANT_NAME = "神所娘"
CHANNEL_HISTORY_INTRO = "这是本频道最近的对话记录:"
CHANNEL_HISTORY_ACK = "我已了解频道的历史对话"


@dataclass(frozen=True)
class SyntheticRetryRequest:
    message: str
    replied_message: Optional[str]
    images: List[Dict[str, Any]]
    videos: List[Dict[str, Any]]
    channel_context: List[Dict[str, Any]]


def _normalize_channel_context(
    channel_context: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    if not isinstance(channel_context, list):
        return []
    return list(channel_context)


def _build_previous_user_input(
    message: Optional[str],
    replied_message: Optional[str],
) -> str:
    parts: List[str] = []

    if isinstance(replied_message, str) and replied_message.strip():
        parts.append(replied_message.rstrip())
    if isinstance(message, str) and message.strip():
        parts.append(message.strip())

    combined = "\n".join(parts).strip()
    if combined:
        return combined

    return "（上一轮用户未提供文本，可能包含图片或视频输入）"


def build_empty_response_retry_request(
    *,
    user_name: str,
    original_message: Optional[str],
    original_replied_message: Optional[str],
    original_channel_context: Optional[List[Dict[str, Any]]],
    fallback_response_text: str,
    assistant_name: str = DEFAULT_ASSISTANT_NAME,
) -> SyntheticRetryRequest:
    previous_user_input = _build_previous_user_input(
        original_message,
        original_replied_message,
    )
    retry_channel_context = _normalize_channel_context(original_channel_context)

    synthetic_history = (
        f"[{user_name}]:\n{previous_user_input}\n\n"
        f"[{assistant_name}]: {fallback_response_text}"
    )

    history_turn_index = _find_channel_history_turn_index(retry_channel_context)
    if history_turn_index is not None:
        history_turn = retry_channel_context[history_turn_index]
        history_parts = list(history_turn.get("parts") or [])
        history_text = str(history_parts[0] or "").rstrip() if history_parts else ""
        if history_text:
            history_text = f"{history_text}\n\n{synthetic_history}"
        else:
            history_text = f"{CHANNEL_HISTORY_INTRO}\n\n{synthetic_history}"

        history_parts = [history_text, *history_parts[1:]]
        retry_channel_context[history_turn_index] = {
            **history_turn,
            "parts": history_parts,
        }
    else:
        insert_index = _find_channel_history_ack_index(retry_channel_context)
        history_turn = {
            "role": "user",
            "parts": [f"{CHANNEL_HISTORY_INTRO}\n\n{synthetic_history}"],
        }
        if insert_index is None:
            retry_channel_context.append(history_turn)
        else:
            retry_channel_context.insert(insert_index, history_turn)

    return SyntheticRetryRequest(
        message=DEFAULT_RETRY_MESSAGE,
        replied_message=None,
        images=[],
        videos=[],
        channel_context=retry_channel_context,
    )


def _find_channel_history_turn_index(
    channel_context: List[Dict[str, Any]],
) -> Optional[int]:
    for index, turn in enumerate(channel_context):
        if turn.get("role") != "user":
            continue
        parts = turn.get("parts") or []
        if not parts:
            continue
        first_part = parts[0]
        if isinstance(first_part, str) and first_part.startswith(CHANNEL_HISTORY_INTRO):
            return index
    return None


def _find_channel_history_ack_index(
    channel_context: List[Dict[str, Any]],
) -> Optional[int]:
    for index, turn in enumerate(channel_context):
        if turn.get("role") != "model":
            continue
        parts = turn.get("parts") or []
        if not parts:
            continue
        first_part = parts[0]
        if isinstance(first_part, str) and first_part.strip() == CHANNEL_HISTORY_ACK:
            return index
    return None
