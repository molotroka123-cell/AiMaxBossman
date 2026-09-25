from __future__ import annotations

import os
from dataclasses import dataclass

from .qwen_vision import QwenVisionBackend, QwenVisionConfig
from .studio_image_edit import StudioImageEditBroker, StudioImageEditConfig


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class PhotoRuntimeConfig:
    ai_max_media_ready: bool
    vision_url: str
    vision_model: str
    studio_url: str
    image_edit_model: str

    @classmethod
    def from_env(cls) -> "PhotoRuntimeConfig":
        return cls(
            ai_max_media_ready=_flag("BOSSMAN_PIT_AI_MAX_MEDIA", False),
            vision_url=os.environ.get("BOSSMAN_PIT_VISION_URL", "http://127.0.0.1:8991/v1").strip(),
            vision_model=os.environ.get("BOSSMAN_PIT_VISION_MODEL", "").strip(),
            studio_url=os.environ.get("BCC_URL", "http://127.0.0.1:8800").strip(),
            image_edit_model=os.environ.get("BOSSMAN_PIT_IMAGE_EDIT_MODEL", "").strip(),
        )


@dataclass
class PhotoServices:
    config: PhotoRuntimeConfig
    vision: QwenVisionBackend | None
    edit: StudioImageEditBroker | None

    async def close(self) -> None:
        if self.vision is not None:
            await self.vision.close()
        if self.edit is not None:
            await self.edit.close()


def build_photo_services(
    *,
    core_token: str,
    vision_token: str = "",
    transport=None,
    studio_transport=None,
) -> PhotoServices:
    cfg = PhotoRuntimeConfig.from_env()
    if not cfg.ai_max_media_ready:
        return PhotoServices(cfg, None, None)

    vision = (
        QwenVisionBackend(
            QwenVisionConfig(
                base_url=cfg.vision_url,
                model=cfg.vision_model,
                token=vision_token,
                # Local Ollama cold-loads the vision model after idle; a 20 s
                # default produced false NETWORK_UNAVAILABLE on first photo.
                fast_timeout=90.0,
                memory_timeout=240.0,
            ),
            transport=transport,
        )
        if cfg.vision_model
        else None
    )

    edit = (
        StudioImageEditBroker(
            StudioImageEditConfig(
                core_url=cfg.studio_url,
                core_token=core_token,
                model_id=cfg.image_edit_model,
            ),
            transport=studio_transport,
        )
        if cfg.image_edit_model
        else None
    )
    return PhotoServices(cfg, vision, edit)


def photo_runtime_status(config: PhotoRuntimeConfig) -> dict:
    """Secret-free status for the Bossman PIT doctor command."""
    return {
        "ai_max_media_ready": config.ai_max_media_ready,
        "vision_configured": bool(config.ai_max_media_ready and config.vision_model),
        "image_edit_configured": bool(config.ai_max_media_ready and config.image_edit_model),
        "vision_endpoint": "LOOPBACK_CONFIGURED" if config.vision_model else "NOT_CONFIGURED",
        "studio_endpoint": "LOOPBACK_CONFIGURED" if config.image_edit_model else "NOT_CONFIGURED",
    }
