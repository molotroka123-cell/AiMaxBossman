"""Local Genjutsu test stack: character swap (Object Swap) and Motion Transfer on the owner's PC.

Design and limits: bugtest-20261001/genjutsu/GENJUTSU_LOCAL_RU.md (sections 3.2-3.5).
Engine: the already-allowed stable-diffusion.cpp Vulkan `sd-cli.exe` with Wan2.1 VACE (1.3B Q8 or
14B Q4_K_M); preprocessing in Bossman's Python runtime with Microsoft-signed ONNX Runtime
(YOLOX + DWPose, Depth-Anything-V2-Small, SAM2.1-tiny). No torch, no OpenCV, no new binaries.

swap   : src video -> person mask (SAM2, box tracked frame to frame) -> control frames = source with
         the person replaced by grey + his pose drawn inside -> VACE with the new character's
         reference image -> composite back over the untouched source (background byte-exact outside
         the feathered mask, colour matched in a ring) -> mp4 with the source audio.
motion : src video -> pose (+ optional depth) frames -> VACE with the reference -> mp4.

Every stage is timed into <job>/trace.jsonl. A RAM guard kills sd-cli when free memory < 16 GB.
Use only your own media or media of people who consented.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pose_draw import draw_pose  # noqa: E402

BOSSMAN = Path.home() / "Bossman"
MEDIA = BOSSMAN / "models" / "media"
PREP = MEDIA / "genjutsu-prep"
SD_CLI = BOSSMAN / "media-runtime" / "sdcpp" / "vulkan" / "sd-cli.exe"
# Official Comfy-Org safetensors (the format stable-diffusion.cpp documents for Wan). The community
# GGUFs were refused by this sd-cli build (74988b2): calcuis 1.3B -> "vace_patch_embedding not in model
# metadata"; QuantStack 14B -> 5-D conv tensor "invalid number of dimensions". 14B is quantised on load.
MODELS = {
    "1.3b": MEDIA / "wan21-vace-1.3b" / "wan2.1_vace_1.3B_fp16.safetensors",
    "14b": MEDIA / "wan21-vace-14b" / "wan2.1_vace_14B_fp16.safetensors",
}
LOAD_TYPE = {"1.3b": None, "14b": "q8_0"}
VAE = MEDIA / "wan21-vace-1.3b" / "wan_2.1_vae.safetensors"
T5 = MEDIA / "wan22-ti2v-5b" / "umt5-xxl-encoder-Q8_0.gguf"
NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，"
            "残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，"
            "背景人很多，倒着走")
MEAN = np.array([123.675, 116.28, 103.53], np.float32)
STD = np.array([58.395, 57.12, 57.375], np.float32)
RAM_FLOOR_GB = 16.0


# ------------------------------------------------------------------ utilities
def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Trace:
    def __init__(self, job: Path):
        self.path = job / "trace.jsonl"

    def __call__(self, stage: str, t0: float, **extra) -> None:
        row = {"t": now(), "stage": stage, "seconds": round(time.time() - t0, 1), **extra}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps(row, ensure_ascii=False), flush=True)


def free_ram_gb() -> float:
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2**30


def ffmpeg_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    apps = sorted((BOSSMAN / "app").glob(f"BOSSMAN-Windows-x64-*/media/{name}.exe"))
    if not apps:
        raise SystemExit(f"{name} not found")
    return str(apps[-1])


def session(path: Path) -> ort.InferenceSession:
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def load_frames(folder: Path) -> list[Image.Image]:
    return [Image.open(p).convert("RGB") for p in sorted(folder.glob("*.png"))]


# ------------------------------------------------------------------ frames
def extract_frames(src: Path, out: Path, w: int, h: int, n: int, fps: int, start: float = 0.0) -> None:
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin("ffmpeg"), "-v", "error", "-y", "-ss", str(start), "-i", str(src), "-vf",
                    f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                    "-frames:v", str(n), str(out / "%04d.png")], check=True)


# ------------------------------------------------------------------ YOLOX person detector
class Detector:
    def __init__(self):
        self.s = session(PREP / "yolox_l.onnx")
        grids, strides = [], []
        for stride in (8, 16, 32):
            g = 640 // stride
            yv, xv = np.meshgrid(np.arange(g), np.arange(g), indexing="ij")
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((g * g, 1), stride))
        self.grids, self.strides = np.concatenate(grids), np.concatenate(strides)

    def __call__(self, img: Image.Image, thr: float = 0.3) -> np.ndarray:
        w, h = img.size
        r = min(640 / w, 640 / h)
        pad = np.full((640, 640, 3), 114, np.uint8)
        pad[:int(h * r), :int(w * r)] = np.asarray(img.resize((int(w * r), int(h * r)), Image.BILINEAR))
        out = self.s.run(None, {"images": pad.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
        xy = (out[:, :2] + self.grids) * self.strides
        wh = np.exp(out[:, 2:4]) * self.strides
        score = out[:, 4] * out[:, 5]
        keep = score > thr
        boxes = np.concatenate([xy - wh / 2, xy + wh / 2], 1)[keep] / r
        score = score[keep]
        order, picked = score.argsort()[::-1], []
        while order.size:
            i = order[0]
            picked.append(i)
            xx0 = np.maximum(boxes[i, 0], boxes[order[1:], 0]); yy0 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
            xx1 = np.minimum(boxes[i, 2], boxes[order[1:], 2]); yy1 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
            inter = np.clip(xx1 - xx0, 0, None) * np.clip(yy1 - yy0, 0, None)
            area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])  # noqa: E731
            iou = inter / (area(boxes[i]) + area(boxes[order[1:]]) - inter + 1e-6)
            order = order[1:][iou < 0.45]
        return np.concatenate([boxes[picked], score[picked, None]], 1) if picked else np.zeros((0, 5))


# ------------------------------------------------------------------ DWPose (RTMPose SimCC, 133 keypoints)
class Pose:
    W, H = 288, 384

    def __init__(self):
        self.s = session(PREP / "dw-ll_ucoco_384.onnx")

    def __call__(self, img: Image.Image, box) -> tuple[np.ndarray, np.ndarray]:
        x0, y0, x1, y1 = box[:4]
        cx, cy, bw, bh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * 1.25, (y1 - y0) * 1.25
        if bw > bh * self.W / self.H:
            bh = bw * self.H / self.W
        else:
            bw = bh * self.W / self.H
        crop = img.crop((cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)).resize((self.W, self.H), Image.BILINEAR)
        x = ((np.asarray(crop, np.float32) - MEAN) / STD).transpose(2, 0, 1)[None]
        sx, sy = self.s.run(None, {"input": x})
        lx, ly = sx[0].argmax(-1), sy[0].argmax(-1)
        score = np.minimum(sx[0].max(-1), sy[0].max(-1))
        kx = lx / (sx.shape[-1] / self.W)
        ky = ly / (sy.shape[-1] / self.H)
        kpts = np.stack([cx - bw / 2 + kx / self.W * bw, cy - bh / 2 + ky / self.H * bh], 1)
        return kpts.astype(np.float32), score.astype(np.float32)


# ------------------------------------------------------------------ Depth-Anything-V2-Small
class Depth:
    def __init__(self):
        self.s = session(PREP / "depth_anything_v2_small_fp16.onnx")

    def __call__(self, img: Image.Image) -> Image.Image:
        w, h = img.size
        th = 518
        tw = max(14, round(th * w / h / 14) * 14)
        x = ((np.asarray(img.resize((tw, th), Image.BICUBIC), np.float32) / 255 - [0.485, 0.456, 0.406])
             / [0.229, 0.224, 0.225]).transpose(2, 0, 1)[None].astype(np.float32)
        d = self.s.run(None, {"pixel_values": x})[0][0]
        d = (d - d.min()) / (d.max() - d.min() + 1e-6) * 255
        return Image.fromarray(d.astype(np.uint8)).resize((w, h), Image.BICUBIC).convert("RGB")


# ------------------------------------------------------------------ SAM2.1-tiny (image mode, box prompt)
class Segmenter:
    def __init__(self):
        self.enc = session(PREP / "sam2" / "vision_encoder.onnx")
        self.dec = session(PREP / "sam2" / "prompt_encoder_mask_decoder.onnx")

    def __call__(self, img: Image.Image, box, points=None) -> np.ndarray:
        """box prompt; optional points [(x, y, label)] with label 1 = this person, 0 = not this one."""
        w, h = img.size
        x = ((np.asarray(img.resize((1024, 1024), Image.BILINEAR), np.float32) / 255 - [0.485, 0.456, 0.406])
             / [0.229, 0.224, 0.225]).transpose(2, 0, 1)[None].astype(np.float32)
        e0, e1, e2 = self.enc.run(None, {"pixel_values": x})
        b = np.array([[[box[0] * 1024 / w, box[1] * 1024 / h, box[2] * 1024 / w, box[3] * 1024 / h]]], np.float32)
        if points:
            pts = np.array([[[[px * 1024 / w, py * 1024 / h] for px, py, _ in points]]], np.float32)
            lab = np.array([[[lbl for _, _, lbl in points]]], np.int64)
        else:
            pts, lab = np.zeros((1, 1, 1, 2), np.float32), np.full((1, 1, 1), -10, np.int64)
        iou, masks, _ = self.dec.run(None, {
            "input_points": pts, "input_labels": lab,
            "input_boxes": b, "image_embeddings.0": e0, "image_embeddings.1": e1, "image_embeddings.2": e2})
        best = masks[0, 0, int(iou[0, 0].argmax())].astype(np.float32)
        # Upsample the logits, then threshold: thresholding the 256x256 mask first left dotted edges.
        up = np.asarray(Image.fromarray(best, mode="F").resize((w, h), Image.BILINEAR))
        return up > 0


def bbox_of(mask: np.ndarray, grow: float, w: int, h: int):
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    gx, gy = (x1 - x0) * grow, (y1 - y0) * grow
    return [max(0, x0 - gx), max(0, y0 - gy), min(w - 1, x1 + gx), min(h - 1, y1 + gy)]


# ------------------------------------------------------------------ stages
def stage_pose_and_masks(job: Path, frames: list[Image.Image], want_mask: bool, depth: bool, box=None):
    det, pose = Detector(), Pose()
    seg = Segmenter() if want_mask else None
    dep = Depth() if depth else None
    (job / "pose").mkdir(exist_ok=True)
    (job / "mask").mkdir(exist_ok=True)
    (job / "depth").mkdir(exist_ok=True)
    masks, poses, lost = [], [], 0
    w, h = frames[0].size
    track = list(box) if box else None
    for i, img in enumerate(frames, 1):
        boxes = det(img)
        if track is None and len(boxes):
            track = list(boxes[0][:4])  # the most confident person on frame 1
        people = []
        target = None
        if len(boxes):
            if track is not None:  # the detection closest to the tracked person
                cx, cy = (track[0] + track[2]) / 2, (track[1] + track[3]) / 2
                target = min(boxes, key=lambda b: abs((b[0] + b[2]) / 2 - cx) + abs((b[1] + b[3]) / 2 - cy))
            else:
                target = boxes[0]
            people.append(pose(img, target))
        poses.append(people)
        draw_pose((w, h), people).save(job / "pose" / f"{i:04d}.png")
        if dep:
            dep(img).save(job / "depth" / f"{i:04d}.png")
        if target is not None:
            track = list(target[:4])
        if seg is not None:
            # The detector box of the tracked person is the prompt on every frame: it survives shot
            # cuts, where propagating the previous mask drifts onto the background. Only when the
            # detector misses the person does the previous mask's (grown) box take over.
            if target is not None:
                bx0, by0, bx1, by1 = target[:4]
                gx, gy = (bx1 - bx0) * 0.04, (by1 - by0) * 0.04
                prompt = [max(0, bx0 - gx), max(0, by0 - gy), min(w - 1, bx1 + gx), min(h - 1, by1 + gy)]
            else:
                lost += 1
                prompt = bbox_of(masks[-1], 0.12, w, h) if masks else None
            m = seg(img, prompt) if prompt is not None else np.zeros((h, w), bool)
            masks.append(m)
    if seg is not None:
        stack = np.stack(masks).astype(np.uint8)
        for i in range(len(stack)):  # temporal median of 3, then dilation
            lo, hi = max(0, i - 1), min(len(stack), i + 2)
            med = (np.median(stack[lo:hi], axis=0) > 0).astype(np.uint8) * 255
            Image.fromarray(med).filter(ImageFilter.MaxFilter(17)).save(job / "mask" / f"{i + 1:04d}.png")
    return poses, lost


def stage_control(job: Path, frames, mode: str, swap_ctrl: str = "hole") -> None:
    out = job / "ctrl"
    out.mkdir(exist_ok=True)
    for i, img in enumerate(frames, 1):
        pose = Image.open(job / "pose" / f"{i:04d}.png").convert("RGB")
        drawn = pose.convert("L").point(lambda v: 255 if v > 10 else 0)
        if mode == "swap":
            m = Image.open(job / "mask" / f"{i:04d}.png").convert("L")
            hole = Image.composite(Image.new("RGB", img.size, (127, 127, 127)), img, m)
            # "hole-pose": the actor's skeleton inside the hole keeps the motion, but the 1.3B model
            # copied the coloured bones into the output (L0d, 2026-10-05). "hole" leaves it grey.
            ctrl = Image.composite(Image.composite(pose, hole, drawn), hole, m) if swap_ctrl == "hole-pose" else hole
        elif mode == "motion-depth":
            ctrl = Image.composite(pose, Image.open(job / "depth" / f"{i:04d}.png").convert("RGB"), drawn)
        else:
            ctrl = pose
        ctrl.save(out / f"{i:04d}.png")


def free_llms(trace: Trace) -> None:
    """Unload resident Ollama models (they reload on their next request) to give VACE the RAM."""
    t0 = time.time()
    exe = shutil.which("ollama") or str(Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe")
    before = free_ram_gb()
    try:
        listing = subprocess.run([exe, "ps"], capture_output=True, text=True, timeout=30).stdout.splitlines()[1:]
        names = [row.split()[0] for row in listing if row.strip()]
        for name in names:
            subprocess.run([exe, "stop", name], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        names = []
    time.sleep(3)
    trace("free_llms", t0, unloaded=names, free_ram_before_gb=round(before, 1), free_ram_after_gb=round(free_ram_gb(), 1))


def stage_generate(job: Path, args, trace: Trace) -> None:
    (job / "gen").mkdir(exist_ok=True)
    if args.free_llms:
        free_llms(trace)
    cmd = [str(SD_CLI), "-M", "vid_gen", "--diffusion-model", str(MODELS[args.model]), "--vae", str(VAE),
           "--t5xxl", str(T5), "-p", args.prompt, "-n", NEGATIVE, "-i", str(args.ref),
           "--control-video", str(job / "ctrl"), "--vace-strength", str(args.vace_strength),
           "-W", str(args.width), "-H", str(args.height), "--video-frames", str(args.frames), "--fps", str(args.fps),
           "--steps", str(args.steps), "--cfg-scale", str(args.cfg), "--sampling-method", "euler", "-s", str(args.seed),
           "--temporal-tiling", "--vae-tiling", "-o", str(job / "gen" / "%04d.png"), "-v"]
    # --vae-tiling: the RAM peak is the final VAE decode (OOM guard fired there on 17 frames, 2026-10-05).
    # No --offload-to-cpu: on this unified-memory APU it only shuttles weights every step
    # (measured 381 s/step at 17 frames with it vs 13-17 s/step at 5 frames without).
    if args.fa:
        cmd.append("--diffusion-fa")
    if LOAD_TYPE.get(args.model):
        cmd += ["--type", LOAD_TYPE[args.model]]
    (job / "sd-cli.cmd.json").write_text(json.dumps(cmd, ensure_ascii=False, indent=1), encoding="utf-8")
    t0, peak, killed = time.time(), [0.0], [False]
    with (job / "sd-cli.log").open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        total = free_ram_gb()

        def guard():
            while proc.poll() is None:
                free = free_ram_gb()
                peak[0] = max(peak[0], total - free)
                if free < RAM_FLOOR_GB:
                    killed[0] = True
                    proc.kill()
                    return
                time.sleep(2)
        threading.Thread(target=guard, daemon=True).start()
        code = proc.wait()
    trace("generate", t0, model=args.model, frames=args.frames, size=f"{args.width}x{args.height}", steps=args.steps,
          fa=args.fa, exit=code, ram_used_peak_gb=round(peak[0], 1), oom_guard=killed[0])
    if killed[0]:
        raise SystemExit(f"OOM_GUARD: free RAM fell below {RAM_FLOOR_GB} GB; sd-cli killed (see sd-cli.log)")
    if code != 0 or not list((job / "gen").glob("*.png")):
        raise SystemExit(f"sd-cli failed (exit {code}); see {job / 'sd-cli.log'}")


def ring_match(gen: np.ndarray, src: np.ndarray, mask: np.ndarray) -> np.ndarray:
    big = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(33))) > 127
    ring = big & ~mask
    if ring.sum() < 50:
        return gen
    g, s = gen[ring].astype(np.float32), src[ring].astype(np.float32)
    out = (gen.astype(np.float32) - g.mean(0)) / (g.std(0) + 1e-3) * s.std(0) + s.mean(0)
    return np.clip(out, 0, 255)


def stage_composite(job: Path, frames, mode: str) -> None:
    out = job / "final"
    out.mkdir(exist_ok=True)
    gens = sorted((job / "gen").glob("*.png"))
    for i, (img, gpath) in enumerate(zip(frames, gens), 1):
        gen = np.asarray(Image.open(gpath).convert("RGB").resize(img.size, Image.LANCZOS), np.float32)
        if mode != "swap":
            Image.fromarray(gen.astype(np.uint8)).save(out / f"{i:04d}.png")
            continue
        src = np.asarray(img, np.float32)
        mimg = Image.open(job / "mask" / f"{i:04d}.png").convert("L")
        m = np.asarray(mimg) > 127
        soft = np.asarray(mimg.filter(ImageFilter.GaussianBlur(6)), np.float32)[..., None] / 255
        gen = ring_match(gen, src, m)
        Image.fromarray((src * (1 - soft) + gen * soft).astype(np.uint8)).save(out / f"{i:04d}.png")


_MP4_AUDIO_COPY = {"aac", "mp3", "alac", "opus"}


def _audio_codec(src: Path) -> str | None:
    probe = subprocess.run([ffmpeg_bin("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=codec_name", "-of", "csv=p=0", str(src)], capture_output=True, text=True)
    return probe.stdout.strip() or None


def stage_encode(job: Path, src: Path, fps: int, start: float) -> Path:
    mp4 = job / "out.mp4"
    n = len(list((job / "final").glob("*.png")))
    # The source audio is copied, not re-encoded, when mp4 can hold it (owner 06.10), and cut to the video's
    # exact length instead of -shortest; other codecs fall back to AAC.
    codec = _audio_codec(src)
    acodec = ["-c:a", "copy"] if codec in _MP4_AUDIO_COPY else ["-c:a", "aac"]
    subprocess.run([ffmpeg_bin("ffmpeg"), "-v", "error", "-y", "-framerate", str(fps), "-i", str(job / "final" / "%04d.png"),
                    "-ss", str(start), "-i", str(src), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-crf", "16", "-preset", "slow", *acodec, "-t", f"{n / fps:.6f}", str(mp4)], check=True)
    # side-by-side review: source | control | result
    subprocess.run([ffmpeg_bin("ffmpeg"), "-v", "error", "-y", "-framerate", str(fps), "-i", str(job / "src" / "%04d.png"),
                    "-framerate", str(fps), "-i", str(job / "ctrl" / "%04d.png"), "-framerate", str(fps),
                    "-i", str(job / "final" / "%04d.png"), "-filter_complex", "hstack=inputs=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "20", str(job / "review_side_by_side.mp4")], check=True)
    return mp4


def accept(mp4: Path, frames: int, fps: int) -> dict:
    probe = json.loads(subprocess.run([ffmpeg_bin("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of",
                                       "json", str(mp4)], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    decode = subprocess.run([ffmpeg_bin("ffmpeg"), "-v", "error", "-i", str(mp4), "-f", "null", "-"],
                            capture_output=True, text=True)
    return {"width": v["width"], "height": v["height"], "duration": float(probe["format"]["duration"]),
            "expected_duration": round(frames / fps, 2), "full_decode": decode.returncode == 0 and not decode.stderr.strip(),
            "has_audio": any(s["codec_type"] == "audio" for s in probe["streams"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["swap", "motion", "motion-depth"])
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--ref", type=Path, required=True, help="one image of the new character (VACE takes one reference)")
    ap.add_argument("--prompt", required=True, help="describe the RESULT frame, not the source")
    ap.add_argument("--job", type=Path, required=True)
    ap.add_argument("--model", choices=sorted(MODELS), default="1.3b")
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--height", type=int, default=832)
    ap.add_argument("--frames", type=int, default=17, help="4k+1: 17/33/49/65/81 (81 = ~5 s at 16 fps)")
    ap.add_argument("--fps", type=int, default=16)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--cfg", type=float, default=6.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--vace-strength", type=float, default=1.0)
    ap.add_argument("--no-fa", dest="fa", action="store_false",
                    help="disable flash attention (A/B 2026-10-05: identical frames, 22%% faster, linear memory)")
    ap.add_argument("--box", type=lambda s: [float(v) for v in s.split(",")], help="x0,y0,x1,y1 of the person on frame 1")
    ap.add_argument("--prep-only", action="store_true")
    ap.add_argument("--free-llms", action="store_true", help="unload resident Ollama models before generating")
    ap.add_argument("--swap-ctrl", choices=["hole", "hole-pose"], default="hole",
                    help="swap control frames: grey hole only, or the actor's skeleton inside the hole")
    args = ap.parse_args(argv)
    if (args.frames - 1) % 4:
        ap.error("--frames must be 4k+1")
    for p in (SD_CLI, MODELS[args.model], VAE, T5, args.src, args.ref):
        if not Path(p).exists():
            ap.error(f"missing: {p}")
    job = args.job
    job.mkdir(parents=True, exist_ok=True)
    trace = Trace(job)
    t_all = time.time()
    trace("start", t_all, mode=args.mode, src=str(args.src), ref=str(args.ref), prompt=args.prompt,
          free_ram_gb=round(free_ram_gb(), 1))
    t0 = time.time()
    extract_frames(args.src, job / "src", args.width, args.height, args.frames, args.fps, args.start)
    frames = load_frames(job / "src")
    trace("frames", t0, count=len(frames))
    t0 = time.time()
    _, lost = stage_pose_and_masks(job, frames, args.mode == "swap", args.mode == "motion-depth", args.box)
    trace("pose_masks", t0, lost_track_frames=lost)
    t0 = time.time()
    stage_control(job, frames, args.mode, args.swap_ctrl)
    trace("control", t0)
    if args.prep_only:
        return 0
    stage_generate(job, args, trace)
    t0 = time.time()
    stage_composite(job, frames, args.mode)
    trace("composite", t0, note="composite is a separate stage from generation (honesty boundary)")
    t0 = time.time()
    mp4 = stage_encode(job, args.src, args.fps, args.start)
    trace("encode", t0, out=str(mp4))
    result = accept(mp4, args.frames, args.fps)
    trace("accept", t_all, **result)
    return 0 if result["full_decode"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
