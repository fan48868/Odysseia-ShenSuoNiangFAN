import base64
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.chat.features.image_generation.cogs.image_generation_cog import (
    PRESET_CHARACTER_PROMPT,
    GatewayImageClient,
    PublicGeneratedImageView,
    ImageGenerationPanelView,
    ReferenceImageInput,
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


def test_gemini_request_with_reference_images_uses_multimodal_content():
    api_url, payload = GatewayImageClient._build_request(
        GatewayImageClient.GEMINI_FLASH_MODEL,
        "keep the character identity and change the clothes",
        reference_images=[
            ReferenceImageInput(
                data=b"ref-image-1",
                filename="ref1.png",
                mime_type="image/png",
            ),
            ReferenceImageInput(
                data=b"ref-image-2",
                filename="ref2.png",
                mime_type="image/jpeg",
            ),
        ],
    )

    assert api_url == GatewayImageClient.CHAT_COMPLETIONS_API_URL
    assert payload["modalities"] == ["text", "image"]
    assert isinstance(payload["messages"][0]["content"], list)
    assert payload["messages"][0]["content"][0] == {
        "type": "text",
        "text": "keep the character identity and change the clothes",
    }
    assert payload["messages"][0]["content"][1]["type"] == "image_url"
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert payload["messages"][0]["content"][2]["type"] == "image_url"
    assert payload["messages"][0]["content"][2]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )


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


@pytest.mark.asyncio
async def test_public_generated_image_view_has_permanent_delete_button():
    view = PublicGeneratedImageView(requester_user_id=123456)

    assert view.timeout is None
    assert view.requester_user_id == 123456
    assert len(view.children) == 1

    delete_button = view.children[0]
    assert delete_button.label == "删除"


@pytest.mark.asyncio
async def test_panel_reference_image_status_line_changes_with_reference_image():
    dummy_user = SimpleNamespace(
        id=123,
        display_name="tester",
        display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
    )
    dummy_interaction = SimpleNamespace(user=dummy_user)

    view_without_reference = ImageGenerationPanelView(
        origin_interaction=dummy_interaction,
        image_client=GatewayImageClient(),
        quota_service=SimpleNamespace(),
        is_developer=False,
        reference_images=None,
    )
    assert (
        view_without_reference._reference_image_status_line()
        == "提示：现在可以传入参考图了，输入命令时加上附加参数吧！"
    )
    view_without_reference.stop()

    view_with_reference = ImageGenerationPanelView(
        origin_interaction=dummy_interaction,
        image_client=GatewayImageClient(),
        quota_service=SimpleNamespace(),
        is_developer=False,
        reference_images=[
            ReferenceImageInput(
                data=b"img-1",
                filename="reference-1.png",
                mime_type="image/png",
            ),
            ReferenceImageInput(
                data=b"img-2",
                filename="reference-2.png",
                mime_type="image/png",
            ),
        ],
    )
    assert view_with_reference._reference_image_status_line() == "当前已传入 2 张参考图。"
    view_with_reference.stop()
