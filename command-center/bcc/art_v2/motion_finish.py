"""Motion finishing (audit 28.10, 2.3 step 3): word-level kinetic subtitles (ASS) and .cube LUT grading.

Words come in as [{"w": "text", "start_ms": int, "end_ms": int}] (e.g. from Whisper word timestamps); nothing here
transcribes. Output of `words_to_ass` is plain ASS text with a karaoke highlight (\\kf) per word. `apply_grade` runs
FFmpeg `lut3d` (+ optional `ass`) and refuses anything but a well-formed .cube inside `base_dir`.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .motion_timeline import SceneError

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _ass_time(ms: int) -> str:
    cs = round(ms / 10)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _bgr(color: str) -> str:
    return f"&H00{color[5:7]}{color[3:5]}{color[1:3]}".upper().replace("&H", "&H")


def _clean(word: str) -> str:
    # ASS override blocks / newlines come from the transcript: neutralise them so text can never inject styling.
    return re.sub(r"[{}\\\r\n]", "", word)


def words_to_ass(words: list[dict[str, Any]], width: int = 1280, height: int = 720, max_words: int = 4,
                 base: str = "#ffffff", highlight: str = "#00e5ff", font: str = "Arial", size: int = 64) -> str:
    """Group words into short lines of `max_words`; each line shows from its first word's start to its last word's
    end, and every word lights up (karaoke fill) exactly for its own spoken duration."""
    if max_words < 1:
        raise SceneError("max_words must be >= 1")
    for c in (base, highlight):
        if not _HEX.match(c):
            raise SceneError("colors must be #rrggbb")
    last = -1
    for i, w in enumerate(words):
        if not isinstance(w.get("w"), str) or not isinstance(w.get("start_ms"), int) or not isinstance(w.get("end_ms"), int):
            raise SceneError(f"words[{i}] needs w, start_ms, end_ms")
        if w["end_ms"] <= w["start_ms"] or w["start_ms"] < last:
            raise SceneError(f"words[{i}]: times must be positive-length and non-decreasing")
        last = w["start_ms"]
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n\n"
            f"[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            f"Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            f"MarginL, MarginR, MarginV, Encoding\n"
            # karaoke: SecondaryColour is the colour BEFORE the fill, PrimaryColour the colour the word fills to
            f"Style: Kinetic,{font},{size},{_bgr(highlight)},{_bgr(base)},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,3,2,40,40,60,1\n\n"
            f"[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    for i in range(0, len(words), max_words):
        grp = words[i:i + max_words]
        parts, cursor = [], grp[0]["start_ms"]
        for w in grp:
            gap = max(0, w["start_ms"] - cursor)
            if gap:
                parts.append(f"{{\\k{round(gap / 10)}}}")
            parts.append(f"{{\\kf{max(1, round((w['end_ms'] - w['start_ms']) / 10))}}}{_clean(w['w'])} ")
            cursor = w["end_ms"]
        lines.append(f"Dialogue: 0,{_ass_time(grp[0]['start_ms'])},{_ass_time(grp[-1]['end_ms'])},Kinetic,,0,0,0,,"
                     + "".join(parts).rstrip())
    return head + "\n".join(lines) + "\n"


def validate_cube(path: Path) -> int:
    """Check a 3D .cube LUT is well-formed; returns its size. Cheap, so a corrupt LUT fails before FFmpeg does."""
    size, rows = 0, 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(("TITLE", "DOMAIN_MIN", "DOMAIN_MAX")):
            continue
        if line.startswith("LUT_3D_SIZE"):
            size = int(line.split()[1])
            continue
        parts = line.split()
        if len(parts) != 3:
            raise SceneError(f"{path.name}: bad LUT row {line!r}")
        if not all(0.0 <= float(p) <= 1.0 for p in parts):
            raise SceneError(f"{path.name}: LUT value outside 0..1")
        rows += 1
    if not 2 <= size <= 128 or rows != size ** 3:
        raise SceneError(f"{path.name}: LUT_3D_SIZE {size} does not match {rows} rows")
    return size


def apply_grade(src: Path, out: Path, base_dir: Path, ffmpeg: str, lut: str | None = None, ass: str | None = None) -> Path:
    """Burn subtitles and/or apply a LUT to `src`. LUT/ASS names are relative to `base_dir` and may not escape it."""
    filters = []
    for name, kind in ((lut, "lut"), (ass, "ass")):
        if not name:
            continue
        p = (base_dir / name).resolve()
        if base_dir.resolve() not in p.parents or not p.is_file():
            raise SceneError(f"{kind} must be an existing file inside the scene directory")
        if kind == "lut":
            validate_cube(p)
        esc = str(p).replace("\\", "/").replace(":", "\\:")
        filters.append(f"lut3d=file='{esc}'" if kind == "lut" else f"ass='{esc}'")
    if not filters:
        raise SceneError("nothing to apply: pass lut and/or ass")
    proc = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", ",".join(filters),
                           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)],
                          capture_output=True, text=True, errors="replace", timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg grade failed: {proc.stderr[-400:]}")
    return out
