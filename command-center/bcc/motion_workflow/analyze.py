"""Analyze a video file into a motion-workflow plan.  Local only, no network, no paid call.

What is measured, and with what:

  * scene cuts       ffmpeg ``select='gte(scene,0)'`` score per frame, cut when score >= threshold, then
                     a minimum scene length (the threshold and the rejected candidates are recorded),
  * cut type         the frame-difference profile around the cut: an isolated spike is a ``hard`` cut,
                     a luminance ramp through black/white is a ``fade``, a flat plateau of change between
                     quiet stretches without coherent motion is a ``dissolve``; anything else is ``unknown``,
  * camera motion    phase correlation between consecutive frames (OpenCV when importable, numpy FFT
                     otherwise) -> pan / tilt / zoom / handheld / static with magnitudes,
  * audio timing     ffmpeg ``silencedetect`` (audible intervals and pauses) and an onset/beat estimate
                     (librosa when importable, else a labelled energy-envelope peak picker),
  * on-screen text   only through an OCR callback (or pytesseract when it and the tesseract binary are
                     present); otherwise the field is ``UNKNOWN: ocr unavailable``.

Anything that cannot be established is the string ``UNKNOWN: ...`` - never a guess and never invented
data.  Optional analyzers (numpy, cv2, librosa, OCR) are imported lazily so the module imports on any
Python; ffmpeg/ffprobe come from ``BOSSMAN_FFMPEG`` / ``BOSSMAN_FFPROBE`` or PATH.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterator

from . import plan as planmod
from .plan import LIMITS, LimitError, unknown

ANALYZER_VERSION = 1

# --- declared analysis constants (recorded in every plan under analysis.parameters) -------------------
SCENE_THRESHOLD = 0.30            # ffmpeg scene score at or above which a frame starts a new scene
MIN_SCENE_SECONDS = 0.5           # candidates closer than this to a neighbour are rejected (recorded)
SCENE_FILTER_WIDTH = 320          # frames are scaled to at most this width before the scene filter
PROFILE_WIDTH = 128               # width of the gray frames used for the difference/motion profile
SILENCE_NOISE_DB = -35.0
SILENCE_MIN_SECONDS = 0.25
MIN_AUDIBLE_SECONDS = 0.02
AUDIO_SAMPLE_RATE = 16000

HARD_SPIKE_MIN = 6.0              # mean abs frame difference (0..255) a hard cut must reach
HARD_SPIKE_RATIO = 2.5            # ... and exceed the largest neighbouring difference by this factor
NEIGHBOUR_WINDOW = 6              # frames looked at on each side of a candidate cut
BLACK_LUMA = 24.0                 # a dip is the run of frames at or below this mean luma (0..255) ...
WHITE_LUMA = 232.0
FADE_BLACK_MIN = 8.0              # ... and a fade must actually reach (near) black: dark UI is not a fade
FADE_WHITE_MAX = 247.0
FADE_RAMP_STEP = 0.8              # luma change per frame that still counts as a ramp
FADE_MIN_RAMP_FRAMES = 3
FADE_MIN_RAMP_DELTA = 15.0
DISSOLVE_MIN_FRAMES = 4
DISSOLVE_MAX_SECONDS = 2.5
DISSOLVE_FLANK_FRAMES = 3
DISSOLVE_PLATEAU_RATIO = 2.5
DISSOLVE_MIN_TOTAL = 10.0
DISSOLVE_MIN_PER_FRAME = 0.4
DISSOLVE_BASELINE_FACTOR = 3.0
MOTION_PX = 0.35                  # px/frame (at PROFILE_WIDTH) above which a run counts as camera motion
MOTION_RESPONSE = 0.15

FEATURELESS_STD = 2.0             # gray-level std below which a frame carries no motion information
MIN_RESPONSE = 0.05               # phase-correlation response below which a pair is discarded
MIN_VALID_PAIRS = 4
SCENE_EDGE_MARGIN_FRAMES = 2      # pairs next to a cut / transition are not used for camera motion
PAN_MIN = 0.02                    # frame widths per second
TILT_MIN = 0.02                   # frame heights per second
ZOOM_MIN = 0.04                   # relative scale change per second
JITTER_MIN = 0.0025               # rms shift, frame widths per frame
HANDHELD_JITTER_OVER_MEAN = 1.5
ZOOM_TRANSLATION_SPILL = 0.5      # translation (frame sizes/s) tolerated per unit of zoom rate
PARTIAL_WINDOW_PAIRS = 6          # frame pairs per window when looking for a partial camera move
PARTIAL_MOVING_RATIO = 0.25       # share of moving windows that makes a mostly-still scene a pan/tilt/zoom
REVERSAL_RATIO = 0.35             # share of direction flips between moving windows that means wobble

OCR_SAMPLE_FRACTIONS = (0.2, 0.5, 0.8)
OCR_MAX_SAMPLES = 120
BEAT_MIN_DISTANCE_SECONDS = 0.25
FFMPEG_TIMEOUT_SECONDS = 900

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

Progress = Callable[[str, float], None]
OcrCallback = Callable[[str, float], list]
IntervalDetector = Callable[[str], list]


class AnalysisError(RuntimeError):
    """The video could not be analyzed (unreadable, no video stream, ffmpeg failed)."""


class AnalyzerUnavailable(AnalysisError):
    """A required tool (ffmpeg / ffprobe) is missing; optional analyzers degrade to UNKNOWN instead."""


# --------------------------------------------------------------------------- optional analyzers

def _load_module(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except Exception:  # ImportError, or a broken native extension
        return None


def _load_numpy() -> Any | None:
    return _load_module("numpy")


def _load_cv2() -> Any | None:
    return _load_module("cv2")


def _load_librosa() -> Any | None:
    return _load_module("librosa")


def _default_ocr() -> OcrCallback | None:
    """pytesseract + the tesseract binary, when both are present; None otherwise."""
    pytesseract = _load_module("pytesseract")
    image_mod = _load_module("PIL.Image")
    if pytesseract is None or image_mod is None:
        return None
    if not (shutil.which("tesseract") or Path(str(getattr(getattr(pytesseract, "pytesseract", None),
                                                           "tesseract_cmd", ""))).is_file()):
        return None

    def run(frame_path: str, _t: float) -> list:
        data = pytesseract.image_to_data(image_mod.open(frame_path), output_type=pytesseract.Output.DICT)
        boxes = []
        for i, text in enumerate(data.get("text", [])):
            text = (text or "").strip()
            try:
                conf = float(data["conf"][i])
            except (KeyError, IndexError, TypeError, ValueError):
                conf = -1.0
            if text and conf >= 30:
                boxes.append({"text": text, "conf": conf,
                              "box": [int(data["left"][i]), int(data["top"][i]),
                                      int(data["width"][i]), int(data["height"][i])]})
        return boxes

    run.__name__ = "pytesseract"
    return run


def find_ffmpeg() -> str | None:
    env = os.environ.get("BOSSMAN_FFMPEG", "").strip()
    if env:
        candidate = Path(env)
        if candidate.is_dir():
            for name in ("ffmpeg.exe", "ffmpeg"):
                if (candidate / name).is_file():
                    return str(candidate / name)
        elif candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


def find_ffprobe(ffmpeg: str | None = None) -> str | None:
    env = os.environ.get("BOSSMAN_FFPROBE", "").strip()
    if env and Path(env).is_file():
        return env
    ffmpeg = ffmpeg or find_ffmpeg()
    if ffmpeg:
        folder = Path(ffmpeg).parent
        for name in ("ffprobe.exe", "ffprobe"):
            if (folder / name).is_file():
                return str(folder / name)
    return shutil.which("ffprobe")


def available(*, include_paths: bool = True) -> dict[str, Any]:
    """What this process can analyze with, for the UI and for the plan's analysis block.

    ``include_paths=False`` reports only file names, so a plan that is shared or exported does not carry
    the owner's directory layout."""
    np, cv2, librosa = _load_numpy(), _load_cv2(), _load_librosa()
    ocr = _default_ocr()
    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe(ffmpeg)
    if not include_paths:
        ffmpeg = Path(ffmpeg).name if ffmpeg else None
        ffprobe = Path(ffprobe).name if ffprobe else None
    return {
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "numpy": getattr(np, "__version__", "present") if np is not None else None,
        "cv2": getattr(cv2, "__version__", "present") if cv2 is not None else None,
        "librosa": getattr(librosa, "__version__", "present") if librosa is not None else None,
        "ocr": getattr(ocr, "__name__", "callback") if ocr is not None else None,
    }


# --------------------------------------------------------------------------- ffmpeg helpers

def _run(cmd: list[str], *, timeout: float = FFMPEG_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError(f"{Path(cmd[0]).name} timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise AnalyzerUnavailable(f"cannot run {cmd[0]}: {exc}") from exc


def _tail(data: bytes, limit: int = 400) -> str:
    return data.decode("utf-8", "replace").strip()[-limit:]


def _fraction(text: Any) -> Fraction | None:
    try:
        value = Fraction(str(text).replace(":", "/"))
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def probe(path: str | os.PathLike, *, ffprobe: str | None = None) -> dict[str, Any]:
    """Normalised video facts from ffprobe: size after rotation, SAR-corrected aspect, fps, audio."""
    ffprobe = ffprobe or find_ffprobe()
    if not ffprobe:
        raise AnalyzerUnavailable("ffprobe not found (set BOSSMAN_FFMPEG or put ffprobe on PATH)")
    done = _run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                timeout=60)
    if done.returncode != 0:
        raise AnalysisError(f"ffprobe failed: {_tail(done.stderr)}")
    try:
        data = json.loads(done.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"ffprobe returned invalid JSON: {exc}") from exc
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise AnalysisError("no video stream in the file")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise AnalysisError("video stream has no usable dimensions")
    rotation = 0
    try:
        rotation = int(float((video.get("tags") or {}).get("rotate", 0)))
    except (TypeError, ValueError):
        rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            try:
                rotation = int(float(side["rotation"]))
            except (TypeError, ValueError):
                pass
    rotation %= 360
    sar = _fraction(video.get("sample_aspect_ratio")) or Fraction(1)
    disp_w = int(round(width * float(sar)))
    disp_h = height
    if rotation in (90, 270):
        disp_w, disp_h = disp_h, disp_w
    fps_value = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    if fps_value is None:
        raise AnalysisError("cannot determine the frame rate")
    r_rate = _fraction(video.get("r_frame_rate"))
    duration = None
    for raw in (video.get("duration"), (data.get("format") or {}).get("duration")):
        try:
            duration = float(raw)
            break
        except (TypeError, ValueError):
            continue
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise AnalysisError("cannot determine the duration")
    try:
        nb_frames = int(video.get("nb_frames"))
    except (TypeError, ValueError):
        nb_frames = None
    return {
        "video_stream_index": int(video.get("index", 0)),
        "width": width, "height": height, "display_width": disp_w, "display_height": disp_h,
        "rotation": rotation, "sample_aspect_ratio": f"{sar.numerator}:{sar.denominator}",
        "aspect_ratio": planmod.aspect_label(disp_w, disp_h),
        "fps": round(float(fps_value), 4), "fps_rational": f"{fps_value.numerator}/{fps_value.denominator}",
        "variable_frame_rate_suspected": bool(r_rate and abs(float(r_rate) - float(fps_value)) > 0.01),
        "duration": round(duration, 3), "nb_frames": nb_frames,
        "video_codec": video.get("codec_name"), "pix_fmt": video.get("pix_fmt"),
        "has_audio": audio is not None,
        "audio_codec": (audio or {}).get("codec_name"),
        "audio_sample_rate": int((audio or {}).get("sample_rate") or 0) or None,
        "audio_channels": (audio or {}).get("channels"),
    }


_META_FRAME = re.compile(r"frame:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*(-?[0-9.eE+-]+)")
_META_SCORE = re.compile(r"lavfi\.scene_score=([0-9.eE+-]+)")


def _video_map(stream: int | None) -> str:
    return f"0:{stream}" if stream is not None else "0:v:0"


def scene_scores(path: str, *, ffmpeg: str, timeout: float = FFMPEG_TIMEOUT_SECONDS,
                 stream: int | None = None) -> list[tuple[int, float, float]]:
    """(frame index, pts seconds, ffmpeg scene score) for every decoded frame."""
    vf = (f"scale=w='min({SCENE_FILTER_WIDTH},iw)':h=-2:flags=area,"
          "select='gte(scene,0)',metadata=mode=print:file=-")
    done = _run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", path, "-map", _video_map(stream),
                 "-an", "-sn", "-vf", vf, "-f", "null", "-"], timeout=timeout)
    if done.returncode != 0:
        raise AnalysisError(f"ffmpeg scene filter failed: {_tail(done.stderr)}")
    rows: list[tuple[int, float, float]] = []
    pending: tuple[int, float] | None = None
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        head = _META_FRAME.search(line)
        if head:
            try:
                pending = (int(head.group(1)), float(head.group(3)))
            except ValueError:
                pending = None
            continue
        score = _META_SCORE.search(line)
        if score and pending is not None:
            try:
                rows.append((pending[0], pending[1], float(score.group(1))))
            except ValueError:
                pass
            pending = None
    if not rows:
        raise AnalysisError("the scene filter produced no frames")
    return rows


def _even(value: float, minimum: int = 2) -> int:
    return max(minimum, int(round(value / 2.0)) * 2)


def profile_size(display_width: int, display_height: int) -> tuple[int, int]:
    width = _even(min(PROFILE_WIDTH, display_width), 8)
    height = _even(width * display_height / max(display_width, 1), 8)
    return width, height


def _raw_frames(path: str, ffmpeg: str, w: int, h: int, timeout: float,
                stream: int | None = None) -> Iterator[bytes]:
    """Gray w*h frames, one ``bytes`` per decoded frame, straight from ffmpeg (no frame dropping)."""
    size = w * h
    variants = (["-fps_mode", "passthrough"], ["-vsync", "0"])
    for attempt, sync in enumerate(variants):
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", path, "-map", _video_map(stream),
               "-an", "-sn", "-vf", f"scale={w}:{h}:flags=area,setsar=1,format=gray", *sync,
               "-f", "rawvideo", "-pix_fmt", "gray", "-"]
        with tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL,
                                        creationflags=NO_WINDOW)
            except OSError as exc:
                raise AnalyzerUnavailable(f"cannot run ffmpeg: {exc}") from exc
            fired: list[bool] = []

            def watchdog() -> None:
                fired.append(True)
                proc.kill()

            timer = threading.Timer(timeout, watchdog)
            timer.daemon = True
            timer.start()
            produced = 0
            try:
                assert proc.stdout is not None
                while True:
                    buf = proc.stdout.read(size)
                    if len(buf) < size:
                        break
                    produced += 1
                    yield buf
            finally:
                timer.cancel()
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
            if fired:
                raise AnalysisError(f"ffmpeg frame decode timed out after {timeout:.0f}s")
            if proc.returncode != 0:
                err.seek(0)
                message = err.read().decode("utf-8", "replace")
                if produced == 0 and attempt == 0 and "fps_mode" in message:
                    continue          # old ffmpeg without -fps_mode: retry with -vsync 0
                raise AnalysisError(f"ffmpeg frame decode failed after {produced} frames: "
                                    f"{message.strip()[-400:]}")
            return


class _Profile:
    """Per-frame luminance and difference, and per-pair motion features.  Index i describes frame i;
    for the pair features it is the pair (i-1, i)."""

    def __init__(self) -> None:
        self.luma: list[float] = []
        self.std: list[float] = []
        self.mafd: list[float] = []
        self.dx: list[float] = []
        self.dy: list[float] = []
        self.zoom: list[float] = []
        self.resp: list[float] = []
        self.valid: list[bool] = []
        self.motion_method: str | None = None

    @property
    def n(self) -> int:
        return len(self.luma)


class _MotionEstimator:
    """Global translation and zoom between consecutive frames by phase correlation.

    The full frame gives the translation; the left/right and top/bottom halves give the divergence
    (zoom).  Uses OpenCV ``phaseCorrelate`` when ``cv2`` is given, otherwise numpy FFT.  Both return the
    displacement of the content from the previous frame to the current one, in pixels."""

    REGIONS = ("full", "left", "right", "top", "bottom")

    def __init__(self, np: Any, h: int, w: int, cv2: Any | None = None) -> None:
        self.np, self.cv2, self.h, self.w = np, cv2, h, w
        self.slices = {
            "full": (slice(0, h), slice(0, w)),
            "left": (slice(0, h), slice(0, w // 2)),
            "right": (slice(0, h), slice(w // 2, w)),
            "top": (slice(0, h // 2), slice(0, w)),
            "bottom": (slice(h // 2, h), slice(0, w)),
        }
        self.windows = {}
        for name, (ys, xs) in self.slices.items():
            rh, rw = ys.stop - ys.start, xs.stop - xs.start
            self.windows[name] = np.outer(np.hanning(rh), np.hanning(rw)).astype(np.float32)
        self._prev: dict[str, Any] | None = None
        self.method = "opencv-phaseCorrelate" if cv2 is not None else "numpy-fft-phase-correlation"

    def _region(self, frame: Any, name: str) -> Any:
        ys, xs = self.slices[name]
        return frame[ys, xs]

    def _prepare(self, frame: Any) -> dict[str, Any]:
        np = self.np
        out: dict[str, Any] = {}
        for name in self.REGIONS:
            part = self._region(frame, name)
            if self.cv2 is not None:
                out[name] = np.ascontiguousarray(part, dtype=np.float32)
            else:
                out[name] = np.fft.rfft2((part - part.mean()) * self.windows[name])
        return out

    def _correlate(self, a: Any, b: Any, name: str) -> tuple[float, float, float]:
        np = self.np
        if self.cv2 is not None:
            (dx, dy), response = self.cv2.phaseCorrelate(a.astype(np.float64), b.astype(np.float64),
                                                         self.windows[name].astype(np.float64))
            return float(dx), float(dy), min(1.0, max(0.0, float(response)))
        ys, xs = self.slices[name]
        shape = (ys.stop - ys.start, xs.stop - xs.start)
        cross = b * np.conj(a)
        cross = cross / (np.abs(cross) + 1e-9)
        r = np.fft.irfft2(cross, s=shape)
        iy, ix = np.unravel_index(int(np.argmax(r)), r.shape)
        peak = float(r[iy, ix])
        rows = [(iy - 1) % shape[0], iy, (iy + 1) % shape[0]]
        cols = [(ix - 1) % shape[1], ix, (ix + 1) % shape[1]]
        patch = np.clip(r[np.ix_(rows, cols)], 0.0, None)
        total = float(patch.sum())
        oy = ox = 0.0
        if total > 0:
            oy = float((patch.sum(axis=1) * np.array([-1.0, 0.0, 1.0])).sum() / total)
            ox = float((patch.sum(axis=0) * np.array([-1.0, 0.0, 1.0])).sum() / total)
        dy, dx = iy + oy, ix + ox
        if dy > shape[0] / 2:
            dy -= shape[0]
        if dx > shape[1] / 2:
            dx -= shape[1]
        return float(dx), float(dy), min(1.0, max(0.0, peak))

    def feed(self, frame: Any) -> dict[str, float] | None:
        """Features for the pair (previous frame, this frame); None for the first frame."""
        current = self._prepare(frame)
        previous, self._prev = self._prev, current
        if previous is None:
            return None
        res = {name: self._correlate(previous[name], current[name], name) for name in self.REGIONS}
        dx, dy, response = res["full"]
        zoom_x = (res["right"][0] - res["left"][0]) / (self.w / 2.0)
        zoom_y = (res["bottom"][1] - res["top"][1]) / (self.h / 2.0)
        return {"dx": dx, "dy": dy, "resp": response, "zoom": (zoom_x + zoom_y) / 2.0}


def build_profile(frames: Iterator[bytes], w: int, h: int, *, np: Any | None, cv2: Any | None = None,
                  want_motion: bool = True, progress: Progress | None = None, total: int | None = None
                  ) -> _Profile:
    prof = _Profile()
    estimator = _MotionEstimator(np, h, w, cv2) if (np is not None and want_motion) else None
    prof.motion_method = estimator.method if estimator else None
    previous_bytes: bytes | None = None
    previous_arr: Any = None
    for buf in frames:
        if np is not None:
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w).astype(np.float32)
            prof.luma.append(float(arr.mean()))
            prof.std.append(float(arr.std()))
            if previous_arr is None:
                prof.mafd.append(0.0)
            else:
                prof.mafd.append(float(np.abs(arr - previous_arr).mean()))
            if estimator is not None:
                feat = estimator.feed(arr)
                if feat is None:
                    prof.dx.append(0.0), prof.dy.append(0.0), prof.zoom.append(0.0)
                    prof.resp.append(0.0), prof.valid.append(False)
                else:
                    ok = (prof.std[-1] >= FEATURELESS_STD and prof.std[-2] >= FEATURELESS_STD
                          and feat["resp"] >= MIN_RESPONSE)
                    prof.dx.append(feat["dx"]), prof.dy.append(feat["dy"]), prof.zoom.append(feat["zoom"])
                    prof.resp.append(feat["resp"]), prof.valid.append(bool(ok))
            previous_arr = arr
        else:
            n = len(buf)
            mean = sum(buf) / n
            prof.luma.append(mean)
            prof.std.append(math.sqrt(sum((v - mean) ** 2 for v in buf) / n))
            if previous_bytes is None:
                prof.mafd.append(0.0)
            else:
                prof.mafd.append(sum(abs(a - b) for a, b in zip(buf, previous_bytes)) / n)
            previous_bytes = buf
        if progress and total and prof.n % 200 == 0:
            progress("profile", min(1.0, prof.n / total))
    return prof


# --------------------------------------------------------------------------- transitions

def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _ramp(luma: list[float], edge: int, step: int, to_black: bool) -> tuple[int, float]:
    """Frames, walking away from a dip edge, over which luma keeps moving away from black (or white)."""
    count = 0
    j = edge + step
    last = luma[edge]
    while 0 <= j < len(luma):
        change = luma[j] - luma[j - step]
        moving_away = change >= FADE_RAMP_STEP if to_black else change <= -FADE_RAMP_STEP
        if not moving_away:
            break
        count += 1
        last = luma[j]
        j += step
    return count, abs(last - luma[edge])


def find_fades(luma: list[float]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Dips through black/white with at least one measured luminance ramp.

    Returns (mid-clip dips, fade-in at clip start or None).  A dip's frame is the centre of the dark run."""
    n = len(luma)
    dips: list[dict[str, Any]] = []
    start_fade: dict[str, Any] | None = None
    for polarity, inside in (("black", lambda v: v <= BLACK_LUMA), ("white", lambda v: v >= WHITE_LUMA)):
        i = 0
        while i < n:
            if not inside(luma[i]):
                i += 1
                continue
            a = i
            while i < n and inside(luma[i]):
                i += 1
            b = i - 1
            before, before_delta = _ramp(luma, a, -1, polarity == "black") if a > 0 else (0, 0.0)
            after, after_delta = _ramp(luma, b, +1, polarity == "black") if b < n - 1 else (0, 0.0)
            good_before = before >= FADE_MIN_RAMP_FRAMES and before_delta >= FADE_MIN_RAMP_DELTA
            good_after = after >= FADE_MIN_RAMP_FRAMES and after_delta >= FADE_MIN_RAMP_DELTA
            record = {"through": polarity, "dip": [a, b], "ramp_out_frames": before if good_before else 0,
                      "ramp_in_frames": after if good_after else 0,
                      "ramp_out_delta": round(before_delta, 1) if good_before else 0.0,
                      "ramp_in_delta": round(after_delta, 1) if good_after else 0.0}
            reached = (min(luma[a:b + 1]) <= FADE_BLACK_MIN) if polarity == "black" \
                else (max(luma[a:b + 1]) >= FADE_WHITE_MAX)
            if not reached:
                continue                      # a dark (or bright) scene, not a fade through black (white)
            if a == 0:
                if good_after and start_fade is None:
                    start_fade = {**record, "frame": 0, "kind": "fade_in_from_" + polarity}
                continue
            if b == n - 1:
                continue                      # fade-out at the very end is not a boundary
            if good_before or good_after:
                # the join is the darkest frame (middle of the darkest stretch), not the middle of the run:
                # the two ramps need not be symmetric
                darkest = min(luma[a:b + 1]) if polarity == "black" else max(luma[a:b + 1])
                tied = [j for j in range(a, b + 1) if abs(luma[j] - darkest) <= 1.0]
                record["frame"] = tied[len(tied) // 2]
                record["covers"] = [max(0, a - record["ramp_out_frames"]), min(n - 1, b + record["ramp_in_frames"])]
                dips.append(record)
    dips.sort(key=lambda r: r["frame"])
    return dips, start_fade


def find_dissolves(mafd: list[float], fps: float, motion_px: list[float] | None, motion_resp: list[float] | None,
                   taken: list[tuple[int, int]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Plateaus of steady change between quiet stretches with no coherent translation.

    Returns (dissolves, unverified) - ``unverified`` are candidates that could not be told from camera
    motion because no motion estimate exists; they are reported as warnings, never turned into cuts."""
    n = len(mafd)
    if n < 2 * DISSOLVE_FLANK_FRAMES + DISSOLVE_MIN_FRAMES + 2:
        return [], []
    baseline = _median(mafd[1:])
    elevated = max(DISSOLVE_MIN_PER_FRAME, DISSOLVE_BASELINE_FACTOR * baseline)
    max_frames = max(DISSOLVE_MIN_FRAMES, int(DISSOLVE_MAX_SECONDS * fps))
    found: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    i = 1
    while i < n:
        if mafd[i] <= elevated:
            i += 1
            continue
        a = i
        while i < n and mafd[i] > elevated:
            i += 1
        b = i - 1
        length = b - a + 1
        if not (DISSOLVE_MIN_FRAMES <= length <= max_frames):
            continue
        if a - DISSOLVE_FLANK_FRAMES < 1 or b + DISSOLVE_FLANK_FRAMES >= n:
            continue
        flanks = mafd[a - DISSOLVE_FLANK_FRAMES:a] + mafd[b + 1:b + 1 + DISSOLVE_FLANK_FRAMES]
        if any(v > elevated for v in flanks):
            continue
        run = mafd[a:b + 1]
        med = _median(run)
        if max(run) > DISSOLVE_PLATEAU_RATIO * med or sum(run) < DISSOLVE_MIN_TOTAL:
            continue
        if any(not (b < lo or a > hi) for lo, hi in taken):
            continue
        record = {"frame": (a + b + 1) // 2, "run": [a, b], "frames": length,
                  "mean_change": round(sum(run) / length, 2), "total_change": round(sum(run), 1)}
        if motion_px is None or motion_resp is None:
            unverified.append(record)
            continue
        coherent = (_median(motion_px[a:b + 1]) >= MOTION_PX and _median(motion_resp[a:b + 1]) >= MOTION_RESPONSE)
        if coherent:
            continue                          # a coherent shift is camera motion, not a dissolve
        found.append(record)
    return found, unverified


def classify_candidate(k: int, mafd: list[float]) -> dict[str, Any]:
    """Hard cut or unknown for a scene-filter candidate at frame k, from its neighbourhood."""
    n = len(mafd)
    lo, hi = max(1, k - NEIGHBOUR_WINDOW), min(n - 1, k + NEIGHBOUR_WINDOW)
    near = [j for j in range(max(1, k - 1), min(n - 1, k + 1) + 1)]
    peak_idx = max(near, key=lambda j: mafd[j]) if near else k
    peak = mafd[peak_idx]
    rest = [mafd[j] for j in range(lo, hi + 1) if j != peak_idx]
    rest_max = max(rest) if rest else 0.0
    detail = {"peak": round(peak, 2), "neighbour_max": round(rest_max, 2),
              "ratio": round(peak / max(rest_max, 1.0), 2)}
    if peak >= HARD_SPIKE_MIN and peak >= HARD_SPIKE_RATIO * max(rest_max, 1.0):
        return {"type": "hard", "frame": peak_idx, "detail": detail,
                "reason": "isolated spike in the frame-difference profile"}
    return {"type": "unknown", "frame": k, "detail": detail,
            "reason": unknown("the scene filter fired but the frame-difference profile is neither an "
                              "isolated spike nor a recognised fade or dissolve")}


# --------------------------------------------------------------------------- scenes

def _boundaries(rows: list[tuple[int, float, float]], threshold: float, prof: _Profile | None, fps: float,
                min_scene: float, warnings: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any] | None,
                                                                list[dict[str, Any]], int]:
    """(accepted boundaries, fade-in at start, rejected candidates, raw candidate count)."""
    times = [r[1] for r in rows]
    candidates = [r for r in rows if r[0] >= 1 and r[2] >= threshold]
    boundaries: list[dict[str, Any]] = []
    start_fade: dict[str, Any] | None = None
    covered: list[tuple[int, int]] = []
    gradual: list[dict[str, Any]] = []

    if prof is not None and prof.n >= 2:
        dips, start_fade = find_fades(prof.luma)
        for dip in dips:
            lo, hi = dip["covers"]
            covered.append((lo, hi))
            gradual.append({"type": "fade", "frame": dip["frame"], "score": None, "source": "gradual-scan",
                            "strength": 0.9, "span": [lo, hi], "detail": {k: dip[k] for k in
                                                        ("through", "dip", "ramp_out_frames", "ramp_in_frames",
                                                         "ramp_out_delta", "ramp_in_delta")},
                            "reason": f"luminance ramp through {dip['through']} over the dip"})
        if start_fade is not None:
            covered.append((0, start_fade["dip"][1] + start_fade["ramp_in_frames"]))
        if prof.motion_method is not None:
            motion_px = [math.hypot(x, y) for x, y in zip(prof.dx, prof.dy)]
            motion_resp = prof.resp
        else:
            motion_px = motion_resp = None
        dissolves, unverified = find_dissolves(prof.mafd, fps, motion_px, motion_resp, covered)
        for d in dissolves:
            gradual.append({"type": "dissolve", "frame": d["frame"], "score": None, "source": "gradual-scan",
                            "strength": 0.6, "span": d["run"], "detail": {"run": d["run"], "frames": d["frames"],
                                                        "mean_change": d["mean_change"],
                                                        "total_change": d["total_change"]},
                            "reason": "steady change over several frames between quiet stretches, "
                                      "no coherent camera translation"})
        for u in unverified:
            warnings.append(f"possible gradual transition at frame {u['run'][0]}-{u['run'][1]} could not be "
                            f"told from camera motion (no motion estimate available); not turned into a cut")

    for cand in candidates:
        k = cand[0]
        match = next((g for g in gradual if g["span"][0] - 2 <= k <= g["span"][1] + 2), None)
        if match is not None:
            match["score"] = max(match["score"] or 0.0, round(cand[2], 3))
            match["source"] = "both"
            continue
        if prof is None or prof.n <= k:
            boundaries.append({"type": "unknown", "frame": k, "score": round(cand[2], 3),
                               "source": "scene-filter", "strength": cand[2], "detail": {},
                               "reason": unknown("frame-difference profile unavailable")})
            continue
        verdict = classify_candidate(k, prof.mafd)
        boundaries.append({"type": verdict["type"], "frame": verdict["frame"], "score": round(cand[2], 3),
                           "source": "scene-filter", "strength": cand[2], "detail": verdict["detail"],
                           "reason": verdict["reason"]})
    boundaries.extend(gradual)
    boundaries.sort(key=lambda b: b["frame"])

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    def t_of(frame: int) -> float:
        return times[frame] if 0 <= frame < len(times) else frame / fps

    for b in boundaries:
        t = t_of(b["frame"])
        while True:
            prev_t = t_of(accepted[-1]["frame"]) if accepted else 0.0
            if t - prev_t >= min_scene:
                accepted.append(b)
                break
            if accepted and b["strength"] > accepted[-1]["strength"]:
                dropped = accepted.pop()
                rejected.append({"frame": dropped["frame"], "time": round(t_of(dropped["frame"]), 3),
                                 "score": dropped["score"], "type": dropped["type"],
                                 "reason": f"closer than {min_scene}s to a stronger cut"})
                continue
            rejected.append({"frame": b["frame"], "time": round(t, 3), "score": b["score"], "type": b["type"],
                             "reason": f"closer than {min_scene}s to the previous cut"})
            break
    total_t = t_of(len(times) - 1) + (1.0 / fps if fps else 0.0)
    if accepted and total_t - t_of(accepted[-1]["frame"]) < min_scene:
        dropped = accepted.pop()
        rejected.append({"frame": dropped["frame"], "time": round(t_of(dropped["frame"]), 3),
                         "score": dropped["score"], "type": dropped["type"],
                         "reason": f"would leave a final scene shorter than {min_scene}s"})
    return accepted, start_fade, rejected, len(candidates)


# --------------------------------------------------------------------------- camera motion

def classify_camera(prof: _Profile, start: int, end: int, fps: float, w: int, h: int,
                    *, np_available: bool = True, exclude: list[tuple[int, int]] | None = None) -> dict[str, Any]:
    """Camera motion class and magnitudes for the frames [start, end)."""
    if not np_available or prof.motion_method is None:
        return {"class": "unknown", "magnitude": None, "samples": 0, "method": None,
                "reason": unknown("analyzer unavailable (numpy is not importable; camera motion not measured)")}
    lo = start + 1 + SCENE_EDGE_MARGIN_FRAMES
    hi = min(end - 1 - SCENE_EDGE_MARGIN_FRAMES, prof.n - 1)
    pairs = [i for i in range(max(lo, 1), hi + 1)]
    if not pairs:
        return {"class": "unknown", "magnitude": None, "samples": 0, "method": prof.motion_method,
                "reason": unknown("scene is too short to measure camera motion")}
    # pairs inside a fade / dissolve are not camera motion; they are excluded, never averaged in
    if exclude:
        pairs = [i for i in pairs if not any(lo <= i <= hi for lo, hi in exclude)]
    featureless = [i for i in pairs if prof.std[i] < FEATURELESS_STD or prof.std[i - 1] < FEATURELESS_STD]
    valid = [i for i in pairs if prof.valid[i]]
    if len(valid) < MIN_VALID_PAIRS:
        if pairs and len(featureless) >= 0.6 * len(pairs):
            why = "frames are featureless (flat or black); camera motion cannot be measured"
        else:
            why = f"too few reliable frame pairs ({len(valid)} of {len(pairs)}; need {MIN_VALID_PAIRS})"
        return {"class": "unknown", "magnitude": None, "samples": len(pairs), "valid_samples": len(valid),
                "method": prof.motion_method, "reason": unknown(why)}
    pan, tilt, zoom, _, mean_px = _components(prof, valid, fps, w, h)
    jitter_px = _window_jitter(prof, valid)
    jitter = jitter_px / w
    scores = _scores(pan, tilt, zoom)
    best = max(scores, key=lambda key: scores[key])
    moving_ratio = None
    if jitter >= JITTER_MIN and jitter_px > HANDHELD_JITTER_OVER_MEAN * mean_px:
        label, reason = "handheld", "oscillating shift with no dominant direction"
    elif scores[best] >= 1.0:
        label, reason = best, f"median {best} {scores[best]:.1f}x the declared minimum"
    else:
        label, reason = "static", "all motion components below the declared minimums"
        # A scene can hold a camera move for only part of its length: look at short windows before
        # calling the whole scene static.
        windows = [valid[i:i + PARTIAL_WINDOW_PAIRS]
                   for i in range(0, len(valid) - PARTIAL_WINDOW_PAIRS + 1, PARTIAL_WINDOW_PAIRS)]
        moving: dict[str, list[tuple[float, float, float]]] = {"pan": [], "tilt": [], "zoom": []}
        sequence: list[tuple[str, int]] = []
        for chunk in windows:
            c_pan, c_tilt, c_zoom, _, _ = _components(prof, chunk, fps, w, h)
            c_scores = _scores(c_pan, c_tilt, c_zoom)
            c_best = max(c_scores, key=lambda key: c_scores[key])
            if c_scores[c_best] >= 1.0:
                moving[c_best].append((c_pan, c_tilt, c_zoom))
                signed = {"pan": c_pan, "tilt": c_tilt, "zoom": c_zoom}[c_best]
                sequence.append((c_best, 1 if signed > 0 else -1))
        total_moving = sum(len(v) for v in moving.values())
        if windows and total_moving / len(windows) >= PARTIAL_MOVING_RATIO:
            flips = sum(1 for a, b in zip(sequence, sequence[1:]) if a[0] == b[0] and a[1] != b[1])
            if len(sequence) >= 4 and flips / (len(sequence) - 1) >= REVERSAL_RATIO:
                label = "handheld"
                moving_ratio = round(total_moving / len(windows), 2)
                reason = "the direction of the shift reverses between windows (wobble, not a camera move)"
            else:
                label = max(moving, key=lambda key: len(moving[key]))
                picked = moving[label]
                pan = _median([p[0] for p in picked])
                tilt = _median([p[1] for p in picked])
                zoom = _median([p[2] for p in picked])
                moving_ratio = round(total_moving / len(windows), 2)
                reason = (f"{label} in {moving_ratio:.0%} of the scene (windows of {PARTIAL_WINDOW_PAIRS} "
                          f"frame pairs); magnitudes are the median of the moving windows")
    direction = None
    if label == "pan":
        direction = "left" if pan > 0 else "right"          # content moving right = camera moving left
    elif label == "tilt":
        direction = "up" if tilt > 0 else "down"
    elif label == "zoom":
        direction = "in" if zoom > 0 else "out"
    return {
        "class": label, "direction": direction, "reason": reason, "method": prof.motion_method,
        "samples": len(pairs), "valid_samples": len(valid), "moving_ratio": moving_ratio,
        "confidence": round(_median([prof.resp[i] for i in valid]), 3),
        "magnitude": {
            "pan_frame_widths_per_s": round(pan, 4),
            "tilt_frame_heights_per_s": round(tilt, 4),
            "zoom_per_s": round(zoom, 4),
            "jitter_frame_widths_per_frame": round(jitter, 5),
            "note": "signed: content moving right/down and zoom-in are positive; "
                    f"measured on {w}x{h} gray frames",
        },
    }


def _components(prof: _Profile, idx: list[int], fps: float, w: int, h: int
                ) -> tuple[float, float, float, float, float]:
    """(pan, tilt, zoom, jitter px, mean shift px) over the pairs ``idx``; pan/tilt in frame sizes per second."""
    dxs = [prof.dx[i] for i in idx]
    dys = [prof.dy[i] for i in idx]
    zs = [prof.zoom[i] for i in idx]
    shift_x, shift_y = _median(dxs), _median(dys)
    jitter_px = math.sqrt(statistics.pstdev(dxs) ** 2 + statistics.pstdev(dys) ** 2)
    return (shift_x / w * fps, shift_y / h * fps, _median(zs) * fps, jitter_px, math.hypot(shift_x, shift_y))


def _window_jitter(prof: _Profile, idx: list[int]) -> float:
    """High-frequency shift (px per frame): median over short windows of the within-window spread, so a
    slow drift or a start/stop of a pan does not count as jitter."""
    size = PARTIAL_WINDOW_PAIRS
    windows = [idx[i:i + size] for i in range(0, len(idx) - size + 1, size)] or [idx]
    spreads = []
    for chunk in windows:
        if len(chunk) < 2:
            continue
        dxs = [prof.dx[i] for i in chunk]
        dys = [prof.dy[i] for i in chunk]
        spreads.append(math.sqrt(statistics.pstdev(dxs) ** 2 + statistics.pstdev(dys) ** 2))
    return _median(spreads)


def _scores(pan: float, tilt: float, zoom: float) -> dict[str, float]:
    # A zoom spreads the shift field, so the whole-frame translation of a pure zoom is not exactly zero
    # (about a quarter of the zoom rate in these units); only the excess counts as pan or tilt.
    spill = ZOOM_TRANSLATION_SPILL * abs(zoom)
    return {"pan": max(0.0, abs(pan) - spill) / PAN_MIN, "tilt": max(0.0, abs(tilt) - spill) / TILT_MIN,
            "zoom": abs(zoom) / ZOOM_MIN}


# --------------------------------------------------------------------------- audio

_SIL_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[0-9.]+)")


def _complement(silences: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in sorted(silences):
        if s - cursor >= MIN_AUDIBLE_SECONDS:
            out.append((cursor, s))
        cursor = max(cursor, e)
    if duration - cursor >= MIN_AUDIBLE_SECONDS:
        out.append((cursor, duration))
    return out


def detect_silence(path: str, duration: float, *, ffmpeg: str, noise_db: float = SILENCE_NOISE_DB,
                   min_silence: float = SILENCE_MIN_SECONDS, timeout: float = FFMPEG_TIMEOUT_SECONDS
                   ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """(silences, audible intervals) from ffmpeg silencedetect."""
    done = _run([ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-i", path, "-map", "0:a:0", "-vn",
                 "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"], timeout=timeout)
    if done.returncode != 0:
        raise AnalysisError(f"ffmpeg silencedetect failed: {_tail(done.stderr)}")
    silences: list[tuple[float, float]] = []
    open_start: float | None = None
    for line in done.stderr.decode("utf-8", "replace").splitlines():
        m = _SIL_START.search(line)
        if m:
            open_start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and open_start is not None:
            silences.append((open_start, min(duration, float(m.group(1)))))
            open_start = None
    if open_start is not None:
        silences.append((open_start, duration))
    silences = [(s, e) for s, e in silences if e > s]
    return silences, _complement(silences, duration)


def _decode_audio(path: str, np: Any, *, ffmpeg: str, timeout: float) -> Any:
    done = _run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0", "-vn",
                 "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE), "-f", "f32le", "-"], timeout=timeout)
    if done.returncode != 0:
        raise AnalysisError(f"ffmpeg audio decode failed: {_tail(done.stderr)}")
    return np.frombuffer(done.stdout[: len(done.stdout) // 4 * 4], dtype="<f4")


def energy_onsets(np: Any, samples: Any, sample_rate: int) -> list[float]:
    """Onset times from a short-time energy envelope: positive dB jumps that are local maxima, well above
    the noise floor of the clip, at least BEAT_MIN_DISTANCE_SECONDS apart.  These are onsets, not tracked beats."""
    hop = max(1, int(sample_rate * 0.010))
    win = max(hop, int(sample_rate * 0.030))
    if len(samples) < win + 8 * hop:
        return []
    x = samples.astype(np.float64)
    cumulative = np.concatenate([[0.0], np.cumsum(x * x)])
    frames = (len(x) - win) // hop + 1
    idx = np.arange(frames) * hop
    energy = (cumulative[idx + win] - cumulative[idx]) / win
    level = 10.0 * np.log10(energy + 1e-10)
    jump = np.maximum(0.0, level[1:] - level[:-1])
    floor = float(np.percentile(level, 10))
    spread = float(jump.std())
    threshold = max(3.0, float(jump.mean()) + 2.0 * spread)
    picks: list[tuple[float, int]] = []
    for i in range(1, len(jump) - 1):
        if jump[i] >= threshold and jump[i] >= jump[i - 1] and jump[i] > jump[i + 1] \
                and level[i + 1] > floor + 10.0:
            picks.append((float(jump[i]), i + 1))
    picks.sort(reverse=True)
    min_gap = int(BEAT_MIN_DISTANCE_SECONDS / (hop / sample_rate))
    chosen: list[int] = []
    for _, i in picks:
        if all(abs(i - j) >= min_gap for j in chosen):
            chosen.append(i)
    chosen.sort()
    return [round((i * hop + win - hop / 2.0) / sample_rate, 3) for i in chosen]


def estimate_tempo(onsets: list[float]) -> float | str:
    if len(onsets) < 5:
        return unknown("fewer than 5 onsets")
    gaps = [b - a for a, b in zip(onsets, onsets[1:])]
    med = _median(gaps)
    spread = _median([abs(g - med) for g in gaps])
    if med <= 0 or spread / med > 0.15:
        return unknown("irregular onsets; no stable tempo")
    return round(60.0 / med, 1)


def analyze_audio(path: str, duration: float, *, ffmpeg: str, np: Any | None, noise_db: float,
                  min_silence: float, speech_detector: IntervalDetector | None,
                  music_detector: IntervalDetector | None, timeout: float) -> dict[str, Any]:
    silences, audible = detect_silence(path, duration, ffmpeg=ffmpeg, noise_db=noise_db,
                                       min_silence=min_silence, timeout=timeout)
    out: dict[str, Any] = {
        "has_audio_stream": True,
        "silence_detect": {"method": "ffmpeg silencedetect", "noise_db": noise_db, "min_silence_seconds": min_silence},
        "silences": [[round(s, 3), round(e, 3)] for s, e in silences],
        "audible_intervals": [[round(s, 3), round(e, 3)] for s, e in audible],
    }

    def intervals(detector: IntervalDetector | None, what: str) -> Any:
        if detector is None:
            return unknown(f"{what} detection unavailable; silencedetect measures loudness only and cannot "
                           f"separate speech from music (pass a {what}_detector)")
        try:
            return [[round(float(a), 3), round(float(b), 3)] for a, b in detector(path)]
        except Exception as exc:  # a plug-in may fail in any way; the field then stays unknown
            return unknown(f"{what} detector failed ({type(exc).__name__})")

    out["speech"] = {"method": getattr(speech_detector, "__name__", "callback") if speech_detector else None,
                     "intervals": intervals(speech_detector, "speech")}
    out["music"] = {"method": getattr(music_detector, "__name__", "callback") if music_detector else None,
                    "intervals": intervals(music_detector, "music")}

    beats: dict[str, Any]
    librosa = _load_librosa()
    if np is None:
        beats = {"times": unknown("analyzer unavailable (numpy not importable)"), "method": None,
                 "tempo_bpm": unknown("analyzer unavailable (numpy not importable)")}
    else:
        try:
            samples = _decode_audio(path, np, ffmpeg=ffmpeg, timeout=timeout)
            if librosa is not None:
                tempo, frames = librosa.beat.beat_track(y=samples.astype("float32"), sr=AUDIO_SAMPLE_RATE)
                times = [round(float(t), 3) for t in librosa.frames_to_time(frames, sr=AUDIO_SAMPLE_RATE)]
                bpm = float(np.atleast_1d(tempo)[0])
                beats = {"times": times, "method": "librosa.beat.beat_track",
                         "tempo_bpm": round(bpm, 1) if bpm > 0 else unknown("librosa returned no tempo")}
            else:
                onsets = energy_onsets(np, samples, AUDIO_SAMPLE_RATE)
                beats = {"times": onsets,
                         "method": "energy-envelope peak picker (numpy; librosa not installed) - onsets, not "
                                   "tracked beats",
                         "tempo_bpm": estimate_tempo(onsets)}
        except AnalysisError as exc:
            beats = {"times": unknown(f"audio decode failed ({exc})"), "method": None,
                     "tempo_bpm": unknown("audio decode failed")}
    out["beats"] = beats
    return out


def _overlap(intervals: list[list[float]], start: float, end: float) -> tuple[list[list[float]], float]:
    clipped, total = [], 0.0
    for a, b in intervals:
        lo, hi = max(a, start), min(b, end)
        if hi - lo > 1e-6:
            clipped.append([round(lo, 3), round(hi, 3)])
            total += hi - lo
    return clipped, total


def scene_audio(audio: dict[str, Any] | None, start: float, end: float) -> dict[str, Any]:
    """The audio cues of one scene, cut from the whole-clip audio analysis."""
    if audio is None:
        return {"has_audio_stream": False, "audible_ratio": 0.0, "audible_intervals": [], "pauses": [],
                "speech_present": False, "music_present": False, "beat_times": [], "beat_count": 0,
                "beat_method": "none (the file has no audio stream)", "tempo_bpm": None}
    span = max(end - start, 1e-9)
    audible, audible_total = _overlap(audio["audible_intervals"], start, end)
    pauses, _ = _overlap(audio["silences"], start, end)

    def present(block: dict[str, Any]) -> Any:
        ivs = block["intervals"]
        if planmod.is_unknown(ivs):
            return ivs
        return _overlap(ivs, start, end)[1] >= 0.2

    beats = audio["beats"]
    times = beats["times"]
    if planmod.is_unknown(times):
        beat_times: Any = times
        count: Any = times
    else:
        beat_times = [t for t in times if start <= t < end]
        count = len(beat_times)
    return {
        "has_audio_stream": True,
        "audible_ratio": round(audible_total / span, 3),
        "audible_intervals": audible,
        "pauses": pauses,
        "speech_present": present(audio["speech"]),
        "music_present": present(audio["music"]),
        "beat_times": beat_times,
        "beat_count": count,
        "beat_method": beats["method"],
        "tempo_bpm": beats["tempo_bpm"],
    }


# --------------------------------------------------------------------------- text (OCR)

def _extract_frame(path: str, t: float, out: Path, *, ffmpeg: str, timeout: float,
                   stream: int | None = None) -> bool:
    done = _run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", f"{max(t, 0.0):.3f}",
                 "-i", path, "-map", _video_map(stream), "-frames:v", "1", str(out)], timeout=timeout)
    return done.returncode == 0 and out.is_file() and out.stat().st_size > 0


def scene_text(path: str, scenes: list[dict[str, Any]], ocr: OcrCallback | None, *, ffmpeg: str,
               timeout: float, stream: int | None = None) -> None:
    """Fill ``text`` for every scene in place: boxes from the OCR callback, or an UNKNOWN marker.

    At most OCR_MAX_SAMPLES frames are sent to the engine for a clip; scenes after the budget keep an
    explicit UNKNOWN marker instead of silently reporting "no text"."""
    if ocr is None:
        for scene in scenes:
            scene["text"] = {"status": unknown("ocr unavailable"), "method": None, "boxes": [], "samples": []}
        return
    name = getattr(ocr, "__name__", "callback")
    per_scene = max(1, min(len(OCR_SAMPLE_FRACTIONS), OCR_MAX_SAMPLES // max(1, len(scenes))))
    fractions = (OCR_SAMPLE_FRACTIONS if per_scene >= 3 else (0.5,) if per_scene == 1 else (0.3, 0.7))[:per_scene]
    cap = LIMITS["max_text_boxes_per_scene"]
    budget = OCR_MAX_SAMPLES
    with tempfile.TemporaryDirectory(prefix="mw-ocr-") as tmp:
        for scene in scenes:
            if budget < len(fractions):
                scene["text"] = {"status": unknown("ocr sample budget exhausted for this clip"),
                                 "method": name, "boxes": [], "samples": []}
                continue
            budget -= len(fractions)
            boxes: list[dict[str, Any]] = []
            samples: list[float] = []
            failure: str | None = None
            for fraction in fractions:
                t = scene["start"] + fraction * scene["duration"]
                frame_path = Path(tmp) / f"{scene['id']}_{int(fraction * 100)}.png"
                if not _extract_frame(path, t, frame_path, ffmpeg=ffmpeg, timeout=timeout, stream=stream):
                    failure = "frame extraction failed"
                    continue
                try:
                    found = ocr(str(frame_path), t)
                except Exception as exc:
                    failure = f"ocr callback failed ({type(exc).__name__})"
                    continue
                samples.append(round(t, 3))
                for item in found or []:
                    try:
                        text = str(item["text"]).strip()
                        box = [int(round(float(v))) for v in item["box"]]
                        if text and len(box) == 4:
                            conf = item.get("conf")
                            boxes.append({"t": round(t, 3), "text": text, "box": box,
                                          "conf": round(float(conf), 1) if conf is not None else None})
                    except (KeyError, TypeError, ValueError):
                        continue
            if not samples:
                scene["text"] = {"status": unknown(failure or "ocr produced no sample"), "method": name,
                                 "boxes": [], "samples": []}
            else:
                scene["text"] = {"status": "measured", "method": name, "boxes": boxes[:cap], "samples": samples}


# --------------------------------------------------------------------------- orchestration

def sha256_file(path: str | os.PathLike, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _scene_elements(scene_text_info: dict[str, Any], audio: dict[str, Any], preset_mode: bool) -> dict[str, Any]:
    detected: dict[str, Any] = {}
    text_status = scene_text_info.get("status")
    if text_status == "measured":
        detected["text"] = {"boxes": len(scene_text_info.get("boxes", [])), "method": scene_text_info.get("method")}
    else:
        detected["text"] = text_status
    detected["music"] = audio.get("music_present", planmod.NO_DETECTOR_MARKER)
    return planmod.default_elements(preset_mode=preset_mode, detected=detected)


def analyze_video(path: str | os.PathLike, *, project_id: str | None = None, title: str | None = None,
                  category: str = "motion", mode: str = "own_video", threshold: float = SCENE_THRESHOLD,
                  min_scene_seconds: float = MIN_SCENE_SECONDS, ocr: OcrCallback | None = None,
                  speech_detector: IntervalDetector | None = None, music_detector: IntervalDetector | None = None,
                  use_default_ocr: bool = True, use_cv2: bool = True, limits: dict[str, Any] | None = None,
                  source_sha256: str | None = None, ffmpeg: str | None = None, ffprobe: str | None = None,
                  progress: Progress | None = None, now: str | None = None,
                  timeout: float = FFMPEG_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Analyze a video file and return a complete plan document (version 0, not yet stored)."""
    src = Path(path)
    if not src.is_file():
        raise AnalysisError(f"not a file: {src}")
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        raise AnalyzerUnavailable("ffmpeg not found (set BOSSMAN_FFMPEG or put ffmpeg on PATH)")
    ffprobe = ffprobe or find_ffprobe(ffmpeg)
    if not 0.0 < threshold <= 1.0:
        raise AnalysisError(f"threshold must be in (0, 1]: {threshold}")
    if min_scene_seconds < 0.0:
        raise AnalysisError("min_scene_seconds must not be negative")

    max_duration = LIMITS["max_duration_seconds"]
    max_bytes = LIMITS["max_source_bytes"]
    if limits:
        max_duration = min(max_duration, float(limits.get("max_duration_seconds", max_duration)))
        max_bytes = min(max_bytes, int(limits.get("max_source_bytes", max_bytes)))
    size = src.stat().st_size
    if size > max_bytes:
        raise LimitError(f"source is {size} bytes, declared limit {max_bytes}")

    def step(stage: str, fraction: float) -> None:
        if progress:
            progress(stage, fraction)

    step("probe", 0.0)
    info = probe(src, ffprobe=ffprobe)
    if info["duration"] > max_duration + 1e-6:
        raise LimitError(f"source duration {info['duration']}s exceeds the declared limit of {max_duration}s")
    if info["fps"] > LIMITS["max_fps"]:
        raise LimitError(f"source fps {info['fps']} exceeds the declared limit of {LIMITS['max_fps']}")
    fps = info["fps"]
    digest = source_sha256 or sha256_file(src)

    np = _load_numpy()
    cv2 = _load_cv2() if (use_cv2 and np is not None) else None
    warnings: list[str] = []

    step("scene-filter", 0.1)
    stream = info["video_stream_index"]
    rows = scene_scores(str(src), ffmpeg=ffmpeg, timeout=timeout, stream=stream)

    step("profile", 0.3)
    width, height = profile_size(info["display_width"], info["display_height"])
    profile: _Profile | None
    try:
        profile = build_profile(_raw_frames(str(src), ffmpeg, width, height, timeout, stream), width, height, np=np,
                                cv2=cv2, progress=progress, total=len(rows))
        if abs(profile.n - len(rows)) > 1:
            warnings.append(f"frame count mismatch between the scene filter ({len(rows)}) and the profile "
                            f"({profile.n}); cut types and camera motion may be misaligned")
    except AnalysisError as exc:
        profile = None
        warnings.append(f"frame-difference profile failed: {exc}")

    step("scenes", 0.7)
    n_frames = len(rows)
    if profile is not None and profile.n:
        n_frames = min(n_frames, profile.n) if abs(profile.n - len(rows)) <= 1 else len(rows)
    boundaries, start_fade, rejected, n_candidates = _boundaries(
        rows[:n_frames], threshold, profile, fps, min_scene_seconds, warnings)
    times = [r[1] for r in rows[:n_frames]]
    starts = [0] + [b["frame"] for b in boundaries]
    ends = starts[1:] + [n_frames]
    total_duration = info["duration"]

    audio_whole: dict[str, Any] | None = None
    if info["has_audio"]:
        step("audio", 0.8)
        try:
            audio_whole = analyze_audio(str(src), total_duration, ffmpeg=ffmpeg, np=np, noise_db=SILENCE_NOISE_DB,
                                        min_silence=SILENCE_MIN_SECONDS, speech_detector=speech_detector,
                                        music_detector=music_detector, timeout=timeout)
        except AnalysisError as exc:
            warnings.append(f"audio analysis failed: {exc}")

    scenes: list[dict[str, Any]] = []
    preset = mode == "preset"
    for index, (sf, ef) in enumerate(zip(starts, ends)):
        t0 = 0.0 if index == 0 else times[sf]
        t1 = total_duration if index == len(starts) - 1 else times[ef]
        if index == 0:
            cut_in: dict[str, Any] = (
                {"type": "fade", "score": None, "method": "luminance ramp from black/white at clip start",
                 "detail": start_fade, "reason": "the clip opens with a fade-in"} if start_fade else
                {"type": "hard", "score": None, "method": "clip start", "detail": {},
                 "reason": "first scene: nothing precedes it"})
        else:
            b = boundaries[index - 1]
            cut_in = {"type": b["type"], "score": b["score"], "source": b["source"],
                      "method": f"ffmpeg scene score >= {threshold} + frame-difference profile"
                                if b["source"] != "gradual-scan" else "frame-difference / luminance profile scan",
                      "detail": b["detail"], "reason": b["reason"]}
        if profile is not None:
            exclude: list[tuple[int, int]] = []
            if index == 0 and start_fade:
                exclude.append((0, start_fade["dip"][1] + start_fade["ramp_in_frames"]))
            for side in (index - 1, index):
                if 0 <= side < len(boundaries) and boundaries[side].get("span"):
                    exclude.append((boundaries[side]["span"][0], boundaries[side]["span"][1]))
            camera = classify_camera(profile, sf, ef, fps, width, height, np_available=np is not None,
                                     exclude=exclude)
        else:
            camera = {"class": "unknown", "magnitude": None, "samples": 0, "method": None,
                      "reason": unknown("analyzer unavailable (frame profile could not be built)")}
        audio_cues = scene_audio(audio_whole, t0, t1)
        scenes.append(planmod.make_scene(
            index, t0, t1, start_frame=sf, end_frame=ef, cut_in=cut_in, camera_motion=camera,
            audio=audio_cues, preset_mode=preset,
            text={"status": unknown("not analyzed"), "boxes": []}))

    step("text", 0.9)
    ocr_callback = ocr if ocr is not None else (_default_ocr() if use_default_ocr else None)
    scene_text(str(src), scenes, ocr_callback, ffmpeg=ffmpeg, timeout=timeout, stream=stream)
    for scene in scenes:
        scene["elements"] = _scene_elements(scene["text"], scene["audio"], preset)

    result = planmod.new_plan(title=title if title is not None else src.stem, category=category, mode=mode,
                              project_id=project_id, now=now)
    result["source"] = {
        "asset": digest, "ext": src.suffix.lower(), "file_name": src.name, "bytes": size,
        "duration": info["duration"], "width": info["width"], "height": info["height"],
        "display_width": info["display_width"], "display_height": info["display_height"],
        "aspect_ratio": info["aspect_ratio"], "fps": info["fps"], "fps_rational": info["fps_rational"],
        "variable_frame_rate_suspected": info["variable_frame_rate_suspected"], "rotation": info["rotation"],
        "video_codec": info["video_codec"], "pix_fmt": info["pix_fmt"], "has_audio": info["has_audio"],
        "audio_codec": info["audio_codec"], "audio_sample_rate": info["audio_sample_rate"],
        "audio_channels": info["audio_channels"], "frames": n_frames,
    }
    result["scenes"] = scenes
    video_lane = result["tracks"]["video"]
    video_lane["segments"] = [{"scene_id": s["id"], "start": s["start"], "end": s["end"], "action": "keep"}
                              for s in scenes]
    video_lane["status"] = "measured"
    for lane, block in (("speech", (audio_whole or {}).get("speech")), ("music", (audio_whole or {}).get("music"))):
        if audio_whole is None:
            result["tracks"][lane]["status"] = "no audio stream" if not info["has_audio"] else \
                unknown("audio analysis failed")
        elif block and not planmod.is_unknown(block["intervals"]):
            result["tracks"][lane]["segments"] = [{"start": a, "end": b, "action": "keep"}
                                                  for a, b in block["intervals"]]
            result["tracks"][lane]["status"] = f"measured by {block['method']}"
    result["analysis"] = {
        "tool": "bossman-motion-workflow", "version": ANALYZER_VERSION,
        "scene_detection": {
            "method": "ffmpeg select scene score, cut when score >= threshold",
            "threshold": threshold, "min_scene_seconds": min_scene_seconds, "scale_width": SCENE_FILTER_WIDTH,
            "candidates": n_candidates, "accepted_cuts": len(boundaries), "rejected": rejected,
            "note": "the ffmpeg scene filter looks at luma only: a cut between equally bright shots can be missed",
        },
        "parameters": {
            "profile_size": [width, height], "hard_spike_min": HARD_SPIKE_MIN, "hard_spike_ratio": HARD_SPIKE_RATIO,
            "black_luma": BLACK_LUMA, "white_luma": WHITE_LUMA, "pan_min": PAN_MIN, "tilt_min": TILT_MIN,
            "zoom_min": ZOOM_MIN, "jitter_min": JITTER_MIN, "silence_noise_db": SILENCE_NOISE_DB,
        },
        "motion_method": profile.motion_method if profile is not None else None,
        "analyzers": available(include_paths=False),
        "audio": ({"silence_detect": audio_whole["silence_detect"], "beat_method": audio_whole["beats"]["method"],
                   "tempo_bpm": audio_whole["beats"]["tempo_bpm"]} if audio_whole else None),
        "warnings": warnings,
        "source_sha256": digest,
        "analyzed_ts": now or planmod.now_iso(),
    }
    result["status"] = "analyzed"
    step("done", 1.0)
    planmod.validate(result)
    return result
