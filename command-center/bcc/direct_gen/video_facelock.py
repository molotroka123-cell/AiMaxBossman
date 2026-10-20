"""FaceLock for a generated VIDEO: the face of the job's own reference (@1 = the start picture) goes back into every
frame, then the clip is checked on many frames before it may ship.

Same mechanism as photo FaceLock, nothing wider: the face comes only from the reference picture the owner attached to
THIS job, the per-frame work is ``facelock_worker.facelock`` (FaceFusion's detector/landmarks, CPU), no other face model.

Pipeline (each step refuses honestly; a refusal keeps the model's own video as the result, labelled):
  probe     ffprobe: size, exact frame rate (r_frame_rate as a fraction), frame count, audio present
  extract   ffmpeg: every frame as PNG, passthrough timing (no frame dropped or duplicated)
  lock      facelock_worker --frames: one reference detection, every frame locked or kept as drawn
  assemble  ffmpeg: the frames at the source's exact rate, same size, the ORIGINAL audio stream copied, into
            ``<name>.part`` (same container as the source), then checked (playable, same size, same frame count)
  gate      ≥ GATE_MIN_SAMPLES frames spread over the whole clip, taken from the ASSEMBLED file (what ships, after the
            encoder), identity similarity to the reference: min and median reported; below GATE_MIN_SIMILARITY on any
            sample -> not shipped. Never decided on one frame (owner rule 16.10).
  ship      the model's video is kept as ``<name>_unlocked<ext>`` (result_unlocked), the .part replaces the result
            atomically (os.replace).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import statistics
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Awaitable, Callable

GATE_MIN_SAMPLES = 8            # owner 16.10: a person-video is never checked on one frame
GATE_TARGET_SAMPLES = 12        # sampled when the clip has enough frames with the locked face
GATE_MIN_SIMILARITY = 0.60      # cosine of FaceFusion recognizer embeddings, reference vs shipped frame (calibrate live)
MAX_REFUSED_SHARE = 0.10        # frames with a face FaceLock refused (other pose ...): above this the clip would flicker
FRAME_PATTERN = "f%06d.png"
LOCK_TIMEOUT_S = 3 * 3600
FFMPEG_TIMEOUT_S = 1800

REASON_RU = {
    "stop_active": "активен STOP владельца",
    "detector_unavailable": "FaceFusion не установлен",
    "ffmpeg_unavailable": "на ПК нет ffmpeg/ffprobe",
    "probe_failed": "не удалось прочитать видео (ffprobe)",
    "extract_failed": "не удалось разобрать видео на кадры",
    "worker_error": "ошибка FaceLock-обработчика",
    "worker_timeout": "FaceLock не уложился во время",
    "no_face_in_reference": "на фото-референсе не найдено лицо",
    "multiple_faces_unsupported": "на фото-референсе больше одного лица",
    "no_locked_frames": "ни в одном кадре лицо не вставлено",
    "too_many_refused": "слишком много кадров, где лицо вставить нельзя (другой ракурс)",
    "assemble_failed": "не удалось собрать видео обратно",
    "assembled_mismatch": "собранное видео не совпало с исходным (кадры/размер)",
    "gate_too_few_samples": "мало кадров с лицом для проверки",
    "gate_low_similarity": "проверка сходства лица не пройдена",
    "gate_failed": "проверка сходства лица не выполнилась",
}


@dataclass
class Proc:
    returncode: int
    output: list[str] = field(default_factory=list)


Runner = Callable[..., Awaitable[Proc]]


async def run_proc(argv: list[str], cwd: Path, timeout: float, on_line: Callable[[str], Awaitable[None]] | None = None) -> Proc:
    """One child process; stdout+stderr lines to ``on_line``; cancel or timeout kills it (never left running)."""
    proc = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd), stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    tail: list[str] = []

    async def pump() -> None:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if line:
                tail.append(line[:400])
                del tail[:-2000]
                if on_line is not None:
                    with contextlib.suppress(Exception):
                        await on_line(line)
        await proc.wait()

    try:
        await asyncio.wait_for(pump(), timeout)
    except BaseException:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 10)
        raise
    return Proc(proc.returncode if proc.returncode is not None else -1, tail)


def label(reason: str) -> str:
    """«FaceLock не применён: <причина>» — the line every surface (pult, phone, provenance) shows."""
    key = reason.split(" ", 1)[0].split("(", 1)[0]
    ru = REASON_RU.get(key, "")
    if not ru:
        return "FaceLock не применён: " + reason
    return "FaceLock не применён: " + (ru if reason == key else f"{ru} ({reason})")


def probe_argv(ffprobe: str, path: Path) -> list[str]:
    return [ffprobe, "-v", "error", "-count_frames", "-print_format", "json", "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,nb_read_frames"
            ":format=duration", str(path)]


def parse_probe(text: str) -> dict:
    """{width, height, fps (exact fraction string), frames, has_audio, duration_s} or ValueError."""
    info = json.loads(text or "{}")
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("no video stream")
    rate = str(video.get("r_frame_rate") or video.get("avg_frame_rate") or "")
    fps = Fraction(rate) if rate and rate != "0/0" else None
    if not fps or fps <= 0:
        raise ValueError("unknown frame rate")
    frames = video.get("nb_read_frames") or video.get("nb_frames")
    frames = int(frames) if str(frames or "").isdigit() else 0
    if frames <= 0:
        raise ValueError("unknown frame count")
    return {"width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
            "fps": f"{fps.numerator}/{fps.denominator}", "frames": frames,
            "has_audio": any(s.get("codec_type") == "audio" for s in streams),
            "duration_s": float((info.get("format") or {}).get("duration") or 0) or None,
            "codec": video.get("codec_name")}


def extract_argv(ffmpeg: str, src: Path, frames_dir: Path) -> list[str]:
    return [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(src), "-map", "0:v:0", "-fps_mode", "passthrough",
            str(frames_dir / FRAME_PATTERN)]


def container_of(path: Path) -> str:
    return {".webm": "webm", ".mkv": "matroska", ".mov": "mov"}.get(path.suffix.lower(), "mp4")


def assemble_argv(ffmpeg: str, frames_dir: Path, fps: str, original: Path, out_part: Path, has_audio: bool) -> list[str]:
    """Frames at the source's exact rate + the original audio stream COPIED (not re-encoded), same container."""
    fmt = container_of(original)
    argv = [ffmpeg, "-v", "error", "-nostdin", "-y", "-framerate", fps, "-i", str(frames_dir / FRAME_PATTERN)]
    if has_audio:
        argv += ["-i", str(original), "-map", "0:v:0", "-map", "1:a", "-c:a", "copy"]
    else:
        argv += ["-map", "0:v:0"]
    if fmt == "webm":
        argv += ["-c:v", "libvpx-vp9", "-crf", "18", "-b:v", "0", "-row-mt", "1"]
    else:
        argv += ["-c:v", "libx264", "-crf", "16", "-preset", "medium"]
        if fmt == "mp4":
            argv += ["-movflags", "+faststart"]
    argv += ["-pix_fmt", "yuv420p", "-r", fps, "-f", fmt, str(out_part)]
    return argv


def sample_indices(candidates: list[int], target: int = GATE_TARGET_SAMPLES) -> list[int]:
    """Up to ``target`` frame indices spread evenly over the whole clip (first and last candidate included)."""
    if len(candidates) <= target:
        return list(candidates)
    step = (len(candidates) - 1) / (target - 1)
    return sorted({candidates[round(i * step)] for i in range(target)})


def select_argv(ffmpeg: str, video: Path, indices: list[int], out_dir: Path, prefix: str = "s") -> list[str]:
    """The listed frames (0-based, ascending) of ``video`` as ``<prefix>001.png`` ... in that order."""
    expr = "+".join(f"eq(n\\,{i})" for i in sorted(indices))
    return [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(video), "-map", "0:v:0", "-vf", f"select='{expr}'",
            "-fps_mode", "passthrough", str(out_dir / f"{prefix}%03d.png")]


def gate(similarities: list[float | None], *, min_samples: int = GATE_MIN_SAMPLES,
         min_similarity: float = GATE_MIN_SIMILARITY) -> dict:
    measured = [float(v) for v in similarities if isinstance(v, (int, float))]
    out = {"samples": len(similarities), "measured": len(measured), "min_samples": min_samples,
           "threshold": min_similarity,
           "min": round(min(measured), 4) if measured else None,
           "median": round(statistics.median(measured), 4) if measured else None,
           "values": [None if v is None else round(float(v), 4) for v in similarities]}
    if len(measured) < min_samples:
        return {**out, "passed": False, "reason": f"gate_too_few_samples measured={len(measured)}<{min_samples}"}
    if out["min"] < min_similarity:
        return {**out, "passed": False, "reason": f"gate_low_similarity min={out['min']:.2f}<{min_similarity:.2f}"}
    return {**out, "passed": True, "reason": ""}


def tools() -> tuple[str, str] | None:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    return (ffmpeg, ffprobe) if ffmpeg and ffprobe else None
