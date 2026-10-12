"""Local, free, honest photo upscaling for Bossman.

    python -m photo_upscale.upscale --input X.jpg --out Y.png [--longest-edge 8192] [--model auto]
                                    [--passes 1|2|3] [--faces] [--download-models] [--dry-run]

(run with cwd = ``tools``, like ``exchange_volume``).

What this is -- and is not
--------------------------
* Neural super-resolution (Real-ESRGAN / HAT / Swin2SR ONNX models, executed by FaceFusion's ONNX Runtime
  DirectML stack on the local GPU) gives a *reconstruction*, not captured detail.  Everything added beyond the source's
  native resolution is inferred.  The sidecar says so in plain words and the tool never labels the exported size as
  native detail.
* Nothing leaves the PC: no cloud, no paid API, no upload.  The only network use is the one-off, explicit
  ``--download-models`` fetch of public model weights (weights only; no image is ever sent).
* The source file is never modified.  The output is a lossless PNG plus ``<out>.json``.
* Large images are processed in overlapping tiles blended with a feathered window (no hard seams); every step runs a
  decode / back-projection check and falls back to plain Lanczos for that step when the check fails.
* ``--faces`` additionally runs a FaceFusion face enhancer (generative restoration prior) on detected faces and is
  labelled as such in the sidecar.

Exit codes: 0 ok (also when it fell back to Lanczos -- the output and the sidecar then say
``fallback: no neural model``); 1 runtime error; 2 request refused (safe limit, bad arguments, would overwrite);
3 ``--require-neural`` was given but no neural model could be used.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageOps

TOOL_NAME = "photo_upscale"
TOOL_VERSION = "1.0"

# ----------------------------------------------------------------------------------------------------------------
# declared limits (set from the 2026-10-12 benchmark, see README.md)
# ----------------------------------------------------------------------------------------------------------------
SAFE_MAX_FACTOR = 4.0            # measured range; anything above is unproven and refused
MAX_OUTPUT_PIXELS = 150_000_000  # ~12k x 12k; memory guard
DEFAULT_LONGEST_EDGE_CAP = 8192
CONSISTENCY_MIN_DB = 24.0        # a step whose area-downscaled output is farther than this from its input is rejected
DEFAULT_TILE = 256
DEFAULT_OVERLAP = 32
BACKPROJECTION_ITERS = 2
DEFAULT_DETAIL = 0.5             # fallback detail strength (0 = Lanczos + projection, 1 = full model output)
# fidelity-optimal detail strength per model, measured on the benchmark corpus (swin2: PSNR 26.40 / SSIM 0.803 at 0.6)
DETAIL_DEFAULTS = {"swin2_sr_x4": 0.6, "face_dat_x4": 0.8, "siax_x4": 0.5, "nomos8k_sc_x4": 0.5, "real_esrgan_x4": 0.5}

HONESTY_EN = (
    "Detail added beyond the source's native resolution is reconstructed (inferred by a neural network or "
    "interpolated), not captured by the camera; the exported resolution is not native detail."
)
HONESTY_RU = (
    "Детали сверх исходного (нативного) разрешения восстановлены моделью (достроены/интерполированы), а не "
    "сняты камерой; экспортированное разрешение не равно нативной детализации."
)
FACE_NOTE = (
    "Face enhancement is generative restoration: skin texture, eyes and teeth are re-drawn by a model prior and "
    "may differ from the real person. Do not use as evidence of appearance."
)

# ----------------------------------------------------------------------------------------------------------------
# paths
# ----------------------------------------------------------------------------------------------------------------
# Environment overrides, first one set wins: the tool-specific name, then the Bossman-wide one.
FACEFUSION_DIR_ENV = ("PHOTO_UPSCALE_FACEFUSION_DIR", "BOSSMAN_FACEFUSION_DIR")


def default_facefusion_dir() -> Path:
    """``<Bossman root>/apps-local/facefusion`` for the current user (no machine-specific path in the source)."""
    return Path.home() / "Bossman" / "apps-local" / "facefusion"


def facefusion_dir() -> Path:
    """FaceFusion checkout: env override, else ``apps-local/facefusion`` beside this repo, else the home default.

    "Beside this repo" means the first ancestor of the repository checkout that has an
    ``apps-local/facefusion`` directory: the checkout's parent, or the Bossman root when the
    checkout is a nested worktree.
    """
    for name in FACEFUSION_DIR_ENV:
        env = os.environ.get(name)
        if env:
            return Path(env)
    here = Path(__file__).resolve()  # <repo>/tools/photo_upscale/upscale.py -> parents[2] is <repo>
    for ancestor in here.parents[3:]:
        cand = ancestor / "apps-local" / "facefusion"
        if cand.exists():
            return cand
    return default_facefusion_dir()


def facefusion_python(ff: Optional[Path] = None) -> Optional[Path]:
    ff = ff or facefusion_dir()
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        p = ff / rel
        if p.exists():
            return p
    return None


def tools_dir() -> Path:
    return Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------------------------------------------------
# model registry (facts from FaceFusion 3.9.1 model metadata + upstream licences; sha256 is recorded at run time)
# ----------------------------------------------------------------------------------------------------------------
GH = "https://github.com/facefusion/facefusion-assets/releases/download/"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    scale: int
    tile: int          # model input tile (px); also the fixed size for fixed-input graphs
    fixed: bool        # graph has a fixed input size
    license: str
    vendor: str
    commercial_ok: bool
    release: str       # FaceFusion asset release tag
    note: str = ""

    @property
    def filename(self) -> str:
        return self.name + ".onnx"

    @property
    def url(self) -> str:
        return f"{GH}{self.release}/{self.filename}"

    @property
    def hash_url(self) -> str:
        return f"{GH}{self.release}/{self.name}.hash"


def _m(name, scale, tile, license_, vendor, commercial, release, fixed=False, note=""):
    return ModelSpec(name, scale, tile, fixed, license_, vendor, commercial, release, note)


FRAME_MODELS: Dict[str, ModelSpec] = {
    s.name: s
    for s in [
        _m("real_esrgan_x2", 2, 256, "BSD-3-Clause", "xinntao", True, "models-3.0.0"),
        _m("real_esrgan_x2_fp16", 2, 256, "BSD-3-Clause", "xinntao", True, "models-3.0.0"),
        _m("real_esrgan_x4", 4, 256, "BSD-3-Clause", "xinntao", True, "models-3.0.0"),
        _m("real_esrgan_x4_fp16", 4, 256, "BSD-3-Clause", "xinntao", True, "models-3.0.0"),
        _m("real_esrgan_x8", 8, 256, "BSD-3-Clause", "xinntao", True, "models-3.0.0"),
        _m("real_esrgan_x8_fp16", 8, 256, "BSD-3-Clause", "xinntao", True, "models-3.0.0"),
        _m("real_hatgan_x4", 4, 256, "Apache-2.0", "XPixelGroup", True, "models-3.0.0", fixed=True),
        _m("swin2_sr_x4", 4, 128, "Apache-2.0", "mv-lab", True, "models-3.1.0"),
        _m("nomos8k_sc_x4", 4, 128, "CC-BY-4.0", "Phhofm", True, "models-3.0.0", note="attribution required"),
        _m("face_dat_x4", 4, 128, "CC-BY-4.0", "Helaman", True, "models-3.5.0", note="attribution required"),
        _m("real_web_photo_x4", 4, 64, "CC-BY-4.0", "Helaman", True, "models-3.1.0", fixed=True, note="attribution required"),
        _m("realistic_rescaler_x4", 4, 128, "WTFPL", "Mutin Choler", True, "models-3.1.0"),
        _m("siax_x4", 4, 128, "WTFPL", "NMKD", True, "models-3.1.0"),
        _m("clear_reality_x4", 4, 128, "Non-Commercial", "Kim2091", False, "models-3.0.0"),
        _m("remacri_x4", 4, 128, "Non-Commercial", "FoolhardyVEVO", False, "models-3.1.0"),
        _m("span_kendata_x4", 4, 128, "Non-Commercial", "terrainer", False, "models-3.0.0"),
        _m("ultra_sharp_x4", 4, 128, "Non-Commercial", "Kim2091", False, "models-3.0.0"),
        _m("ultra_sharp_2_x4", 4, 256, "Non-Commercial", "Kim2091", False, "models-3.3.0"),
        _m("tghq_face_x8", 8, 128, "GPL-3.0", "TorrentGuy", False, "models-3.5.0", note="copyleft, face-trained"),
    ]
}

FACE_MODELS: Dict[str, ModelSpec] = {
    s.name: s
    for s in [
        _m("gfpgan_1.4", 1, 512, "Apache-2.0", "TencentARC", True, "models-3.0.0", note="trained on FFHQ (CC BY-NC-SA 2.0 images)"),
        _m("gfpgan_1.3", 1, 512, "Apache-2.0", "TencentARC", True, "models-3.0.0", note="trained on FFHQ (CC BY-NC-SA 2.0 images)"),
        _m("restoreformer_plus_plus", 1, 512, "Apache-2.0", "wzhouxiff", True, "models-3.0.0", note="trained on FFHQ"),
        _m("codeformer", 1, 512, "S-Lab-1.0", "sczhou", False, "models-3.0.0", note="non-commercial"),
        _m("gpen_bfr_256", 1, 256, "Non-Commercial", "yangxy", False, "models-3.0.0"),
        _m("gpen_bfr_512", 1, 512, "Non-Commercial", "yangxy", False, "models-3.0.0"),
    ]
}

# Preference order for ``--model auto`` (first model that is present locally and licence-clean wins).
# Filled from the 2026-10-12 benchmark (README.md has the table).
# Order = measured fidelity (PSNR/SSIM/GMSD after back-projection, 2026-10-12, 12 GT images, x4 area LR) among the
# licence-clean models.  Swin2SR is the faithful default; the others are only used when it is not on disk.
AUTO_FAITHFUL: Tuple[str, ...] = ("swin2_sr_x4", "face_dat_x4", "siax_x4", "nomos8k_sc_x4", "real_esrgan_x4")
# Faces: NOT a ranking (one portrait measured, see README). gfpgan_1.4 at blend 60 was the best licence-clean
# enhancer there; every enhancer still lowered fidelity vs the frame enhancer alone, so --faces is opt-in.
AUTO_FACES: Tuple[str, ...] = ("gfpgan_1.4",)
DEFAULT_FACE_BLEND = 60


def model_path(spec: ModelSpec, ff: Optional[Path] = None) -> Path:
    return (ff or facefusion_dir()) / ".assets" / "models" / spec.filename


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_model(spec: ModelSpec, ff: Optional[Path] = None, log: Callable[[str], None] = print) -> Path:
    """Fetch a public weight file + FaceFusion's CRC32 .hash and verify it.  Weights only; sends no user data."""
    import urllib.request

    dst = model_path(spec, ff)
    dst.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading model weights {spec.name} from {spec.url} (weights only, no image is sent)")
    tmp = dst.with_suffix(".part")
    req = urllib.request.Request(spec.url, headers={"User-Agent": "bossman-photo-upscale/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    hreq = urllib.request.Request(spec.hash_url, headers={"User-Agent": "bossman-photo-upscale/1.0"})
    want = urllib.request.urlopen(hreq, timeout=60).read().decode().strip()
    got = format(zlib.crc32(tmp.read_bytes()), "08x")
    if want != got:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"crc32 mismatch for {spec.name}: expected {want}, got {got}")
    os.replace(tmp, dst)
    (dst.parent / f"{spec.name}.hash").write_text(want)
    return dst


def model_record(spec: ModelSpec, role: str, ff: Optional[Path] = None) -> dict:
    p = model_path(spec, ff)
    return {
        "name": spec.name,
        "role": role,
        "file": str(p),
        "sha256": sha256_file(p) if p.exists() else None,
        "bytes": p.stat().st_size if p.exists() else None,
        "native_scale": spec.scale,
        "license": spec.license,
        "vendor": spec.vendor,
        "commercial_use_ok": spec.commercial_ok,
        "license_note": spec.note,
        "source_url": spec.url,
    }


# ----------------------------------------------------------------------------------------------------------------
# image io
# ----------------------------------------------------------------------------------------------------------------
class Refusal(Exception):
    """The request is refused (exit code 2)."""


@dataclass
class Source:
    rgb: np.ndarray                    # HxWx3 uint8
    alpha: Optional[np.ndarray]        # HxW uint8 or None
    icc: Optional[bytes]
    info: dict


def load_source(path: Path) -> Source:
    with Image.open(path) as im:
        im.load()
        fmt, mode, size = im.format, im.mode, im.size
        exif_orient = None
        try:
            exif_orient = im.getexif().get(0x0112)
        except Exception:
            pass
        icc = im.info.get("icc_profile")
        im = ImageOps.exif_transpose(im)
        notes: List[str] = []
        alpha = None
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            alpha = np.asarray(rgba.getchannel("A")).copy()
            rgb = np.asarray(rgba.convert("RGB")).copy()
        elif im.mode in ("I;16", "I;16L", "I;16B", "I", "F"):
            a = np.asarray(im).astype(np.float64)
            top = 65535.0 if im.mode.startswith("I;16") else max(float(a.max()), 1.0)
            g = np.clip(a / top * 255.0, 0, 255).astype(np.uint8)
            rgb = np.stack([g] * 3, axis=-1)
            notes.append(f"{im.mode} source reduced to 8 bit")
        else:
            rgb = np.asarray(im.convert("RGB")).copy()
    return Source(rgb, alpha, icc, {"format": fmt, "mode": mode, "exif_orientation": exif_orient, "notes": notes})


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def pixel_hash(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


# ----------------------------------------------------------------------------------------------------------------
# resampling helpers
# ----------------------------------------------------------------------------------------------------------------
def lanczos_resize(rgb: np.ndarray, size: Tuple[int, int]) -> np.ndarray:
    return np.asarray(Image.fromarray(rgb).resize(size, Image.LANCZOS))


def box_reduce(rgb: np.ndarray, factor: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    return np.asarray(Image.fromarray(rgb).resize((w // factor, h // factor), Image.BOX))


def _reflect_pad(a: np.ndarray, top: int, bottom: int, left: int, right: int) -> np.ndarray:
    """Reflect padding that tolerates pads larger than the image (repeats the reflection)."""
    for _ in range(64):
        if not (top or bottom or left or right):
            return a
        h, w = a.shape[:2]
        t, b = min(top, h - 1), min(bottom, h - 1)
        l, r = min(left, w - 1), min(right, w - 1)
        if h == 1 or w == 1:
            return np.pad(a, ((top, bottom), (left, right), (0, 0)), mode="edge")
        a = np.pad(a, ((t, b), (l, r), (0, 0)), mode="reflect")
        top, bottom, left, right = top - t, bottom - b, left - l, right - r
    return np.pad(a, ((top, bottom), (left, right), (0, 0)), mode="edge")


# ----------------------------------------------------------------------------------------------------------------
# tiled engine (model independent: ``infer`` maps float32 NCHW [1,3,t,t] -> [1,3,t*s,t*s])
# ----------------------------------------------------------------------------------------------------------------
InferFn = Callable[[np.ndarray], np.ndarray]


def axis_starts(n: int, tile: int, stride: int) -> List[int]:
    if n <= tile:
        return [0]
    starts = list(range(0, n - tile + 1, stride))
    if starts[-1] + tile < n:
        starts.append(n - tile)
    return starts


def _ramp(n: int, ov: int, left: bool, right: bool) -> np.ndarray:
    w = np.ones(n, np.float32)
    if ov > 0:
        t = (np.arange(ov, dtype=np.float32) + 0.5) / ov
        t = np.sin(0.5 * np.pi * t) ** 2  # smooth, rises 0 -> 1, t + t[::-1] == 1
        if left:
            w[:ov] = t
        if right:
            w[n - ov:] = t[::-1]
    return w


@dataclass
class TileStats:
    tiles: int = 0
    tile: int = 0
    overlap: int = 0
    seam_cols: List[int] = field(default_factory=list)
    seam_rows: List[int] = field(default_factory=list)
    infer_seconds: float = 0.0


def tiled_upscale(
    rgb: np.ndarray,
    infer: InferFn,
    scale: int,
    tile: int = DEFAULT_TILE,
    overlap: int = DEFAULT_OVERLAP,
    fixed_tile: bool = False,
    stats: Optional[TileStats] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> np.ndarray:
    """Upscale ``rgb`` (HxWx3 uint8) by ``scale`` using overlapping tiles and feathered blending."""
    h, w = rgb.shape[:2]
    pad = max(overlap // 2, 4)
    padded = _reflect_pad(rgb, pad, pad, pad, pad)
    ph, pw = padded.shape[:2]
    t = tile
    if not fixed_tile and max(ph, pw) < tile:  # tiny image: one smaller tile instead of wasted padding
        t = int(math.ceil(max(ph, pw) / 16.0) * 16)
    if ph < t or pw < t:
        padded = _reflect_pad(padded, 0, max(0, t - ph), 0, max(0, t - pw))
        ph, pw = padded.shape[:2]
    ov = min(overlap, t // 2)
    stride = t - ov
    ys, xs = axis_starts(ph, t, stride), axis_starts(pw, t, stride)
    S = scale
    acc = np.zeros((ph * S, pw * S, 3), np.float32)
    wsum = np.zeros((ph * S, pw * S), np.float32)
    src = padded.astype(np.float32) / 255.0
    total = len(ys) * len(xs)
    n = 0
    t_inf = 0.0
    for yi, y0 in enumerate(ys):
        wy = _ramp(t * S, ov * S, yi > 0, yi < len(ys) - 1)
        for xi, x0 in enumerate(xs):
            wx = _ramp(t * S, ov * S, xi > 0, xi < len(xs) - 1)
            chunk = src[y0:y0 + t, x0:x0 + t, :].transpose(2, 0, 1)[None]
            t0 = time.perf_counter()
            res = infer(np.ascontiguousarray(chunk))
            t_inf += time.perf_counter() - t0
            res = np.asarray(res)[0].transpose(1, 2, 0)
            if res.shape[0] != t * S or res.shape[1] != t * S:
                raise RuntimeError(f"model returned {res.shape} for a {t}x{t} tile at scale {S}")
            wgt = wy[:, None] * wx[None, :]
            acc[y0 * S:(y0 + t) * S, x0 * S:(x0 + t) * S, :] += np.clip(res, 0.0, 1.0) * wgt[:, :, None]
            wsum[y0 * S:(y0 + t) * S, x0 * S:(x0 + t) * S] += wgt
            n += 1
            if progress:
                progress(n, total)
    acc /= wsum[:, :, None]
    out = np.clip(np.rint(acc * 255.0), 0, 255).astype(np.uint8)
    out = out[pad * S:(pad + h) * S, pad * S:(pad + w) * S, :]
    if stats is not None:
        stats.tiles, stats.tile, stats.overlap = total, t, ov
        stats.infer_seconds = t_inf
        # seam positions in *cropped output* coordinates: centre of each overlap band between neighbouring tiles
        stats.seam_cols = [int((x0 + ov / 2.0) * S - pad * S) for x0 in xs[1:] if 0 < (x0 + ov / 2.0) * S - pad * S < w * S]
        stats.seam_rows = [int((y0 + ov / 2.0) * S - pad * S) for y0 in ys[1:] if 0 < (y0 + ov / 2.0) * S - pad * S < h * S]
    return out


# ----------------------------------------------------------------------------------------------------------------
# ONNX Runtime wrapper (FaceFusion's runtime: onnxruntime-directml in <facefusion>/.venv)
# ----------------------------------------------------------------------------------------------------------------
def onnxruntime_available() -> bool:
    try:
        import onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def onnxruntime_has_dml() -> bool:
    try:
        import onnxruntime as ort

        return "DmlExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


class OnnxRunner:
    def __init__(self, path: Path, device: str = "auto", device_id: int = 0):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.enable_mem_pattern = False  # DirectML requirement
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        avail = ort.get_available_providers()
        order: List = []
        if device in ("auto", "directml") and "DmlExecutionProvider" in avail:
            order.append(("DmlExecutionProvider", {"device_id": device_id}))
        if device == "directml" and not order:
            raise RuntimeError("DmlExecutionProvider not available in this onnxruntime build")
        order.append("CPUExecutionProvider")
        self.session = ort.InferenceSession(str(path), so, providers=order)
        self.input_name = self.session.get_inputs()[0].name
        self.providers = self.session.get_providers()
        self.ort_version = ort.__version__

    @property
    def execution_provider(self) -> str:
        return self.providers[0] if self.providers else "unknown"

    def __call__(self, x: np.ndarray) -> np.ndarray:
        return self.session.run(None, {self.input_name: x})[0]


# ----------------------------------------------------------------------------------------------------------------
# request planning (refusals happen here, before any heavy work)
# ----------------------------------------------------------------------------------------------------------------
@dataclass
class Plan:
    src_size: Tuple[int, int]
    target_size: Tuple[int, int]
    factor: float
    model: Optional[str]
    steps: List[int]            # per network application: output factor of that step (2, 4 or 8)
    neural_reach: int           # product of steps
    explicit_passes: bool
    longest_edge: int
    notes: List[str] = field(default_factory=list)


def target_size_for(src: Tuple[int, int], longest_edge: int) -> Tuple[int, int]:
    w, h = src
    f = longest_edge / float(max(w, h))
    if w >= h:
        return longest_edge, max(1, int(round(h * f)))
    return max(1, int(round(w * f))), longest_edge


def plan_request(
    src_size: Tuple[int, int],
    longest_edge: Optional[int],
    model_scale: int,
    passes: Optional[int],
    safe_max_factor: float = SAFE_MAX_FACTOR,
    accept_unproven: bool = False,
) -> Plan:
    w, h = src_size
    longest_src = max(w, h)
    notes: List[str] = []
    if longest_edge is None:
        longest_edge = int(min(DEFAULT_LONGEST_EDGE_CAP, math.floor(longest_src * safe_max_factor)))
        notes.append(f"--longest-edge not given: using {longest_edge} px (cap {DEFAULT_LONGEST_EDGE_CAP}, "
                     f"safe factor {safe_max_factor:g}x)")
    if longest_edge <= longest_src:
        raise Refusal(f"REFUSED: requested long edge {longest_edge}px is not larger than the source ({longest_src}px); "
                      f"this tool only enlarges (no downscale, no silent copy).")
    factor = longest_edge / float(longest_src)
    if factor > safe_max_factor + 1e-9 and not accept_unproven:
        ok_edge = int(math.floor(longest_src * safe_max_factor))
        raise Refusal(
            f"REFUSED: requested factor {factor:.2f}x ({longest_src}px -> {longest_edge}px) exceeds the declared safe "
            f"limit of {safe_max_factor:g}x. Beyond that the extra pixels are invention, not recovery. "
            f"Largest allowed here: --longest-edge {ok_edge}. (--accept-unproven-factor overrides and is recorded.)"
        )
    if factor > safe_max_factor + 1e-9:
        notes.append(f"WARNING: factor {factor:.2f}x exceeds the safe limit {safe_max_factor:g}x (override accepted)")
    tw, th = target_size_for(src_size, longest_edge)
    if tw * th > MAX_OUTPUT_PIXELS:
        raise Refusal(f"REFUSED: output {tw}x{th} = {tw * th / 1e6:.0f} MP exceeds the {MAX_OUTPUT_PIXELS / 1e6:.0f} MP guard.")
    if passes is None:
        n = max(1, int(math.ceil(math.log(factor, model_scale) - 1e-9))) if model_scale > 1 else 1
        steps = [model_scale] * n
        explicit = False
    else:
        if passes not in (1, 2, 3):
            raise Refusal("REFUSED: --passes must be 1, 2 or 3 (each pass is one 2x step).")
        steps = [2] * passes
        explicit = True
    reach = int(np.prod(steps))
    if reach < factor - 1e-9:
        raise Refusal(f"REFUSED: {len(steps)} pass(es) reach only {reach}x but the request needs {factor:.2f}x; "
                      f"use more --passes or a smaller --longest-edge.")
    return Plan((w, h), (tw, th), factor, None, steps, reach, explicit, longest_edge, notes)


# ----------------------------------------------------------------------------------------------------------------
# core pipeline
# ----------------------------------------------------------------------------------------------------------------
def peak_working_set_mb() -> Optional[float]:
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return pmc.PeakWorkingSetSize / (1024.0 * 1024.0)
            return None
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return None


def consistency_db(src: np.ndarray, up: np.ndarray, step: int) -> float:
    """Back-projection check: area-downscale the enlarged image and compare it to the input (PSNR dB)."""
    down = box_reduce(up, step)
    if down.shape != src.shape:
        return 0.0
    mse = float(np.mean((down.astype(np.float64) - src.astype(np.float64)) ** 2))
    return 99.0 if mse == 0 else 10.0 * math.log10(255.0 * 255.0 / mse)


def backproject(up: np.ndarray, src: np.ndarray, step: int, iters: int = 2) -> np.ndarray:
    """Iterative back-projection (data-consistency projection).

    Forces ``area_downscale(up, step)`` to equal ``src``: the part of the output that the source actually
    constrains is the source; whatever the network added lives only in the band the source cannot see.  This removes
    colour casts / tone drift of GAN models without touching their added fine structure.
    """
    h, w = src.shape[:2]
    if up.shape[0] != h * step or up.shape[1] != w * step:
        return up
    out = up.astype(np.float32)
    ref = src.astype(np.float32)
    for _ in range(iters):
        down = out.reshape(h, step, w, step, 3).mean(axis=(1, 3))
        resid = ref - down
        for c in range(3):
            out[:, :, c] += np.asarray(Image.fromarray(resid[:, :, c]).resize((w * step, h * step), Image.BICUBIC))
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def run_neural_step(
    cur: np.ndarray, runner: Callable[[np.ndarray], np.ndarray], spec: ModelSpec, step: int,
    tile: int, overlap: int, progress: Optional[Callable[[int, int], None]] = None,
    project: bool = True, detail: float = DEFAULT_DETAIL,
) -> Tuple[np.ndarray, dict]:
    """One enhancement application followed by the intermediate decode / back-projection check."""
    t0 = time.perf_counter()
    stats = TileStats()
    t = spec.tile if spec.fixed else tile
    out = tiled_upscale(cur, runner, spec.scale, tile=t, overlap=overlap, fixed_tile=spec.fixed, stats=stats, progress=progress)
    if step < spec.scale:
        out = box_reduce(out, spec.scale // step)
    rec = {
        "model": spec.name,
        "model_scale": spec.scale,
        "step_factor": step,
        "in_px": [cur.shape[1], cur.shape[0]],
        "out_px": [out.shape[1], out.shape[0]],
        "tile": stats.tile,
        "overlap": stats.overlap,
        "tiles": stats.tiles,
        "seconds": round(time.perf_counter() - t0, 3),
        "inference_seconds": round(stats.infer_seconds, 3),
    }
    ok_shape = out.shape[0] == cur.shape[0] * step and out.shape[1] == cur.shape[1] * step
    ok_range = float(out.std()) > 0.0 or float(cur.std()) == 0.0
    db = consistency_db(cur, out, step) if ok_shape else 0.0
    rec["consistency_psnr_db"] = round(db, 2)
    try:
        from . import measure as _measure
        sr = _measure.seam_report(out, stats.seam_cols, stats.seam_rows) if stats.tiles > 1 else None
    except Exception:
        sr = None
    rec["seam_ratio"] = None if sr is None else round(sr["seam"], 4)
    rec["seam_control_ratio"] = None if sr is None else round(sr["control"], 4)
    rec["seam_excess"] = None if sr is None else round(sr["excess"], 4)
    rec["check"] = "ok" if (ok_shape and ok_range and db >= CONSISTENCY_MIN_DB) else "failed"
    if rec["check"] == "ok" and project:
        # data-consistency projection, then optional pull towards the (also projected) Lanczos step
        t1 = time.perf_counter()
        fixed = backproject(out, cur, step, iters=BACKPROJECTION_ITERS)
        if detail < 1.0 - 1e-9:
            base = backproject(lanczos_resize(cur, (cur.shape[1] * step, cur.shape[0] * step)), cur, step, iters=BACKPROJECTION_ITERS)
            fixed = np.clip(np.rint(base.astype(np.float32) + detail * (fixed.astype(np.float32) - base.astype(np.float32))), 0, 255).astype(np.uint8)
            del base
        out = fixed
        rec["backprojection"] = {"applied": True, "iterations": BACKPROJECTION_ITERS, "detail_strength": detail,
                                 "consistency_psnr_db_after": round(consistency_db(cur, out, step), 2),
                                 "seconds": round(time.perf_counter() - t1, 3)}
    else:
        rec["backprojection"] = {"applied": False, "detail_strength": 1.0}
    return out, rec


def _log(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class Result:
    exit_code: int
    out_path: Optional[Path] = None
    sidecar_path: Optional[Path] = None
    sidecar: Optional[dict] = None
    message: str = ""


def resolve_model(name: str, ff: Path, allow_noncommercial: bool) -> Tuple[Optional[ModelSpec], List[str]]:
    """Return (spec or None, notes).  ``auto`` picks the first present, licence-clean model."""
    notes: List[str] = []
    if name == "lanczos":
        return None, ["model=lanczos: classical resampling requested, no neural model"]
    if name == "auto":
        for cand in AUTO_FAITHFUL:
            spec = FRAME_MODELS[cand]
            if not spec.commercial_ok and not allow_noncommercial:
                continue
            if model_path(spec, ff).exists():
                return spec, notes
            notes.append(f"auto: {cand} not present at {model_path(spec, ff)}")
        return None, notes
    if name not in FRAME_MODELS:
        raise Refusal(f"REFUSED: unknown model {name!r}. Known: {', '.join(sorted(FRAME_MODELS))} (or auto, lanczos).")
    spec = FRAME_MODELS[name]
    if not spec.commercial_ok:
        notes.append(f"LICENCE: {spec.name} is {spec.license} (not for commercial use)")
    return spec, notes


def upscale_file(
    input_path: Path,
    out_path: Path,
    longest_edge: Optional[int] = None,
    model: str = "auto",
    passes: Optional[int] = None,
    faces: bool = False,
    face_model: str = "auto",
    face_blend: int = DEFAULT_FACE_BLEND,
    tile: int = DEFAULT_TILE,
    overlap: int = DEFAULT_OVERLAP,
    detail: Optional[float] = None,
    project: bool = True,
    device: str = "auto",
    overwrite: bool = False,
    allow_noncommercial: bool = False,
    accept_unproven: bool = False,
    download_models: bool = False,
    require_neural: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = _log,
) -> Result:
    t_start = time.perf_counter()
    ff = facefusion_dir()
    input_path = Path(input_path)
    out_path = Path(out_path)
    if out_path.suffix.lower() != ".png":
        out_path = out_path.with_name(out_path.name + ".png")
        log(f"note: output is always lossless PNG -> {out_path}")
    sidecar_path = out_path.with_name(out_path.name + ".json")
    try:
        if not input_path.is_file():
            raise Refusal(f"REFUSED: input not found: {input_path}")
        if out_path.resolve() == input_path.resolve() or (out_path.exists() and os.path.samefile(out_path, input_path)):
            raise Refusal("REFUSED: output path is the source file; the source is never overwritten.")
        if sidecar_path.resolve() == input_path.resolve():
            raise Refusal("REFUSED: sidecar path is the source file; the source is never overwritten.")
        if out_path.exists() and not overwrite:
            raise Refusal(f"REFUSED: {out_path} exists; pass --overwrite to replace the previous output.")
        if tile < 64 or tile % 8:
            raise Refusal("REFUSED: --tile must be a multiple of 8 and >= 64.")
        if overlap < 0 or overlap > tile // 2:
            raise Refusal("REFUSED: --overlap must be between 0 and tile/2.")
        if detail is not None and not (0.0 <= detail <= 1.0):
            raise Refusal("REFUSED: --detail must be between 0 and 1.")

        with Image.open(input_path) as probe:
            probe_size = ImageOps.exif_transpose(probe).size
        spec, notes = resolve_model(model, ff, allow_noncommercial)
        scale_for_plan = spec.scale if spec else 2
        plan = plan_request(probe_size, longest_edge, scale_for_plan, passes, accept_unproven=accept_unproven)
        plan.model = spec.name if spec else None
        plan.notes.extend(notes)

        # ---- announce the plan (and any refusal above) BEFORE running anything heavy
        log(f"plan: {plan.src_size[0]}x{plan.src_size[1]} -> {plan.target_size[0]}x{plan.target_size[1]} "
            f"({plan.factor:.2f}x), model={plan.model or 'none'}, steps={plan.steps}, safe limit {SAFE_MAX_FACTOR:g}x")
        for n in plan.notes:
            log(f"note: {n}")
        if dry_run:
            return Result(0, message="dry-run: plan accepted, nothing executed")

        if spec is not None and not model_path(spec, ff).exists():
            if download_models:
                download_model(spec, ff, log)
            else:
                plan.notes.append(f"model {spec.name} is missing at {model_path(spec, ff)}; run with --download-models "
                                  f"to fetch it from {spec.url}")
                spec = None

        eff_detail = detail if detail is not None else DETAIL_DEFAULTS.get(spec.name if spec else "", DEFAULT_DETAIL)
        src = load_source(input_path)
        src_hash = sha256_file(input_path)
        runner = None
        fallback_reason = None
        engine_info: dict = {"name": None}
        if spec is not None:
            if not onnxruntime_available():
                fallback_reason = "onnxruntime is not importable in this interpreter"
                spec = None
            else:
                try:
                    runner = OnnxRunner(model_path(spec, ff), device=device)
                    engine_info = {"name": "onnxruntime", "version": runner.ort_version,
                                   "execution_provider": runner.execution_provider, "providers": runner.providers}
                except Exception as e:  # DML/op/session errors are recorded verbatim, never hidden
                    fallback_reason = f"model session failed: {type(e).__name__}: {e}"
                    spec = None
        elif model != "lanczos":
            fallback_reason = "no neural model available (" + "; ".join(plan.notes[-2:] or ["none present"]) + ")"

        passes_rec: List[dict] = []
        cur = src.rgb
        neural_steps_ok = 0
        warnings: List[str] = list(plan.notes)
        if spec is not None and runner is not None:
            for i, step in enumerate(plan.steps):
                log(f"pass {i + 1}/{len(plan.steps)}: {cur.shape[1]}x{cur.shape[0]} -> x{step} with {spec.name}")
                nxt, rec = run_neural_step(cur, runner, spec, step, tile, overlap, project=project, detail=eff_detail)
                rec["index"] = i + 1
                if rec["check"] != "ok":
                    log(f"  intermediate check FAILED (consistency {rec['consistency_psnr_db']} dB); "
                        f"this step falls back to Lanczos")
                    nxt = lanczos_resize(cur, (cur.shape[1] * step, cur.shape[0] * step))
                    rec["fallback"] = "lanczos"
                    warnings.append(f"pass {i + 1}: neural output failed the back-projection check -> Lanczos for this step")
                else:
                    neural_steps_ok += 1
                passes_rec.append(rec)
                cur = nxt
            if neural_steps_ok == 0:
                fallback_reason = "every neural step failed the intermediate check"
        else:
            t0 = time.perf_counter()
            cur = lanczos_resize(cur, plan.target_size)  # one resample straight to the target, not repeated steps
            passes_rec.append({"index": 1, "model": None, "step_factor": round(plan.factor, 4), "method": "lanczos",
                               "out_px": [cur.shape[1], cur.shape[0]], "seconds": round(time.perf_counter() - t0, 3)})

        # final exact size (a reduction from the neural reach, or the exact Lanczos size when classical)
        neural_reach_px = (cur.shape[1], cur.shape[0])
        if neural_reach_px != plan.target_size:
            cur = lanczos_resize(cur, plan.target_size)
        used_neural = neural_steps_ok > 0
        classical = not used_neural

        face_rec = {"requested": bool(faces), "applied": False}
        face_spec = None
        if faces:
            face_rec.update(_face_stage_prepare(face_model, ff, allow_noncommercial, download_models))
            face_spec = face_rec.pop("_spec", None)

        # ---- measurements on the *pre-face* result
        meas: dict = {}
        try:
            from . import measure as _measure
            base = lanczos_resize(src.rgb, plan.target_size)
            sr = _measure.sharpness_ratio(cur, base)
            meas = {
                "baseline": "lanczos (PIL, a=3) of the source to the same size",
                "sharpness_ratio_vs_lanczos": {"laplacian_variance": round(sr["lap_var_ratio"], 4),
                                               "hf_energy": round(sr["hf_energy_ratio"], 4)},
                "backprojection_psnr_db": {"output": round(_back_db(src.rgb, cur), 2),
                                           "lanczos_baseline": round(_back_db(src.rgb, base), 2)},
                "metric_version": _measure.METRIC_VERSION,
                "measured_stage": "frame enhancement only (before the optional face enhancer)",
            }
            del base
        except Exception as e:  # pragma: no cover
            meas = {"error": f"{type(e).__name__}: {e}"}

        # ---- write the lossless PNG (+ alpha), then face stage works on the written file
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_png(out_path, cur, src, plan.target_size)
        if faces and face_spec is not None:
            face_rec.update(_run_face_enhancer(ff, out_path, face_spec, face_blend, device))
            if face_rec.get("applied"):
                with Image.open(out_path) as im:
                    cur = np.asarray(im.convert("RGB")).copy()
        elif faces and face_spec is None and "reason" not in face_rec:
            face_rec["reason"] = "no face enhancer available"

        # ---- decode check of the final file
        with Image.open(out_path) as im:
            im.load()
            dec = np.asarray(im.convert("RGB"))
        decode_ok = dec.shape[:2] == (plan.target_size[1], plan.target_size[0]) and bool(np.array_equal(dec, cur))
        if not decode_ok:
            raise RuntimeError("decode check failed: written PNG differs from the in-memory result")

        if fallback_reason is None and classical and model == "lanczos":
            fb = {"used": False, "label": None, "reason": "classical resampling was requested explicitly"}
        elif classical:
            fb = {"used": True, "label": "fallback: no neural model", "reason": fallback_reason or "no neural step succeeded"}
        else:
            fb = {"used": False, "label": None, "reason": None}
        if fb["used"]:
            log("WARNING fallback: no neural model -- result is plain Lanczos resampling, not neural reconstruction. "
                f"({fb['reason']})")
            warnings.append("fallback: no neural model")
        elif classical:
            log("note: classical Lanczos resampling (requested).")

        models = []
        if spec is not None and used_neural:
            models.append(model_record(spec, "frame_enhancer", ff))
        if face_rec.get("applied") and face_spec is not None:
            models.append(model_record(face_spec, "face_enhancer", ff))
        if spec is not None and not spec.commercial_ok:
            warnings.append(f"model licence {spec.license}: not for commercial use")
        exported = {"width": plan.target_size[0], "height": plan.target_size[1]}
        side = {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "created_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": {
                "path": str(input_path),
                "sha256": src_hash,
                "bytes": input_path.stat().st_size,
                "width": src.rgb.shape[1], "height": src.rgb.shape[0],
                "format": src.info.get("format"), "mode": src.info.get("mode"),
                "alpha_preserved": src.alpha is not None, "notes": src.info.get("notes", []),
                "unchanged_note": "source file is opened read-only and never written",
            },
            "output": {
                "path": str(out_path), "format": "PNG (lossless)", "sha256": sha256_file(out_path),
                "bytes": out_path.stat().st_size, **exported,
                "decode_check": "ok" if decode_ok else "failed",
            },
            "resolution": {
                "native_px": [src.rgb.shape[1], src.rgb.shape[0]],
                "exported_px": [exported["width"], exported["height"]],
                "upscale_factor": round(plan.factor, 4),
                "neural_reach_factor": plan.neural_reach if used_neural else 1,
                "neural_steps_ok": neural_steps_ok,
                "neural_steps_planned": len(plan.steps) if (spec is not None and runner is not None) else 0,
                "classical_resample": classical or neural_reach_px != plan.target_size,
                "meaning": "native_px is the only resolution that was captured; exported_px is reconstructed",
            },
            "request": {
                "longest_edge": longest_edge, "effective_longest_edge": plan.longest_edge, "model": model,
                "passes": passes, "steps": plan.steps, "faces": faces, "tile": tile, "overlap": overlap,
                "device": device, "safe_max_factor": SAFE_MAX_FACTOR, "accept_unproven_factor": accept_unproven,
                "detail_strength": eff_detail, "detail_strength_requested": detail, "backprojection": project,
            },
            "engine": {**engine_info, "python": sys.version.split()[0], "platform": platform.platform(),
                       "facefusion_dir": str(ff)},
            "models": models,
            "passes": passes_rec,
            "faces": face_rec,
            "measured": meas,
            "fallback": fb,
            "timings": {"total_seconds": round(time.perf_counter() - t_start, 3),
                        "seconds_per_output_megapixel": round((time.perf_counter() - t_start) / max(1e-9, plan.target_size[0] * plan.target_size[1] / 1e6), 3),
                        "peak_working_set_mb": None},
            "honesty": {
                "reconstructed_detail": True,
                "statement_en": HONESTY_EN,
                "statement_ru": HONESTY_RU,
                "faces_generative": bool(face_rec.get("applied")),
                "faces_note": FACE_NOTE if face_rec.get("applied") else None,
            },
            "warnings": warnings,
        }
        pk = peak_working_set_mb()
        side["timings"]["peak_working_set_mb"] = None if pk is None else round(pk, 1)
        sidecar_path.write_text(json.dumps(side, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"wrote {out_path} ({exported['width']}x{exported['height']}) and {sidecar_path.name}")
        if require_neural and classical:
            return Result(3, out_path, sidecar_path, side, "no neural model was used (--require-neural)")
        return Result(0, out_path, sidecar_path, side, "ok")
    except Refusal as r:
        log(str(r))
        return Result(2, message=str(r))
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")
        return Result(1, message=f"{type(e).__name__}: {e}")


def _back_db(src: np.ndarray, up: np.ndarray) -> float:
    """Back-projection PSNR of ``up`` against the source when ``up`` is reduced to the source size (area/Lanczos)."""
    h, w = src.shape[:2]
    down = np.asarray(Image.fromarray(up).resize((w, h), Image.BOX if (up.shape[0] % h == 0 and up.shape[1] % w == 0) else Image.LANCZOS))
    mse = float(np.mean((down.astype(np.float64) - src.astype(np.float64)) ** 2))
    return 99.0 if mse == 0 else 10.0 * math.log10(255.0 * 255.0 / mse)


def _write_png(path: Path, rgb: np.ndarray, src: Source, size: Tuple[int, int]) -> None:
    img = Image.fromarray(rgb)
    if src.alpha is not None:
        a = Image.fromarray(src.alpha).resize(size, Image.LANCZOS)
        img.putalpha(a)
    kw = {"icc_profile": src.icc} if src.icc else {}
    img.save(path, format="PNG", compress_level=6, **kw)


# ----------------------------------------------------------------------------------------------------------------
# faces (FaceFusion CLI, face_enhancer processor)
# ----------------------------------------------------------------------------------------------------------------
def _face_stage_prepare(name: str, ff: Path, allow_noncommercial: bool, download: bool) -> dict:
    chosen: Optional[ModelSpec] = None
    if name == "auto":
        for cand in AUTO_FACES:
            s = FACE_MODELS[cand]
            if s.commercial_ok or allow_noncommercial:
                chosen = s
                if model_path(s, ff).exists():
                    break
    elif name in FACE_MODELS:
        chosen = FACE_MODELS[name]
    else:
        return {"reason": f"unknown face model {name!r}; known: {', '.join(FACE_MODELS)}"}
    if chosen is None:
        return {"reason": "no licence-clean face enhancer"}
    if facefusion_python(ff) is None or not (ff / "facefusion.py").exists():
        return {"reason": f"FaceFusion not found at {ff}"}
    if not model_path(chosen, ff).exists():
        if download:
            download_model(chosen, ff)
        else:
            return {"reason": f"face model {chosen.name} missing at {model_path(chosen, ff)}; use --download-models"}
    return {"_spec": chosen}


def _run_face_enhancer(ff: Path, png: Path, spec: ModelSpec, blend: int, device: str) -> dict:
    py = facefusion_python(ff)
    if py is None:
        return {"applied": False, "reason": "FaceFusion venv python missing"}
    work = Path(tempfile.mkdtemp(prefix="photo_upscale_faces_"))
    tmp_out = work / "faces_out.png"
    cmd = [str(py), "facefusion.py", "headless-run", "-t", str(png), "-o", str(tmp_out),
           "--processors", "face_enhancer", "--face-enhancer-model", spec.name,
           "--face-enhancer-blend", str(int(blend)), "--face-selector-mode", "many",
           "--execution-providers", "cpu" if device == "cpu" else "directml",
           "--execution-thread-count", "1", "--temp-path", str(work / "temp"), "--jobs-path", str(work / "jobs")]
    t0 = time.perf_counter()
    try:
        cp = subprocess.run(cmd, cwd=str(ff), capture_output=True, text=True, timeout=1800)
    except Exception as e:
        return {"applied": False, "reason": f"FaceFusion run failed: {type(e).__name__}: {e}"}
    rec = {"model": spec.name, "blend": int(blend), "seconds": round(time.perf_counter() - t0, 2),
           "engine": "facefusion headless-run face_enhancer", "returncode": cp.returncode}
    if cp.returncode != 0 or not tmp_out.exists():
        rec.update({"applied": False, "reason": "FaceFusion exited with an error", "stderr_tail": (cp.stderr or cp.stdout)[-600:]})
        return rec
    with Image.open(png) as a, Image.open(tmp_out) as b:
        aa, bb = np.asarray(a.convert("RGB")), np.asarray(b.convert("RGB"))
    if aa.shape != bb.shape:
        rec.update({"applied": False, "reason": f"FaceFusion changed the image size {aa.shape}->{bb.shape}; rejected"})
        return rec
    changed = float(np.mean(np.any(aa != bb, axis=2)))
    rec["changed_pixel_fraction"] = round(changed, 5)
    if changed == 0.0:
        rec.update({"applied": False, "reason": "no face detected (output identical to input)"})
        return rec
    bb_img = Image.fromarray(bb)
    with Image.open(png) as orig:
        alpha = orig.getchannel("A") if orig.mode == "RGBA" else None
        icc = orig.info.get("icc_profile")
    if alpha is not None:
        bb_img.putalpha(alpha)
    bb_img.save(png, format="PNG", compress_level=6, **({"icc_profile": icc} if icc else {}))
    rec["applied"] = True
    return rec


# ----------------------------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m photo_upscale.upscale", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="source image (never modified)")
    ap.add_argument("--out", required=True, help="output PNG path (a <out>.json sidecar is written next to it)")
    ap.add_argument("--longest-edge", type=int, default=None,
                    help=f"target long edge in px (default: min({DEFAULT_LONGEST_EDGE_CAP}, {SAFE_MAX_FACTOR:g} x source))")
    ap.add_argument("--model", default="auto", help="auto | lanczos | <frame enhancer name> (default auto)")
    ap.add_argument("--passes", type=int, choices=(1, 2, 3), default=None,
                    help="number of 2x enhancement steps (default: fewest network applications reaching the target)")
    ap.add_argument("--faces", action="store_true", help="also run a FaceFusion face enhancer (generative; labelled)")
    ap.add_argument("--face-model", default="auto")
    ap.add_argument("--face-blend", type=int, default=DEFAULT_FACE_BLEND)
    ap.add_argument("--tile", type=int, default=DEFAULT_TILE)
    ap.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    ap.add_argument("--detail", type=float, default=None,
                    help="detail strength 0..1: 0 = Lanczos (+projection), 1 = full model output "
                         "(default: measured per model, swin2_sr_x4 = 0.6)")
    ap.add_argument("--no-backprojection", action="store_true",
                    help="skip the data-consistency projection (raw model output; measured to drift in colour/tone)")
    ap.add_argument("--device", choices=("auto", "directml", "cpu"), default="auto")
    ap.add_argument("--overwrite", action="store_true", help="allow replacing an existing output (never the source)")
    ap.add_argument("--allow-noncommercial", action="store_true", help="let --model auto consider non-commercial models")
    ap.add_argument("--accept-unproven-factor", action="store_true", help="override the safe-factor refusal (recorded)")
    ap.add_argument("--download-models", action="store_true", help="fetch missing public model weights (no image is sent)")
    ap.add_argument("--require-neural", action="store_true", help="exit 3 instead of 0 when only Lanczos was possible")
    ap.add_argument("--dry-run", action="store_true", help="print the plan / refusal and stop")
    return ap


def _maybe_reexec(args: argparse.Namespace, argv: Sequence[str]) -> Optional[int]:
    """Neural execution needs onnxruntime-directml, which lives in FaceFusion's venv; re-run there if needed."""
    if args.model == "lanczos" or args.dry_run:
        return None
    # a CPU-only onnxruntime in this interpreter is NOT enough: the GPU build lives in FaceFusion's venv
    if onnxruntime_available() and (args.device == "cpu" or onnxruntime_has_dml()):
        return None
    if os.environ.get("PHOTO_UPSCALE_REEXEC") or os.environ.get("PHOTO_UPSCALE_NO_REEXEC"):
        return None
    py = facefusion_python()
    if py is None or Path(sys.executable).resolve() == py.resolve():
        return None
    env = dict(os.environ, PHOTO_UPSCALE_REEXEC="1", PYTHONPATH=str(tools_dir()) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    print(f"note: no DirectML onnxruntime in {sys.executable}; re-running inside FaceFusion venv {py}", flush=True)
    return subprocess.call([str(py), "-m", "photo_upscale.upscale", *argv], cwd=str(tools_dir()), env=env)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    rc = _maybe_reexec(args, argv)
    if rc is not None:
        return rc
    res = upscale_file(
        Path(args.input), Path(args.out), longest_edge=args.longest_edge, model=args.model, passes=args.passes,
        faces=args.faces, face_model=args.face_model, face_blend=args.face_blend, tile=args.tile, overlap=args.overlap,
        detail=args.detail, project=not args.no_backprojection, device=args.device, overwrite=args.overwrite, allow_noncommercial=args.allow_noncommercial,
        accept_unproven=args.accept_unproven_factor, download_models=args.download_models,
        require_neural=args.require_neural, dry_run=args.dry_run,
    )
    return res.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
