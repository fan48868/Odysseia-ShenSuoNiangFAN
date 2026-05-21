# -*- coding: utf-8 -*-

"""
手动测试 Gemini 参考图生图。

示例：
python scripts/test_gemini_reference_image.py ^
  --prompt "把这张图改成夜晚霓虹风格，保持人物主体一致" ^
  --image "D:\\images\\ref.png"
"""

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, Tuple

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_BASE_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.1-flash-image-preview"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "reference_image_test_outputs"


def _load_api_key() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = (
        str(os.getenv("IMAGINE_API_KEY", "") or "").strip()
        or str(os.getenv("GROK_IMAGINE_API_KEY", "") or "").strip()
    )
    if not api_key:
        raise ValueError("缺少 IMAGINE_API_KEY（或兼容回退 GROK_IMAGINE_API_KEY）。")
    return api_key


def _guess_mime_type(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    return mime_type or "image/png"


def _build_data_url(image_path: Path) -> Tuple[str, str]:
    image_bytes = image_path.read_bytes()
    mime_type = _guess_mime_type(image_path)
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{image_b64}", mime_type


def _decode_data_url_image(data_url: str) -> Tuple[bytes, str]:
    normalized_data_url = str(data_url or "").strip()
    if not normalized_data_url.startswith("data:") or "," not in normalized_data_url:
        raise ValueError("返回的图片不是合法 data URI。")

    header, encoded_data = normalized_data_url.split(",", 1)
    if ";base64" not in header.lower():
        raise ValueError("返回的图片 data URI 不是 base64 编码。")

    mime_type = header[5:].split(";", 1)[0].strip() or "image/png"
    return base64.b64decode(encoded_data), mime_type


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = str(item.get("text", "") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _save_first_image(response_payload: dict[str, Any], output_dir: Path) -> Path:
    choices = response_payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        raise ValueError("响应里没有 choices。")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ValueError("响应中的第一个 choice 格式不正确。")

    message = first_choice.get("message", {})
    if not isinstance(message, dict):
        raise ValueError("响应中的 message 格式不正确。")

    images = message.get("images", [])
    if not isinstance(images, list) or not images:
        raise ValueError("响应成功，但没有返回 images。")

    first_image = images[0]
    if not isinstance(first_image, dict):
        raise ValueError("响应中的第一张图片格式不正确。")

    image_url = ""
    if isinstance(first_image.get("image_url"), dict):
        image_url = str(first_image["image_url"].get("url") or "").strip()
    if not image_url:
        image_url = str(first_image.get("url") or "").strip()
    if not image_url:
        raise ValueError("响应中的第一张图片没有 url。")

    if image_url.startswith("data:"):
        image_bytes, mime_type = _decode_data_url_image(image_url)
    else:
        image_response = requests.get(image_url, timeout=180)
        image_response.raise_for_status()
        image_bytes = image_response.content
        mime_type = image_response.headers.get("content-type", "image/png")

    output_dir.mkdir(parents=True, exist_ok=True)
    file_extension = mimetypes.guess_extension(
        str(mime_type).split(";", 1)[0].strip().lower()
    ) or ".png"
    output_path = output_dir / f"gemini_reference_test_{int(time.time())}{file_extension}"
    output_path.write_bytes(image_bytes)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="测试 Gemini 参考图生图。")
    parser.add_argument("--prompt", required=True, help="提示词")
    parser.add_argument("--image", required=True, help="参考图片路径")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"模型名，默认 {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"输出目录，默认 {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"接口地址，默认 {DEFAULT_BASE_URL}",
    )
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(f"参考图片不存在：{image_path}")

    api_key = _load_api_key()
    image_data_url, mime_type = _build_data_url(image_path)

    payload = {
        "model": args.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": args.prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_url,
                            "detail": "auto",
                        },
                    },
                ],
            }
        ],
        "modalities": ["text", "image"],
        "stream": False,
    }

    print("=== 请求信息 ===")
    print(f"模型: {args.model}")
    print(f"参考图: {image_path}")
    print(f"MIME: {mime_type}")
    print(f"接口: {args.base_url}")
    print()

    response = requests.post(
        args.base_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=180,
    )

    print("=== HTTP 响应 ===")
    print(f"状态码: {response.status_code}")
    print()

    try:
        response_payload = response.json()
    except json.JSONDecodeError:
        print(response.text)
        response.raise_for_status()
        raise ValueError("接口返回的不是 JSON。")

    if response.status_code >= 400:
        print(json.dumps(response_payload, ensure_ascii=False, indent=2))
        response.raise_for_status()

    assistant_text = _extract_text_content(
        (((response_payload.get("choices") or [{}])[0] or {}).get("message") or {}).get(
            "content"
        )
    )

    print("=== 文本响应 ===")
    print(assistant_text or "（无文本）")
    print()

    output_path = _save_first_image(response_payload, Path(args.output_dir))
    print("=== 输出图片 ===")
    print(output_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
