"""Optional, local-only adapter for the experimental E compositor.

Inputs must be time-aligned constant-frame-rate video and head_zoom boxes. No
audio is copied. A failed or incomplete render never publishes an output.
"""
from __future__ import annotations

import hashlib
import json
import os
from fractions import Fraction
from pathlib import Path
import subprocess
import tempfile

import cv2
import numpy as np

if __package__:
    from .genjutsu_composite_e import iter_composite_e
else:
    from genjutsu_composite_e import iter_composite_e


def _probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,nb_read_frames",
         "-of", "json", str(path)], capture_output=True, text=True, check=True,
    )
    try:
        stream = json.loads(result.stdout)["streams"][0]
        fps = Fraction(stream["r_frame_rate"])
        average = Fraction(stream["avg_frame_rate"])
        width, height = int(stream["width"]), int(stream["height"])
        frames = int(stream["nb_read_frames"])
    except (KeyError, ValueError, IndexError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid video metadata: {path}") from exc
    if fps <= 0 or fps != average or frames <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"video is empty or not constant frame rate: {path}")
    # A matching nominal and average fps alone does not rule out VFR. Verify
    # actual presentation timestamps, streaming them instead of buffering video.
    process = subprocess.Popen(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "frame=best_effort_timestamp_time", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    count = 0
    first = None
    try:
        for line in process.stdout:
            value = line.strip().split(",", 1)[0]
            if not value:
                continue
            timestamp = Fraction(value)
            if first is None:
                first = timestamp
            if abs(timestamp - first - Fraction(count, 1) / fps) > Fraction(2, 1000):
                raise ValueError(f"variable or irregular frame timestamps: {path}")
            count += 1
        if process.wait() != 0 or count != frames:
            raise ValueError(f"missing frame timestamps: {path}")
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    return {"width": width, "height": height, "fps": fps, "frames": frames}


def _round(value):
    return int(value + Fraction(1, 2))


def _capture(path):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"cannot decode: {path}")
    return capture


def _frames(capture):
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        yield frame


def render_composite_e_video(*, base, head, source, mask, boxes, start, output, matte):
    """Render aligned inputs to a new MP4 and return checked metadata.

    ``start`` is the absolute source time in seconds and must equal box ``t0``.
    The input mask is already aligned to base, or is the existing 480:832 guide
    mask that is uniformly scaled then centre-padded. ``matte`` is a stateful
    RVM-compatible callable; it is invoked once for every decoded head frame.
    """
    paths = {key: Path(value) for key, value in
             (("base", base), ("head", head), ("source", source), ("mask", mask), ("boxes", boxes))}
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    if not output.parent.is_dir() or output.suffix.lower() != ".mp4":
        raise ValueError("output must be an MP4 in an existing directory")
    if not callable(matte) or isinstance(start, bool):
        raise ValueError("invalid matte or start")
    try:
        start = Fraction(str(start))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("invalid start") from exc
    if start < 0:
        raise ValueError("start must be non-negative")
    for path in paths.values():
        if not path.is_file():
            raise ValueError(f"input not found: {path}")
    videos = {key: _probe(paths[key]) for key in ("base", "head", "source", "mask")}
    b, h, s, m = (videos[key] for key in ("base", "head", "source", "mask"))
    if b["width"] * 16 != b["height"] * 9 or s["width"] * 16 != s["height"] * 9:
        raise ValueError("base and source must both be exact 9:16")
    if b["width"] % 2 or b["height"] % 2 or h["width"] != h["height"]:
        raise ValueError("base dimensions must be even and head must be square")
    if any(v["fps"] != b["fps"] or v["frames"] != b["frames"] for v in (h, m)):
        raise ValueError("base, head and mask fps/frame counts differ")
    if (m["width"], m["height"]) == (b["width"], b["height"]):
        mask_height = b["height"]
    elif m["width"] * 832 == m["height"] * 480:
        mask_height = _round(Fraction(m["height"] * b["width"], m["width"]))
        if mask_height > b["height"] or (b["height"] - mask_height) % 2:
            raise ValueError("guide mask cannot be centred without distortion")
    else:
        raise ValueError("mask geometry does not match base or the guide")
    try:
        meta = json.loads(paths["boxes"].read_text(encoding="utf-8"))
        raw_boxes = meta["boxes"]
        if (meta["src_w"] != s["width"] or meta["src_h"] != s["height"] or
                Fraction(str(meta["fps"])) != b["fps"] or Fraction(str(meta["t0"])) != start or
                meta["frames"] != b["frames"] or len(raw_boxes) != b["frames"]):
            raise ValueError("box metadata does not match source/window")
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("invalid or unsynchronised boxes") from exc
    scale = Fraction(b["width"], s["width"])
    scaled_boxes = []
    for index, box in enumerate(raw_boxes):
        if not isinstance(box, dict) or any(type(box.get(k)) is not int for k in ("i", "x", "y", "s")):
            raise ValueError("invalid box")
        if box["i"] != index or box["x"] < 0 or box["y"] < 0 or box["s"] <= 0 or box["x"] + box["s"] > s["width"] or box["y"] + box["s"] > s["height"]:
            raise ValueError("box is outside source or out of order")
        scaled = {k: _round(box[k] * scale) for k in ("x", "y", "s")}
        scaled["i"] = index
        if scaled["s"] <= 0 or scaled["x"] + scaled["s"] > b["width"] or scaled["y"] + scaled["s"] > b["height"]:
            raise ValueError("scaled box outside base")
        scaled_boxes.append(scaled)
    # Match head_zoom.crop_heads(), which uses Python's round at each time.
    indices = [int(round((float(start) + i / float(b["fps"])) * float(s["fps"]))) for i in range(b["frames"])]
    if indices[-1] >= s["frames"]:
        raise ValueError("source does not cover requested window")

    captures = []
    try:
        for key in ("base", "head", "source", "mask"):
            captures.append(_capture(paths[key]))
        cb, ch, cs, cm = captures

        def source_frames():
            for index in indices:
                if not cs.set(cv2.CAP_PROP_POS_FRAMES, index):
                    raise ValueError("source seek failed")
                ok, frame = cs.read()
                if not ok:
                    raise ValueError("source ended before requested frame")
                if frame.shape[:2] != (s["height"], s["width"]):
                    raise ValueError("source frame geometry changed")
                if frame.shape[:2] != (b["height"], b["width"]):
                    frame = cv2.resize(frame, (b["width"], b["height"]), interpolation=cv2.INTER_AREA)
                yield frame

        def masks():
            for frame in _frames(cm):
                if frame.shape[:2] != (m["height"], m["width"]):
                    raise ValueError("mask geometry changed")
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if gray.shape != (b["height"], b["width"]):
                    gray = cv2.resize(gray, (b["width"], mask_height), interpolation=cv2.INTER_LINEAR)
                    pad = (b["height"] - mask_height) // 2
                    gray = cv2.copyMakeBorder(gray, pad, pad, 0, 0, cv2.BORDER_CONSTANT, value=0)
                yield gray.astype(np.float32) / 255.0

        with tempfile.TemporaryDirectory(prefix=".genjutsu-e-", dir=output.parent) as temp:
            temporary = Path(temp) / "render.mp4"
            log_path = Path(temp) / "ffmpeg.log"
            command = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "rawvideo", "-pixel_format", "bgr24",
                       "-video_size", f"{b['width']}x{b['height']}", "-framerate", str(b["fps"]), "-i", "-", "-an",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "14", str(temporary)]
            with log_path.open("wb") as log:
                proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
                count = 0
                try:
                    for frame in iter_composite_e(_frames(cb), _frames(ch), source_frames(), masks(), scaled_boxes, matte=matte):
                        proc.stdin.write(frame.tobytes())
                        count += 1
                    proc.stdin.close()
                    if proc.wait(timeout=30) != 0:
                        raise RuntimeError("video encoder failed")
                except BaseException:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
                    raise
            if count != b["frames"] or _probe(temporary) != b:
                raise ValueError("encoded output differs from expected video")
            digest = hashlib.sha256()
            with temporary.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(chunk)
            # A hard link publishes atomically, without replacing an existing file.
            os.link(temporary, output)
    finally:
        for capture in captures:
            capture.release()
    return {"output": str(output), "frames": count, "width": b["width"], "height": b["height"],
            "fps": str(b["fps"]), "sha256": digest.hexdigest(), "audio": False}
