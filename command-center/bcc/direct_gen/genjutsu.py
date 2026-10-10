"""Genjutsu live constructor: region masks (hair / top / bottom / clothes) for live recolour in the browser.

The backend computes masks ONCE per frame; the browser recolours live (canvas, Lab, debounced) — no round trip per
slider move. Two local engines, both run as a child process with FaceFusion's interpreter (its venv already has
onnxruntime-directml + opencv + numpy, so nothing is installed into it and nothing into Bossman's venv):

  segformer   parsing_worker.py — SegFormer-B2 «clothes» (ATR human parsing), soft masks, real top/bottom classes.
              LICENCE: NVIDIA Source Code License for SegFormer §3.3 = NON-COMMERCIAL (research/evaluation) only.
  facefusion  masks_worker.py — u2net_human person + bisenet hair + YCrCb skin; top/bottom = a split at the
              person's middle (APPROXIMATE). Models under FaceFusion's licences (u2net Apache-2.0, bisenet MIT);
              coarser, but usable commercially.

LoRA training is NOT connected: Bossman has no training backend. `describe()` says so; the UI shows «не подключено».
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from pathlib import Path

from . import faceswap

ENGINES = ("segformer", "facefusion")
REGIONS = ("hair", "top", "bottom", "clothes")           # what the constructor edits; workers may write more
EXTRA_REGIONS = ("person", "skin", "dress")
MAX_SIDE = 4096
SEGFORMER_SHA256 = "a93a8dac171b5c1fcc53632a8bfc180bfd9759ea69a3e207451bb07f76add54f"
SEGFORMER_LICENSE = {"id": "NVIDIA-SegFormer-NC", "commercial": False,
                     "text": "NVIDIA Source Code License for SegFormer §3.3: только некоммерческое использование "
                             "(исследование/оценка)",
                     "url": "https://github.com/NVlabs/SegFormer/blob/master/LICENSE"}
FACEFUSION_LICENSE = {"id": "u2net Apache-2.0 + bisenet MIT (через FaceFusion OpenRAIL-AS)", "commercial": True,
                      "text": "модели FaceFusion: u2net_human (Apache-2.0), bisenet face parser (MIT)",
                      "url": "https://github.com/facefusion/facefusion"}
LORA_TRAINING = {"available": False, "status": "не подключено",
                 "reason": "В Bossman нет бэкенда обучения LoRA: набор данных экспортируется ZIP-архивом, "
                           "обучение запускается вне Bossman."}

_sha_cache: dict[tuple[str, int, float], str] = {}


def default_segformer_model() -> Path:
    env = os.environ.get("BOSSMAN_GENJUTSU_PARSER_MODEL", "").strip()
    return Path(env) if env else Path.home() / "Bossman" / "models" / "genjutsu" / "segformer_b2_clothes" / "model.onnx"


def file_sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    if key not in _sha_cache:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        _sha_cache[key] = digest.hexdigest()
    return _sha_cache[key]


def describe(facefusion_home: Path, segformer_model: Path) -> dict:
    """Which engines can run right now, and why not. Never guesses: interpreter, worker and weights must exist."""
    py = faceswap.python_of(facefusion_home)
    ff_ok = (facefusion_home / "facefusion.py").is_file() and py.is_file()
    venv_reason = "" if py.is_file() else f"нет интерпретатора FaceFusion ({py})"
    seg_reason = venv_reason
    if not seg_reason:
        if not segformer_model.is_file():
            seg_reason = f"нет весов SegFormer: {segformer_model}"
        elif file_sha256(segformer_model) != SEGFORMER_SHA256:
            seg_reason = "веса SegFormer не совпадают с проверенной ревизией (sha256)"
    engines = [
        {"id": "segformer", "label": "SegFormer-B2 (точнее; только некоммерческое)", "available": not seg_reason,
         "reason": seg_reason, "license": SEGFORMER_LICENSE, "soft": True, "approximate": []},
        {"id": "facefusion", "label": "FaceFusion u2net + bisenet (грубее; коммерчески допустимо)",
         "available": ff_ok, "reason": "" if ff_ok else f"FaceFusion не установлен в {facefusion_home}",
         "license": FACEFUSION_LICENSE, "soft": False, "approximate": ["top", "bottom"]},
    ]
    default = next((e["id"] for e in engines if e["available"]), None)
    return {"engines": engines, "default_engine": default, "regions": list(REGIONS),
            "lora_training": dict(LORA_TRAINING)}


def worker_argv(engine: str, facefusion_home: Path, segformer_model: Path, image: Path, out_dir: Path,
                provider: str = "directml") -> list[str]:
    py = str(faceswap.python_of(facefusion_home))
    here = Path(__file__).parent
    if engine == "segformer":
        return [py, str(here / "parsing_worker.py"), "--model", str(segformer_model), "--image", str(image),
                "--out", str(out_dir), "--provider", provider]
    if engine == "facefusion":
        return [py, str(here / "masks_worker.py"), str(image), str(out_dir)]
    raise ValueError(f"unknown engine {engine!r}")


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a PNG or baseline/progressive JPEG from its header, without decoding it."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def collect(out_dir: Path, engine: str) -> dict:
    """Worker output -> API payload: each region as PNG base64 + area share. Missing constructor regions = error."""
    info = json.loads((out_dir / "masks.json").read_text(encoding="utf-8"))
    regions = {}
    for name in REGIONS + EXTRA_REGIONS:
        p = out_dir / f"{name}.png"
        if not p.is_file():
            if name in REGIONS:
                raise FileNotFoundError(f"worker wrote no {name}.png")
            continue
        regions[name] = {"png_b64": base64.b64encode(p.read_bytes()).decode(),
                         "area": (info.get("area") or {}).get(name)}
    seconds = info.get("seconds")
    return {"engine": engine, "size": info.get("size"), "regions": regions, "soft": bool(info.get("soft")),
            "approximate": info.get("approximate") or [], "worker_seconds": seconds,
            "providers": info.get("providers"), "passes": info.get("passes"),
            "license": SEGFORMER_LICENSE if engine == "segformer" else FACEFUSION_LICENSE,
            "source": info.get("source") or info.get("models")}
