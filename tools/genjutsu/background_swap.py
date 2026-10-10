"""Stage 4 background swap: keep the person (hair, edges, motion) and replace everything behind them, locally.

Thresholds: docs/owner/VIDEO_PIPELINE_STAGES_20261010.md, section "Stage 4 — background swap" (metric s4-v1, committed
before this tool ran). Measured by tools/video_gate/background_gate.py.

  1. source frames a..b at native size (PNG), source rate kept;
  2. RobustVideoMatting ONNX (GPL-3.0, github.com/PeterL1n/RobustVideoMatting, rvm_mobilenetv3_fp32.onnx) frame by
     frame WITH its recurrent state r1..r4 -> alpha + foreground F at native size; DirectML when available, else CPU
     (the provider actually used is written to background_swap.json);
  3. optional --subject sam2: YOLOX person box -> SAM2.1-tiny mask (tools/genjutsu/genjutsu.py, CPU) of the largest
     person, dilated by SUBJECT_DILATE x H and feathered; alpha is multiplied by it (other people become background);
  4. alpha quantised to 8 bit (the same alpha the gate reads), composite at native size:
       alpha >= CORE_U8  -> the source pixel exactly (lighting, skin, clothes untouched)
       otherwise         -> alpha*F + (1-alpha)*B   (F is RVM's foreground, cleaned of the old background colour)
  5. master.mp4 (libx264rgb -qp 0, RGB-lossless), source.mp4 (same codec, the untouched source frames) and
     final.mp4 (H.264 crf 10, yuv420p, the source audio cut to the clip).

The new background is --bg IMAGE, --bg DIR (PNG frames) or --bg studio (procedural neutral studio gradient, no web image).
Personal media stays on this PC: nothing here talks to the network.

Run with an interpreter that has numpy, PIL and onnxruntime (DirectML build preferred), e.g. FaceFusion's .venv:
  python tools/genjutsu/background_swap.py --src clip.mp4 --job JOB --first 0 --frames 90 --bg studio [--subject sam2]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

BOSSMAN = Path.home() / "Bossman"
RVM_MODEL = BOSSMAN / "models" / "media" / "rvm" / "rvm_mobilenetv3_fp32.onnx"
CORE_U8 = 250            # ceil(0.98 * 255): alpha at or above this keeps the source pixel (S4-T2 core)
SUBJECT_DILATE = 0.03    # x frame height: room for hair outside the SAM2 mask
DELIVERY_CRF = 10        # as faceswap.DELIVERY_CRF after Gate 0


# ------------------------------------------------------------------ pure parts (unit-tested, no GPU, no network)
def auto_downsample(h: int, w: int) -> float:
    """RVM guidance: the internal low-resolution pass should be ~256-512 px on the long side."""
    return float(min(1.0, 512.0 / max(h, w)))


def studio_background(w: int, h: int) -> np.ndarray:
    """Neutral studio backdrop, deterministic: soft radial light on a grey-blue wall, darker floor towards the bottom.
    RGB uint8. Chosen far from typical indoor backgrounds so leftovers of the old one are measurable."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w * 0.5, h * 0.38
    r = np.sqrt(((x - cx) / (0.75 * w)) ** 2 + ((y - cy) / (0.75 * h)) ** 2)
    light = np.clip(1.0 - 0.55 * r ** 1.6, 0.35, 1.0)
    wall = np.array([118.0, 128.0, 142.0], np.float32)
    floor = np.array([84.0, 88.0, 96.0], np.float32)
    t = np.clip((y - 0.78 * h) / (0.22 * h), 0.0, 1.0)[..., None]
    t = t * t * (3 - 2 * t)                       # smoothstep wall -> floor
    base = wall * (1 - t) + floor * t
    img = base * light[..., None]
    return np.clip(img.round(), 0, 255).astype(np.uint8)


def quantize_alpha(pha: np.ndarray) -> np.ndarray:
    """float alpha (any shape, nominally 0..1) -> uint8 0..255, rounding; the gate reads exactly this."""
    return np.clip(np.round(np.asarray(pha, np.float32) * 255.0), 0, 255).astype(np.uint8)


def composite(src: np.ndarray, fgr: np.ndarray, alpha_u8: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """src/fgr/bg HxWx3 (uint8 or float 0..255), alpha_u8 HxW uint8 -> uint8 HxWx3.
    Core (alpha >= CORE_U8) = source pixel exactly; alpha 0 = background exactly; between = alpha*F + (1-alpha)*B."""
    a = alpha_u8.astype(np.float32)[..., None] / 255.0
    mix = a * np.asarray(fgr, np.float32) + (1.0 - a) * np.asarray(bg, np.float32)
    out = np.clip(mix.round(), 0, 255).astype(np.uint8)
    core = alpha_u8 >= CORE_U8
    out[core] = np.asarray(src)[core]
    return out          # alpha 0 -> mix == B (rounded), so the background there is exact


def subject_gate(mask: np.ndarray, dilate_px: int, feather_px: int) -> np.ndarray:
    """bool HxW subject mask -> float HxW gate in 0..1: 1 on the dilated mask, a soft fall-off of feather_px outside it."""
    from PIL import Image, ImageFilter
    k = max(3, 2 * int(dilate_px) + 1)
    big = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(k))
    if feather_px > 0:
        soft = big.filter(ImageFilter.GaussianBlur(feather_px))
        g = np.maximum(np.asarray(soft, np.float32), np.asarray(big, np.float32)) / 255.0
    else:
        g = np.asarray(big, np.float32) / 255.0
    return g


def rate_seconds(rate: str) -> float:
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1)


# ------------------------------------------------------------------ RVM (ONNX, recurrent)
class Matting:
    def __init__(self, model: Path = RVM_MODEL, device: str = "auto"):
        import onnxruntime as ort
        avail = ort.get_available_providers()
        if device == "cpu":
            providers = ["CPUExecutionProvider"]
        elif device == "dml":
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = [p for p in ("DmlExecutionProvider", "CPUExecutionProvider") if p in avail]
        opts = ort.SessionOptions()
        if providers[0] == "DmlExecutionProvider":   # DirectML wants sequential execution without memory pattern
            opts.enable_mem_pattern = False
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.s = ort.InferenceSession(str(model), sess_options=opts, providers=providers)
        self.providers = self.s.get_providers()
        self.rec = [np.zeros((1, 1, 1, 1), np.float32)] * 4

    def __call__(self, rgb: np.ndarray, ratio: float) -> tuple[np.ndarray, np.ndarray]:
        x = (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        fgr, pha, *self.rec = self.s.run(None, {"src": x, "r1i": self.rec[0], "r2i": self.rec[1], "r3i": self.rec[2],
                                                "r4i": self.rec[3], "downsample_ratio": np.array([ratio], np.float32)})
        return np.clip(fgr[0].transpose(1, 2, 0) * 255.0, 0, 255), np.clip(pha[0, 0], 0, 1)


# ------------------------------------------------------------------ io
def ffmpeg_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    apps = sorted((BOSSMAN / "app").glob(f"BOSSMAN-Windows-x64-*/media/{name}.exe"))
    if not apps:
        raise SystemExit(f"{name} not found")
    return str(apps[-1])


def source_rate(src: Path) -> str:
    r = subprocess.run([ffmpeg_bin("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=r_frame_rate", "-of", "csv=p=0", str(src)], capture_output=True, text=True)
    return r.stdout.strip() or "30/1"


def has_audio(src: Path) -> bool:
    r = subprocess.run([ffmpeg_bin("ffprobe"), "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name",
                        "-of", "csv=p=0", str(src)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def audio_codec(src: Path) -> str | None:
    r = subprocess.run([ffmpeg_bin("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                        "-of", "csv=p=0", str(src)], capture_output=True, text=True)
    return r.stdout.strip() or None


def extract_native(src: Path, out: Path, first: int, n: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-vf",
                    f"select='between(n,{first},{first + n - 1})',setpts=PTS-STARTPTS", "-fps_mode", "passthrough",
                    str(out / "%04d.png")], check=True)


def load_background(spec: str, w: int, h: int, n: int) -> list[np.ndarray]:
    from PIL import Image
    if spec == "studio":
        return [studio_background(w, h)] * n
    p = Path(spec)
    if p.is_dir():
        files = sorted(p.glob("*.png"))
        return [np.asarray(Image.open(files[i % len(files)]).convert("RGB").resize((w, h), Image.LANCZOS)) for i in range(n)]
    return [np.asarray(Image.open(p).convert("RGB").resize((w, h), Image.LANCZOS))] * n


def subject_masks(frames: list[np.ndarray]) -> list[np.ndarray]:
    """Largest YOLOX person per frame -> SAM2.1-tiny mask (genjutsu.py models, CPU). Missing person -> previous mask."""
    from PIL import Image
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import genjutsu as g
    det, seg = g.Detector(), g.Segmenter()
    out, prev = [], None
    for f in frames:
        img = Image.fromarray(f)
        boxes = [b for b in det(img) if b[4] >= 0.5]
        if boxes:
            b = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            prev = seg(img, b[:4])
        out.append(prev if prev is not None else np.zeros(f.shape[:2], bool))
    return out


def encode(job: Path, src: Path, rate: str, first: int, n: int) -> None:
    ff = ffmpeg_bin("ffmpeg")
    for folder, name in (("final", "master.mp4"), ("src", "source.mp4")):
        subprocess.run([ff, "-v", "error", "-y", "-framerate", rate, "-i", str(job / folder / "%04d.png"), "-c:v",
                        "libx264rgb", "-qp", "0", "-preset", "ultrafast", str(job / name)], check=True)
    start = first / rate_seconds(rate)
    dur = n / rate_seconds(rate)
    acodec = ["-c:a", "copy"] if audio_codec(src) in {"aac", "mp3", "alac", "opus"} else ["-c:a", "aac"]
    subprocess.run([ff, "-v", "error", "-y", "-framerate", rate, "-i", str(job / "final" / "%04d.png"), "-ss", f"{start:.6f}",
                    "-i", str(src), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-crf", str(DELIVERY_CRF), "-preset",
                    "slow", "-pix_fmt", "yuv420p", *acodec, "-t", f"{dur:.6f}", str(job / "final.mp4")], check=True)


def main(argv=None) -> int:
    from PIL import Image
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--job", type=Path, required=True)
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--frames", type=int, default=90)
    ap.add_argument("--bg", default="studio", help="'studio', an image, or a folder of PNG frames")
    ap.add_argument("--subject", choices=("none", "sam2"), default="none")
    ap.add_argument("--device", choices=("auto", "dml", "cpu"), default="auto")
    ap.add_argument("--ratio", type=float, default=0.0, help="RVM downsample_ratio; 0 = auto (long side ~512)")
    ap.add_argument("--model", type=Path, default=RVM_MODEL)
    a = ap.parse_args(argv)
    job = a.job
    job.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    extract_native(a.src, job / "src", a.first, a.frames)
    frames = [np.asarray(Image.open(p).convert("RGB")) for p in sorted((job / "src").glob("*.png"))][: a.frames]
    if len(frames) < a.frames:
        raise SystemExit(f"only {len(frames)} frames from {a.src}")
    h, w = frames[0].shape[:2]
    bgs = load_background(a.bg, w, h, len(frames))
    (job / "bg").mkdir(exist_ok=True)
    if a.bg == "studio" or not Path(a.bg).is_dir():
        Image.fromarray(bgs[0]).save(job / "bg" / "background.png")
    else:
        for i, b in enumerate(bgs, 1):
            Image.fromarray(b).save(job / "bg" / f"{i:04d}.png")
    t_sub = time.time()
    gates = None
    if a.subject == "sam2":
        gates = [subject_gate(m, int(SUBJECT_DILATE * h), max(2, int(0.006 * h))) for m in subject_masks(frames)]
    t_sub = time.time() - t_sub
    ratio = a.ratio or auto_downsample(h, w)
    mat = Matting(a.model, a.device)
    for d in ("alpha", "final", "fgr"):
        (job / d).mkdir(exist_ok=True)
    per_frame, t_mat, t_comp = [], 0.0, 0.0
    for i, (f, b) in enumerate(zip(frames, bgs), 1):
        t1 = time.time()
        fgr, pha = mat(f, ratio)
        t_mat += time.time() - t1
        t2 = time.time()
        if gates is not None:
            pha = pha * gates[i - 1]
        alpha = quantize_alpha(pha)
        out = composite(f, fgr, alpha, b)
        Image.fromarray(alpha).save(job / "alpha" / f"{i:04d}.png")
        Image.fromarray(out).save(job / "final" / f"{i:04d}.png")
        t_comp += time.time() - t2
        per_frame.append(round(time.time() - t1, 3))
    rate = source_rate(a.src)
    t3 = time.time()
    encode(job, a.src, rate, a.first, len(frames))
    t_enc = time.time() - t3
    meta = {"src": str(a.src), "first": a.first, "frames": len(frames), "size": [w, h], "rate": rate, "bg": a.bg,
            "subject": a.subject, "model": a.model.name, "downsample_ratio": ratio, "providers": mat.providers,
            "device_requested": a.device, "core_u8": CORE_U8, "subject_dilate": SUBJECT_DILATE,
            "seconds": {"total": round(time.time() - t0, 1), "matting": round(t_mat, 1), "subject_sam2": round(t_sub, 1),
                        "composite_png": round(t_comp, 1), "encode": round(t_enc, 1)},
            "matting_fps": round(len(frames) / t_mat, 2) if t_mat else None, "per_frame_seconds": per_frame,
            "audio_in_source": has_audio(a.src)}
    (job / "background_swap.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in meta.items() if k != "per_frame_seconds"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
