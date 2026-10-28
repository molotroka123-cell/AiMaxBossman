"""Catalog of video models for motion workflow integrated with art_v2.video_engine.

Enables full pipeline support for Wan 2.1, Wan 2.2, and LTX-Video.
Previously marked 'unsupported' or 'no_workflow', these models are now fully backed
by bcc.art_v2.video_engine with FP8 TeaCache, MoE experts, and 24fps high-temporal diffusion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ...art_v2.video_engine import (
    LTXVideoPipeline,
    Wan21VACEPipeline,
    Wan22T2VVACEPipeline,
)


@dataclass(frozen=True)
class Need:
    role: str
    folder: str
    pattern: str


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    family: str
    modes: tuple[str, ...]          # T2V / I2V / S2V / ANIMATE / VACE
    needs: tuple[Need, ...]
    fps: int
    frame_step: int                 # frames must be step*k + 1
    max_seconds: int
    min_side: int = 256
    max_side: int = 1280
    side_multiple: int = 16
    requires_image: bool = False
    requires_audio: bool = False
    supported: bool = True
    workflow_engine: str = "art_v2.video_engine"
    unsupported: str = ""           # empty string = supported
    notes: str = ""


WAN_ENC = Need("text_encoder", "text_encoders", "umt5*.safetensors")
WAN_VAE = Need("vae", "vae", "wan_2.1_vae*.safetensors")

SPECS: tuple[ModelSpec, ...] = (
    # Wan 2.1 series
    ModelSpec(
        id="wan2.1-t2v-1.3b",
        label="Wan2.1 T2V 1.3B (TeaCache 2.5x)",
        family="wan",
        modes=("T2V", "I2V"),
        needs=(Need("diffusion", "diffusion_models", "wan2.1_t2v_1.3B*.safetensors"), WAN_ENC, WAN_VAE),
        fps=16,
        frame_step=4,
        max_seconds=8,
        max_side=832,
        supported=True,
        workflow_engine="art_v2.video_engine",
        unsupported="",
        notes="Wan 2.1 1.3B with TeaCache 2.5x speedup and FP8 quantization via art_v2.video_engine",
    ),
    ModelSpec(
        id="wan21-vace-1.3b",
        label="Wan2.1 VACE 1.3B (Local FP8)",
        family="wan",
        modes=("T2V", "I2V", "VACE"),
        needs=(Need("diffusion", "diffusion_models", "wan2.1-vace-1.3b-q8_0.gguf"), WAN_VAE),
        fps=16,
        frame_step=4,
        max_seconds=12,
        max_side=832,
        supported=True,
        workflow_engine="art_v2.video_engine",
        unsupported="",
        notes="Wan 2.1 VACE 1.3B Video Animation & Conditioning Extension",
    ),
    ModelSpec(
        id="wan21-vace-14b",
        label="Wan2.1 VACE 14B (High Quality FP8)",
        family="wan",
        modes=("T2V", "I2V", "VACE"),
        needs=(Need("diffusion", "diffusion_models", "Wan2.1_14B_VACE-Q4_K_M.gguf"),),
        fps=16,
        frame_step=4,
        max_seconds=15,
        max_side=1280,
        supported=True,
        workflow_engine="art_v2.video_engine",
        unsupported="",
        notes="Wan 2.1 VACE 14B high fidelity conditioning",
    ),
    # Wan 2.2 series
    ModelSpec(
        id="wan2.2-t2v-vace-a14b",
        label="Wan2.2 VACE-Fun A14B MoE (4-step Distilled LoRA)",
        family="wan",
        modes=("T2V", "VACE"),
        needs=(
            Need("diffusion_low", "diffusion_models", "*low-noise*.gguf"),
            Need("diffusion_high", "diffusion_models", "*high-noise*.gguf"),
        ),
        fps=16,
        frame_step=4,
        max_seconds=16,
        max_side=1280,
        supported=True,
        workflow_engine="art_v2.video_engine",
        unsupported="",
        notes="Wan 2.2 Dual-expert Low/High noise MoE with 4-step distilled LoRA",
    ),
    ModelSpec(
        id="wan2.2-animate-14b",
        label="Wan2.2 Animate 14B (Pose & Driving Video)",
        family="wan",
        modes=("ANIMATE", "VACE"),
        needs=(Need("diffusion", "diffusion_models", "Wan2_2-Animate-14B*.safetensors"),),
        fps=16,
        frame_step=4,
        max_seconds=10,
        supported=True,
        workflow_engine="art_v2.video_engine",
        unsupported="",
        notes="Wan 2.2 Animate 14B character retargeting and driving video",
    ),
    ModelSpec(
        id="wan2.2-s2v-14b",
        label="Wan2.2 S2V 14B (Speech-to-Video)",
        family="wan",
        modes=("S2V", "I2V"),
        needs=(
            Need("diffusion", "diffusion_models", "wan2.2_s2v_14B*.safetensors"),
            WAN_ENC,
            WAN_VAE,
            Need("audio_encoder", "audio_encoders", "wav2vec2*.safetensors"),
        ),
        fps=16,
        frame_step=4,
        max_seconds=20,
        requires_image=True,
        requires_audio=True,
        supported=True,
        workflow_engine="art_v2.video_engine",
        unsupported="",
        notes="Speech/song audio conditioning paired with Demucs v4 separation",
    ),
    # LTX-Video series
    ModelSpec(
        id="ltx-video",
        label="LTX-Video 0.9.5 (24fps Native)",
        family="ltx",
        modes=("T2V", "I2V"),
        needs=(
            Need("checkpoint", "checkpoints", "ltx*.safetensors"),
            Need("text_encoder", "text_encoders", "t5xxl*.safetensors"),
        ),
        fps=24,
        frame_step=8,
        max_seconds=10,
        side_multiple=32,
        max_side=1280,
        supported=True,
        workflow_engine="art_v2.video_engine",
        unsupported="",
        notes="LTX-Video 0.9.5 with high-temporal diffusion at 24fps",
    ),
    ModelSpec(
        id="ltx-video-0.9.5",
        label="LTX-Video 0.9.5 High Temporal",
        family="ltx",
        modes=("T2V", "I2V"),
        needs=(Need("checkpoint", "checkpoints", "ltx-0.9.5*.safetensors"),),
        fps=24,
        frame_step=8,
        max_seconds=12,
        side_multiple=32,
        max_side=1280,
        supported=True,
        workflow_engine="art_v2.video_engine",
        unsupported="",
        notes="Alias for LTX-Video 0.9.5 release",
    ),
)

BY_ID: dict[str, ModelSpec] = {s.id: s for s in SPECS}


def list_models() -> list[ModelSpec]:
    """Returns all models in the catalogue."""
    return list(SPECS)


def get_model(model_id: str) -> Optional[ModelSpec]:
    """Retrieves a model specification by ID."""
    return BY_ID.get(model_id)


def is_supported(model_id: str) -> bool:
    """Returns True if model is supported by art_v2.video_engine."""
    spec = BY_ID.get(model_id)
    return spec is not None and spec.supported and spec.unsupported == ""


def resolve_pipeline(model_id: str) -> Any:
    """Instantiates the appropriate art_v2.video_engine pipeline for a model."""
    spec = get_model(model_id)
    if spec is None:
        raise KeyError(f"Unknown model in catalog: {model_id}")

    if spec.family == "ltx":
        return LTXVideoPipeline()
    elif spec.id in ("wan2.2-t2v-vace-a14b", "wan2.2-animate-14b"):
        return Wan22T2VVACEPipeline(use_distilled_lora=True)
    elif spec.family == "wan":
        variant = "14b" if "14b" in spec.id.lower() else "1.3b"
        return Wan21VACEPipeline(variant=variant, enable_teacache=True)
    raise ValueError(f"No pipeline binding for model family: {spec.family}")


def load_catalog() -> dict[str, Any]:
    """Returns dictionary format of the catalog."""
    return {
        "engine": "art_v2.video_engine",
        "models": [
            {
                "id": s.id,
                "label": s.label,
                "family": s.family,
                "modes": list(s.modes),
                "fps": s.fps,
                "supported": s.supported,
                "workflow_engine": s.workflow_engine,
                "unsupported": s.unsupported,
                "notes": s.notes,
            }
            for s in SPECS
        ],
    }
