"""Stage 2 clothes swap: repaint only the clothes of the person in a clip from one outfit reference, keep everything else.

Unlike `genjutsu.py swap` (which re-frames the clip to 480x832 @ 16 fps), this keeps the SOURCE frames, resolution and
timing, so the Stage 2 gate (docs/owner/VIDEO_PIPELINE_STAGES_20261010.md, thresholds 779cebc7) can compare frame for frame:
  1. source frames a..b at native size (PNG);
  2. clothes mask per frame (PNG, 255 = clothes) from --masks, dilated by DILATE x frame height;
  3. control frames = the source scaled to the generation size with the dilated mask filled grey ("hole");
  4. sd-cli Wan2.1 VACE (Vulkan) with the outfit reference — genjutsu.stage_generate;
  5. composite at native size: outside the dilated mask the source pixel exactly (alpha 0), inside a soft edge and the
     generated clothes colour-matched on a ring around the mask (genjutsu.ring_match);
  6. master.mp4 (libx264rgb -qp 0, RGB-lossless) + final.mp4 (H.264 crf 10, source audio).

Run with Bossman's genjutsu runtime (numpy, PIL, onnxruntime):
  python tools/genjutsu/clothes_swap.py --src clip.mov --masks masks_dir --ref outfit.png --prompt "..." --job JOB
      --first 0 --frames 81 [--model 1.3b] [--width 576 --height 480] [--steps 20]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genjutsu as g  # noqa: E402

DILATE = 0.03


def source_rate(src: Path) -> str:
    r = subprocess.run([g.ffmpeg_bin("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=r_frame_rate", "-of", "csv=p=0", str(src)], capture_output=True, text=True)
    return r.stdout.strip() or "30/1"


def extract_native(src: Path, out: Path, first: int, n: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([g.ffmpeg_bin("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-vf",
                    f"select='between(n,{first},{first + n - 1})',setpts=PTS-STARTPTS", "-fps_mode", "passthrough",
                    str(out / "%04d.png")], check=True)


def dilated(mask: Image.Image, frac: float) -> Image.Image:
    k = max(3, int(frac * mask.size[1]) | 1)
    return mask.convert("L").point(lambda v: 255 if v > 127 else 0).filter(ImageFilter.MaxFilter(k))


def composite_native(src: np.ndarray, gen: np.ndarray, hole: np.ndarray, edge_px: int) -> np.ndarray:
    """alpha = blur(hole) clipped to the hole: exactly 0 outside the dilated mask, soft only inside its border."""
    soft = np.asarray(Image.fromarray(hole.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(edge_px)), np.float32) / 255
    alpha = (soft * hole)[..., None]
    gen = g.ring_match(gen, src, hole)
    out = src.astype(np.float32) * (1 - alpha) + gen.astype(np.float32) * alpha
    out = np.where(alpha > 0, out, src.astype(np.float32))
    return np.clip(out.round(), 0, 255).astype(np.uint8)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--masks", type=Path, required=True, help="folder of PNG clothes masks named 0001.png.. for the frames")
    ap.add_argument("--ref", type=Path, required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--job", type=Path, required=True)
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--frames", type=int, default=81, help="4k+1")
    ap.add_argument("--model", choices=sorted(g.MODELS), default="1.3b")
    ap.add_argument("--width", type=int, default=576)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--cfg", type=float, default=6.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--vace-strength", type=float, default=1.0)
    ap.add_argument("--free-llms", action="store_true")
    ap.add_argument("--prep-only", action="store_true")
    a = ap.parse_args(argv)
    if (a.frames - 1) % 4:
        ap.error("--frames must be 4k+1")
    job = a.job
    job.mkdir(parents=True, exist_ok=True)
    trace = g.Trace(job)
    t0 = time.time()
    extract_native(a.src, job / "src", a.first, a.frames)
    frames = g.load_frames(job / "src")[: a.frames]
    if len(frames) < a.frames:
        raise SystemExit(f"only {len(frames)} frames from {a.src}")
    (job / "ctrl").mkdir(exist_ok=True)
    (job / "hole").mkdir(exist_ok=True)
    for i, img in enumerate(frames, 1):
        hole = dilated(Image.open(a.masks / f"{i:04d}.png"), DILATE)
        hole.save(job / "hole" / f"{i:04d}.png")
        small = img.resize((a.width, a.height), Image.LANCZOS)
        m = hole.resize((a.width, a.height), Image.NEAREST)
        Image.composite(Image.new("RGB", small.size, (127, 127, 127)), small, m).save(job / "ctrl" / f"{i:04d}.png")
    trace("prep", t0, frames=len(frames), native=f"{frames[0].size[0]}x{frames[0].size[1]}", gen=f"{a.width}x{a.height}")
    if a.prep_only:
        return 0
    args = SimpleNamespace(free_llms=a.free_llms, model=a.model, prompt=a.prompt, ref=a.ref, vace_strength=a.vace_strength,
                           width=a.width, height=a.height, frames=a.frames, fps=16, steps=a.steps, cfg=a.cfg, seed=a.seed,
                           fa=True)
    g.stage_generate(job, args, trace)
    t1 = time.time()
    (job / "final").mkdir(exist_ok=True)
    gens = sorted((job / "gen").glob("*.png"))
    edge = max(2, int(0.006 * frames[0].size[1]))
    for i, (img, gp) in enumerate(zip(frames, gens), 1):
        src = np.asarray(img)
        gen = np.asarray(Image.open(gp).convert("RGB").resize(img.size, Image.LANCZOS))
        hole = np.asarray(Image.open(job / "hole" / f"{i:04d}.png")) > 127
        Image.fromarray(composite_native(src, gen, hole, edge)).save(job / "final" / f"{i:04d}.png")
    rate = source_rate(a.src)
    ff = g.ffmpeg_bin("ffmpeg")
    subprocess.run([ff, "-v", "error", "-y", "-framerate", rate, "-i", str(job / "final" / "%04d.png"), "-c:v", "libx264rgb",
                    "-qp", "0", "-preset", "ultrafast", str(job / "master.mp4")], check=True)
    subprocess.run([ff, "-v", "error", "-y", "-framerate", rate, "-i", str(job / "src" / "%04d.png"), "-c:v", "libx264rgb",
                    "-qp", "0", "-preset", "ultrafast", str(job / "source.mp4")], check=True)
    subprocess.run([ff, "-v", "error", "-y", "-framerate", rate, "-i", str(job / "final" / "%04d.png"), "-c:v", "libx264",
                    "-crf", "10", "-preset", "slow", "-pix_fmt", "yuv420p", str(job / "final.mp4")], check=True)
    trace("composite", t1, frames=len(gens), rate=rate)
    (job / "clothes_swap.json").write_text(json.dumps({"src": str(a.src), "first": a.first, "frames": a.frames,
                                                       "ref": str(a.ref), "prompt": a.prompt, "model": a.model,
                                                       "gen": [a.width, a.height], "steps": a.steps, "seed": a.seed,
                                                       "dilate": DILATE, "seconds": round(time.time() - t0, 1)}, indent=1),
                                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
