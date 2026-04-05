import base64
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.chat.features.image_generation.cogs.image_generation_cog import (
    PRESET_CHARACTER_PROMPT,
    GatewayImageClient,
)


def test_model_cycle_order():
    assert GatewayImageClient.DEFAULT_MODEL == GatewayImageClient.GEMINI_FLASH_MODEL
    assert (
        GatewayImageClient.get_next_model(GatewayImageClient.DEFAULT_MODEL)
        == GatewayImageClient.GROK_MODEL
    )
    assert (
        GatewayImageClient.get_next_model(GatewayImageClient.GEMINI_PRO_MODEL)
        == GatewayImageClient.GEMINI_FLASH_MODEL
    )
    assert (
        GatewayImageClient.get_next_model(GatewayImageClient.GEMINI_FLASH_MODEL)
        == GatewayImageClient.GROK_MODEL
    )
    assert (
        GatewayImageClient.get_next_model(GatewayImageClient.GROK_MODEL)
        == GatewayImageClient.GEMINI_PRO_MODEL
    )


def test_gemini_request_uses_chat_completions():
    api_url, payload = GatewayImageClient._build_request(
        GatewayImageClient.GEMINI_PRO_MODEL,
        "draw a fox in neon rain",
    )

    assert api_url == GatewayImageClient.CHAT_COMPLETIONS_API_URL
    assert payload["model"] == GatewayImageClient.GEMINI_PRO_MODEL
    assert payload["messages"] == [
        {"role": "user", "content": "draw a fox in neon rain"}
    ]
    assert payload["modalities"] == ["image"]
    assert payload["stream"] is False


def test_grok_request_uses_images_api():
    api_url, payload = GatewayImageClient._build_request(
        GatewayImageClient.GROK_MODEL,
        "draw a fox in neon rain",
    )

    assert api_url == GatewayImageClient.IMAGE_API_URL
    assert payload == {
        "model": GatewayImageClient.GROK_MODEL,
        "prompt": "draw a fox in neon rain",
    }


def test_collect_and_decode_gemini_data_url_image():
    image_bytes = b"fake-image-bytes"
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "choices": [
            {
                "message": {
                    "images": [
                        {
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}"
                            }
                        }
                    ]
                }
            }
        ]
    }

    candidates = GatewayImageClient._collect_image_candidates(
        GatewayImageClient.GEMINI_PRO_MODEL,
        payload,
    )

    assert len(candidates) == 1
    image, error = GatewayImageClient._decode_data_url_image(
        candidates[0]["image_url"]["url"],
        GatewayImageClient.GEMINI_PRO_MODEL,
    )

    assert error is None
    assert image is not None
    assert image.data == image_bytes
    assert image.filename.endswith(".png")


def test_collect_grok_b64_payload_candidates():
    payload = {
        "data": [
            {
                "b64_json": base64.b64encode(b"grok-image").decode("ascii"),
            }
        ]
    }

    candidates = GatewayImageClient._collect_image_candidates(
        GatewayImageClient.GROK_MODEL,
        payload,
    )

    assert candidates == payload["data"]


def test_preset_character_prompt_contains_both_characters():
    assert "神所娘：" in PRESET_CHARACTER_PROMPT
    assert "类脑娘：" in PRESET_CHARACTER_PROMPT
