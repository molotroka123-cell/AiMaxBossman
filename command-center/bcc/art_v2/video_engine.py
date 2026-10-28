"""Video Studio 2.0 engine: HONEST STATE (audit 28.10).

This module used to claim «Real Generation Pipelines & 7/7 Quality Gates». It contained no generator: every pipeline `run()`
returned a dict with status COMPLETED, a hash of the prompt and constants like `vram_allocated_gb: 11.8`, and
`run_video_studio_v2_all_7_gates` returned hard-coded «measured» numbers (ArcFace 0.842, LPIPS 0.138, sync 0.995, flow 0.961)
and PASS. Nothing was generated or measured.

What it is now:
  * the pipeline classes keep their names and parameters (the catalog describes them) but `run()` raises PipelineNotWired:
    no video generator is connected to art_v2 (real video paths today: ComfyUI workflows in bcc.direct_gen, WanGP, SCAIL-2 via
    the launcher outside git, local_genjutsu);
  * `run_video_studio_v2_all_7_gates` reports every gate as NOT passed with the last REAL observation and its source
    (docs/audits/2026-10-27-video-studio-2/STATUS_RU.md: face ArcFace median 0.516 FAIL, clothes T3 0.284 FAIL, the other five
    never measured on a finished video). `overall_pass` is False until real measurements replace these records.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

#: True only when a real video generator is connected to this package. It is not.
WIRED = False
STATUS_SOURCE = "docs/audits/2026-10-27-video-studio-2/STATUS_RU.md"
NOT_WIRED_MESSAGE = ("art_v2 has no video generator connected: this pipeline is only described here, it was not run "
                     "(real paths: ComfyUI workflows in bcc.direct_gen, WanGP, SCAIL-2 launcher, local_genjutsu)")


class PipelineNotWired(NotImplementedError):
    """The pipeline exists as a description only; calling it must never look like a generation."""


@dataclass
class QualityGateRecord:
    name: str
    title_ru: str
    passed: bool
    threshold: str
    measured: Any
    margin_ratio: Optional[float]
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityGatesResult:
    project_id: str
    overall_pass: bool
    gates_passed: int
    total_gates: int
    timestamp_utc: str
    engine_version: str
    gates: dict[str, QualityGateRecord]
    provenance_hash: str
    receipt_path: Optional[str] = None


def _parse_resolution(resolution: str) -> Tuple[int, int]:
    w, h = map(int, resolution.split("x"))
    return w, h


class Wan21VACEPipeline:
    """Wan 2.1 I2V / T2V (1.3B / 14B FP8, TeaCache): described, NOT wired."""

    def __init__(self, variant: str = "1.3b", quantization: str = "fp8", enable_teacache: bool = True,
                 teacache_threshold: float = 0.15):
        self.variant = variant
        self.quantization = quantization
        self.enable_teacache = enable_teacache
        self.teacache_threshold = teacache_threshold
        self.supported_modes = ("T2V", "I2V", "VACE")

    def run(self, prompt: str, image_reference: Optional[str] = None, frames: int = 49, fps: int = 16,
            resolution: str = "832x480", steps: int = 30, seed: int = 42) -> dict[str, Any]:
        _parse_resolution(resolution)
        raise PipelineNotWired(NOT_WIRED_MESSAGE)


class Wan22T2VVACEPipeline:
    """Wan 2.2 T2V VACE 14B MoE with a 4-step distilled LoRA: described, NOT wired."""

    def __init__(self, use_distilled_lora: bool = True):
        self.use_distilled_lora = use_distilled_lora
        self.experts = ("low_noise_expert", "high_noise_expert")

    def run(self, prompt: str, frames: int = 81, fps: int = 16, resolution: str = "1280x720", steps: int = 4,
            seed: int = 42) -> dict[str, Any]:
        _parse_resolution(resolution)
        raise PipelineNotWired(NOT_WIRED_MESSAGE)


class LTXVideoPipeline:
    """LTX-Video 0.9.5 at native 24 fps: described, NOT wired."""

    def __init__(self):
        self.native_fps = 24

    def run(self, prompt: str, frames: int = 97, resolution: str = "1280x720", steps: int = 25,
            seed: int = 42) -> dict[str, Any]:
        _parse_resolution(resolution)
        raise PipelineNotWired(NOT_WIRED_MESSAGE)


class LivePortraitLipSync:
    """Audio-driven lip articulation: described, NOT wired (no sync was ever measured)."""

    def process(self, video_or_face_image: str, audio_path: str, separate_audio: bool = True) -> dict[str, Any]:
        raise PipelineNotWired(NOT_WIRED_MESSAGE)


class RIFEInterpolator:
    """RIFE frame interpolation 24 -> 60 fps: described, NOT wired."""

    def interpolate_24_to_60(self, input_fps: int = 24, target_fps: int = 60, enable_deflicker: bool = True) -> dict[str, Any]:
        raise PipelineNotWired(NOT_WIRED_MESSAGE)


#: The seven gates with the owner's thresholds (fixed before any run) and the LAST REAL observation of each.
GATES: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("face_identity", "Идентичность лица (ArcFace)", "ArcFace mean >= 0.70, min >= 0.59",
     {"status": "FAIL", "observed": "ArcFace median 0.516, min 0.196, p10 0.31",
      "source": "docs/audits/2026-10-26-genjutsu-local-head-compose/receipt.json"}),
    ("clothes_body", "Одежда и тело", "T3 v2 <= 0.276 на живом клипе",
     {"status": "FAIL", "observed": "T3 0.284 (VACE 1.3B)", "source": "ТЗ V2, раздел 2"}),
    ("hair_contour", "Контур волос", "GT IoU >= 0.90, F1 >= 0.80",
     {"status": "NOT_MEASURED", "observed": "на старом пайплайне не замерялось", "source": STATUS_SOURCE}),
    ("mimic_lipsync", "Мимика и губы", "blendshape-косинус >= 0.99",
     {"status": "NOT_MEASURED", "observed": "пороги записаны, замер не выполнен", "source": STATUS_SOURCE}),
    ("backgrounds_7_of_7", "Фоны 7 из 7", "7/7 проверок, SSIM фона вне маски",
     {"status": "NOT_MEASURED", "observed": "SSIM вне маски 0.984 на рабочей квитанции 26.10, 7/7 не прогонялось",
      "source": STATUS_SOURCE}),
    ("sharpness_laplacian", "Резкость", ">= 1.5x от v1",
     {"status": "NOT_MEASURED", "observed": "Лапласиан лица 12.1 (Higgsfield 9.1), сравнение с порогом не выполнено",
      "source": STATUS_SOURCE}),
    ("temporal_deflicker", "Мерцание", "не хуже +10%",
     {"status": "NOT_MEASURED", "observed": "1.725 против 1.514 у Higgsfield", "source": STATUS_SOURCE}),
)


def run_video_studio_v2_all_7_gates(project_id: str, output_dir: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """Report the seven gates as they REALLY stand: none passed, nothing measured here. Replace the records with real
    measurements of a finished video before this can ever say overall_pass."""
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    gates: dict[str, QualityGateRecord] = {}
    for name, title, threshold, last in GATES:
        gates[name] = QualityGateRecord(name=name, title_ru=title, passed=False, threshold=threshold, measured=None,
                                        margin_ratio=None, details={"last_observed": last, "measured_here": False})
    data = {"project_id": project_id, "timestamp_utc": timestamp, "wired": WIRED,
            "gates": {k: asdict(v) for k, v in gates.items()}}
    result = {
        "project_id": project_id,
        "overall_pass": False,
        "status": "NOT_MEASURED",
        "wired": WIRED,
        "gates_passed": 0,
        "total_gates": len(gates),
        "timestamp_utc": timestamp,
        "engine_version": "VideoStudio-v2.0-NOT-WIRED",
        "gates": data["gates"],
        "provenance_hash": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
    }
    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        receipt_file = out_path / "receipt.json"
        receipt_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        result["receipt_path"] = str(receipt_file)
    return result
