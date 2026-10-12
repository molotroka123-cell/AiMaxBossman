"""Local renderer for a validated motion-workflow plan.  ffmpeg / ffprobe only: no network, no paid call.

What this renderer EXECUTES (everything else is reported ``not_executed`` with a reason, never skipped
silently and never faked):

  * text burn-in         ``drawtext`` with an explicit ``fontfile`` and ``textfile`` (``expansion=none``):
                         the scenes whose ``text`` element is ``replace`` (text = bound ``@text`` reference,
                         else the scene prompt) and the cues of the ``subtitles`` lane;
  * cover                ``cover.png``: a frame at a chosen time (+ optional title text);
  * soundtrack           keep / replace with a supplied audio reference (trim, fades, two-pass loudnorm,
                         silence-padded to the video length) / mute.  The original soundtrack is ONE mixed
                         stream: speech and music cannot be separated here, so a lane combination that needs
                         the separation is reported ``not_executed`` and the original sound is kept;
  * export               H.264 yuv420p + AAC mp4.  Resolution, frame rate and duration are those of the
                         source unless the plan explicitly changes them (fit + pad, no stretching).

NOT executed here, by design: face / character, clothes, background and product replacement (they need
a face tool or masks and a generative step).  The picture of such a scene is left exactly as in the source.

Process model: every ffmpeg / ffprobe call is a SUBPROCESS with a hard time limit, a cancel event and
(for the render) progress parsed from ``-progress pipe:1``; nothing here runs inside an event loop.  A
render is "done" only after ``verify_output``: ffprobe facts compared with what the plan promised plus a
full decode of the file.  ``render_report.json`` records the ACTUAL parameters, command lines, hashes and
the per-scene applied / not_executed list.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from . import analyze as analyze_mod
from . import plan as planmod

RENDERER_NAME = "bossman-motion-workflow-render"
RENDERER_VERSION = 1
REPORT_VERSION = 1

NO_WINDOW = analyze_mod.NO_WINDOW
PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
PREVIEW_FRACTIONS = (0.15, 0.5, 0.85)       # three sample frames per scene (plan.COST_MODEL says three)
LOUDNORM_TARGET = {"I": -16.0, "TP": -1.5, "LRA": 11.0}
AUDIO_SAMPLE_RATE_REPLACED = 48000
DEFAULT_REPLACE_AUDIO = {"loudnorm": True, "fade_in": 0.3, "fade_out": 0.8, "ref_start": 0.0, "volume_db": 0.0}
DEFAULT_KEEP_AUDIO = {"loudnorm": False, "fade_in": 0.0, "fade_out": 0.0, "ref_start": 0.0, "volume_db": 0.0}
AUDIO_OPTION_LIMITS = {"fade_in": (0.0, 30.0), "fade_out": (0.0, 30.0), "ref_start": (0.0, 36000.0),
                       "volume_db": (-40.0, 20.0)}
POSITIONS = ("top", "center", "bottom")
MAX_TEXT_LINES = 5
MAX_OVERLAY_CHARS = 400
MAX_COVER_TITLE_CHARS = 120
EVIDENCE_SAMPLES = 6
CHANGED_PIXEL_DELTA = 32          # luma delta (0..255) above which a pixel counts as changed by an overlay

# Why an element is not run by this renderer.  The strings are stable: the UI maps ``reason_code``.
REASON_GENERATIVE = {
    "face_character": ("needs a face-replacement tool (FaceFusion class) and a recorded consent; "
                       "not run by this ffmpeg-only renderer"),
    "clothes": "needs person / clothes masks and a generative edit (GPU tool); not run by this ffmpeg-only renderer",
    "background": "needs matting / segmentation and a composite or generative step; not run by this ffmpeg-only renderer",
    "product": "needs object masks and a composite or generative step; not run by this ffmpeg-only renderer",
}
GENERATIVE_ELEMENTS = tuple(REASON_GENERATIVE)
PICTURE_UNCHANGED = "the picture of this scene is left exactly as in the source"

CAPABILITIES = {
    "executed": [
        {"id": "text_overlay", "title": "text burn-in (scene text, subtitle cues)"},
        {"id": "cover", "title": "cover.png: frame at a chosen time + optional title"},
        {"id": "soundtrack", "title": "soundtrack: keep / replace with a reference (trim, fades, loudnorm) / mute"},
        {"id": "export", "title": "export: H.264 yuv420p + AAC mp4, source resolution / fps / duration preserved"},
    ],
    "not_executed": [
        {"id": name, "reason_code": "needs_generative_tool", "reason": reason}
        for name, reason in REASON_GENERATIVE.items()
    ] + [
        {"id": "speech_music_separation", "reason_code": "cannot_separate_mixed_stream",
         "reason": "the original soundtrack is one mixed stream: replacing, muting or keeping only one of "
                   "speech / music needs source separation, which this renderer does not do"},
    ],
}


class RenderError(RuntimeError):
    code = "render_error"
    context: dict[str, Any] | None = None     # the command / graph of the failed run, when there was one


class RenderUnavailable(RenderError):
    code = "unavailable"


class RenderCancelled(RenderError):
    code = "cancelled"


class RenderTimeout(RenderError):
    code = "timeout"


class VerificationError(RenderError):
    code = "verification_failed"

    def __init__(self, message: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report or {}


# --------------------------------------------------------------------------------------------- environment

_ENV_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_ENV_LOCK = threading.Lock()


def find_font() -> Path | None:
    """An explicit font file (drawtext without ``fontfile`` crashes ffmpeg on Windows builds)."""
    candidates: list[Path] = []
    env = os.environ.get("BOSSMAN_MOTION_FONT", "").strip()
    if env:
        candidates.append(Path(env))
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    for name in ("arial.ttf", "segoeui.ttf", "tahoma.ttf", "verdana.ttf"):
        candidates.append(Path(windir) / "Fonts" / name)
    for raw in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        candidates.append(Path(raw))
    for path in candidates:
        with contextlib.suppress(OSError):
            if path.is_file() and path.stat().st_size > 1000:
                return path
    return None


def _run_capture(argv: list[str], timeout: float = 60.0, cwd: str | None = None) -> tuple[int, str, str]:
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout, cwd=cwd, creationflags=NO_WINDOW,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def _probe_environment(ffmpeg: str, ffprobe: str | None) -> dict[str, Any]:
    rc, out, err = _run_capture([ffmpeg, "-hide_banner", "-version"])
    version_line = (out or err).splitlines()[0] if (out or err).strip() else ""
    match = re.search(r"version\s+(\S+)", version_line)
    rc_f, filters_out, _ = _run_capture([ffmpeg, "-hide_banner", "-filters"])
    rc_e, encoders_out, _ = _run_capture([ffmpeg, "-hide_banner", "-encoders"])
    filter_names = set()
    for line in filters_out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) <= 3:
            filter_names.add(parts[1])
    encoder_names = set()
    for line in encoders_out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            encoder_names.add(parts[1])
    # capability probes: option spellings changed between ffmpeg releases
    script_opt, fps_opt = None, None
    tmp = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp") / f"mw-probe-{os.getpid()}-{threading.get_ident()}.txt"
    try:
        tmp.write_text("[0:v]null[v]", encoding="utf-8")
        for candidate in ("-/filter_complex", "-filter_complex_script"):
            rc_s, _, _ = _run_capture([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                       "nullsrc=s=16x16:d=0.1", candidate, str(tmp), "-map", "[v]", "-f", "null", "-"])
            if rc_s == 0:
                script_opt = candidate
                break
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    for candidate in (["-fps_mode", "passthrough"], ["-vsync", "passthrough"]):
        rc_p, _, _ = _run_capture([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                   "nullsrc=s=16x16:d=0.1", *candidate, "-f", "null", "-"])
        if rc_p == 0:
            fps_opt = candidate
            break
    return {"version_line": version_line, "version": match.group(1) if match else None,
            "has_drawtext": "drawtext" in filter_names, "has_loudnorm": "loudnorm" in filter_names,
            "has_libx264": "libx264" in encoder_names, "has_aac": "aac" in encoder_names,
            "filter_script_option": script_opt, "passthrough_option": fps_opt}


def environment(*, refresh: bool = False) -> dict[str, Any]:
    """What this machine can render with.  ``ready`` is true only when every required piece was probed."""
    ffmpeg = analyze_mod.find_ffmpeg()
    ffprobe = analyze_mod.find_ffprobe(ffmpeg)
    font = find_font()
    problems: list[str] = []
    info: dict[str, Any] = {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "font": str(font) if font else None}
    if not ffmpeg:
        problems.append("ffmpeg not found (set BOSSMAN_FFMPEG or put ffmpeg on PATH)")
    if not ffprobe:
        problems.append("ffprobe not found (needed to verify the output file)")
    if ffmpeg:
        try:
            st = Path(ffmpeg).stat()
            key = (ffmpeg, int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            key = (ffmpeg, 0, 0)
        with _ENV_LOCK:
            probed = None if refresh else _ENV_CACHE.get(key)
            if probed is None:
                probed = _probe_environment(ffmpeg, ffprobe)
                _ENV_CACHE[key] = probed
        info.update(probed)
        if not probed["has_libx264"]:
            problems.append("this ffmpeg has no libx264 encoder")
        if not probed["has_aac"]:
            problems.append("this ffmpeg has no aac encoder")
        if not probed["filter_script_option"]:
            problems.append("this ffmpeg accepts neither -/filter_complex nor -filter_complex_script")
        if not probed["passthrough_option"]:
            problems.append("this ffmpeg accepts neither -fps_mode nor -vsync")
    warnings: list[str] = []
    if ffmpeg and not info.get("has_drawtext"):
        warnings.append("this ffmpeg has no drawtext filter: text burn-in and cover titles are unavailable")
    if font is None:
        warnings.append("no usable font file found (set BOSSMAN_MOTION_FONT): text burn-in is unavailable, "
                        "drawtext crashes ffmpeg without an explicit font")
    info["problems"] = problems
    info["warnings"] = warnings
    info["ready"] = not problems
    info["text_ready"] = bool(not problems and info.get("has_drawtext") and font is not None)
    info["python"] = sys.version.split()[0]
    info["platform"] = platform.platform()
    return info


# --------------------------------------------------------------------------------------------- processes

def _kill(proc: subprocess.Popen) -> None:
    with contextlib.suppress(OSError, ProcessLookupError):
        proc.kill()


class ProcResult:
    def __init__(self, rc: int, stdout: str, stderr: str, wall: float) -> None:
        self.rc, self.stdout, self.stderr, self.wall_seconds = rc, stdout, stderr, round(wall, 3)


def _run(argv: list[str], *, cwd: str | os.PathLike | None = None, timeout: float = 600.0,
         cancel: threading.Event | None = None, log_path: Path | None = None,
         on_line: Callable[[str], None] | None = None, pid_path: Path | None = None,
         label: str = "ffmpeg") -> ProcResult:
    """Run one subprocess; kill it on cancel or timeout.

    ``log_path``: stderr goes to that file (append) and stdout is streamed line by line to ``on_line``.
    Without it both streams are captured.  Raises RenderCancelled / RenderTimeout / RenderUnavailable."""
    started = time.monotonic()
    stderr_target: Any = subprocess.PIPE
    log_handle = None
    if log_path is not None:
        log_handle = open(log_path, "ab")
        stderr_target = log_handle
    try:
        try:
            proc = subprocess.Popen(argv, cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=stderr_target, creationflags=NO_WINDOW)
        except OSError as exc:
            raise RenderUnavailable(f"cannot run {label}: {exc}") from exc
        if pid_path is not None:
            with contextlib.suppress(OSError):
                pid_path.write_text(json.dumps({"pid": proc.pid, "argv0": Path(argv[0]).name,
                                                "started": time.time()}), encoding="utf-8")
        state: dict[str, str | None] = {"killed": None}
        stop = threading.Event()

        def watch() -> None:
            deadline = time.monotonic() + timeout
            while not stop.is_set() and proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    state["killed"] = "cancelled"
                    _kill(proc)
                    return
                if time.monotonic() > deadline:
                    state["killed"] = "timeout"
                    _kill(proc)
                    return
                stop.wait(0.05)

        watcher = threading.Thread(target=watch, name="mw-render-watch", daemon=True)
        watcher.start()
        out_text = err_text = ""
        try:
            if log_path is not None:
                assert proc.stdout is not None
                chunks: list[str] = []
                for raw in proc.stdout:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if on_line is not None:
                        on_line(line)
                    else:
                        chunks.append(line)
                out_text = "\n".join(chunks)
                proc.wait()
            else:
                out_b, err_b = proc.communicate()
                out_text, err_text = out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")
        finally:
            stop.set()
            if proc.poll() is None:
                _kill(proc)
                with contextlib.suppress(Exception):
                    proc.wait(timeout=10)
            watcher.join(timeout=2)
            if pid_path is not None:
                with contextlib.suppress(OSError):
                    pid_path.unlink()
        if state["killed"] == "cancelled":
            raise RenderCancelled(f"{label} was cancelled")
        if state["killed"] == "timeout":
            raise RenderTimeout(f"{label} was stopped after the {timeout:.0f}s time limit")
        return ProcResult(proc.returncode, out_text, err_text, time.monotonic() - started)
    finally:
        if log_handle is not None:
            log_handle.close()


def _tail_file(path: Path, limit: int = 1500) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - limit * 4))
            return handle.read().decode("utf-8", "replace")[-limit:]
    except OSError:
        return ""


def ffprobe_json(path: str | os.PathLike, ffprobe: str, *, timeout: float = 60.0) -> dict[str, Any]:
    done = _run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                timeout=timeout, label="ffprobe")
    if done.rc != 0:
        raise RenderError(f"ffprobe could not read {Path(path).name}: {done.stderr.strip()[-300:]}")
    try:
        return json.loads(done.stdout)
    except ValueError as exc:
        raise RenderError(f"ffprobe returned invalid JSON for {Path(path).name}") from exc


# --------------------------------------------------------------------------------------------- text helpers

def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def cue_problems(cue: Any) -> list[str]:
    """Why a subtitle cue is unusable ([] = fine).  Cue shape: {"start": s, "end": s, "text": str}."""
    if not isinstance(cue, dict):
        return ["cue must be an object {start, end, text}"]
    found: list[str] = []
    start, end = cue.get("start"), cue.get("end")
    if not _num(start) or not _num(end) or start < 0 or end <= start:
        found.append("cue needs finite start >= 0 and end > start")
    text = cue.get("text")
    if not isinstance(text, str) or not text.strip():
        found.append("cue needs non-empty text")
    elif len(text) > MAX_OVERLAY_CHARS:
        found.append(f"cue text is longer than {MAX_OVERLAY_CHARS} characters")
    elif "\x00" in text:
        found.append("cue text contains NUL")
    return found


def expand_text_tokens(text: str, texts: dict[str, str]) -> str:
    """Replace ``@name`` tokens that name a text reference with that reference's content."""
    def sub(match: re.Match) -> str:
        return texts.get(match.group(1), match.group(0)).strip()
    return planmod._TOKEN_RE.sub(sub, text)


def wrap_text(text: str, max_px: float, font_px: int, max_lines: int = MAX_TEXT_LINES) -> tuple[str, bool]:
    """Wrap by an average glyph width (drawtext does not wrap).  Returns (text, truncated)."""
    max_chars = max(8, int(max_px / max(1.0, font_px * 0.56)))
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        wrapped = textwrap.wrap(paragraph, width=max_chars, break_long_words=True, break_on_hyphens=False)
        lines.extend(wrapped or [""])
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(0, max_chars - 1)] + "\u2026"
    return "\n".join(lines).strip("\n"), truncated


def _fmt(value: float) -> str:
    return f"{value:.3f}"


# --------------------------------------------------------------------------------------------- resolve

def _lane_state(lane: Any) -> tuple[str, str | None]:
    """(mode, reference name or None) of an audio lane; a disabled lane contributes nothing (= mute)."""
    if not isinstance(lane, dict) or lane.get("enabled") is False:
        return "mute", None
    mode = lane.get("mode")
    source = lane.get("source")
    ref = source[1:] if isinstance(source, str) and source.startswith("@") else None
    if mode == "replace":
        return ("replace", ref)
    return (mode if mode in ("keep", "mute") else "keep"), None


def audio_options(plan: dict[str, Any], action: str) -> dict[str, Any]:
    base = dict(DEFAULT_REPLACE_AUDIO if action == "replace" else DEFAULT_KEEP_AUDIO)
    given = plan.get("audio_options")
    if isinstance(given, dict):
        for key, value in given.items():
            if key == "loudnorm" and isinstance(value, bool):
                base[key] = value
            elif key in AUDIO_OPTION_LIMITS and _num(value):
                lo, hi = AUDIO_OPTION_LIMITS[key]
                base[key] = min(hi, max(lo, float(value)))
    return base


def audio_decision(plan: dict[str, Any], *, source_has_audio: bool) -> dict[str, Any]:
    """What happens to the soundtrack, derived from the speech and music lanes (see the module docstring)."""
    tracks = plan.get("tracks") or {}
    sp_mode, sp_ref = _lane_state(tracks.get("speech"))
    mu_mode, mu_ref = _lane_state(tracks.get("music"))
    not_executed: list[dict[str, Any]] = []
    action, reference = "keep", None

    def blocked(lane: str, mode: str, why_code: str, why: str) -> None:
        not_executed.append({"lane": lane, "mode": mode, "reason_code": why_code, "reason": why})

    sep = ("the original soundtrack is one mixed stream: speech and music cannot be separated by this "
           "renderer, so the original sound is kept as it is")
    if sp_mode == "keep" and mu_mode == "keep":
        action = "keep"
    elif sp_mode == "mute" and mu_mode == "mute":
        action = "mute"
    elif "replace" in (sp_mode, mu_mode):
        refs = [r for r in (sp_ref, mu_ref) if r]
        modes = {"speech": sp_mode, "music": mu_mode}
        others = [m for lane, m in modes.items() if m != "replace"]
        missing = [lane for lane, m in modes.items() if m == "replace" and not {"speech": sp_ref, "music": mu_ref}[lane]]
        if missing:
            for lane in missing:
                blocked(lane, "replace", "no_reference", "mode is replace but no @reference is bound to the lane")
            action = "keep"
        elif len(set(refs)) > 1:
            action, reference = "replace", mu_ref or refs[0]
            blocked("speech" if mu_ref else "music", "replace", "two_references_not_supported",
                    "speech and music are replaced by two different references; mixing two replacement "
                    "tracks is not supported, only one reference is used as the soundtrack")
        elif all(m in ("mute", "replace") for m in modes.values()):
            action, reference = "replace", refs[0]
        else:
            action = "keep"
            for lane, mode in modes.items():
                if mode != "keep":
                    blocked(lane, mode, "cannot_separate_mixed_stream", sep)
    else:
        action = "keep"
        for lane, mode in (("speech", sp_mode), ("music", mu_mode)):
            if mode != "keep":
                blocked(lane, mode, "cannot_separate_mixed_stream", sep)
    if action == "keep" and not source_has_audio:
        action = "none"
    options = audio_options(plan, "replace" if action == "replace" else "keep")
    return {"action": action, "reference": reference,
            "lanes": {"speech": {"mode": sp_mode, "reference": sp_ref}, "music": {"mode": mu_mode, "reference": mu_ref}},
            "not_executed": not_executed, "options": options, "source_has_audio": source_has_audio,
            "note": ("the original soundtrack is one mixed stream (speech / music not separated)"
                     if action in ("keep", "none") else "")}


def cover_spec(plan: dict[str, Any], duration: float) -> dict[str, Any]:
    cover = plan.get("cover") if isinstance(plan.get("cover"), dict) else None
    time_value = cover.get("time") if cover else None
    if _num(time_value) and time_value >= 0:
        time_s, source = min(float(time_value), max(0.0, duration - 0.05)), "plan.cover.time"
    else:
        time_s, source = round(max(0.0, duration / 2.0), 3), "default: middle of the video"
    title = str(cover.get("title") or "").strip() if cover else ""
    position = cover.get("position") if cover and cover.get("position") in POSITIONS else "center"
    return {"requested": cover is not None, "time": round(time_s, 3), "time_source": source,
            "title": title[:MAX_COVER_TITLE_CHARS], "position": position, "file": "cover.png"}


def source_geometry(plan: dict[str, Any]) -> dict[str, Any]:
    """Coded frame size ffmpeg will see after autorotate, fps and the resolved output."""
    source = plan.get("source") or {}
    width, height = int(source.get("width") or 0), int(source.get("height") or 0)
    if int(source.get("rotation") or 0) in (90, 270):
        width, height = height, width
    out = planmod.effective_output(plan)
    out_w, out_h, out_fps = out.get("width"), out.get("height"), out.get("fps")
    warnings: list[str] = []
    size_changed = False
    if isinstance(out_w, int) and isinstance(out_h, int):
        disp_w, disp_h = int(source.get("display_width") or width), int(source.get("display_height") or height)
        if (out_w, out_h) != (disp_w, disp_h):
            size_changed = True
            if out_w % 2 or out_h % 2:
                out_w, out_h = out_w // 2 * 2, out_h // 2 * 2
                warnings.append("output size was rounded down to even numbers (yuv420p needs them)")
        final_w, final_h = (out_w, out_h) if size_changed else (width, height)
    else:
        final_w, final_h = width, height
    src_fps = float(source.get("fps") or 0.0)
    fps_changed = _num(out_fps) and src_fps > 0 and abs(float(out_fps) - src_fps) > 0.001
    return {"source_width": width, "source_height": height, "width": final_w, "height": final_h,
            "size_changed": size_changed, "source_fps": src_fps,
            "fps": float(out_fps) if fps_changed else src_fps, "fps_changed": bool(fps_changed),
            "aspect_ratio": out.get("aspect_ratio"), "warnings": warnings}


def resolve(plan: dict[str, Any], texts: dict[str, str] | None = None) -> dict[str, Any]:
    """Turn a plan into concrete, checkable operations.  Pure: no file access, no ffmpeg."""
    texts = dict(texts or {})
    source = plan.get("source") or {}
    tracks = plan.get("tracks") or {}
    scenes = plan.get("scenes") or []
    geometry = source_geometry(plan)
    duration = float(source.get("duration") or 0.0)
    warnings: list[str] = list(geometry["warnings"])
    operations: list[dict[str, Any]] = []
    overlays: list[dict[str, Any]] = []
    audio = audio_decision(plan, source_has_audio=bool(source.get("has_audio")))
    height = geometry["height"] or 0
    width = geometry["width"] or 0

    def add_op(kind: str, status: str, **fields: Any) -> dict[str, Any]:
        entry = {"id": f"op{len(operations) + 1:03d}", "kind": kind, "status": status, **fields}
        operations.append(entry)
        return entry

    scene_reports: list[dict[str, Any]] = []
    for scene in scenes:
        sid = scene.get("id")
        elements = scene.get("elements") or {}
        items: list[dict[str, Any]] = []
        generative_replaced = [n for n in GENERATIVE_ELEMENTS
                               if (elements.get(n) or {}).get("policy") == "replace"]
        for name in planmod.ELEMENTS:
            element = elements.get(name) or {}
            policy = element.get("policy")
            item: dict[str, Any] = {"element": name, "policy": policy, "with": element.get("with")}
            if policy == "keep":
                item.update(status="kept", detail="unchanged: this part of the scene stays as in the source")
                if name == "text" and (scene.get("text") or {}).get("boxes"):
                    item["note"] = "the source shows on-screen text here; it is left as is"
            elif policy != "replace":
                item.update(status="not_executed", reason_code="undecided",
                            reason="the element policy is unknown: nothing was done for it")
                add_op("element", "not_executed", scene_id=sid, element=name, reason_code="undecided",
                       reason=item["reason"])
            elif name in GENERATIVE_ELEMENTS:
                reason = f"{REASON_GENERATIVE[name]}; {PICTURE_UNCHANGED}"
                item.update(status="not_executed", reason_code="needs_generative_tool", reason=reason)
                add_op("element", "not_executed", scene_id=sid, element=name,
                       reason_code="needs_generative_tool", reason=reason)
            elif name == "text":
                ref_name = element.get("with")[1:] if isinstance(element.get("with"), str) else None
                raw = texts.get(ref_name, "") if ref_name else str(scene.get("prompt") or "")
                body = expand_text_tokens(raw, texts).strip()
                if ref_name and ref_name not in texts:
                    item.update(status="not_executed", reason_code="text_reference_unreadable",
                                reason=f"the text reference @{ref_name} could not be read")
                    add_op("element", "not_executed", scene_id=sid, element=name,
                           reason_code="text_reference_unreadable", reason=item["reason"])
                elif not body:
                    item.update(status="not_executed", reason_code="no_text",
                                reason="the text element is replace but there is no text (no @text reference, "
                                       "empty scene prompt)")
                    add_op("element", "not_executed", scene_id=sid, element=name, reason_code="no_text",
                           reason=item["reason"])
                else:
                    if len(body) > MAX_OVERLAY_CHARS:
                        body = body[:MAX_OVERLAY_CHARS]
                        warnings.append(f"scene {sid}: overlay text was cut to {MAX_OVERLAY_CHARS} characters")
                    overlay = {"id": f"scene_{sid}", "kind": "scene_text", "scene_id": sid,
                               "start": float(scene["start"]), "end": float(scene["end"]), "text": body,
                               "position": "top", "source": f"@{ref_name}" if ref_name else "scene prompt"}
                    overlays.append(overlay)
                    item.update(status="applied", detail=f"text burned in from {overlay['source']}, "
                                                         f"{_fmt(overlay['start'])}-{_fmt(overlay['end'])} s",
                                overlay_id=overlay["id"])
                    if generative_replaced and not ref_name:
                        item["note"] = (f"the scene prompt is shared with {', '.join(generative_replaced)} "
                                        "(not executed); it is used verbatim as the overlay text")
                    if (scene.get("text") or {}).get("boxes"):
                        item.setdefault("note", "the new text is drawn ON TOP: the source's own on-screen "
                                                "text is not removed")
                    add_op("element", "applied", scene_id=sid, element=name, overlay_id=overlay["id"])
            elif name == "music":
                if audio["action"] == "replace":
                    item.update(status="applied", detail="the soundtrack is replaced for the whole video "
                                                         "(see the soundtrack lane)")
                    add_op("element", "applied", scene_id=sid, element=name, via="soundtrack lane")
                else:
                    reason = ("music is a soundtrack-level lane in this renderer: set the music lane to "
                              "replace with a reference; a per-scene music replacement is not executed")
                    item.update(status="not_executed", reason_code="music_is_soundtrack_lane", reason=reason)
                    add_op("element", "not_executed", scene_id=sid, element=name,
                           reason_code="music_is_soundtrack_lane", reason=reason)
            items.append(item)
        scene_reports.append({"scene_id": sid, "start": scene.get("start"), "end": scene.get("end"),
                              "items": items})

    # subtitles lane
    subs = tracks.get("subtitles") or {}
    cues_total = 0
    cues_ok = 0
    if subs.get("enabled") and subs.get("mode") != "mute":
        for index, cue in enumerate(subs.get("cues") or []):
            cues_total += 1
            found = cue_problems(cue)
            if found:
                warnings.append(f"subtitle cue {index}: {'; '.join(found)}; skipped")
                continue
            cues_ok += 1
            overlays.append({"id": f"cue_{index:04d}", "kind": "subtitle", "scene_id": None,
                             "start": float(cue["start"]), "end": min(float(cue["end"]), duration or float(cue["end"])),
                             "text": cue["text"].strip(), "position": "bottom", "source": "subtitles lane"})
        if cues_total:
            skipped = cues_total - cues_ok
            if cues_ok:
                add_op("subtitles", "applied", lane="subtitles", cues=cues_ok, skipped=skipped)
            if skipped:
                add_op("subtitles", "not_executed", lane="subtitles", cues=skipped, reason_code="invalid_cue",
                       reason="cues with a bad time range or empty text were skipped")
    overlays = [o for o in overlays if o["end"] > o["start"] and (duration <= 0 or o["start"] < duration)]

    # video lane: only "keep" is executed
    video_lane = tracks.get("video") or {}
    if video_lane.get("mode") not in (None, "keep"):
        add_op("lane", "not_executed", lane="video", reason_code="video_lane_not_supported",
               reason=f"video lane mode {video_lane.get('mode')!r} is not executed; the picture is kept")

    # soundtrack lanes
    for lane in ("speech", "music"):
        state = audio["lanes"][lane]
        if state["mode"] == "keep":
            continue
        failed = next((x for x in audio["not_executed"] if x["lane"] == lane), None)
        if failed:
            add_op("lane", "not_executed", lane=lane, mode=failed["mode"], reason_code=failed["reason_code"],
                   reason=failed["reason"])
        else:
            add_op("lane", "applied", lane=lane, mode=state["mode"], reference=state["reference"],
                   action=audio["action"])

    # export changes the plan asked for
    if geometry["size_changed"]:
        add_op("export", "applied", what="size", to=f"{geometry['width']}x{geometry['height']}",
               detail="fit inside the new frame and pad (no stretching)")
    if geometry["fps_changed"]:
        add_op("export", "applied", what="fps", to=geometry["fps"])
    cover = cover_spec(plan, duration)
    if cover["requested"]:
        add_op("cover", "applied", time=cover["time"], title=bool(cover["title"]))

    counts = {"requested": len(operations),
              "applied": sum(1 for o in operations if o["status"] == "applied"),
              "not_executed": sum(1 for o in operations if o["status"] == "not_executed")}
    counts["kept"] = sum(1 for r in scene_reports for i in r["items"] if i["status"] == "kept")
    counts["complete"] = counts["not_executed"] == 0
    return {"geometry": geometry, "overlays": overlays, "audio": audio, "cover": cover, "operations": operations,
            "scenes": scene_reports, "counts": counts, "warnings": warnings,
            "subtitle_cues": {"total": cues_total, "used": cues_ok},
            "frame": {"width": width, "height": height}}


def summary_line(counts: dict[str, Any]) -> str:
    if counts["not_executed"] == 0:
        return f"all {counts['requested']} requested operation(s) were applied" if counts["requested"] else \
            "nothing was requested beyond a re-encode of the source"
    return (f"PARTIAL: {counts['applied']} of {counts['requested']} requested operation(s) applied, "
            f"{counts['not_executed']} NOT executed (see not_executed per scene)")


# --------------------------------------------------------------------------------------------- graphs

def _drawtext(overlay: dict[str, Any], frame_w: int, frame_h: int, textfile: str, *, enable: bool = True,
              font_name: str = "font.ttf") -> str:
    kind = overlay.get("kind")
    ratio = {"scene_text": 0.055, "subtitle": 0.045, "cover_title": 0.09}.get(kind, 0.05)
    size = max(16, int(round(frame_h * ratio)))
    border = max(1, size // 16)
    pad = max(6, int(round(size * 0.3)))
    position = overlay.get("position", "bottom")
    margin = int(round(frame_h * 0.07))
    if position == "top":
        y = str(int(round(frame_h * 0.12)))
    elif position == "center":
        y = "(h-text_h)/2"
    else:
        y = f"h-text_h-{margin}"
    parts = [f"fontfile={font_name}", f"textfile={textfile}", "expansion=none", f"fontsize={size}",
             "fontcolor=white", f"borderw={border}", "bordercolor=black", "box=1", "boxcolor=black@0.45",
             f"boxborderw={pad}", "x=(w-text_w)/2", f"y={y}"]
    if enable:
        parts.append(f"enable='gte(t,{_fmt(overlay['start'])})*lt(t,{_fmt(overlay['end'])})'")
    return "drawtext=" + ":".join(parts)


def _write_textfiles(overlays: Iterable[dict[str, Any]], frame_w: int, frame_h: int, folder: Path,
                     warnings: list[str]) -> dict[str, str]:
    """overlay id -> text file name (relative to ``folder``); the wrapped text is written UTF-8, LF."""
    written: dict[str, str] = {}
    folder.mkdir(parents=True, exist_ok=True)
    for overlay in overlays:
        kind = overlay.get("kind")
        ratio = {"scene_text": 0.055, "subtitle": 0.045, "cover_title": 0.09}.get(kind, 0.05)
        size = max(16, int(round(frame_h * ratio)))
        wrapped, truncated = wrap_text(overlay["text"], frame_w * 0.86, size)
        if truncated:
            warnings.append(f"{overlay['id']}: text did not fit in {MAX_TEXT_LINES} lines and was truncated")
        name = f"{overlay['id']}.txt"
        (folder / name).write_bytes(wrapped.encode("utf-8"))
        written[overlay["id"]] = name
    return written


def video_chain(resolved: dict[str, Any], textfiles: dict[str, str], *, overlays: list[dict[str, Any]] | None = None,
                include_fps: bool = True) -> list[str]:
    geo = resolved["geometry"]
    chain: list[str] = []
    if include_fps and geo["fps_changed"]:
        chain.append(f"fps=fps={geo['fps']:g}")
    if geo["size_changed"]:
        chain.append(f"scale={geo['width']}:{geo['height']}:force_original_aspect_ratio=decrease:flags=lanczos")
        chain.append(f"pad={geo['width']}:{geo['height']}:(ow-iw)/2:(oh-ih)/2:color=black")
        chain.append("setsar=1")
    for overlay in (resolved["overlays"] if overlays is None else overlays):
        chain.append(_drawtext(overlay, geo["width"], geo["height"], textfiles[overlay["id"]]))
    return chain


# --------------------------------------------------------------------------------------------- prepare

def prepare(plan: dict[str, Any], *, source_path: str | os.PathLike, ref_paths: dict[str, str | os.PathLike],
            texts: dict[str, str], env: dict[str, Any] | None = None, verify_source_hash: bool = True) -> dict[str, Any]:
    """Everything a run needs, checked: tool availability, source hash, reference files, resolved operations."""
    env = env or environment()
    if not env["ready"]:
        raise RenderUnavailable("renderer is not ready: " + "; ".join(env["problems"]))
    source = plan.get("source")
    if not isinstance(source, dict) or not source.get("asset"):
        raise RenderError("this renderer needs an analyzed source video (own_video mode)")
    src = Path(source_path)
    if not src.is_file():
        raise RenderError(f"source video file is missing: {src.name}")
    digest = analyze_mod.sha256_file(src) if verify_source_hash else source["asset"]
    if digest != source["asset"]:
        raise RenderError("source video does not match the hash recorded in the plan (file changed or damaged)")
    resolved = resolve(plan, texts)
    refs: dict[str, dict[str, Any]] = {}
    action = resolved["audio"]["action"]
    if action == "replace":
        name = resolved["audio"]["reference"]
        path = ref_paths.get(name)
        if path is None or not Path(path).is_file():
            raise RenderError(f"audio reference @{name} is not available on disk")
        info = ffprobe_json(path, env["ffprobe"])
        stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
        if stream is None:
            raise RenderError(f"audio reference @{name} has no audio stream")
        try:
            ref_duration = float((info.get("format") or {}).get("duration") or stream.get("duration"))
        except (TypeError, ValueError):
            raise RenderError(f"audio reference @{name}: cannot determine its duration") from None
        refs[name] = {"path": str(path), "duration": round(ref_duration, 3), "codec": stream.get("codec_name"),
                      "sample_rate": stream.get("sample_rate"), "channels": stream.get("channels"),
                      "sha256": analyze_mod.sha256_file(path)}
    font = Path(env["font"]) if env.get("font") else None
    needs_font = bool(resolved["overlays"]) or bool(resolved["cover"]["title"])
    if needs_font and not env.get("text_ready"):
        raise RenderUnavailable("text burn-in is unavailable on this machine: "
                                + "; ".join(env.get("warnings") or ["no font or no drawtext filter"]))
    return {"env": env, "source": {"path": str(src), "sha256": digest, "bytes": src.stat().st_size,
                                    "duration": float(source.get("duration") or 0.0), "fps": float(source.get("fps") or 0.0),
                                    "has_audio": bool(source.get("has_audio")), "audio_codec": source.get("audio_codec"),
                                    "video_codec": source.get("video_codec"), "pix_fmt": source.get("pix_fmt"),
                                    "frames": source.get("frames")},
            "resolved": resolved, "refs": refs, "font": str(font) if font else None,
            "plan": {"project_id": plan.get("project_id"), "version": plan.get("version"),
                     "content_sha256": planmod.content_sha256(plan)},
            "render_settings": dict(plan.get("render") or {})}


# --------------------------------------------------------------------------------------------- workdir

def _prepare_workdir(prepared: dict[str, Any], folder: Path) -> Path:
    work = folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    if prepared["font"]:
        shutil.copyfile(prepared["font"], work / "font.ttf")      # relative name: no drive-letter escaping
    return work


def _clean_workdir(work: Path) -> None:
    with contextlib.suppress(OSError):
        (work / "font.ttf").unlink()


# --------------------------------------------------------------------------------------------- preview

def _frame_png(env: dict[str, Any], src: str, t: float, vf: str | None, out: Path, work: Path,
               cancel: threading.Event | None, timeout: float) -> ProcResult:
    argv = [env["ffmpeg"], "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{max(t, 0.0):.3f}",
            "-i", src]
    if vf:
        argv += ["-vf", vf]
    argv += ["-frames:v", "1", "-update", "1", str(out)]
    done = _run(argv, cwd=work, timeout=timeout, cancel=cancel)
    if done.rc != 0 or not out.is_file() or out.stat().st_size < 200:
        raise RenderError(f"frame extraction failed at t={t:.3f}s: {done.stderr.strip()[-300:]}")
    return done


def make_cover(prepared: dict[str, Any], out_dir: Path, *, from_video: str | None = None,
               cancel: threading.Event | None = None, timeout: float = 120.0) -> dict[str, Any]:
    """cover.png: the frame at the chosen time (+ title).  From the rendered video when given, otherwise
    from the source with the scene / subtitle overlays that are active at that time."""
    env, resolved = prepared["env"], prepared["resolved"]
    cover = dict(resolved["cover"])
    geo = resolved["geometry"]
    t = cover["time"]
    work = _prepare_workdir(prepared, out_dir)
    warnings: list[str] = []
    try:
        chain: list[str] = []
        extra: list[dict[str, Any]] = []
        if from_video is None:
            active = [o for o in resolved["overlays"] if o["start"] <= t < o["end"]]
            extra.extend(active)
        title_overlay = None
        if cover["title"]:
            title_overlay = {"id": "cover_title", "kind": "cover_title", "text": cover["title"],
                             "position": cover["position"], "start": 0.0, "end": 1.0}
            extra.append(title_overlay)
        files = _write_textfiles(extra, geo["width"], geo["height"], work, warnings)
        if from_video is None:
            chain.append(f"setpts=PTS+{t:.3f}/TB")
            chain.extend(video_chain(resolved, files, overlays=[o for o in extra if o is not title_overlay],
                                     include_fps=False))
        if title_overlay is not None:
            chain.append(_drawtext(title_overlay, geo["width"], geo["height"], files["cover_title"], enable=False))
        target = out_dir / "cover.png"
        tmp = out_dir / "cover.part.png"
        vf = ",".join(chain) if chain else None
        _frame_png(env, from_video or prepared["source"]["path"], t, vf, tmp, work, cancel, timeout)
        os.replace(tmp, target)
        from_label = "rendered video" if from_video else "source video with the active overlays"
        return {**cover, "file": "cover.png", "bytes": target.stat().st_size, "frame_from": from_label,
                "sha256": analyze_mod.sha256_file(target), "warnings": warnings, "vf": vf}
    finally:
        _clean_workdir(work)


def run_preview(prepared: dict[str, Any], out_dir: str | os.PathLike, *, cancel: threading.Event | None = None,
                on_progress: Callable[[str, float, dict[str, Any]], None] | None = None,
                on_scene: Callable[[str, list[str]], None] | None = None, skip_scenes: Iterable[str] = (),
                timeout: float = 600.0) -> dict[str, Any]:
    """Three sample frames per scene (with the scene's overlays) and the cover, from the SOURCE video."""
    folder = Path(out_dir)
    preview = folder / "preview"
    preview.mkdir(parents=True, exist_ok=True)
    env, resolved = prepared["env"], prepared["resolved"]
    geo = resolved["geometry"]
    work = _prepare_workdir(prepared, folder)
    started = time.monotonic()
    skip = set(skip_scenes)
    scenes = resolved["scenes"]
    warnings: list[str] = []
    produced: dict[str, list[str]] = {}
    commands: list[dict[str, Any]] = []
    try:
        files = _write_textfiles(resolved["overlays"], geo["width"], geo["height"], work, warnings)
        for index, scene in enumerate(scenes):
            sid = scene["scene_id"]
            names: list[str] = []
            if sid in skip and all((preview / f"{sid}_{k}.png").is_file() for k in (1, 2, 3)):
                produced[sid] = [f"{sid}_{k}.png" for k in (1, 2, 3)]
                continue
            start, end = float(scene["start"]), float(scene["end"])
            for k, fraction in enumerate(PREVIEW_FRACTIONS, start=1):
                if cancel is not None and cancel.is_set():
                    raise RenderCancelled("preview was cancelled")
                t = start + fraction * (end - start)
                frame_step = 1.0 / max(prepared["source"]["fps"], 1.0)
                t = min(max(t, start), max(start, end - frame_step))
                active = [o for o in resolved["overlays"] if o["start"] <= t < o["end"]]
                chain = [f"setpts=PTS+{t:.3f}/TB", *video_chain(resolved, files, overlays=active, include_fps=False)]
                out = preview / f"{sid}_{k}.png"
                tmp = preview / f"{sid}_{k}.part.png"
                done = _frame_png(env, prepared["source"]["path"], t, ",".join(chain), tmp, work, cancel, timeout)
                os.replace(tmp, out)
                names.append(out.name)
                if index == 0 and k == 1:
                    commands.append({"purpose": "preview frame (first one shown as an example)",
                                     "vf": ",".join(chain), "t": round(t, 3), "wall_seconds": done.wall_seconds})
            produced[sid] = names
            if on_scene and sid not in skip:        # a scene the job already recorded is regenerated, not re-recorded
                on_scene(sid, names)
            if on_progress:
                on_progress("preview", (index + 1) / max(1, len(scenes)), {"scene": sid})
        cover = make_cover(prepared, folder, cancel=cancel, timeout=timeout)
        if on_progress:
            on_progress("preview", 1.0, {"cover": True})
    finally:
        _clean_workdir(work)
    return {"frames": produced, "cover": cover, "commands": commands, "warnings": warnings + cover.get("warnings", []),
            "wall_seconds": round(time.monotonic() - started, 3)}


# --------------------------------------------------------------------------------------------- audio

_JSON_BLOCK = re.compile(r"\{[^{}]*\}", re.S)


def measure_loudness(env: dict[str, Any], src: str, *, trim: str = "", cancel: threading.Event | None = None,
                     timeout: float = 600.0, audio_map: str = "0:a:0") -> dict[str, Any]:
    """EBU R128 measurement with ffmpeg ``loudnorm`` (print_format=json); ``{}`` when it cannot be read."""
    flt = f"{trim}loudnorm=I={LOUDNORM_TARGET['I']}:TP={LOUDNORM_TARGET['TP']}:LRA={LOUDNORM_TARGET['LRA']}:print_format=json"
    done = _run([env["ffmpeg"], "-hide_banner", "-nostdin", "-nostats", "-i", src, "-map", audio_map, "-vn",
                 "-af", flt, "-f", "null", "-"], timeout=timeout, cancel=cancel)
    if done.rc != 0:
        return {}
    blocks = _JSON_BLOCK.findall(done.stderr)
    for block in reversed(blocks):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if "input_i" in data:
            return data
    return {}


def _loudnorm_filter(measured: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    t = LOUDNORM_TARGET
    base = f"loudnorm=I={t['I']}:TP={t['TP']}:LRA={t['LRA']}"
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    try:
        values = {k: float(measured[k]) for k in keys}
    except (KeyError, TypeError, ValueError):
        return base, {"mode": "single-pass (dynamic)", "measured": None}
    if not all(math.isfinite(v) for v in values.values()):
        return "", {"mode": "skipped", "measured": values, "reason": "the reference is silent (no finite loudness)"}
    flt = (f"{base}:measured_I={values['input_i']}:measured_TP={values['input_tp']}:"
           f"measured_LRA={values['input_lra']}:measured_thresh={values['input_thresh']}:"
           f"offset={values['target_offset']}:linear=true")
    return flt, {"mode": "two-pass (linear)", "measured": values}


def _audio_graph(prepared: dict[str, Any], duration: float, cancel: threading.Event | None,
                 timeout: float) -> tuple[list[str], list[str], dict[str, Any]]:
    """(extra input args, filter-graph fragments producing [aout], parameters) for the audio decision."""
    env, resolved = prepared["env"], prepared["resolved"]
    audio = resolved["audio"]
    action, options = audio["action"], dict(audio["options"])
    params: dict[str, Any] = {"action": action, "reference": audio["reference"], "options": options,
                              "lanes": audio["lanes"], "not_executed": audio["not_executed"]}
    needs_filter = (action == "replace" or options["loudnorm"] or options["fade_in"] > 0 or options["fade_out"] > 0
                    or abs(options["volume_db"]) > 1e-9)
    if action in ("mute", "none") or not needs_filter:
        return [], [], params
    if action == "replace":
        ref = prepared["refs"][audio["reference"]]
        input_args = ["-i", ref["path"]]
        label, available = "[1:a:0]", max(0.0, ref["duration"] - options["ref_start"])
        params["reference_file"] = {k: ref[k] for k in ("duration", "codec", "sample_rate", "channels", "sha256")}
        trim_prefix = f"atrim=start={options['ref_start']:.3f},asetpts=PTS-STARTPTS,"
        measure_src, measure_trim, measure_map = ref["path"], f"atrim=start={options['ref_start']:.3f},", "0:a:0"
    else:
        input_args = []
        label, available = "[0:a:0]", duration
        trim_prefix = ""
        measure_src, measure_trim, measure_map = prepared["source"]["path"], "", "0:a:0"
    length = max(0.001, min(available, duration))
    chain: list[str] = []
    if trim_prefix:
        chain.append(trim_prefix.rstrip(","))
    chain.append(f"atrim=end={length:.3f}")
    chain.append("asetpts=PTS-STARTPTS")
    params["sample_rate"] = AUDIO_SAMPLE_RATE_REPLACED
    if not options["loudnorm"]:
        chain.append(f"aresample={AUDIO_SAMPLE_RATE_REPLACED}")
    if options["loudnorm"]:
        measured = measure_loudness(env, measure_src, trim=measure_trim + f"atrim=end={length:.3f},",
                                    cancel=cancel, timeout=timeout, audio_map=measure_map)
        flt, info = _loudnorm_filter(measured)
        info["target"] = dict(LOUDNORM_TARGET)
        params["loudnorm"] = info
        if flt:
            chain.append(flt)
        # loudnorm works at 192 kHz internally; bring the stream back to a normal rate
        chain.append(f"aresample={AUDIO_SAMPLE_RATE_REPLACED}")
    if abs(options["volume_db"]) > 1e-9:
        chain.append(f"volume={options['volume_db']:g}dB")
    fade_in = min(options["fade_in"], length / 2.0)
    fade_out = min(options["fade_out"], length / 2.0)
    if fade_in > 0:
        chain.append(f"afade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        chain.append(f"afade=t=out:st={max(0.0, length - fade_out):.3f}:d={fade_out:.3f}")
    padded = max(0.0, duration - length)
    if padded > 0.0005:
        chain.append(f"apad=whole_dur={duration:.3f}")
    chain.append(f"atrim=end={duration:.3f}")
    params["applied_seconds"] = round(length, 3)
    params["padded_silence_seconds"] = round(padded, 3)
    params["fade_in_applied"] = round(fade_in, 3)
    params["fade_out_applied"] = round(fade_out, 3)
    params["target_duration"] = round(duration, 3)
    return input_args, [f"{label}{','.join(chain)}[aout]"], params


# --------------------------------------------------------------------------------------------- render

def _parse_progress_line(line: str, state: dict[str, Any]) -> bool:
    """Feed one ``-progress`` line; True when a full block (``progress=...``) was completed."""
    if "=" not in line:
        return False
    key, _, value = line.partition("=")
    key, value = key.strip(), value.strip()
    if key in ("out_time_us", "out_time_ms"):
        with contextlib.suppress(ValueError):
            state["out_time"] = max(0.0, int(value) / 1_000_000.0)
    elif key == "frame":
        with contextlib.suppress(ValueError):
            state["frame"] = int(value)
    elif key == "fps":
        with contextlib.suppress(ValueError):
            state["fps"] = float(value)
    elif key == "speed":
        state["speed"] = value
    elif key == "progress":
        state["progress"] = value
        return True
    return False


def run_render(prepared: dict[str, Any], out_dir: str | os.PathLike, *, cancel: threading.Event | None = None,
               on_progress: Callable[[str, float, dict[str, Any]], None] | None = None,
               on_scene: Callable[[str], None] | None = None, skip_scenes: Iterable[str] = (),
               timeout: float | None = None, telegram_variant: bool = False) -> dict[str, Any]:
    """Single-pass render of the whole timeline.  Writes ``output.part.mp4`` (renamed by ``finalize``)."""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    env, resolved, source = prepared["env"], prepared["resolved"], prepared["source"]
    geo = resolved["geometry"]
    duration = source["duration"]
    if timeout is None:
        timeout = min(7200.0, max(120.0, 40.0 + duration * 30.0))
    work = _prepare_workdir(prepared, folder)
    warnings: list[str] = []
    settings = prepared["render_settings"]
    crf = settings.get("crf", 20)
    preset = settings.get("preset", "medium")
    if preset not in PRESETS:
        warnings.append(f"render.preset {preset!r} is not an x264 preset; 'medium' used")
        preset = "medium"
    try:
        files = _write_textfiles(resolved["overlays"], geo["width"], geo["height"], work, warnings)
        chain = video_chain(resolved, files)
        copy_video = bool(settings.get("stream_copy_unchanged")) and not chain \
            and source.get("video_codec") == "h264" and source.get("pix_fmt") == "yuv420p"
        audio_inputs, audio_graph, audio_params = _audio_graph(prepared, duration, cancel, timeout)
        graph_parts: list[str] = []
        if not copy_video:
            graph_parts.append("[0:v:0]" + ",".join([*chain, "format=yuv420p"]) + "[vout]")
        graph_parts.extend(audio_graph)
        part = folder / "output.part.mp4"
        argv: list[str] = [env["ffmpeg"], "-hide_banner", "-nostdin", "-y", "-loglevel", "info",
                           "-progress", "pipe:1", "-nostats", "-i", source["path"], *audio_inputs]
        graph_text = ";".join(graph_parts)
        if graph_parts:
            (work / "filter_complex.txt").write_bytes(graph_text.encode("utf-8"))
            argv += [env["filter_script_option"], "filter_complex.txt"]
        if copy_video:
            argv += ["-map", "0:v:0", "-c:v", "copy"]
        else:
            argv += ["-map", "[vout]", "-c:v", "libx264", "-preset", preset, "-crf", str(int(crf)),
                     "-pix_fmt", "yuv420p", *env["passthrough_option"]]
        audio_action = audio_params["action"]
        if audio_action in ("mute", "none"):
            argv += ["-an"]
        elif audio_graph:
            argv += ["-map", "[aout]", "-c:a", "aac", "-b:a", f"{int(settings.get('audio_bitrate_k', 192))}k"]
        elif source.get("audio_codec") == "aac":
            argv += ["-map", "0:a:0", "-c:a", "copy"]
            audio_params["codec_action"] = "copy (source audio is already AAC and unchanged)"
        else:
            argv += ["-map", "0:a:0", "-c:a", "aac", "-b:a", f"{int(settings.get('audio_bitrate_k', 192))}k"]
        argv += ["-movflags", "+faststart", "-f", "mp4", str(part)]
        log_path = folder / "render.log"
        log_path.write_bytes(b"")
        boundaries = [(s["scene_id"], float(s["end"])) for s in resolved["scenes"]]
        skip = set(skip_scenes)
        pointer = {"i": 0}
        state: dict[str, Any] = {"out_time": 0.0}
        total_seconds = max(duration, 0.001)

        def fire_scenes(position: float, final: bool = False) -> None:
            while pointer["i"] < len(boundaries):
                sid, end = boundaries[pointer["i"]]
                if not final and position + 1e-6 < end - 0.001:
                    break
                pointer["i"] += 1
                if sid not in skip and on_scene:
                    on_scene(sid)

        def on_line(line: str) -> None:
            if _parse_progress_line(line, state):
                fraction = min(1.0, state.get("out_time", 0.0) / total_seconds)
                fire_scenes(state.get("out_time", 0.0))
                if on_progress:
                    on_progress("render", fraction, {"frame": state.get("frame"), "fps": state.get("fps"),
                                                     "speed": state.get("speed"), "out_time": state.get("out_time")})

        if on_progress:
            on_progress("render", 0.0, {"starting": True})
        context = {"argv": argv, "graph": {"filter_complex": graph_text, "textfiles": {}},
                   "wall_seconds": None, "timeout_seconds": timeout, "video": None, "audio": audio_params,
                   "warnings": warnings}
        try:
            done = _run(argv, cwd=work, timeout=timeout, cancel=cancel, log_path=log_path, on_line=on_line,
                        pid_path=folder / "ffmpeg.pid")
            if done.rc != 0 or not part.is_file():
                raise RenderError(f"ffmpeg exited with code {done.rc}: "
                                  f"{_tail_file(log_path, 600).strip()[-600:]}")
        except RenderError as exc:
            exc.context = context            # type: ignore[attr-defined]
            raise
        fire_scenes(duration, final=True)
        graph_snapshot = {"filter_complex": graph_text,
                          "textfiles": {name: (work / name).read_text(encoding="utf-8") for name in files.values()}}
        return {"part": str(part), "argv": argv, "graph": graph_snapshot, "audio": audio_params,
                "video": {"codec_action": "copy" if copy_video else "libx264", "preset": preset, "crf": int(crf),
                          "pix_fmt": "yuv420p", "filters": chain},
                "wall_seconds": done.wall_seconds, "warnings": warnings, "timeout_seconds": timeout,
                "log_file": "render.log", "telegram_variant": telegram_variant}
    finally:
        _clean_workdir(work)
        with contextlib.suppress(OSError):
            for leftover in work.glob("*.txt"):
                if leftover.name != "filter_complex.txt":
                    leftover.unlink()


def make_telegram_variant(prepared: dict[str, Any], video: Path, out: Path, *, cancel: threading.Event | None = None,
                          timeout: float = 1800.0) -> dict[str, Any]:
    env = prepared["env"]
    part = out.with_name(out.stem + ".part.mp4")
    argv = [env["ffmpeg"], "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", str(video),
            "-vf", "scale='min(1280,iw)':-2,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-f", "mp4", str(part)]
    done = _run(argv, timeout=timeout, cancel=cancel)
    if done.rc != 0 or not part.is_file():
        with contextlib.suppress(OSError):
            part.unlink()
        raise RenderError(f"telegram variant failed: {done.stderr.strip()[-300:]}")
    os.replace(part, out)
    return {"file": out.name, "argv": argv, "wall_seconds": done.wall_seconds, "bytes": out.stat().st_size}


# --------------------------------------------------------------------------------------------- verify

def _rate(text: Any) -> float | None:
    try:
        num, _, den = str(text).partition("/")
        value = float(num) / float(den or 1)
        return value if value > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def expected_output(prepared: dict[str, Any]) -> dict[str, Any]:
    resolved, source = prepared["resolved"], prepared["source"]
    geo = resolved["geometry"]
    action = resolved["audio"]["action"]
    return {"width": geo["width"], "height": geo["height"], "fps": geo["fps"], "duration": source["duration"],
            "frames": None if geo["fps_changed"] else source.get("frames"),
            "audio": "none" if action in ("mute", "none") else "aac"}


def verify_output(path: str | os.PathLike, env: dict[str, Any], expect: dict[str, Any], *,
                  cancel: threading.Event | None = None, timeout: float = 900.0) -> dict[str, Any]:
    """The gate a job must pass to be ``done``: facts from ffprobe vs the promise, then a full decode."""
    target = Path(path)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, expected: Any, actual: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "expected": expected, "actual": actual})

    result: dict[str, Any] = {"ok": False, "checks": checks, "file": target.name}
    if not target.is_file() or target.stat().st_size < 1000:
        check("file_exists", False, ">= 1000 bytes", target.stat().st_size if target.is_file() else "missing")
        return result
    check("file_exists", True, ">= 1000 bytes", target.stat().st_size)
    try:
        info = ffprobe_json(target, env["ffprobe"], timeout=min(timeout, 120.0))
    except RenderError as exc:
        check("ffprobe_readable", False, "readable media file", str(exc)[:300])
        return result
    check("ffprobe_readable", True, "readable media file", "ok")
    streams = info.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    fmt = info.get("format") or {}
    check("one_video_stream", len(video) == 1, 1, len(video))
    v = video[0] if video else {}
    check("video_codec_h264", v.get("codec_name") == "h264", "h264", v.get("codec_name"))
    check("pix_fmt_yuv420p", v.get("pix_fmt") == "yuv420p", "yuv420p", v.get("pix_fmt"))
    check("width", v.get("width") == expect["width"], expect["width"], v.get("width"))
    check("height", v.get("height") == expect["height"], expect["height"], v.get("height"))
    rate = _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate"))
    check("fps", rate is not None and abs(rate - expect["fps"]) <= max(0.02, expect["fps"] * 0.002),
          round(expect["fps"], 4), round(rate, 4) if rate else None)
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        duration = 0.0
    tolerance = max(0.15, 3.0 / max(expect["fps"], 1.0))
    check("duration", abs(duration - expect["duration"]) <= tolerance, round(expect["duration"], 3),
          round(duration, 3))
    if expect["audio"] == "none":
        check("no_audio_stream", not audio, 0, len(audio))
    else:
        check("one_audio_stream", len(audio) == 1, 1, len(audio))
        if audio:
            check("audio_codec_aac", audio[0].get("codec_name") == "aac", "aac", audio[0].get("codec_name"))
            try:
                a_dur = float(audio[0].get("duration") or fmt.get("duration"))
            except (TypeError, ValueError):
                a_dur = 0.0
            check("audio_duration", abs(a_dur - duration) <= max(0.25, tolerance), round(duration, 3),
                  round(a_dur, 3))
    # full decode: catches truncated / corrupt files; the frame count comes from the same pass
    state: dict[str, Any] = {}

    def on_line(line: str) -> None:
        _parse_progress_line(line, state)

    log = target.with_name(target.name + ".verify.log")
    log.write_bytes(b"")
    try:
        done = _run([env["ffmpeg"], "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(target),
                     "-map", "0", "-f", "null", "-progress", "pipe:1", "-nostats", "-"],
                    timeout=timeout, cancel=cancel, log_path=log, on_line=on_line)
        errors = _tail_file(log, 400).strip()
    finally:
        with contextlib.suppress(OSError):
            log.unlink()
    check("decodes_without_errors", done.rc == 0 and not errors, "rc 0 and no decoder messages",
          f"rc {done.rc}" + (f": {errors[-160:]}" if errors else ""))
    frames = state.get("frame")
    if expect.get("frames") is not None and frames is not None:
        check("frame_count", abs(frames - int(expect["frames"])) <= 1, int(expect["frames"]), frames)
    result.update({
        "video": {"codec": v.get("codec_name"), "profile": v.get("profile"), "pix_fmt": v.get("pix_fmt"),
                  "width": v.get("width"), "height": v.get("height"), "fps": round(rate, 4) if rate else None,
                  "nb_frames_decoded": frames, "bit_rate": v.get("bit_rate")},
        "audio": [{"codec": a.get("codec_name"), "sample_rate": a.get("sample_rate"), "channels": a.get("channels"),
                   "bit_rate": a.get("bit_rate"), "duration": a.get("duration")} for a in audio],
        "format": {"name": fmt.get("format_name"), "duration": duration, "bytes": target.stat().st_size,
                   "bit_rate": fmt.get("bit_rate")},
    })
    result["ok"] = all(c["ok"] for c in checks)
    return result


def overlay_evidence(prepared: dict[str, Any], output: Path, work_dir: Path, *,
                     cancel: threading.Event | None = None) -> list[dict[str, Any]]:
    """Objective presence check for burned-in text: source frame vs output frame at an overlay's middle.

    Share of pixels that changed by more than codec noise inside the band where the text sits vs the rest of
    the frame.  The numbers are recorded; the verdict is a heuristic and never changes a status."""
    try:
        from PIL import Image, ImageChops, ImageStat
    except Exception:   # Pillow missing: no evidence, say so
        return [{"note": "Pillow is not importable; text presence was not measured"}]
    env, resolved = prepared["env"], prepared["resolved"]
    overlays = resolved["overlays"]
    if not overlays:
        return []
    step = max(1, len(overlays) // EVIDENCE_SAMPLES)
    sample = overlays[::step][:EVIDENCE_SAMPLES]
    work_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    bands = {"top": (0.05, 0.32), "center": (0.30, 0.70), "bottom": (0.60, 0.97)}
    for overlay in sample:
        t = (overlay["start"] + overlay["end"]) / 2.0
        a, b = work_dir / "ev_src.png", work_dir / "ev_out.png"
        try:
            _frame_png(env, prepared["source"]["path"], t, None, a, work_dir, cancel, 60.0)
            _frame_png(env, str(output), t, None, b, work_dir, cancel, 60.0)
            with Image.open(a) as ia, Image.open(b) as ib:
                ia, ib = ia.convert("L"), ib.convert("L")
                if ia.size != ib.size:
                    ia = ia.resize(ib.size)
                diff = ImageChops.difference(ia, ib)
                w, h = diff.size
                lo, hi = bands[overlay["position"]]
                band = diff.crop((0, int(h * lo), w, int(h * hi)))
                rest_boxes = [diff.crop((0, 0, w, int(h * lo))), diff.crop((0, int(h * hi), w, h))]
                rest_boxes = [r for r in rest_boxes if r.size[1] > 0]

                def changed(image: Any) -> float:      # share of pixels that moved by more than codec noise
                    return ImageStat.Stat(image.point(lambda v: 255 if v > CHANGED_PIXEL_DELTA else 0)).mean[0] / 255.0

                band_ratio = changed(band)
                rest_ratio = (sum(changed(r) * r.size[0] * r.size[1] for r in rest_boxes)
                              / max(1, sum(r.size[0] * r.size[1] for r in rest_boxes))) if rest_boxes else 0.0
                band_mean = ImageStat.Stat(band).mean[0]
        except RenderCancelled:
            raise
        except Exception as exc:    # noqa: BLE001 - evidence must never fail the job
            out.append({"overlay_id": overlay["id"], "t": round(t, 3), "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        present = band_ratio >= 0.004 and band_ratio >= 5.0 * max(rest_ratio, 0.0005)
        out.append({"overlay_id": overlay["id"], "t": round(t, 3), "position": overlay["position"],
                    "band_changed_pixel_ratio": round(band_ratio, 5), "rest_changed_pixel_ratio": round(rest_ratio, 5),
                    "band_mean_abs_diff": round(band_mean, 3), "changed_pixel_delta": CHANGED_PIXEL_DELTA,
                    "text_present_heuristic": bool(present)})
    for leftover in (work_dir / "ev_src.png", work_dir / "ev_out.png"):
        with contextlib.suppress(OSError):
            leftover.unlink()
    return out


# --------------------------------------------------------------------------------------------- report

def finalize_statuses(resolved: dict[str, Any], mode: str) -> dict[str, Any]:
    """The resolved operations with "applied" -> "previewed" for a preview-only job."""
    if mode == "full":
        return resolved
    clone = json.loads(json.dumps(resolved))
    for op in clone["operations"]:
        if op["status"] == "applied":
            op["status"] = "previewed"
    for scene in clone["scenes"]:
        for item in scene["items"]:
            if item["status"] == "applied":
                item["status"] = "previewed"
    return clone


def build_report(*, prepared: dict[str, Any], job: dict[str, Any], mode: str, status: str,
                 render: dict[str, Any] | None, verification: dict[str, Any] | None,
                 preview: dict[str, Any] | None, cover: dict[str, Any] | None,
                 output_file: Path | None, evidence: list[dict[str, Any]], timings: dict[str, Any],
                 telegram: dict[str, Any] | None, error: str | None = None) -> dict[str, Any]:
    resolved = finalize_statuses(prepared["resolved"], mode)
    env = prepared["env"]
    font = prepared.get("font")
    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "renderer": {"name": RENDERER_NAME, "version": RENDERER_VERSION},
        "job": {**job, "mode": mode},
        "status": status,
        "verified": bool(verification and verification.get("ok")) if mode == "full" else None,
        "error": error,
        "execution": {**resolved["counts"], "summary": summary_line(resolved["counts"]),
                      "every_requested_operation_applied": resolved["counts"]["complete"]},
        "environment": {"ffmpeg": {"path": env.get("ffmpeg"), "version": env.get("version"),
                                   "version_line": env.get("version_line")},
                        "ffprobe": {"path": env.get("ffprobe")},
                        "filter_script_option": env.get("filter_script_option"),
                        "passthrough_option": env.get("passthrough_option"),
                        "font": {"path": font, "sha256": analyze_mod.sha256_file(font) if font else None},
                        "python": env.get("python"), "platform": env.get("platform")},
        "input": {"sha256": prepared["source"]["sha256"], "bytes": prepared["source"]["bytes"],
                  "duration": prepared["source"]["duration"], "fps": prepared["source"]["fps"],
                  "video_codec": prepared["source"].get("video_codec"), "pix_fmt": prepared["source"].get("pix_fmt"),
                  "has_audio": prepared["source"]["has_audio"], "audio_codec": prepared["source"].get("audio_codec"),
                  "frames": prepared["source"].get("frames"),
                  "width": resolved["geometry"]["source_width"], "height": resolved["geometry"]["source_height"]},
        "references": {name: {k: v for k, v in info.items() if k != "path"} for name, info in prepared["refs"].items()},
        "output": None,
        "parameters": {
            "resolution": {"width": resolved["geometry"]["width"], "height": resolved["geometry"]["height"],
                           "changed": resolved["geometry"]["size_changed"]},
            "fps": {"value": resolved["geometry"]["fps"], "changed": resolved["geometry"]["fps_changed"]},
            "video": render["video"] if render else None,
            "audio": render["audio"] if render else resolved["audio"],
            "text_overlays": [{k: o[k] for k in ("id", "kind", "scene_id", "start", "end", "position", "source")}
                              for o in resolved["overlays"]],
            "subtitle_cues": resolved["subtitle_cues"],
            "cover": cover or resolved["cover"],
            "render_settings": prepared["render_settings"],
        },
        "commands": ([{"purpose": "render", "argv": render["argv"], "wall_seconds": render["wall_seconds"],
                       "filter_complex": render["graph"]["filter_complex"], "textfiles": render["graph"]["textfiles"],
                       "timeout_seconds": render["timeout_seconds"]}] if render else [])
                    + ([{"purpose": "telegram variant", "argv": telegram["argv"],
                         "wall_seconds": telegram["wall_seconds"]}] if telegram else [])
                    + (preview["commands"] if preview else []),
        "scenes": resolved["scenes"],
        "operations": resolved["operations"],
        "lanes": {"audio": resolved["audio"]["lanes"], "subtitles": resolved["subtitle_cues"]},
        "verification": verification,
        "text_evidence": evidence,
        "preview": {"frames": preview["frames"]} if preview else None,
        "timing": timings,
        "warnings": (resolved["warnings"] + (render["warnings"] if render else [])
                     + (preview["warnings"] if preview else [])),
        "cloud_spend_usd": 0.0, "paid_calls": 0,
        "notes": [
            "scene completion in the render phase is derived from encoder progress (single pass)",
            "the report records parameters of THIS run; a re-run with the same plan version is expected "
            "to give the same parameters, not necessarily the same bytes",
        ],
    }
    if output_file is not None and output_file.is_file() and verification:
        report["output"] = {"file": output_file.name, "bytes": output_file.stat().st_size,
                            "sha256": analyze_mod.sha256_file(output_file),
                            "video": verification.get("video"), "audio": verification.get("audio"),
                            "format": verification.get("format")}
    return report


def write_report(report: dict[str, Any], path: str | os.PathLike) -> None:
    target = Path(path)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    data = (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def finalize_output(part: str | os.PathLike, final: str | os.PathLike) -> None:
    os.replace(part, final)
