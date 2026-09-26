from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

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
    image_license_mode: str = "unconfirmed"
    image_generation_model: str = ""
    allow_unmeasured_media: bool = False

    @classmethod
    def from_env(cls, data_dir: Path | None = None) -> "PhotoRuntimeConfig":
        # Public, non-secret owner settings survive a PIT restart. Invalid
        # or oversized files fail closed; environment overrides aid live runs.
        saved: dict = {}
        if data_dir is not None:
            path = Path(data_dir) / "pit-v1.7" / "media.json"
            try:
                if path.stat().st_size <= 16384:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(value, dict):
                        saved = value
            except (OSError, ValueError, UnicodeError):
                pass

        def value(env: str, key: str, default: str = "") -> str:
            raw = os.environ.get(env, saved.get(key, default))
            return raw.strip() if isinstance(raw, str) else default

        return cls(
            ai_max_media_ready=_flag("BOSSMAN_PIT_AI_MAX_MEDIA", saved.get("ai_max_media_ready") is True),
            vision_url=value("BOSSMAN_PIT_VISION_URL", "vision_url", "http://127.0.0.1:8991/v1"),
            vision_model=value("BOSSMAN_PIT_VISION_MODEL", "vision_model"),
            studio_url=value("BCC_URL", "studio_url", "http://127.0.0.1:8800"),
            image_edit_model=value("BOSSMAN_PIT_IMAGE_EDIT_MODEL", "image_edit_model"),
            image_license_mode=value("BOSSMAN_PIT_IMAGE_LICENSE_MODE", "image_license_mode", "unconfirmed").lower(),
            image_generation_model=value("BOSSMAN_PIT_IMAGE_GENERATION_MODEL", "image_generation_model"),
            allow_unmeasured_media=_flag(
                "BOSSMAN_PIT_ALLOW_UNMEASURED_MEDIA",
                saved.get("allow_unmeasured_media") is True),
        )

    def image_use_allowed(self, *, public_mode: bool) -> bool:
        if self.image_license_mode == "commercial_licensed":
            return True
        return self.image_license_mode == "research_eval" and not public_mode


@dataclass
class PhotoServices:
    config: PhotoRuntimeConfig
    vision: QwenVisionBackend | None
    edit: StudioImageEditBroker | None
    generate: StudioImageEditBroker | None = None

    async def close(self) -> None:
        if self.vision is not None:
            await self.vision.close()
        if self.edit is not None:
            await self.edit.close()
        if self.generate is not None and self.generate is not self.edit:
            await self.generate.close()


def build_photo_services(
    *,
    core_token: str,
    vision_token: str = "",
    transport=None,
    studio_transport=None,
    data_dir: Path | None = None,
) -> PhotoServices:
    cfg = PhotoRuntimeConfig.from_env(data_dir)
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
    generate = (
        edit if cfg.image_generation_model == cfg.image_edit_model and edit is not None
        else StudioImageEditBroker(
            StudioImageEditConfig(
                core_url=cfg.studio_url,
                core_token=core_token,
                model_id=cfg.image_generation_model,
            ),
            transport=studio_transport,
        ) if cfg.image_generation_model else None
    )
    return PhotoServices(cfg, vision, edit, generate)


def photo_runtime_status(config: PhotoRuntimeConfig) -> dict:
    """Secret-free status for the Bossman PIT doctor command."""
    return {
        "ai_max_media_ready": config.ai_max_media_ready,
        "vision_configured": bool(config.ai_max_media_ready and config.vision_model),
        "image_edit_configured": bool(config.ai_max_media_ready and config.image_edit_model),
        "image_generation_configured": bool(config.ai_max_media_ready and config.image_generation_model),
        "allow_unmeasured_media": config.allow_unmeasured_media,
        "image_license_mode": config.image_license_mode if config.image_license_mode in {
            "research_eval", "commercial_licensed"} else "unconfirmed",
        "vision_endpoint": "LOOPBACK_CONFIGURED" if config.vision_model else "NOT_CONFIGURED",
        "studio_endpoint": "LOOPBACK_CONFIGURED" if (
            config.image_edit_model or config.image_generation_model) else "NOT_CONFIGURED",
    }
