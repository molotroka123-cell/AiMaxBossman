"""Motion workflow plan: one versioned JSON document that describes what to do with a video.

A plan is plain JSON (no classes) so it can be stored, diffed, shown in the UI and sent over HTTP
unchanged.  This module owns everything that is a pure function of the document:

  * the schema constants and the declared LIMITS (shown to the owner before any run),
  * construction (``new_plan``, ``make_scene``) and validation (``problems`` / ``validate``),
  * edits as JSON-friendly operations (``apply_ops``) with a structural ``diff`` for the history,
  * ``@name`` reference binding (a scene may only bind references that exist and have the right kind),
  * ``readiness`` (what blocks a run) and ``estimate`` (machine time per scene, cloud spend = 0).

Honesty rules that the rest of the package follows:

  * a fact the analyzers could not establish is the string ``"UNKNOWN: <reason>"`` - never a guess,
  * every element of every scene carries a policy (keep / replace / unknown) and a reason, and
    unchanged parts default to ``keep``,
  * nothing in this package makes a network call or a paid API call; ``estimate`` says so explicitly.

Persistence (version files, assets, jobs, locks) lives in ``store.py``; video analysis in ``analyze.py``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from fractions import Fraction
from typing import Any, Callable, Iterable

SCHEMA_VERSION = 1

CATEGORIES = ("motion", "clipping", "stylized")
MODES = ("preset", "own_video")
REFERENCE_KINDS = ("character", "product", "clothing", "background", "audio", "text")
ELEMENTS = ("face_character", "clothes", "background", "product", "text", "music")
POLICIES = ("keep", "replace", "unknown")
CUT_TYPES = ("hard", "fade", "dissolve", "unknown")
CAMERA_CLASSES = ("static", "pan", "tilt", "zoom", "handheld", "unknown")
LANES = ("video", "speech", "music", "subtitles")
LANE_MODES = ("keep", "replace", "mute")
SCENE_STATUSES = ("analyzed", "edited", "approved")
PLAN_STATUSES = ("draft", "analyzed", "edited")

# Which reference kind an element can be replaced WITH.
ELEMENT_REFERENCE_KIND = {
    "face_character": "character",
    "clothes": "clothing",
    "background": "background",
    "product": "product",
    "text": "text",
    "music": "audio",
}

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
TEXT_EXTENSIONS = (".txt",)
KIND_EXTENSIONS = {
    "character": IMAGE_EXTENSIONS, "product": IMAGE_EXTENSIONS, "clothing": IMAGE_EXTENSIONS,
    "background": IMAGE_EXTENSIONS, "audio": AUDIO_EXTENSIONS, "text": TEXT_EXTENSIONS,
}
KIND_SIZE_CLASS = {"character": "image", "product": "image", "clothing": "image", "background": "image",
                   "audio": "audio", "text": "text"}

MIB = 1024 * 1024

# Declared constants, not discovered from any provider.  The plan carries a snapshot of them and the
# UI is expected to show it BEFORE any preview or render is started.
LIMITS: dict[str, Any] = {
    "max_references": 12,
    "max_reference_bytes": {"image": 25 * MIB, "audio": 64 * MIB, "text": 64 * 1024},
    "max_total_reference_bytes": 256 * MIB,
    "max_source_bytes": 2048 * MIB,
    "max_duration_seconds": 180.0,
    "max_scenes": 150,
    "max_versions": 1000,
    "max_fps": 120.0,
    "max_title_chars": 120,
    "max_prompt_chars": 4000,
    "max_notes_chars": 4000,
    "max_subtitle_cues": 1000,
    "max_text_boxes_per_scene": 100,
}

CLOUD_STATEMENT = ("Cloud spend is 0 USD: this package makes no network call and no paid API call, ever. "
                   "Estimates are local machine time only.")

# Machine-time model: declared constants per operation.  They are NOT measured on this machine; they are
# the numbers the estimate is computed from, and they are printed with every estimate.
COST_MODEL: dict[str, Any] = {
    "model_version": 1,
    "basis": "declared constants, not measured on this machine; calibrate on the target PC before trusting",
    "ops": {
        "copy_trim": {"unit": "video_second", "seconds_per_unit": 0.05,
                      "note": "cut / stream copy of an unchanged scene"},
        "reencode": {"unit": "video_second", "seconds_per_unit": 0.60,
                     "note": "re-encode a scene that has at least one visual replacement"},
        "subtitle_burn": {"unit": "video_second", "seconds_per_unit": 0.80,
                          "note": "burn subtitles / replacement text into the picture"},
        "faceswap": {"unit": "frame", "seconds_per_unit": 0.45,
                     "note": "local face replacement, per frame"},
        "element_replace": {"unit": "frame", "seconds_per_unit": 0.60,
                            "note": "masked replacement of clothes / background / product, per frame"},
        "music_mix": {"unit": "audio_second", "seconds_per_unit": 0.08,
                      "note": "mix or replace the soundtrack"},
        "mux_concat": {"unit": "project", "seconds_per_unit": 2.0,
                       "note": "concatenate scenes and mux the lanes once"},
    },
    "preview_frames_per_scene": 3,
    "preview_overhead_seconds_per_scene": 0.30,
    "cloud_spend_usd": 0.0,
    "paid_calls": 0,
}

UNKNOWN_PREFIX = "UNKNOWN"
DEFAULT_KEEP_REASON = "default: this part is unchanged in the source and stays as it is"
NO_DETECTOR_MARKER = "UNKNOWN: no detector for this element in the core analysis"

_VOLATILE_KEYS = ("version", "parent_version", "updated_ts")
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_REF_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
_SCENE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ASPECT_RE = re.compile(r"^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$")
_TOKEN_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]*)")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                     *(f"lpt{i}" for i in range(1, 10))}


class PlanError(ValueError):
    """The plan (or an edit of it) is invalid; the message lists every problem."""

    def __init__(self, message: str, problems: Iterable[str] = ()):
        super().__init__(message)
        self.problems = list(problems)


class LimitError(PlanError):
    """A declared limit would be exceeded."""


class MissingReferenceError(PlanError):
    """A scene binds ``@name`` but no such reference exists."""


# --------------------------------------------------------------------------------------------- helpers

def unknown(reason: str) -> str:
    return f"{UNKNOWN_PREFIX}: {reason}"


def is_unknown(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(UNKNOWN_PREFIX)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_project_id() -> str:
    return "mw_" + uuid.uuid4().hex[:12]


def safe_id(value: Any, what: str = "id") -> str:
    """An identifier that is safe as a single path component (no separators, dots or device names)."""
    if not isinstance(value, str) or not _PROJECT_ID_RE.match(value):
        raise PlanError(f"{what} must match {_PROJECT_ID_RE.pattern}: {str(value)[:80]!r}")
    if value.lower() in _WINDOWS_RESERVED:
        raise PlanError(f"{what} is a reserved device name: {value!r}")
    return value


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.match(value))


def limits_snapshot() -> dict[str, Any]:
    snap = copy.deepcopy(LIMITS)
    snap["declared_in"] = "bcc/motion_workflow/plan.py LIMITS"
    snap["note"] = "declared constants, not read from any provider quota; enforced by this package"
    return snap


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_sha256(plan: dict[str, Any]) -> str:
    """Hash of the plan content without the per-version bookkeeping keys, so a revert hashes equal."""
    body = {k: v for k, v in plan.items() if k not in _VOLATILE_KEYS}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def find_reference_tokens(text: Any) -> list[str]:
    """``@name`` tokens in free text, in order of first appearance, without duplicates."""
    if not isinstance(text, str):
        return []
    seen: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def aspect_label(width: int, height: int) -> str:
    """Exact reduced ``w:h`` for the pixel size (``1920x1080`` -> ``16:9``)."""
    if width <= 0 or height <= 0:
        return unknown("non-positive dimensions")
    frac = Fraction(width, height)
    return f"{frac.numerator}:{frac.denominator}"


def scene_bindings(scene: dict[str, Any]) -> list[str]:
    """Every reference name a scene binds: explicit ``refs``, ``@tokens`` in prompt/notes, element ``with``."""
    names: list[str] = []

    def add(name: str) -> None:
        if name not in names:
            names.append(name)

    for name in scene.get("refs") or []:
        if isinstance(name, str):
            add(name.lstrip("@"))
    for field in ("prompt", "notes"):
        for name in find_reference_tokens(scene.get(field)):
            add(name)
    for element in (scene.get("elements") or {}).values():
        if isinstance(element, dict):
            for name in find_reference_tokens(element.get("with")):
                add(name)
    return names


def _lane_reference(lane: dict[str, Any]) -> str | None:
    source = lane.get("source")
    if isinstance(source, str) and source.startswith("@"):
        return source[1:]
    return None


# ------------------------------------------------------------------------------------- construction

def default_render(category: str = "motion") -> dict[str, Any]:
    return {
        "container": "mp4",
        "video_codec": "libx264",
        "crf": 20,
        "preset": "medium",
        "audio_codec": "aac",
        "audio_bitrate_k": 192,
        "aspect_ratio": "keep",
        "fps": "keep",
        "width": "keep",
        "height": "keep",
        "stream_copy_unchanged": category == "clipping",
    }


def default_tracks() -> dict[str, Any]:
    separation = unknown("the original soundtrack is one mixed stream; speech/music were not separated")
    return {
        "video": {"lane": "video", "enabled": True, "mode": "keep", "source": "original",
                  "segments": [], "status": "empty"},
        "speech": {"lane": "speech", "enabled": True, "mode": "keep", "source": "original",
                   "segments": [], "status": separation},
        "music": {"lane": "music", "enabled": True, "mode": "keep", "source": "original",
                  "segments": [], "status": separation},
        "subtitles": {"lane": "subtitles", "enabled": False, "mode": "keep", "source": None,
                      "cues": [], "status": "empty"},
    }


def default_elements(*, preset_mode: bool = False, detected: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every element ``keep`` by default (unchanged parts stay), ``unknown`` only when there is no source."""
    detected = detected or {}
    out: dict[str, Any] = {}
    for name in ELEMENTS:
        if preset_mode:
            policy = "unknown"
            reason = unknown("preset mode has no source video; choose keep or replace for this element")
        else:
            policy = "keep"
            reason = DEFAULT_KEEP_REASON
        out[name] = {"policy": policy, "reason": reason, "with": None,
                     "detected": detected.get(name, NO_DETECTOR_MARKER), "set_by": "default"}
    return out


def make_scene(index: int, start: float, end: float, *, start_frame: int | None = None,
               end_frame: int | None = None, cut_in: dict[str, Any] | None = None,
               camera_motion: dict[str, Any] | None = None, text: dict[str, Any] | None = None,
               audio: dict[str, Any] | None = None, elements: dict[str, Any] | None = None,
               preset_mode: bool = False) -> dict[str, Any]:
    start = round(float(start), 3)
    end = round(float(end), 3)
    return {
        "id": f"s{index + 1:03d}",
        "index": index,
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "start_frame": start_frame,
        "end_frame": end_frame,
        "cut_in": cut_in or {"type": "unknown", "reason": unknown("not analyzed")},
        "camera_motion": camera_motion or {"class": "unknown", "reason": unknown("not analyzed")},
        "text": text or {"status": unknown("not analyzed"), "boxes": []},
        "audio": audio or {"status": unknown("not analyzed")},
        "elements": elements or default_elements(preset_mode=preset_mode),
        "refs": [],
        "prompt": "",
        "notes": "",
        "status": "analyzed",
    }


def new_plan(*, title: str = "", category: str = "motion", mode: str = "own_video",
             project_id: str | None = None, now: str | None = None) -> dict[str, Any]:
    if category not in CATEGORIES:
        raise PlanError(f"category must be one of {CATEGORIES}: {category!r}")
    if mode not in MODES:
        raise PlanError(f"mode must be one of {MODES}: {mode!r}")
    stamp = now or now_iso()
    plan = {
        "schema_version": SCHEMA_VERSION,
        "project_id": safe_id(project_id, "project_id") if project_id is not None else new_project_id(),
        "title": str(title or "")[: LIMITS["max_title_chars"]],
        "category": category,
        "mode": mode,
        "status": "draft",
        "created_ts": stamp,
        "updated_ts": stamp,
        "version": 0,
        "parent_version": None,
        "source": None,
        "render": default_render(category),
        "references": {},
        "scenes": [],
        "tracks": default_tracks(),
        "limits": limits_snapshot(),
        "analysis": None,
        "notes": "",
    }
    return plan


# ------------------------------------------------------------------------------------- validation

def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_reference(name: Any, ref: Any, out: list[str]) -> None:
    label = f"references[{str(name)[:40]!r}]"
    if not isinstance(name, str) or not _REF_NAME_RE.match(name):
        out.append(f"{label}: name must match {_REF_NAME_RE.pattern}")
        return
    if not isinstance(ref, dict):
        out.append(f"{label}: must be an object")
        return
    kind = ref.get("kind")
    if kind not in REFERENCE_KINDS:
        out.append(f"{label}: kind must be one of {REFERENCE_KINDS}: {kind!r}")
        return
    if not is_sha256(ref.get("sha256")):
        out.append(f"{label}: sha256 must be 64 lowercase hex characters")
    ext = ref.get("ext")
    if not isinstance(ext, str) or ext not in KIND_EXTENSIONS[kind]:
        out.append(f"{label}: extension {ext!r} is not allowed for kind {kind!r} {KIND_EXTENSIONS[kind]}")
    size = ref.get("bytes")
    cap = LIMITS["max_reference_bytes"][KIND_SIZE_CLASS[kind]]
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        out.append(f"{label}: bytes must be a non-negative integer")
    elif size > cap:
        out.append(f"{label}: {size} bytes exceeds the declared limit of {cap} for kind {kind!r}")


def _check_scene(scene: Any, index: int, references: dict[str, Any], out: list[str]) -> None:
    label = f"scenes[{index}]"
    if not isinstance(scene, dict):
        out.append(f"{label}: must be an object")
        return
    sid = scene.get("id")
    if not isinstance(sid, str) or not _SCENE_ID_RE.match(sid):
        out.append(f"{label}: id must match {_SCENE_ID_RE.pattern}")
    label = f"scene {sid}" if isinstance(sid, str) else label
    start, end = scene.get("start"), scene.get("end")
    if not _num(start) or not _num(end) or start < 0 or end <= start:
        out.append(f"{label}: needs finite start >= 0 and end > start (got {start!r}, {end!r})")
    elif not _num(scene.get("duration")) or abs(scene["duration"] - (end - start)) > 0.002:
        out.append(f"{label}: duration does not equal end - start")
    cut = scene.get("cut_in")
    if not isinstance(cut, dict) or cut.get("type") not in CUT_TYPES:
        out.append(f"{label}: cut_in.type must be one of {CUT_TYPES}")
    camera = scene.get("camera_motion")
    if not isinstance(camera, dict) or camera.get("class") not in CAMERA_CLASSES:
        out.append(f"{label}: camera_motion.class must be one of {CAMERA_CLASSES}")
    text = scene.get("text")
    if not isinstance(text, dict) or not isinstance(text.get("boxes", []), list):
        out.append(f"{label}: text must be an object with a boxes list")
    elif len(text.get("boxes", [])) > LIMITS["max_text_boxes_per_scene"]:
        out.append(f"{label}: more than {LIMITS['max_text_boxes_per_scene']} text boxes")
    elements = scene.get("elements")
    if not isinstance(elements, dict):
        out.append(f"{label}: elements must be an object")
        elements = {}
    for name in ELEMENTS:
        element = elements.get(name)
        if not isinstance(element, dict):
            out.append(f"{label}: element {name} is missing")
            continue
        if element.get("policy") not in POLICIES:
            out.append(f"{label}: element {name} policy must be one of {POLICIES}")
        if not isinstance(element.get("reason"), str) or not element["reason"].strip():
            out.append(f"{label}: element {name} needs a non-empty reason")
        with_ref = element.get("with")
        if with_ref is not None:
            tokens = find_reference_tokens(with_ref)
            if len(tokens) != 1 or with_ref != "@" + tokens[0]:
                out.append(f"{label}: element {name} 'with' must be a single @name, got {with_ref!r}")
            elif tokens[0] in references:
                kind = references[tokens[0]].get("kind") if isinstance(references[tokens[0]], dict) else None
                want = ELEMENT_REFERENCE_KIND[name]
                if kind != want:
                    out.append(f"{label}: element {name} needs a {want!r} reference, "
                               f"@{tokens[0]} is {kind!r}")
    for field, cap in (("prompt", LIMITS["max_prompt_chars"]), ("notes", LIMITS["max_notes_chars"])):
        value = scene.get(field, "")
        if not isinstance(value, str):
            out.append(f"{label}: {field} must be a string")
        elif len(value) > cap:
            out.append(f"{label}: {field} is {len(value)} characters, limit {cap}")
    if scene.get("status") not in SCENE_STATUSES:
        out.append(f"{label}: status must be one of {SCENE_STATUSES}")
    for name in scene_bindings(scene):
        if len(name) > 32 or not _REF_NAME_RE.match(name):
            out.append(f"{label}: '@{name[:40]}' is not a valid reference name")
        elif name not in references:
            out.append(f"{label}: binds @{name} but no such reference exists")


def _check_render(render: Any, out: list[str]) -> None:
    if not isinstance(render, dict):
        out.append("render must be an object")
        return
    aspect = render.get("aspect_ratio", "keep")
    if aspect != "keep" and not (isinstance(aspect, str) and _ASPECT_RE.match(aspect)):
        out.append(f"render.aspect_ratio must be 'keep' or 'W:H': {aspect!r}")
    fps = render.get("fps", "keep")
    if fps != "keep" and not (_num(fps) and 0 < fps <= LIMITS["max_fps"]):
        out.append(f"render.fps must be 'keep' or a number in (0, {LIMITS['max_fps']}]: {fps!r}")
    for key in ("width", "height"):
        value = render.get(key, "keep")
        if value != "keep" and not (isinstance(value, int) and not isinstance(value, bool)
                                    and 16 <= value <= 8192):
            out.append(f"render.{key} must be 'keep' or an integer in [16, 8192]: {value!r}")
    crf = render.get("crf", 20)
    if not (isinstance(crf, int) and not isinstance(crf, bool) and 0 <= crf <= 51):
        out.append(f"render.crf must be an integer in [0, 51]: {crf!r}")


def problems(plan: Any, *, check_json: bool = True) -> list[str]:
    """Every reason the plan is invalid, as a list of strings (empty = valid)."""
    out: list[str] = []
    if not isinstance(plan, dict):
        return ["plan must be an object"]
    if plan.get("schema_version") != SCHEMA_VERSION:
        out.append(f"schema_version must be {SCHEMA_VERSION}, got {plan.get('schema_version')!r}")
    try:
        safe_id(plan.get("project_id"), "project_id")
    except PlanError as exc:
        out.append(str(exc))
    if plan.get("category") not in CATEGORIES:
        out.append(f"category must be one of {CATEGORIES}: {plan.get('category')!r}")
    if plan.get("mode") not in MODES:
        out.append(f"mode must be one of {MODES}: {plan.get('mode')!r}")
    title = plan.get("title", "")
    if not isinstance(title, str) or len(title) > LIMITS["max_title_chars"]:
        out.append(f"title must be a string of at most {LIMITS['max_title_chars']} characters")
    if plan.get("status") not in PLAN_STATUSES:
        out.append(f"status must be one of {PLAN_STATUSES}")
    notes = plan.get("notes", "")
    if not isinstance(notes, str) or len(notes) > LIMITS["max_notes_chars"]:
        out.append(f"notes must be a string of at most {LIMITS['max_notes_chars']} characters")

    source = plan.get("source")
    if source is not None:
        if not isinstance(source, dict):
            out.append("source must be an object or null")
        else:
            for key in ("duration", "fps"):
                if not _num(source.get(key)) or source[key] <= 0:
                    out.append(f"source.{key} must be a positive number")
            if _num(source.get("duration")) and source["duration"] > LIMITS["max_duration_seconds"] + 1e-6:
                out.append(f"source duration {source['duration']}s exceeds the declared limit of "
                           f"{LIMITS['max_duration_seconds']}s")
            if _num(source.get("fps")) and source["fps"] > LIMITS["max_fps"]:
                out.append(f"source fps {source['fps']} exceeds the declared limit of {LIMITS['max_fps']}")
            if source.get("asset") is not None and not is_sha256(source.get("asset")):
                out.append("source.asset must be a sha256 or null")

    references = plan.get("references")
    if not isinstance(references, dict):
        out.append("references must be an object")
        references = {}
    if len(references) > LIMITS["max_references"]:
        out.append(f"{len(references)} references exceed the declared limit of {LIMITS['max_references']}")
    total = 0
    for name, ref in references.items():
        _check_reference(name, ref, out)
        if isinstance(ref, dict) and isinstance(ref.get("bytes"), int):
            total += ref["bytes"]
    if total > LIMITS["max_total_reference_bytes"]:
        out.append(f"references total {total} bytes and exceed the declared limit of "
                   f"{LIMITS['max_total_reference_bytes']}")

    scenes = plan.get("scenes")
    if not isinstance(scenes, list):
        out.append("scenes must be a list")
        scenes = []
    if len(scenes) > LIMITS["max_scenes"]:
        out.append(f"{len(scenes)} scenes exceed the declared limit of {LIMITS['max_scenes']}")
    seen_ids: set[str] = set()
    previous_end = 0.0
    for index, scene in enumerate(scenes):
        _check_scene(scene, index, references, out)
        if isinstance(scene, dict):
            sid = scene.get("id")
            if sid in seen_ids:
                out.append(f"duplicate scene id {sid!r}")
            seen_ids.add(sid)
            if _num(scene.get("start")) and scene["start"] < previous_end - 1e-3:
                out.append(f"scene {sid}: starts before the previous scene ends")
            if _num(scene.get("end")):
                previous_end = scene["end"]

    tracks = plan.get("tracks")
    if not isinstance(tracks, dict):
        out.append("tracks must be an object")
        tracks = {}
    for lane in LANES:
        data = tracks.get(lane)
        if not isinstance(data, dict):
            out.append(f"tracks.{lane} is missing")
            continue
        if data.get("mode") not in LANE_MODES:
            out.append(f"tracks.{lane}.mode must be one of {LANE_MODES}")
        ref_name = _lane_reference(data)
        if ref_name is not None and ref_name not in references:
            out.append(f"tracks.{lane} uses @{ref_name} but no such reference exists")
        cues = data.get("cues")
        if cues is not None and (not isinstance(cues, list) or len(cues) > LIMITS["max_subtitle_cues"]):
            out.append(f"tracks.{lane}.cues must be a list of at most {LIMITS['max_subtitle_cues']}")
    _check_render(plan.get("render"), out)

    if check_json:
        try:
            canonical_json(plan)
        except (TypeError, ValueError) as exc:
            out.append(f"plan is not finite JSON: {exc}")
    return out


def validate(plan: Any) -> None:
    """Raise ``MissingReferenceError`` / ``LimitError`` / ``PlanError`` with every problem listed."""
    found = problems(plan)
    if not found:
        return
    message = "; ".join(found[:8]) + (f" (+{len(found) - 8} more)" if len(found) > 8 else "")
    if any("no such reference exists" in p for p in found):
        raise MissingReferenceError(message, found)
    if any("exceed the declared limit" in p or "exceeds the declared limit" in p for p in found):
        raise LimitError(message, found)
    raise PlanError(message, found)


# ------------------------------------------------------------------------------------- unknowns

def find_unknowns(plan: dict[str, Any]) -> list[dict[str, str]]:
    """Every ``UNKNOWN: ...`` marker in the plan with its path, so the UI can list what is not known."""
    found: list[dict[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for i, value in enumerate(node):
                label = f"{path}[{value['id']}]" if isinstance(value, dict) and "id" in value else f"{path}[{i}]"
                walk(value, label)
        elif is_unknown(node):
            found.append({"path": path, "marker": node})

    walk({k: v for k, v in plan.items() if k not in ("limits",)}, "")
    return found


# ------------------------------------------------------------------------------------- output

def effective_output(plan: dict[str, Any]) -> dict[str, Any]:
    """Resolved aspect ratio / fps / size: preserved from the source unless the render settings change them."""
    render = plan.get("render") or {}
    source = plan.get("source") or {}
    out: dict[str, Any] = {}

    aspect = render.get("aspect_ratio", "keep")
    if aspect != "keep":
        out["aspect_ratio"], out["aspect_source"] = aspect, "render"
    elif source.get("aspect_ratio"):
        out["aspect_ratio"], out["aspect_source"] = source["aspect_ratio"], "source"
    else:
        out["aspect_ratio"] = unknown("no source video; set render.aspect_ratio explicitly")
        out["aspect_source"] = "none"

    fps = render.get("fps", "keep")
    if fps != "keep":
        out["fps"], out["fps_source"] = fps, "render"
    elif _num(source.get("fps")):
        out["fps"], out["fps_source"] = source["fps"], "source"
    else:
        out["fps"] = unknown("no source video; set render.fps explicitly")
        out["fps_source"] = "none"

    width, height = render.get("width", "keep"), render.get("height", "keep")
    src_w, src_h = source.get("display_width"), source.get("display_height")
    if width != "keep" and height != "keep":
        out["width"], out["height"] = width, height
    elif aspect != "keep" and ":" in str(aspect) and (height != "keep" or src_h):
        num, den = (int(x) for x in aspect.split(":"))
        base_h = height if height != "keep" else src_h
        out["height"] = int(base_h)
        out["width"] = int(base_h * num / den) // 2 * 2
    elif src_w and src_h:
        out["width"], out["height"] = int(src_w), int(src_h)
    else:
        out["width"] = out["height"] = unknown("no source video")
    return out


# ------------------------------------------------------------------------------------- limits

def limits_report(plan: dict[str, Any]) -> dict[str, Any]:
    """Declared limits next to current usage; meant to be shown to the owner BEFORE any run."""
    refs = plan.get("references") or {}
    source = plan.get("source") or {}
    scenes = plan.get("scenes") or []
    duration = source.get("duration") if _num(source.get("duration")) else None
    ref_bytes = sum(r.get("bytes", 0) for r in refs.values() if isinstance(r, dict))
    usage = {
        "references": {"used": len(refs), "max": LIMITS["max_references"]},
        "reference_bytes": {"used": ref_bytes, "max": LIMITS["max_total_reference_bytes"]},
        "duration_seconds": {"used": duration, "max": LIMITS["max_duration_seconds"]},
        "scenes": {"used": len(scenes), "max": LIMITS["max_scenes"]},
    }
    violations = [p for p in problems(plan, check_json=False)
                  if "declared limit" in p or "exceed the declared limit" in p]
    return {"limits": limits_snapshot(), "usage": usage, "violations": violations}


# ------------------------------------------------------------------------------------- readiness

def readiness(plan: dict[str, Any]) -> dict[str, Any]:
    """What blocks a preview or render.  ``ready`` is true only when ``blockers`` is empty."""
    blockers = list(problems(plan))
    warnings: list[str] = []
    scenes = plan.get("scenes") or []
    if not scenes:
        blockers.append("no scenes: analyze a source video or add scenes first")
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        sid = scene.get("id")
        for name, element in (scene.get("elements") or {}).items():
            if not isinstance(element, dict):
                continue
            policy = element.get("policy")
            if policy == "unknown":
                blockers.append(f"scene {sid}: element {name} is undecided (policy unknown) - choose keep or replace")
            elif policy == "replace" and not element.get("with") and not str(scene.get("prompt") or "").strip():
                blockers.append(f"scene {sid}: element {name} is set to replace but has no @reference "
                                f"and the scene has no prompt")
    output = effective_output(plan)
    for key in ("aspect_ratio", "fps"):
        if is_unknown(output.get(key)):
            blockers.append(f"output {key} is unresolved: {output[key]}")
    unknowns = find_unknowns(plan)
    if unknowns:
        warnings.append(f"{len(unknowns)} field(s) are UNKNOWN (kept as in the source); see find_unknowns")
    return {"ready": not blockers, "blockers": blockers, "warnings": warnings,
            "limits": limits_report(plan), "output": output}


# ------------------------------------------------------------------------------------- estimate

def _fps_of(plan: dict[str, Any]) -> float:
    fps = effective_output(plan).get("fps")
    return float(fps) if _num(fps) else 25.0


def estimate(plan: dict[str, Any]) -> dict[str, Any]:
    """Machine-time estimate per scene from the declared COST_MODEL constants.

    Cloud spend is 0 and no paid call is made: this package only describes local work.
    """
    ops = COST_MODEL["ops"]
    fps = _fps_of(plan)
    per_frame_preview = COST_MODEL["preview_frames_per_scene"]
    subtitles_on = bool((plan.get("tracks") or {}).get("subtitles", {}).get("enabled")
                        and (plan.get("tracks") or {}).get("subtitles", {}).get("cues"))
    scenes_out: list[dict[str, Any]] = []
    render_total = 0.0
    preview_total = 0.0

    def line(op: str, units: float) -> dict[str, Any]:
        seconds = round(units * ops[op]["seconds_per_unit"], 3)
        return {"op": op, "units": round(units, 3), "unit": ops[op]["unit"],
                "seconds_per_unit": ops[op]["seconds_per_unit"], "seconds": seconds}

    for scene in plan.get("scenes") or []:
        duration = float(scene.get("duration") or 0.0)
        frames = duration * fps
        elements = scene.get("elements") or {}
        replaced = {n for n, e in elements.items() if isinstance(e, dict) and e.get("policy") == "replace"}
        visual = replaced & {"face_character", "clothes", "background", "product", "text"}
        render_ops: list[dict[str, Any]] = []
        preview_ops: list[dict[str, Any]] = []
        if visual:
            render_ops.append(line("reencode", duration))
        else:
            render_ops.append(line("copy_trim", duration))
        if "face_character" in replaced:
            render_ops.append(line("faceswap", frames))
            preview_ops.append(line("faceswap", per_frame_preview))
        for name in ("clothes", "background", "product"):
            if name in replaced:
                render_ops.append(line("element_replace", frames))
                preview_ops.append(line("element_replace", per_frame_preview))
        if "text" in replaced or subtitles_on:
            render_ops.append(line("subtitle_burn", duration))
            preview_ops.append(line("subtitle_burn", min(duration, 1.0)))
        if "music" in replaced:
            render_ops.append(line("music_mix", duration))
        render_s = round(sum(o["seconds"] for o in render_ops), 3)
        preview_s = round(sum(o["seconds"] for o in preview_ops)
                          + COST_MODEL["preview_overhead_seconds_per_scene"], 3)
        render_total += render_s
        preview_total += preview_s
        scenes_out.append({"scene_id": scene.get("id"), "duration": duration, "frames": round(frames, 1),
                           "replaced": sorted(replaced), "preview_seconds": preview_s,
                           "render_seconds": render_s, "operations": render_ops})
    mux = line("mux_concat", 1.0)
    return {
        "model_version": COST_MODEL["model_version"],
        "basis": COST_MODEL["basis"],
        "fps_used": fps,
        "scenes": scenes_out,
        "mux": mux,
        "preview_seconds": round(preview_total, 3),
        "render_seconds": round(render_total + mux["seconds"], 3),
        "cloud_spend_usd": 0.0,
        "paid_calls": 0,
        "statement": CLOUD_STATEMENT,
    }


# ------------------------------------------------------------------------------------- diff

def _short(value: Any) -> str:
    text = canonical_json(value) if not isinstance(value, str) else value
    return text if len(text) <= 60 else text[:57] + "..."


def _walk_diff(old: Any, new: Any, path: str, out: list[dict[str, Any]]) -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in old:
                out.append({"path": sub, "change": "added", "new": _short(new[key])})
            elif key not in new:
                out.append({"path": sub, "change": "removed", "old": _short(old[key])})
            else:
                _walk_diff(old[key], new[key], sub, out)
        return
    if isinstance(old, list) and isinstance(new, list):
        keyed = (all(isinstance(x, dict) and "id" in x for x in old + new) and (old or new))
        if keyed:
            old_by = {x["id"]: x for x in old}
            new_by = {x["id"]: x for x in new}
            for key in list(old_by) + [k for k in new_by if k not in old_by]:
                sub = f"{path}[{key}]"
                if key not in old_by:
                    out.append({"path": sub, "change": "added", "new": "(object)"})
                elif key not in new_by:
                    out.append({"path": sub, "change": "removed", "old": "(object)"})
                else:
                    _walk_diff(old_by[key], new_by[key], sub, out)
            return
        for i in range(max(len(old), len(new))):
            sub = f"{path}[{i}]"
            if i >= len(old):
                out.append({"path": sub, "change": "added", "new": _short(new[i])})
            elif i >= len(new):
                out.append({"path": sub, "change": "removed", "old": _short(old[i])})
            else:
                _walk_diff(old[i], new[i], sub, out)
        return
    if old != new:
        out.append({"path": path, "change": "changed", "old": _short(old), "new": _short(new)})


def diff(old: dict[str, Any] | None, new: dict[str, Any], *, limit: int = 200) -> dict[str, Any]:
    """Structural diff ignoring version bookkeeping; scenes are matched by id, not by position."""
    strip = lambda p: {k: v for k, v in (p or {}).items() if k not in _VOLATILE_KEYS}  # noqa: E731
    changes: list[dict[str, Any]] = []
    _walk_diff(strip(old), strip(new), "", changes)
    total = len(changes)
    shown = changes[:limit]
    if old is None:
        summary = "initial version"
    elif total == 0:
        summary = "no content change"
    else:
        parts = []
        for c in shown[:4]:
            if c["change"] == "changed":
                parts.append(f"{c['path']}: {c['old']} -> {c['new']}")
            elif c["change"] == "added":
                parts.append(f"{c['path']} added")
            else:
                parts.append(f"{c['path']} removed")
        more = f" (+{total - 4} more)" if total > 4 else ""
        summary = f"{total} change(s): " + "; ".join(parts) + more
    return {"count": total, "changes": shown, "truncated": total > limit, "summary": summary[:400]}


# ------------------------------------------------------------------------------------- edits

def _scene_index(plan: dict[str, Any], scene_id: Any) -> dict[str, Any]:
    for scene in plan["scenes"]:
        if scene.get("id") == scene_id:
            return scene
    raise PlanError(f"no scene with id {scene_id!r}")


def _targets(plan: dict[str, Any], op: dict[str, Any]) -> list[dict[str, Any]]:
    scene_id = op.get("scene")
    if scene_id == "*":
        return list(plan["scenes"])
    return [_scene_index(plan, scene_id)]


def _need(op: dict[str, Any], key: str, kind: type | tuple[type, ...] = str) -> Any:
    if key not in op or not isinstance(op[key], kind):
        raise PlanError(f"op {op.get('op')!r} needs {key!r} of type {kind}")
    return op[key]


def _touch_scene(scene: dict[str, Any]) -> None:
    if scene.get("status") == "analyzed":
        scene["status"] = "edited"


def _normalise_with(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise PlanError(f"'with' must be a string like '@name', got {value!r}")
    name = value[1:] if value.startswith("@") else value
    if not _REF_NAME_RE.match(name):
        raise PlanError(f"'with' is not a valid reference name: {value!r}")
    return "@" + name


def _op_set_policy(plan: dict[str, Any], op: dict[str, Any], author: str) -> None:
    element = _need(op, "element")
    policy = _need(op, "policy")
    reason = _need(op, "reason")
    if element not in ELEMENTS:
        raise PlanError(f"element must be one of {ELEMENTS}: {element!r}")
    if policy not in POLICIES:
        raise PlanError(f"policy must be one of {POLICIES}: {policy!r}")
    if not reason.strip():
        raise PlanError("a policy change needs a non-empty reason")
    with_ref = _normalise_with(op.get("with"))
    for scene in _targets(plan, op):
        current = scene["elements"][element]
        current["policy"] = policy
        current["reason"] = reason.strip()
        current["with"] = with_ref if policy == "replace" else None
        current["set_by"] = author
        _touch_scene(scene)


def _op_set_render(plan: dict[str, Any], op: dict[str, Any], _author: str) -> None:
    settings = _need(op, "settings", dict)
    allowed = set(default_render(plan.get("category", "motion")))
    extra = sorted(set(settings) - allowed)
    if extra:
        raise PlanError(f"unknown render settings: {extra}")
    plan["render"].update(copy.deepcopy(settings))


def _op_set_track(plan: dict[str, Any], op: dict[str, Any], _author: str) -> None:
    lane = _need(op, "lane")
    if lane not in LANES:
        raise PlanError(f"lane must be one of {LANES}: {lane!r}")
    settings = _need(op, "settings", dict)
    allowed = {"enabled", "mode", "source", "cues"}
    extra = sorted(set(settings) - allowed)
    if extra:
        raise PlanError(f"unknown track settings: {extra}")
    if "source" in settings and settings["source"] not in (None, "original"):
        settings = dict(settings, source=_normalise_with(settings["source"]))
    if "cues" in settings and lane != "subtitles":
        raise PlanError("only the subtitles lane has cues")
    plan["tracks"][lane].update(copy.deepcopy(settings))


def _op_add_reference(plan: dict[str, Any], op: dict[str, Any], _author: str) -> None:
    name = _need(op, "name")
    entry = {key: op.get(key) for key in ("kind", "sha256", "ext", "bytes", "original_name", "added_ts")}
    entry["added_ts"] = entry["added_ts"] or now_iso()
    if name in plan["references"] and not op.get("replace"):
        raise PlanError(f"reference @{name} already exists (pass replace=true to rebind it)")
    plan["references"][name] = entry


def _op_remove_reference(plan: dict[str, Any], op: dict[str, Any], _author: str) -> None:
    name = _need(op, "name")
    if name not in plan["references"]:
        raise PlanError(f"no reference named @{name}")
    users = [s["id"] for s in plan["scenes"] if name in scene_bindings(s)]
    lanes = [lane for lane, data in plan["tracks"].items() if _lane_reference(data) == name]
    if users or lanes:
        raise PlanError(f"reference @{name} is still bound by scenes {users} and lanes {lanes}; unbind it first",
                        [f"@{name} bound by {users + lanes}"])
    del plan["references"][name]


def _op_set_refs(plan: dict[str, Any], op: dict[str, Any], author: str) -> None:
    refs = _need(op, "refs", list)
    names = []
    for item in refs:
        norm = _normalise_with(item)
        if norm is None:
            raise PlanError("empty reference name in refs")
        names.append(norm[1:])
    for scene in _targets(plan, op):
        scene["refs"] = list(dict.fromkeys(names))
        _touch_scene(scene)


def _op_set_text_field(field: str, cap_key: str) -> Callable[[dict[str, Any], dict[str, Any], str], None]:
    def apply(plan: dict[str, Any], op: dict[str, Any], _author: str) -> None:
        value = _need(op, field)
        if len(value) > LIMITS[cap_key]:
            raise LimitError(f"{field} is {len(value)} characters, declared limit {LIMITS[cap_key]}")
        for scene in _targets(plan, op):
            scene[field] = value
            _touch_scene(scene)
    return apply


def _op_set_scene_status(plan: dict[str, Any], op: dict[str, Any], _author: str) -> None:
    status = _need(op, "status")
    if status not in SCENE_STATUSES:
        raise PlanError(f"status must be one of {SCENE_STATUSES}: {status!r}")
    for scene in _targets(plan, op):
        scene["status"] = status


def _op_set_top(field: str, allowed: tuple[str, ...] | None = None, cap_key: str | None = None):
    def apply(plan: dict[str, Any], op: dict[str, Any], _author: str) -> None:
        value = _need(op, field)
        if allowed is not None and value not in allowed:
            raise PlanError(f"{field} must be one of {allowed}: {value!r}")
        if cap_key is not None and len(value) > LIMITS[cap_key]:
            raise LimitError(f"{field} is {len(value)} characters, declared limit {LIMITS[cap_key]}")
        plan[field] = value
    return apply


OPS: dict[str, Callable[[dict[str, Any], dict[str, Any], str], None]] = {
    "set_title": _op_set_top("title", cap_key="max_title_chars"),
    "set_category": _op_set_top("category", CATEGORIES),
    "set_mode": _op_set_top("mode", MODES),
    "set_notes": _op_set_top("notes", cap_key="max_notes_chars"),
    "set_render": _op_set_render,
    "set_track": _op_set_track,
    "set_policy": _op_set_policy,
    "set_prompt": _op_set_text_field("prompt", "max_prompt_chars"),
    "set_scene_notes": _op_set_text_field("notes", "max_notes_chars"),
    "set_scene_refs": _op_set_refs,
    "set_scene_status": _op_set_scene_status,
    "add_reference": _op_add_reference,
    "remove_reference": _op_remove_reference,
}


def apply_ops(plan: dict[str, Any], ops: Iterable[dict[str, Any]], *, author: str = "owner",
              now: str | None = None) -> dict[str, Any]:
    """Return a new plan with every op applied, or raise and change nothing (all-or-nothing)."""
    result = copy.deepcopy(plan)
    count = 0
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise PlanError(f"unknown op {op.get('op') if isinstance(op, dict) else op!r}; known: {sorted(OPS)}")
        OPS[op["op"]](result, op, author)
        count += 1
    if count == 0:
        raise PlanError("no operations given")
    validate(result)
    if content_sha256(result) == content_sha256(plan):
        return result                      # nothing changed: no new status, timestamp or limits snapshot
    result["updated_ts"] = now or now_iso()
    result["limits"] = limits_snapshot()
    if result.get("status") in ("analyzed", "draft"):
        result["status"] = "edited"
    validate(result)
    return result


def attach_analysis(plan: dict[str, Any], analyzed: dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
    """Replace the analysis-owned parts (source, scenes, tracks, analysis) and keep the owner's own parts
    (title, category, mode, references, notes, render choices).  Returns a new plan."""
    result = copy.deepcopy(plan)
    for key in ("source", "scenes", "tracks", "analysis"):
        result[key] = copy.deepcopy(analyzed[key])
    result["status"] = "analyzed"
    result["updated_ts"] = now or now_iso()
    result["limits"] = limits_snapshot()
    validate(result)
    return result


def summary(plan: dict[str, Any]) -> dict[str, Any]:
    source = plan.get("source") or {}
    return {
        "project_id": plan.get("project_id"), "title": plan.get("title"), "category": plan.get("category"),
        "mode": plan.get("mode"), "version": plan.get("version"), "status": plan.get("status"),
        "scenes": len(plan.get("scenes") or []), "duration": source.get("duration"),
        "references": len(plan.get("references") or {}), "updated_ts": plan.get("updated_ts"),
    }
