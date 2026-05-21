# -*- coding: utf-8 -*-

from typing import Dict, Optional

import httpx


def _safe_exception_str(exc: BaseException) -> str:
    try:
        return str(exc) or "<empty>"
    except Exception as err:  # pragma: no cover - extremely defensive
        return f"<unavailable: {type(err).__name__}>"


def _safe_request_from_error(exc: httpx.RequestError) -> Optional[httpx.Request]:
    try:
        return exc.request
    except Exception:
        return None


def build_request_error_log_fields(e: httpx.RequestError) -> Dict[str, str]:
    req = _safe_request_from_error(e)
    cause = getattr(e, "__cause__", None)

    return {
        "exc_type": type(e).__name__,
        "exc_repr": repr(e),
        "exc_str": _safe_exception_str(e),
        "cause_type": type(cause).__name__ if cause else "<none>",
        "cause_repr": repr(cause) if cause else "<none>",
        "request_method": getattr(req, "method", "<none>") if req else "<none>",
        "request_url": str(getattr(req, "url", "<none>")) if req else "<none>",
    }
