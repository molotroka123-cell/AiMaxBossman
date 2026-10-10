"""Face swap in a video («Genjutsu» по-нашему): one button, local, no credits.

Engine: FaceFusion (OpenRAIL-AS, https://github.com/facefusion/facefusion), run as its own process from its
own venv, on the GPU through ONNX Runtime (DirectML on the owner's Radeon 8060S). Measured on the owner PC
10.10: 444 frames 1320x1002 in 70 s with hyperswap_1a_256 + gfpgan_1.4.

Pipeline: source video + 1..5 face photos -> FaceFusion -> optional 16:9 frame (the picture of every frame
is cut out of screen-recording black bars and fitted on a blurred copy of itself) -> the ORIGINAL audio
track muxed back. Each step is a pure function here so the service and the tests share it.

Consent: the person whose face is used must agree; the job records the owner's confirmation flag.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: FaceFusion's tqdm line: "processing:  45%|####  | 100/222 [00:16<00:20, ...]"
PROGRESS = re.compile(r"processing:.*?\|\s*(\d+)/(\d+)\s*\[")
MAX_SOURCES = 5
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    processors: tuple[str, ...]
    args: tuple[str, ...]


PRESETS: dict[str, Preset] = {p.id: p for p in (
    Preset("fast", "Быстро: 7 кадров/с, похожесть 0.80 (замер 10.10)", ("face_swapper", "face_enhancer"),
           ("--face-swapper-model", "hyperswap_1a_256", "--face-swapper-pixel-boost", "512x512",
            "--face-enhancer-model", "gfpgan_1.4", "--face-enhancer-blend", "50")),
    # Measured 10.10 on the owner's Radeon 8060S (3 s clip, arcface identity to the photos): hyperswap_1a 0.82 beat
    # 1b 0.62 / 1c 0.57 / simswap_512 0.62. Several models in one process crash DirectML (segfault 139) unless the
    # execution thread count is 1 — hence the flag; xseg_2 crashed even alone, xseg_1 works.
    Preset("quality", "Качество: детализация 1024, маска рук/очков; 1.7 кадра/с, похожесть 0.80",
           ("face_swapper", "face_enhancer"),
           ("--face-swapper-model", "hyperswap_1a_256", "--face-swapper-pixel-boost", "1024x1024",
            "--face-mask-types", "box", "occlusion", "--face-occluder-model", "xseg_1",
            "--face-enhancer-model", "gfpgan_1.4", "--face-enhancer-blend", "50", "--execution-thread-count", "1")),
    Preset("lively", "Живая мимика: как «Качество» + мимика исходника 40% (похожесть 0.79)",
           ("face_swapper", "expression_restorer", "face_enhancer"),
           ("--face-swapper-model", "hyperswap_1a_256", "--face-swapper-pixel-boost", "1024x1024",
            "--face-mask-types", "box", "occlusion", "--face-occluder-model", "xseg_1",
            "--expression-restorer-model", "live_portrait", "--expression-restorer-factor", "40",
            "--face-enhancer-model", "gfpgan_1.4", "--face-enhancer-blend", "50", "--execution-thread-count", "1")),
)}


def default_root() -> Path:
    env = os.environ.get("BOSSMAN_FACEFUSION_HOME", "").strip()
    return Path(env) if env else Path.home() / "Bossman" / "apps-local" / "facefusion"


def python_of(root: Path) -> Path:
    win = root / ".venv" / "Scripts" / "python.exe"
    return win if win.exists() else root / ".venv" / "bin" / "python"


def describe(root: Path | None = None) -> dict:
    """Is the engine installed? Never guesses: the entry script and its venv must both exist."""
    root = Path(root) if root else default_root()
    ok = (root / "facefusion.py").is_file() and python_of(root).is_file()
    return {"engine": "facefusion", "available": ok, "home": str(root), "license": "OpenRAIL-AS",
            "reason": "" if ok else f"FaceFusion is not installed at {root} (clone + install.py directml)",
            "presets": [{"id": p.id, "label": p.label} for p in PRESETS.values()]}


def provider() -> str:
    """ONNX execution provider: DirectML on Windows (AMD/Intel/NVIDIA), CUDA where FaceFusion was set up for it."""
    return os.environ.get("BOSSMAN_FACEFUSION_PROVIDER", "directml" if os.name == "nt" else "cuda")


def build_argv(root: Path, sources: list[Path], target: Path, output: Path, preset: str = "fast",
               execution: str | None = None, *, temp_path: Path | None = None, jobs_path: Path | None = None) -> list[str]:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}")
    if not 1 <= len(sources) <= MAX_SOURCES:
        raise ValueError(f"1..{MAX_SOURCES} face photos are needed")
    p = PRESETS[preset]
    return [str(python_of(root)), str(root / "facefusion.py"), "headless-run",
            "--source-paths", *map(str, sources), "--target-path", str(target), "--output-path", str(output),
            "--processors", *p.processors, *p.args, "--face-selector-mode", "many",
            "--execution-providers", execution or provider(), "--output-video-quality", "90",
            *(["--temp-path", str(temp_path)] if temp_path else []), *(["--jobs-path", str(jobs_path)] if jobs_path else [])]


# ---------------------------------------------------------------- post-processing

def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FileNotFoundError("ffmpeg is not on PATH")
    return exe


def picture_box(gray, threshold: int = 18, share: float = 0.02):
    """(top, bottom, left, right) of the real picture inside screen-recording black bars, or None when blank."""
    import numpy as np
    lit = gray > threshold
    rows = np.where(lit.mean(axis=1) > share)[0]
    cols = np.where(lit.mean(axis=0) > share)[0]
    if len(rows) < 20 or len(cols) < 20:
        return None
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def fit_16x9(frame, box, width: int = 1280, height: int = 720):
    """The cut-out picture fitted whole (no crop) on a darkened, blurred copy of itself filling 16:9."""
    import cv2
    import numpy as np
    top, bottom, left, right = box
    pic = frame[top:bottom, left:right]
    ph, pw = pic.shape[:2]
    s = min(width / pw, height / ph)
    fg = cv2.resize(pic, (max(2, int(pw * s)), max(2, int(ph * s))), interpolation=cv2.INTER_LANCZOS4)
    sb = max(width / pw, height / ph)
    bg = cv2.resize(pic, (int(pw * sb) + 2, int(ph * sb) + 2))
    y0, x0 = (bg.shape[0] - height) // 2, (bg.shape[1] - width) // 2
    bg = cv2.GaussianBlur(bg[y0:y0 + height, x0:x0 + width], (0, 0), 28)
    bg = (bg * 0.75).astype(np.uint8)
    y, x = (height - fg.shape[0]) // 2, (width - fg.shape[1]) // 2
    bg[y:y + fg.shape[0], x:x + fg.shape[1]] = fg
    return bg


def compose_16x9(src: Path, dst: Path, width: int = 1280, height: int = 720) -> int:
    """Every frame -> 16:9 (silent). Returns the number of frames written."""
    import cv2
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    out = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    last, n = None, 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            box = picture_box(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)) or last or (0, frame.shape[0], 0, frame.shape[1])
            last = box
            out.write(fit_16x9(frame, box, width, height))
            n += 1
    finally:
        cap.release()
        out.release()
    return n


def video_seconds(video: Path) -> float:
    r = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(video)], capture_output=True, text=True, timeout=120)
    return float((r.stdout or "0").strip() or 0)


DELIVERY_CRF = 10


def finish(video: Path, original: Path, dst: Path, *, fps: int | None = None) -> None:
    """H.264 + the original audio of the source, at the source frame timing (no resampling). The VIDEO length wins:
    a shorter audio track just ends (10.10: -shortest cut 1.8 s of picture when the source audio was shorter).
    crf 10 (Gate 0, 10.10, ref-clip-01): the pipeline before this encode is bit-exact; at crf 16 the delivered copy moved
    68-point face landmarks by 0.024 IOD on average (gate limit 0.02), at crf 10 by 0.017, min PSNR 41.7 -> 42.8 dB."""
    cmd = [ffmpeg(), "-v", "error", "-y", "-i", str(video), "-i", str(original), "-map", "0:v:0", "-map", "1:a:0?",
           "-c:v", "libx264", "-crf", str(DELIVERY_CRF), "-preset", "slow", "-pix_fmt", "yuv420p",
           *(["-r", str(fps)] if fps else ["-fps_mode", "passthrough"]),
           "-c:a", "aac", "-b:a", "192k", "-t", f"{video_seconds(video):.3f}", "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not dst.is_file():
        raise RuntimeError("ffmpeg finish failed: " + (r.stderr or "")[-400:])


def pose_gate_argv(root: Path, source: Path, swapped: Path, output: Path, report: Path) -> list[str]:
    """Keep the source face on frames where the head is bowed toward the camera (see pose_gate_worker)."""
    worker = Path(__file__).with_name("pose_gate_worker.py")
    return [str(python_of(root)), str(worker), str(source), str(swapped), str(output), str(report)]


# ---------------------------------------------------------------- lossless parallel chunks
#: Measured 10.10 on the Radeon 8060S: 2 FaceFusion processes on halves of a clip took 40.5 s instead of 64.7 s and
#: produced bit-identical decoded frames; a 3rd process added little on a 3 s clip (model load dominates).
DEFAULT_WORKERS = 2
MIN_FRAMES_PER_CHUNK = 60


def frame_count(video: Path) -> int:
    r = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, timeout=600)
    return int((r.stdout or "0").strip().split(",")[0] or 0)


def plan_chunks(frames: int, workers: int) -> list[tuple[int, int]]:
    """[(first, last)] frame ranges; one chunk when the clip is too short to pay for another model load."""
    if frames <= 0:
        return [(0, -1)]                                     # unknown length: the whole clip in one process
    n = max(1, min(workers, frames // MIN_FRAMES_PER_CHUNK))
    size = -(-frames // n)
    return [(i * size, min(frames, (i + 1) * size) - 1) for i in range(n) if i * size < frames]


def cut_lossless(src: Path, first: int, last: int, dst: Path) -> None:
    """Exact frame range, H.264 -qp 0: the decoded frames FaceFusion sees are the source frames, bit for bit."""
    cmd = [ffmpeg(), "-v", "error", "-y", "-i", str(src), "-vf", f"select='between(n,{first},{last})',setpts=PTS-STARTPTS",
           "-an", "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not dst.is_file():
        raise RuntimeError("ffmpeg cut failed: " + (r.stderr or "")[-300:])


def join(parts: list[Path], dst: Path) -> None:
    lst = dst.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'" + chr(10) for p in parts), encoding="utf-8")
    r = subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(dst)],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not dst.is_file():
        raise RuntimeError("ffmpeg join failed: " + (r.stderr or "")[-300:])


def stream_rates(video: Path) -> tuple[str, str]:
    r = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=r_frame_rate,avg_frame_rate", "-of", "csv=p=0", str(video)], capture_output=True, text=True, timeout=120)
    parts = (r.stdout or "").strip().split(",")
    return (parts[0], parts[1]) if len(parts) >= 2 else ("0/0", "0/0")


def _ratio(text: str) -> float:
    a, _, b = text.partition("/")
    try:
        return float(a) / float(b or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def normalize_cfr(src: Path, dst: Path) -> bool:
    """A variable-frame-rate source (phone/screen recordings) becomes constant-rate at its OWN average rate, losslessly
    re-encoded (-qp 0). FaceFusion writes constant-rate video, so without this the chunks' timing drifts from the audio
    (10.10: 503 frames over 19.97 s were written as 24 fps). Returns False (nothing written) when the source is already CFR."""
    nominal, average = stream_rates(src)
    if _ratio(average) <= 0 or abs(_ratio(nominal) - _ratio(average)) < 0.01:
        return False
    cmd = [ffmpeg(), "-v", "error", "-y", "-i", str(src), "-vf", f"fps={average}", "-an", "-c:v", "libx264", "-qp", "0",
           "-preset", "ultrafast", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not dst.is_file():
        raise RuntimeError("ffmpeg cfr failed: " + (r.stderr or "")[-300:])
    return True
