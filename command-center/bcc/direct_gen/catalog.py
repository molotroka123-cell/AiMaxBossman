"""Installed video models: availability from files actually present on disk.

Nothing here is downloaded or assumed. Source/revision/licence/hash are
``UNKNOWN`` unless the owner supplies ``models.json`` (overrides) or a
``<weights>.sha256`` sidecar next to the file.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

UNKNOWN = "UNKNOWN"
RESOLUTION = re.compile(r"^(\d{3,4})x(\d{3,4})$")


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
    modes: tuple[str, ...]          # T2V / I2V / S2V / ANIMATE
    needs: tuple[Need, ...]
    fps: int
    frame_step: int                 # frames must be step*k + 1
    max_seconds: int
    min_side: int = 256
    max_side: int = 1280
    side_multiple: int = 16
    requires_image: bool = False
    requires_audio: bool = False
    unsupported: str = ""           # non-empty: not usable by the Direct window (reason)
    notes: str = ""


WAN_ENC = Need("text_encoder", "text_encoders", "umt5*.safetensors")
SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("wan2.1-t2v-1.3b", "Wan2.1 T2V 1.3B", "wan", ("T2V",),
              (Need("diffusion", "diffusion_models", "wan2.1_t2v_1.3B*.safetensors"), WAN_ENC,
               Need("vae", "vae", "wan_2.1_vae*.safetensors")),
              fps=16, frame_step=4, max_seconds=8, max_side=832),
    ModelSpec("wan2.2-s2v-14b", "Wan2.2 S2V 14B", "wan", ("S2V",),
              (Need("diffusion", "diffusion_models", "wan2.2_s2v_14B*.safetensors"), WAN_ENC,
               Need("vae", "vae", "wan_2.1_vae*.safetensors"),
               Need("audio_encoder", "audio_encoders", "wav2vec2*.safetensors")),
              fps=16, frame_step=4, max_seconds=20, requires_image=True, requires_audio=True,
              notes="speech/song-to-video: needs a reference image and an audio file"),
    ModelSpec("wan2.2-animate-14b", "Wan2.2 Animate 14B", "wan", ("ANIMATE",),
              (Need("diffusion", "diffusion_models", "Wan2_2-Animate-14B*.safetensors"),),
              fps=16, frame_step=4, max_seconds=10,
              unsupported="ANIMATE needs a driving video and pose inputs; the Direct window has no such input yet"),
    ModelSpec("ltx-video", "LTX-Video", "ltx", ("T2V", "I2V"),
              (Need("checkpoint", "checkpoints", "ltx*.safetensors"),
               Need("text_encoder", "text_encoders", "t5xxl*.safetensors")),
              fps=24, frame_step=8, max_seconds=8, side_multiple=32, max_side=1280),
)
BY_ID = {s.id: s for s in SPECS}


def _find(models: Path, need: Need) -> Path | None:
    folder = models / need.folder
    if not folder.is_dir():
        return None
    for p in sorted(folder.iterdir()):
        if p.is_file() and fnmatch.fnmatch(p.name.lower(), need.pattern.lower()) and p.stat().st_size > 0:
            return p
    return None


def _sidecar_hash(path: Path) -> str:
    side = path.with_name(path.name + ".sha256")
    try:
        token = side.read_text(encoding="utf-8").split()[0].lower()
    except (OSError, IndexError):
        return UNKNOWN
    return token if re.fullmatch(r"[0-9a-f]{64}", token) else UNKNOWN


def _overrides(data_dir: Path | None) -> dict:
    if data_dir is None:
        return {}
    try:
        value = json.loads((Path(data_dir) / "models.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def default_models_dir() -> Path:
    env = os.environ.get("BOSSMAN_COMFYUI_MODELS_DIR", "").strip()
    return Path(env) if env else Path.home() / "Bossman" / "media-runtime" / "ComfyUI" / "models"


def template_path(workflows: Path, model_id: str) -> Path:
    return Path(workflows) / f"{model_id}.json"


def describe(spec: ModelSpec, models: Path, workflows: Path, overrides: dict | None = None) -> dict:
    over = (overrides or {}).get(spec.id, {}) if isinstance(overrides, dict) else {}
    weights, missing = [], []
    for need in spec.needs:
        found = _find(Path(models), need)
        if found is None:
            missing.append(f"{need.role} ({need.folder}/{need.pattern})")
            weights.append({"role": need.role, "found": False, "file": None, "size_bytes": None, "sha256": UNKNOWN})
        else:
            weights.append({"role": need.role, "found": True, "file": found.name,
                            "size_bytes": found.stat().st_size, "sha256": _sidecar_hash(found)})
    reasons = []
    if spec.unsupported:
        reasons.append(spec.unsupported)
    if missing:
        reasons.append("weights missing on disk: " + "; ".join(missing))
    if not spec.unsupported and not template_path(workflows, spec.id).is_file():
        reasons.append(f"no verified workflow template installed ({spec.id}.json)")
    main = next((w for w in weights if w["found"]), None)
    return {
        "id": spec.id, "label": spec.label, "family": spec.family, "runtime": "comfyui",
        "modes": list(spec.modes), "available": not reasons, "reason": "; ".join(reasons),
        "weights": weights, "fps": spec.fps, "max_seconds": spec.max_seconds,
        "min_side": spec.min_side, "max_side": spec.max_side, "side_multiple": spec.side_multiple,
        "requires_image": spec.requires_image, "requires_audio": spec.requires_audio,
        "notes": spec.notes,
        "source": str(over.get("source") or UNKNOWN), "revision": str(over.get("revision") or UNKNOWN),
        "license": str(over.get("license") or UNKNOWN),
        "sha256": str(over.get("sha256") or (main["sha256"] if main else UNKNOWN)),
    }


def list_models(models: Path, workflows: Path, data_dir: Path | None = None) -> list[dict]:
    over = _overrides(data_dir)
    return [describe(s, models, workflows, over) for s in SPECS]


def parse_resolution(spec: ModelSpec, value: str) -> tuple[int, int]:
    m = RESOLUTION.fullmatch(str(value))
    if not m:
        raise ValueError("resolution must look like 832x480")
    w, h = int(m.group(1)), int(m.group(2))
    for side in (w, h):
        if not spec.min_side <= side <= spec.max_side or side % spec.side_multiple:
            raise ValueError(f"each side must be {spec.min_side}..{spec.max_side} and a multiple of {spec.side_multiple}")
    return w, h


def frames_for(spec: ModelSpec, seconds: float) -> int:
    raw = max(1, round(seconds * spec.fps))
    k = max(1, round((raw - 1) / spec.frame_step))
    return k * spec.frame_step + 1
