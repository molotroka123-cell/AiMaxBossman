from __future__ import annotations

import base64
import io

import httpx
import pytest
from PIL import Image

from bcc.oss.openai_images import OpenAIImageProvider, validate_spec


def png(width: int = 256, height: int = 256) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (12, 24, 48)).save(out, format="PNG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_openai_image_provider_calls_current_images_api_and_verifies_result():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(png()).decode()}]},
            request=request,
        )

    provider = OpenAIImageProvider(
        "openai-image-fast",
        api_key="test-key",
        base_url="https://api.openai.test/v1",
        transport=httpx.MockTransport(handler),
    )
    raw, mime, meta = await provider.render(
        {
            "prompt": "A clean futuristic dashboard",
            "width": 256,
            "height": 256,
            "options": {"quality": "high", "output_format": "png"},
        },
        0,
    )

    assert raw.startswith(b"\x89PNG")
    assert mime == "image/png"
    assert meta["provider"] == "openai"
    assert meta["model"] == "gpt-image-2.5-flare"
    assert meta["width"] == 256 and meta["height"] == 256
    assert seen["authorization"] == "Bearer test-key"
    assert '"model":"gpt-image-2.5-flare"' in seen["body"]
    assert '"size":"256x256"' in seen["body"]
    assert '"quality":"high"' in seen["body"]


@pytest.mark.asyncio
async def test_openai_image_provider_rejects_wrong_measured_dimensions():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(png(512, 256)).decode()}]},
            request=request,
        )

    provider = OpenAIImageProvider(
        "openai-image-precise",
        api_key="test-key",
        base_url="https://api.openai.test/v1",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="expected 256x256"):
        await provider.render({"prompt": "x", "width": 256, "height": 256}, 0)


def test_openai_image_spec_rejects_reference_jobs_until_edit_adapter_exists():
    with pytest.raises(ValueError, match="text only"):
        validate_spec(
            {
                "prompt": "edit this",
                "width": 1024,
                "height": 1024,
                "source_asset_id": 7,
            }
        )
