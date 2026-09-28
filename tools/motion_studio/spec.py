"""Motion Studio scene spec: the JSON a local model writes, and its validator.

A video is a list of timed scenes of a few fixed types. The engine (engine.html)
owns every pixel, easing and effect; the model only chooses scene types, texts,
numbers and timings. That split is deliberate: a 30B local model reliably fills a
validated JSON form, while free-form canvas code from it is fragile.

`validate(spec)` returns a list of human-readable errors (empty = valid). The
same messages are fed back to the model by generate_spec.py when it retries.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

SCENE_TYPES = {
    "title":     {"required": ["title"], "optional": ["kicker", "typed", "chip"]},
    "bars":      {"required": ["values"], "optional": ["headline", "headline_label", "counter_label",
                                                       "x_from", "x_to", "highlight"]},
    "cards":     {"required": ["items"], "optional": ["heading"]},
    "grid":      {"required": ["value", "label"], "optional": ["caption", "then_title", "then_sub"]},
    "voice":     {"required": ["name", "lines"], "optional": ["sub", "status"]},
    "roadmap":   {"required": ["items"], "optional": ["heading", "disclaimer"]},
    "logo":      {"required": ["name"], "optional": ["tagline"]},
    "end_card":  {"required": ["text"], "optional": ["sub"]},
    "sticker":   {"required": ["lottie", "text"], "optional": ["sub"]},
}
ICONS = {"agents", "memory", "cursor", "play", "plane", "chart", "shield", "bolt", "mic", "code"}
LIMITS = {"title": 12, "kicker": 24, "typed": 48, "chip": 12, "headline_label": 10, "counter_label": 12,
          "x_from": 12, "x_to": 12,
          "heading": 32, "label": 18, "caption": 44, "then_title": 16, "then_sub": 48, "name": 12,
          "tagline": 44, "text": 18, "sub": 44, "disclaimer": 52}
MAX_DURATION = 60.0
# Kokoro am_fenrir at speed 1.05: measured duration ~= 0.3 s + chars / 24 (within 0.15 s on 12 lines)
VO_BASE_SECONDS, VO_CHARS_PER_SECOND = 0.3, 24.0


def _lottie_ids() -> set[str]:
    path = Path(__file__).resolve().parent / "lottie" / "catalog.json"
    return {i["id"] for i in json.loads(path.read_text(encoding="utf-8"))["items"]}


def _err(errors: list[str], where: str, msg: str) -> None:
    errors.append(f"{where}: {msg}")


def shorten(value: str, limit: int) -> str:
    """A fitting candidate for an over-long text: whole words up to the limit, no dangling separator.

    Local models cannot count characters reliably (rc19 owner-PC run: 3 retries stuck at 11-12
    chars for a 10-char field); a concrete candidate lets them converge instead of guessing."""
    words, out = value.split(), ""
    for w in words:
        cand = f"{out} {w}".strip()
        if len(cand) > limit:
            break
        out = cand
    out = out.rstrip(" ·,;:-—/|&+").strip()
    return out or value[:limit].rstrip()


def _text(errors: list[str], where: str, key: str, value: Any, limit: int | None = None) -> None:
    if not isinstance(value, str) or not value.strip():
        _err(errors, where, f"'{key}' must be a non-empty string")
        return
    limit = limit or LIMITS.get(key)
    if limit and len(value) > limit:
        _err(errors, where, f"'{key}' {json.dumps(value, ensure_ascii=False)} is {len(value)} chars, max {limit} (it will not fit on screen); "
                            f"use at most {limit} chars, e.g. {json.dumps(shorten(value, limit), ensure_ascii=False)}")


def _item_times(errors: list[str], where: str, items: list, start: float, end: float) -> None:
    last = float('-inf')
    for i, it in enumerate(items):
        t = it.get("t") if isinstance(it, dict) else None
        if not isinstance(t, (int, float)):
            _err(errors, f"{where}.items[{i}]", "'t' (seconds) is required")
            continue
        if not (start <= t < end):
            _err(errors, f"{where}.items[{i}]", f"t={t} must be inside the scene [{start}, {end})")
        if t < last + 0.5:
            _err(errors, f"{where}.items[{i}]", "items must be at least 0.5 s apart and in order")
        last = t


def validate(spec: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]
    meta = spec.get("meta")
    if not isinstance(meta, dict):
        _err(errors, "meta", "object required")
        meta = {}
    dur = meta.get("duration")
    if not isinstance(dur, (int, float)) or not (3 <= dur <= MAX_DURATION):
        _err(errors, "meta", f"'duration' must be a number of seconds in [3, {MAX_DURATION}]")
        dur = MAX_DURATION
    bpm = meta.get("bpm", 120)
    if not isinstance(bpm, (int, float)) or not (70 <= bpm <= 170):
        _err(errors, "meta", "'bpm' must be in [70, 170]")
    if meta.get("key", "D") not in {"C", "D", "E", "F", "G", "A", "B"}:
        _err(errors, "meta", "'key' must be one of C D E F G A B")
    scenes = spec.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        _err(errors, "scenes", "non-empty list required")
        return errors
    prev_end = 0.0
    vo_spans: list[tuple[float, float, str, float]] = []
    for n, sc in enumerate(scenes):
        where = f"scenes[{n}]"
        if not isinstance(sc, dict):
            _err(errors, where, "object required")
            continue
        kind = sc.get("type")
        if kind not in SCENE_TYPES:
            _err(errors, where, f"unknown type {kind!r}; use one of {sorted(SCENE_TYPES)}")
            continue
        start, end = sc.get("start"), sc.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            _err(errors, where, "'start' < 'end' (seconds) required")
            continue
        if abs(start - prev_end) > 1e-6:
            _err(errors, where, f"must start where the previous scene ends ({prev_end}), got {start}")
        if end - start < (1.0 if kind == "end_card" else 1.5):
            _err(errors, where, "a scene must last at least 1.5 s (end_card: 1.0 s)")
        prev_end = end
        for key in SCENE_TYPES[kind]["required"]:
            if key not in sc:
                _err(errors, where, f"'{key}' is required for type {kind}")
        allowed = set(SCENE_TYPES[kind]["required"]) | set(SCENE_TYPES[kind]["optional"]) | {"type", "start", "end", "vo"}
        for key in sc:
            if key not in allowed:
                _err(errors, where, f"unknown field '{key}' for type {kind}")
        for key in ("title", "kicker", "typed", "chip", "headline_label", "counter_label", "x_from", "x_to",
                    "heading", "label",
                    "caption", "then_title", "then_sub", "name", "tagline", "text", "sub", "disclaimer"):
            if key in sc:
                _text(errors, where, key, sc[key])
        if kind == "bars":
            vals = sc.get("values")
            if not isinstance(vals, list) or not (3 <= len(vals) <= 60) or not all(
                    isinstance(v, (int, float)) and v >= 0 for v in vals):
                _err(errors, where, "'values' must be 3..60 non-negative numbers (real data only)")
            hl = sc.get("highlight")
            if hl is not None and (not isinstance(hl, dict) or not isinstance(hl.get("index"), int)
                                   or not isinstance(vals, list) or not (0 <= hl["index"] < len(vals))):
                _err(errors, where, "'highlight' needs an 'index' inside values and a 'text'")
            elif hl is not None:
                _text(errors, where, "highlight.text", hl.get("text"), 20)
        if kind in ("cards", "roadmap"):
            items = sc.get("items")
            if not isinstance(items, list) or not (1 <= len(items) <= (5 if kind == "cards" else 4)):
                _err(errors, where, f"'items' must be 1..{5 if kind == 'cards' else 4} objects")
            else:
                _item_times(errors, where, items, start, end)
                for i, it in enumerate(items):
                    w = f"{where}.items[{i}]"
                    if kind == "cards":
                        _text(errors, w, "title", it.get("title"), 14)
                        _text(errors, w, "sub", it.get("sub"), 24)
                        icon = it.get("icon")
                        if not (icon in ICONS or (isinstance(icon, str) and icon.startswith("lottie:")
                                                  and icon[7:] in _lottie_ids())):
                            _err(errors, w, f"'icon' {icon!r} is not allowed: use one of {sorted(ICONS)} or lottie:<catalog id> "
                                            f"with an exact id from the LOTTIE list")
                    else:
                        _text(errors, w, "version", it.get("version"), 5)
                        _text(errors, w, "when", it.get("when"), 10)
                        lines = it.get("lines")
                        if not isinstance(lines, list) or not (1 <= len(lines) <= 2):
                            _err(errors, w, "'lines' must be 1..2 short strings")
                        else:
                            for line in lines:
                                _text(errors, w, "line", line, 22)
            if kind == "roadmap" and "disclaimer" not in sc:
                _err(errors, where, "a roadmap is a projection: 'disclaimer' is required (e.g. 'targets, not promises')")
        if kind == "sticker" and sc.get("lottie") not in _lottie_ids():
            _err(errors, where, f"'lottie' must be an id from tools/motion_studio/lottie/catalog.json (the LOTTIE list); "
                               f"{sc.get('lottie')!r} is not one")
        if kind == "grid" and (not isinstance(sc.get("value"), int) or not (1 <= sc["value"] <= 5200)):
            _err(errors, where, "'value' must be an integer 1..5200 (one cell per unit)")
        if kind == "voice":
            lines = sc.get("lines")
            if not isinstance(lines, list) or not (1 <= len(lines) <= 3):
                _err(errors, where, "'lines' must be 1..3 short strings")
            status = sc.get("status", [])
            if not isinstance(status, list) or len(status) > 2 or not all(
                    isinstance(s, dict) and s.get("state") in {"live", "next"} for s in status):
                _err(errors, where, "'status' = up to 2 {text, state: live|next}; say 'next' for anything not shipped")
        for i, vo in enumerate(sc.get("vo", [])):
            w = f"{where}.vo[{i}]"
            if not isinstance(vo, dict) or not isinstance(vo.get("t"), (int, float)) or not isinstance(vo.get("text"), str):
                _err(errors, w, "{t, text} required")
                continue
            if not re.fullmatch(r"[A-Za-z0-9 ,.'!?:;\-]+", vo["text"]):
                _err(errors, w, "voice-over must be plain English text (TTS reads it literally; spell numbers out)")
            elif re.search(r"\d", vo["text"]):
                # rc19 owner-PC run: "960 commits in the last 7 days." passed, was estimated at 1.6 s, spoke
                # for 2.6 s and make_video stopped on a real overlap. Digits are spoken far longer than written.
                _err(errors, w, "voice-over must be plain English with numbers spelled out in words "
                                "(e.g. 'nine hundred sixty', not '960'): digits are read much longer than the timing estimate")
            est = VO_BASE_SECONDS + len(vo["text"]) / VO_CHARS_PER_SECOND
            if not (start <= vo["t"] < end):
                _err(errors, w, f"t={vo['t']} must be inside the scene [{start}, {end})")
            vo_spans.append((vo["t"], vo["t"] + est, w, end))
    if isinstance(dur, (int, float)) and abs(prev_end - dur) > 1e-6:
        _err(errors, "scenes", f"the last scene must end at meta.duration ({dur}), got {prev_end}")
    vo_spans.sort()
    for (a0, a1, wa, _e), (b0, _b1, wb, b_end) in zip(vo_spans, vo_spans[1:]):
        if b0 < a1 - 0.15:                  # TTS clips carry ~0.1 s of trailing silence
            # the earliest start that passes, rounded up to 0.05 s: a concrete target, not "move it later"
            earliest = math.ceil((a1 - 0.15) * 20 - 1e-9) / 20
            if earliest < b_end:
                _err(errors, wb, f"voice-over overlaps {wa} (estimated end {a1:.2f} s); set its t to {earliest:.2f} "
                                 f"or later (it must stay inside its scene), or shorten the earlier line")
            else:                           # moving it would leave the scene: the text itself is too long
                _err(errors, wb, f"voice-over overlaps {wa} (estimated end {a1:.2f} s) and there is no room left "
                                 f"before this scene ends at {b_end}: shorten {wa} or this line, or drop one")
    return errors


def hits(spec: dict) -> list[float]:
    """Accent times shared by picture and score: scene cuts and per-item pops."""
    out: set[float] = set()
    for sc in spec["scenes"][1:]:
        out.add(round(float(sc["start"]), 3))
    for sc in spec["scenes"]:
        for it in sc.get("items", []):
            out.add(round(float(it["t"]), 3))
    return sorted(out)


def load(path: str | Path) -> dict:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = validate(spec)
    if problems:
        raise ValueError("invalid motion spec:\n  " + "\n  ".join(problems))
    return spec


if __name__ == "__main__":
    import sys
    problems = validate(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    print("VALID" if not problems else "\n".join(problems))
    raise SystemExit(1 if problems else 0)
