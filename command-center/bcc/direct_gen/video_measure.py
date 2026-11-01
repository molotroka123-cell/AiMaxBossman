"""Measured video quality: every number comes from frames that ffmpeg decoded out of the file (studio real path 01.11).

``measure_video(path, ffmpeg)`` decodes ALL frames of the file as 8-bit luma (``-pix_fmt gray``) through a rawvideo
pipe and computes, with numpy:

  * per keyframe at 0/25/50/75/100 % of the decoded frames: brightness (mean/255), contrast (std/255), Laplacian
    sharpness (variance of the 4-neighbour Laplacian), non-black ratio (share of pixels with luma > NON_BLACK_LUMA),
    the frame's own sha256 (of its decoded luma bytes) and its motion to the next frame (mean |diff|, luma levels);
  * for the whole file: frames decoded, distinct frames (by sha256), mean/max frame-to-frame difference and the number
    of identical consecutive pairs. ``moving`` = at least two distinct frames and a mean difference >= MOTION_MIN: a
    still image wrapped in a video container is not a video. The floor is not 0 because a lossy re-encode of a still
    (x264 crf 16, measured 01.11) differs by ~1e-4 luma levels between frames; synthetic moving content measured 4.3.

There is no fallback: a file ffmpeg cannot decode raises ``MeasureError``. The output is meant to be passed as-is to
``bcc.art_v2.vlm_critic`` (``source`` = ``SOURCE``, bound to ``video_sha256``).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

SOURCE = "ffmpeg_decoded_frames+numpy"
METRIC_VERSION = "studio-measure-v1"
NON_BLACK_LUMA = 16            # 8-bit luma; limited-range video black is 16, so "non-black" = strictly brighter
MOTION_MIN = 0.05              # mean |frame diff| (luma levels) below which a file is a still + codec noise
KEYFRAME_POSITIONS = (0, 25, 50, 75, 100)
MAX_FRAMES = 1200              # Studio clips are <= 10 s; a longer file is refused rather than half-measured
TIMEOUT_S = 300


class MeasureError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _size(path: Path, ffprobe: str | None, ffmpeg: str) -> tuple[int, int]:
    exe = ffprobe or str(Path(ffmpeg).with_name("ffprobe"))
    try:
        out = subprocess.run([exe, "-v", "error", "-select_streams", "v:0", "-print_format", "json",
                              "-show_entries", "stream=width,height", str(path)],
                             capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
        stream = (json.loads(out.stdout or "{}").get("streams") or [{}])[0]
        w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired, IndexError) as exc:
        raise MeasureError(f"ffprobe_failed:{type(exc).__name__}") from None
    if w <= 0 or h <= 0:
        raise MeasureError("no_video_stream")
    return w, h


def decode_gray(path: Path, ffmpeg: str, ffprobe: str | None = None):
    """All frames as a uint8 numpy array (n, h, w). Raises MeasureError when nothing decodes."""
    import numpy as np

    w, h = _size(path, ffprobe, ffmpeg)
    try:
        proc = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "rawvideo",
                               "-pix_fmt", "gray", "-frames:v", str(MAX_FRAMES + 1), "-"],
                              capture_output=True, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MeasureError(f"ffmpeg_failed:{type(exc).__name__}") from None
    data, size = proc.stdout, w * h
    if proc.returncode != 0 or len(data) < size:
        raise MeasureError("no_decodable_frames")
    n = len(data) // size
    if n > MAX_FRAMES:
        raise MeasureError(f"too_many_frames:>{MAX_FRAMES}")
    return np.frombuffer(data[: n * size], dtype=np.uint8).reshape(n, h, w)


def laplacian_variance(frame) -> float:
    import numpy as np

    f = frame.astype(np.float32)
    lap = f[1:-1, :-2] + f[1:-1, 2:] + f[:-2, 1:-1] + f[2:, 1:-1] - 4.0 * f[1:-1, 1:-1]
    return float(lap.var())


def measure_frames(frames, video_sha256: str) -> dict[str, Any]:
    import numpy as np

    n = int(frames.shape[0])
    if n < 1:
        raise MeasureError("no_frames")
    hashes = [hashlib.sha256(frames[i].tobytes()).hexdigest() for i in range(n)]
    diffs = [float(np.abs(frames[i + 1].astype(np.int16) - frames[i].astype(np.int16)).mean()) for i in range(n - 1)]
    keyframes = []
    for pct in KEYFRAME_POSITIONS:
        idx = round(pct / 100 * (n - 1))
        f = frames[idx]
        motion = diffs[idx] if idx < len(diffs) else (diffs[idx - 1] if diffs else 0.0)
        keyframes.append({
            "source": SOURCE, "metric_version": METRIC_VERSION, "position_pct": pct, "frame_index": idx,
            "frame_sha256": hashes[idx], "video_sha256": video_sha256,
            "brightness": round(float(f.mean()) / 255.0, 4),
            "contrast": round(float(f.std()) / 255.0, 4),
            "laplacian_sharpness": round(laplacian_variance(f), 3),
            "non_black_ratio": round(float((f > NON_BLACK_LUMA).mean()), 4),
            "motion_to_next": round(motion, 4),
            # not measured here: no face model runs on this path (Studio presets refuse faces)
            "face_cosine_score": None,
            "artifacts_detected": [],
        })
    motion_mean = round(sum(diffs) / len(diffs), 4) if diffs else 0.0
    return {
        "source": SOURCE, "metric_version": METRIC_VERSION, "video_sha256": video_sha256,
        "width": int(frames.shape[2]), "height": int(frames.shape[1]), "frames_decoded": n,
        "distinct_frames": len(set(hashes)), "motion_mean": motion_mean,
        "motion_max": round(max(diffs), 4) if diffs else 0.0,
        "identical_consecutive_pairs": sum(1 for d in diffs if d == 0.0),
        "non_black_luma_threshold": NON_BLACK_LUMA,
        "non_black_ratio_min": min(k["non_black_ratio"] for k in keyframes),
        "laplacian_sharpness_min": min(k["laplacian_sharpness"] for k in keyframes),
        "motion_min_threshold": MOTION_MIN,
        "moving": bool(len(set(hashes)) >= 2 and motion_mean >= MOTION_MIN),
        "keyframes": keyframes,
    }


def measure_video(path: Path, ffmpeg: str, ffprobe: str | None = None) -> dict[str, Any]:
    """Decode the file and measure it (see module doc). The result is bound to the file's sha256."""
    path = Path(path)
    return measure_frames(decode_gray(path, ffmpeg, ffprobe), sha256_file(path))
