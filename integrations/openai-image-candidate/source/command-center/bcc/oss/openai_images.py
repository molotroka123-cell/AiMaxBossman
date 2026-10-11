"""OpenAI GPT Image adapter for BOSSMAN Images.

This adapter intentionally talks to the public Images API instead of pretending
that ChatGPT's private image tool is callable from BOSSMAN.  It gives the owner
the closest public equivalent: current GPT Image generation models, with a
small, explicit contract and no SDK dependency.

Configuration:
  OPENAI_API_KEY=...
  BOSSMAN_OPENAI_IMAGE_MODEL_FAST=gpt-image-2.5-flare
  BOSSMAN_OPENAI_IMAGE_MODEL_PRECISE=gpt-image-2.5-sunburst
  BOSSMAN_OPENAI_IMAGE_BASE_URL=https://api.openai.com/v1   # optional

The worker always asks for one image. BOSSMAN's queue handles count/retries and
persists every produced artifact with measured dimensions and provenance.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
from dataclasses import dataclass
from typing import Any

import httpx
from PIL import Image

MAX_IMAGE_BYTES = 64 * 1024 * 1024
DEFAULT_BASE_URL = "https://api.openai.com/v1"
MODEL_BY_ALIAS = {
    "openai-image-fast": "gpt-image-2.5-flare",
    "openai-image-precise": "gpt-image-2.5-sunburst",
}


def _api_key() -> str:
    return (
        os.getenv("BOSSMAN_OPENAI_IMAGE_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )


def configured() -> bool:
    return bool(_api_key())


def model_for_alias(alias: str) -> str:
    if alias == "openai-image-fast":
        return os.getenv(
            "BOSSMAN_OPENAI_IMAGE_MODEL_FAST", MODEL_BY_ALIAS[alias]
        ).strip() or MODEL_BY_ALIAS[alias]
    if alias == "openai-image-precise":
        return os.getenv(
            "BOSSMAN_OPENAI_IMAGE_MODEL_PRECISE", MODEL_BY_ALIAS[alias]
        ).strip() or MODEL_BY_ALIAS[alias]
    raise ValueError(f"unsupported OpenAI image alias: {alias}")


def _bounded_size(width: int, height: int) -> tuple[int, int]:
    """Return an Images-API-safe arbitrary size for current GPT Image models."""
    width = int(width)
    height = int(height)
    if not 256 <= width <= 3840 or not 256 <= height <= 3840:
        raise ValueError("OpenAI image dimensions must be in 256..3840")
    ratio = width / height
    if ratio < (1 / 3) or ratio > 3:
        raise ValueError("OpenAI image aspect ratio must be between 1:3 and 3:1")
    # Current arbitrary-size GPT Image endpoints require 16px alignment.
    width = max(256, min(3840, int(round(width / 16)) * 16))
    height = max(256, min(3840, int(round(height / 16)) * 16))
    return width, height


def validate_spec(spec: dict[str, Any]) -> None:
    if spec.get("source_asset_id") is not None or spec.get("reference_asset_ids"):
        raise ValueError(
            "OpenAI text-to-image aliases currently accept text only in this queue; "
            "use Studio/native edit for source images"
        )
    if spec.get("kind", "generate") not in ("generate", "variation"):
        raise ValueError("OpenAI image alias supports generate/variation jobs")
    _bounded_size(int(spec.get("width") or 1024), int(spec.get("height") or 1024))
    options = dict(spec.get("options") or {})
    quality = str(options.get("quality", "auto"))
    if quality not in {"auto", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError("image quality must be auto|low|medium|high|xhigh|max")
    background = str(options.get("background", "auto"))
    if background not in {"auto", "opaque", "transparent"}:
        raise ValueError("image background must be auto|opaque|transparent")
    output_format = str(options.get("output_format", "png"))
    if output_format not in {"png", "jpeg", "webp"}:
        raise ValueError("image output_format must be png|jpeg|webp")
    if background == "transparent" and output_format not in {"png", "webp"}:
        raise ValueError("transparent background requires png or webp")


def _prompt(spec: dict[str, Any], index: int) -> str:
    prompt = str(spec.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("image prompt is empty")
    negative = str(spec.get("negative_prompt") or "").strip()
    if negative:
        prompt += f"\n\nAvoid in the final image: {negative}"
    # Count is implemented as independent jobs so each result gets provenance.
    if index:
        prompt += f"\n\nCreate a distinct variation #{index + 1} of the same request."
    return prompt


def _verify_image(raw: bytes, expected_format: str) -> tuple[int, int, str]:
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("OpenAI image payload is empty or exceeds 64 MiB")
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            width, height = im.size
            fmt = (im.format or "").upper()
    except Exception as exc:
        raise ValueError("OpenAI response did not contain a decodable image") from exc

    mime_by_fmt = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
    mime = mime_by_fmt.get(fmt)
    if not mime:
        raise ValueError(f"unsupported image format returned by OpenAI: {fmt or 'unknown'}")
    expected_mime = {
        "png": "image/png",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }[expected_format]
    if mime != expected_mime:
        raise ValueError(f"OpenAI returned {mime}, expected {expected_mime}")
    return width, height, mime


@dataclass
class OpenAIImageProvider:
    alias: str
    api_key: str | None = None
    base_url: str | None = None
    transport: httpx.AsyncBaseTransport | None = None
    timeout_seconds: float = 180.0

    @property
    def name(self) -> str:
        return self.alias

    def __post_init__(self) -> None:
        if self.alias not in MODEL_BY_ALIAS:
            raise ValueError(f"unsupported OpenAI image alias: {self.alias}")
        self.api_key = (self.api_key or _api_key()).strip()
        if not self.api_key:
            raise ValueError(
                "Set OPENAI_API_KEY or BOSSMAN_OPENAI_IMAGE_API_KEY to use OpenAI image generation"
            )
        self.base_url = (
            self.base_url
            or os.getenv("BOSSMAN_OPENAI_IMAGE_BASE_URL", "").strip()
            or DEFAULT_BASE_URL
        ).rstrip("/")

    async def render(
        self, spec: dict[str, Any], index: int
    ) -> tuple[bytes, str, dict[str, Any]]:
        validate_spec(spec)
        width, height = _bounded_size(
            int(spec.get("width") or 1024), int(spec.get("height") or 1024)
        )
        options = dict(spec.get("options") or {})
        output_format = str(options.get("output_format", "png"))
        model = model_for_alias(self.alias)

        payload: dict[str, Any] = {
            "model": model,
            "prompt": _prompt(spec, index),
            "n": 1,
            "size": f"{width}x{height}",
            "quality": str(options.get("quality", "auto")),
            "background": str(options.get("background", "auto")),
            "output_format": output_format,
        }
        compression = options.get("output_compression")
        if compression is not None and output_format in {"jpeg", "webp"}:
            value = int(compression)
            if not 0 <= value <= 100:
                raise ValueError("output_compression must be 0..100")
            payload["output_compression"] = value

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout_seconds,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            response = await client.post("/images/generations", json=payload)
            response.raise_for_status()
            body = response.json()

        data = body.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise ValueError("OpenAI image response did not contain exactly one result")
        encoded = data[0].get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("OpenAI image response is missing b64_json")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("OpenAI image response contains invalid base64") from exc

        measured_w, measured_h, mime = _verify_image(raw, output_format)
        if (measured_w, measured_h) != (width, height):
            raise ValueError(
                f"OpenAI returned {measured_w}x{measured_h}, expected {width}x{height}"
            )
        return raw, mime, {
            "provider": "openai",
            "provider_alias": self.alias,
            "model": model,
            "mock": False,
            "width": measured_w,
            "height": measured_h,
            "file_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "quality": payload["quality"],
            "background": payload["background"],
            "output_format": output_format,
            "evidence": "openai_images_api_base64_decoded_and_image_verified",
        }
