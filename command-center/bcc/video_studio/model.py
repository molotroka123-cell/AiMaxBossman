"""Versioned video project model, integer time and structural invariants."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
import math
from pathlib import PurePosixPath
import re
import uuid

TICKS = 1_000_000
MAX_TIME = 7 * 86400 * TICKS
SCHEMA_VERSION = 1


class StudioError(ValueError):
    code = "invalid_operation"


class Conflict(StudioError):
    code = "revision_conflict"


class MissingObject(StudioError):
    code = "missing_object"


class EditLocked(Conflict):
    code = "object_locked"


def uid():
    return uuid.uuid4().hex


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value):
        raise StudioError("Invalid stable object ID")
    return value


def number(value, low=-1e9, high=1e9):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise StudioError("Numeric value outside the supported finite range")
    return value


def ticks(value):
    if type(value) is not int or not 0 <= value <= MAX_TIME:
        raise StudioError("Time must be a nonnegative integer tick value")
    return value


def label(value, max_length=300):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length or "\x00" in value:
        raise StudioError("Invalid text")
    return value.strip()


def rate(value):
    if not isinstance(value, dict) or set(value) != {"num", "den"}:
        raise StudioError("Rational rate requires num and den")
    if any(type(value[k]) is not int or not 1 <= value[k] <= 100000 for k in ("num", "den")):
        raise StudioError("Invalid rational rate")
    result = Fraction(value["num"], value["den"])
    if not Fraction(1, 100) <= result <= 1000:
        raise StudioError("Rate is outside supported range")
    return result


def clip_duration(clip):
    if clip.get("freeze") or clip.get("title") or clip.get("adjustment"):
        return ticks(clip.get("freeze_duration", TICKS))
    segments = clip.get("speed_ramp") or [clip]
    total = Fraction(0)
    for segment in segments:
        span = ticks(segment["source_out"]) - ticks(segment["source_in"])
        if span <= 0:
            raise StudioError("Source out must be after source in")
        total += span / rate(segment.get("speed", {"num": 1, "den": 1}))
    return (total.numerator + total.denominator // 2) // total.denominator


def sequence(project, sequence_id=None):
    sid = sequence_id or project["active_sequence_id"]
    return next((s for s in project["sequences"] if s["id"] == sid), None) or _missing("sequence", sid)


def _missing(kind, key):
    raise MissingObject(f"Unknown {kind} ID: {str(key)[:100]}")


def track(project, track_id):
    for seq in project["sequences"]:
        for item in seq["tracks"]:
            if item["id"] == track_id:
                return seq, item
    return _missing("track", track_id)


def clip(project, clip_id):
    for seq in project["sequences"]:
        for tr in seq["tracks"]:
            for item in tr["clips"]:
                if item["id"] == clip_id:
                    return seq, tr, item
    return _missing("clip", clip_id)


def sequence_duration(seq):
    return max((c["start"] + clip_duration(c) for tr in seq["tracks"] for c in tr["clips"]), default=0)


def new_track(kind="video", name=None, id=None):
    if kind not in ("video", "audio", "adjustment"):
        raise StudioError("Unknown track kind")
    return {"id": identity(id) if id else uid(), "name": name or ("Видео" if kind == "video" else "Звук"),
            "kind": kind, "mute": False, "solo": False, "locked": False,
            "volume": 1.0, "pan": 0.0, "effects": [], "clips": []}


def new_sequence(name="Монтаж", id=None):
    return {"id": identity(id) if id else uid(), "name": label(name), "width": 1280, "height": 720,
            "fps": {"num": 25, "den": 1}, "sample_rate": 48000,
            "tracks": [new_track("video", "V1"), new_track("audio", "A1")], "range": None}


def new_project(project_id, name, links=None):
    seq = new_sequence()
    return {"id": identity(project_id), "name": label(name), "schema_version": SCHEMA_VERSION,
            "timebase": TICKS, "revision": 0, "archived": False, "active_sequence_id": seq["id"],
            "media": {}, "sequences": [seq], "markers": [], "captions": [],
            "links": deepcopy(links or {}), "layout": {}, "metadata": {}}


def new_clip(values):
    item = {"id": uid(), "start": 0, "source_in": 0, "speed": {"num": 1, "den": 1},
            "reverse": False, "freeze": False,
            "transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1, "crop": [0, 0, 0, 0]},
            "effects": [], "keyframes": {}, "group_id": None, "linked_id": None,
            "volume": 1.0, "pan": 0.0}
    item.update(deepcopy(values))
    return item


def migrate(project):
    """Explicit migration from the documented pre-release v0; reject future data."""
    result = deepcopy(project)
    version = result.get("schema_version", 0)
    if version == 0:
        if result.get("timebase") != TICKS:
            raise StudioError("Legacy timebase needs an explicit conversion")
        result.update(schema_version=1)
        result.setdefault("metadata", {})
        result.setdefault("captions", [])
        result.setdefault("layout", {})
    elif version != SCHEMA_VERSION:
        raise StudioError("Unsupported project schema version")
    validate_project(result)
    return result


def validate_project(project):
    try:
        encoded = json.dumps(project, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise StudioError("Project must contain finite JSON data") from exc
    if len(encoded) > 32 * 1024 * 1024:
        raise StudioError("Project metadata exceeds 32 MiB")
    identity(project["id"])
    label(project["name"])
    if project["schema_version"] != 1 or project["timebase"] != TICKS:
        raise StudioError("Unsupported schema/timebase")
    if type(project["revision"]) is not int or project["revision"] < 0:
        raise StudioError("Invalid revision")
    seen = set()

    def unique(key):
        identity(key)
        if key in seen:
            raise StudioError("Duplicate stable object ID")
        seen.add(key)

    for key, media in project["media"].items():
        identity(key)
        if media["id"] != key:
            raise StudioError("Media key differs from media ID")
        path = media.get("relative_path", "")
        if (not isinstance(path, str) or not path or "\\" in path or ":" in path
                or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts):
            raise StudioError("Media paths must be host-issued relative paths")
        ticks(media["duration_ticks"])
    sequence_ids = {s["id"] for s in project["sequences"]}
    nested = {}
    for seq in project["sequences"]:
        unique(seq["id"])
        nested[seq["id"]] = set()
        for dimension in ("width", "height"):
            if type(seq[dimension]) is not int or not 16 <= seq[dimension] <= 8192 or seq[dimension] % 2:
                raise StudioError("Output dimensions must be even integers in 16..8192")
        rate(seq["fps"])
        if seq["sample_rate"] not in (32000, 44100, 48000, 96000):
            raise StudioError("Unsupported sample rate")
        for tr in seq["tracks"]:
            unique(tr["id"])
            if tr["kind"] not in ("video", "audio", "adjustment"):
                raise StudioError("Unknown track kind")
            for flag in ("mute", "solo", "locked"):
                if type(tr[flag]) is not bool:
                    raise StudioError("Track flags must be boolean")
            number(tr.get("volume", 1), 0, 16)
            number(tr.get("pan", 0), -1, 1)
            for c in tr["clips"]:
                unique(c["id"])
                ticks(c["start"])
                duration = clip_duration(c)
                if duration <= 0 or c["start"] + duration > MAX_TIME:
                    raise StudioError("Invalid clip duration or timeline end")
                if c.get("nested_sequence_id"):
                    nested_id = c["nested_sequence_id"]
                    if nested_id not in sequence_ids:
                        raise MissingObject("Unknown nested sequence")
                    nested[seq["id"]].add(nested_id)
                elif c.get("adjustment"):
                    if tr["kind"] != "adjustment":
                        raise StudioError("Adjustment clips require an adjustment track")
                elif c.get("title"):
                    label(c["title"]["text"], 10000)
                else:
                    media = project["media"].get(c.get("media_id"))
                    if media is None:
                        raise MissingObject("Clip references missing media")
                    if c["source_out"] > media["duration_ticks"] and media["duration_ticks"] > 0:
                        raise StudioError("Clip extends beyond source duration")
                    if tr["kind"] == "audio" and not media.get("has_audio"):
                        raise StudioError("Audio track requires an audio stream")
                transform = c.get("transform", {})
                number(transform.get("x", 0), -32768, 32768)
                number(transform.get("y", 0), -32768, 32768)
                number(transform.get("scale", 1), .01, 32)
                number(transform.get("rotation", 0), -3600, 3600)
                number(transform.get("opacity", 1), 0, 1)
                crop = transform.get("crop", [0, 0, 0, 0])
                if not isinstance(crop, list) or len(crop) != 4:
                    raise StudioError("Crop requires four edge fractions")
                for value in crop:
                    number(value, 0, .99)
                if crop[0] + crop[2] >= 1 or crop[1] + crop[3] >= 1:
                    raise StudioError("Crop removes the complete image")
                for param, keys in c.get("keyframes", {}).items():
                    if not isinstance(param, str) or len(param) > 80 or not isinstance(keys, list):
                        raise StudioError("Invalid keyframe parameter")
                    times = []
                    for key in keys:
                        times.append(ticks(key["t"]))
                        number(key["value"])
                        if key.get("easing", "linear") not in ("linear", "hold", "ease_in", "ease_out", "ease_in_out"):
                            raise StudioError("Unsupported easing")
                    if times != sorted(set(times)) or any(t > duration for t in times):
                        raise StudioError("Keyframe times must be unique, ordered and inside clip")
    sequence(project)
    for origin in nested:
        pending = [(origin, frozenset())]
        while pending:
            node, ancestors = pending.pop()
            if node in ancestors or len(ancestors) > 16:
                raise StudioError("Nested sequences contain a cycle or exceed depth 16")
            pending.extend((child, ancestors | {node}) for child in nested[node])
    return project
