import pathlib
import sys

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.chat.utils.httpx_error_utils import build_request_error_log_fields


def test_build_request_error_log_fields_handles_missing_request():
    err = httpx.ReadTimeout("boom")

    fields = build_request_error_log_fields(err)

    assert fields["exc_type"] == "ReadTimeout"
    assert fields["exc_str"] == "boom"
    assert fields["request_method"] == "<none>"
    assert fields["request_url"] == "<none>"


def test_build_request_error_log_fields_uses_bound_request():
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    err = httpx.ReadTimeout("boom", request=request)

    fields = build_request_error_log_fields(err)

    assert fields["request_method"] == "POST"
    assert fields["request_url"] == "https://example.com/v1/chat/completions"
