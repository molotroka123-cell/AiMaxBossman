"""Motion Design Engine 2.0 core (audit 28.10, 2.3): a declarative scene graph with keyframes, Bezier/spring easing
and beat-synced cuts. Pure Python, no I/O: rendering and audio analysis plug in from outside.

A scene is plain JSON:
  {"fps": 30, "duration_ms": 8000,
   "layers": [{"id": "title", "kind": "kinetic_text",
               "props": {"x": [{"t": 0, "v": 0.0}, {"t": 600, "v": 1.0, "ease": "easeOutExpo"}]}}]}
Every animated property is a list of keyframes {t (ms), v, ease}; `ease` shapes the segment that ENDS at that keyframe.
"""
from __future__ import annotations

import bisect
import math
from typing import Any, Callable

LAYER_KINDS = {"video", "image", "voxel3d", "kinetic_text", "particles", "audio"}
ANIMATABLE = {"x", "y", "scale", "rotation", "opacity", "blur", "glow", "camera_fov"}
MAX_LAYERS = 64
MAX_KEYFRAMES = 2048


class SceneError(ValueError):
    pass


def _cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    def at(t: float) -> float:
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        lo, hi = 0.0, 1.0                      # solve bx(u)=t by bisection, return by(u)
        for _ in range(40):
            u = (lo + hi) / 2
            bx = 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3
            lo, hi = (u, hi) if bx < t else (lo, u)
        u = (lo + hi) / 2
        return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3
    return at


def _spring(stiffness: float = 170.0, damping: float = 12.0) -> Callable[[float], float]:
    """Unit-mass damped spring settling at 1 over normalized time 0..1 (1 s of physical time); overshoots when underdamped."""
    w0 = math.sqrt(stiffness)
    zeta = damping / (2 * w0)

    def at(t: float) -> float:
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        if zeta < 1:
            wd = w0 * math.sqrt(1 - zeta * zeta)
            return 1 - math.exp(-zeta * w0 * t) * (math.cos(wd * t) + zeta * w0 / wd * math.sin(wd * t))
        return 1 - math.exp(-w0 * t) * (1 + w0 * t)
    return at


def _out_expo(t: float) -> float:
    return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


def _in_out_back(t: float) -> float:
    c = 1.70158 * 1.525
    return (((2 * t) ** 2 * ((c + 1) * 2 * t - c)) / 2 if t < 0.5
            else ((2 * t - 2) ** 2 * ((c + 1) * (t * 2 - 2) + c) + 2) / 2)


EASINGS: dict[str, Callable[[float], float]] = {
    "linear": lambda t: t,
    "easeInQuad": lambda t: t * t,
    "easeOutQuad": lambda t: 1 - (1 - t) ** 2,
    "easeInOutCubic": lambda t: 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2,
    "easeOutExpo": _out_expo,
    "easeInOutBack": _in_out_back,
    "spring": _spring(),
    "bezier": _cubic_bezier(0.25, 0.1, 0.25, 1.0),
}


def ease(name: str, t: float) -> float:
    fn = EASINGS.get(name)
    if fn is None:
        raise SceneError(f"unknown easing {name!r}; known: {sorted(EASINGS)}")
    return fn(min(1.0, max(0.0, t)))


def evaluate(keyframes: list[dict[str, Any]], t_ms: float) -> float:
    """Value of an animated property at `t_ms`. Before the first / after the last keyframe it holds the end value."""
    if not keyframes:
        raise SceneError("empty keyframe list")
    times = [k["t"] for k in keyframes]
    if t_ms <= times[0]:
        return float(keyframes[0]["v"])
    if t_ms >= times[-1]:
        return float(keyframes[-1]["v"])
    i = bisect.bisect_right(times, t_ms)
    a, b = keyframes[i - 1], keyframes[i]
    span = b["t"] - a["t"]
    f = ease(b.get("ease", "linear"), (t_ms - a["t"]) / span)
    return float(a["v"]) + (float(b["v"]) - float(a["v"])) * f


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_scene(scene: Any) -> dict[str, Any]:
    """Strict structural validation; returns the scene or raises SceneError with the exact path of the problem."""
    if not isinstance(scene, dict):
        raise SceneError("scene must be an object")
    fps, dur = scene.get("fps"), scene.get("duration_ms")
    if not isinstance(fps, int) or isinstance(fps, bool) or not 1 <= fps <= 120:
        raise SceneError("fps must be an integer 1..120")
    if not _num(dur) or not 0 < dur <= 600_000:
        raise SceneError("duration_ms must be 1..600000")
    layers = scene.get("layers")
    if not isinstance(layers, list) or not layers or len(layers) > MAX_LAYERS:
        raise SceneError(f"layers must be a list of 1..{MAX_LAYERS}")
    seen: set[str] = set()
    total = 0
    for li, layer in enumerate(layers):
        where = f"layers[{li}]"
        if not isinstance(layer, dict) or not isinstance(layer.get("id"), str) or not layer["id"]:
            raise SceneError(f"{where}.id required")
        if layer["id"] in seen:
            raise SceneError(f"{where}.id duplicate {layer['id']!r}")
        seen.add(layer["id"])
        if layer.get("kind") not in LAYER_KINDS:
            raise SceneError(f"{where}.kind must be one of {sorted(LAYER_KINDS)}")
        for prop, kfs in (layer.get("props") or {}).items():
            pw = f"{where}.props.{prop}"
            if prop not in ANIMATABLE:
                raise SceneError(f"{pw}: not animatable; allowed {sorted(ANIMATABLE)}")
            if not isinstance(kfs, list) or not kfs:
                raise SceneError(f"{pw}: keyframes must be a non-empty list")
            total += len(kfs)
            if total > MAX_KEYFRAMES:
                raise SceneError(f"too many keyframes (>{MAX_KEYFRAMES})")
            last = -1.0
            for ki, k in enumerate(kfs):
                if not isinstance(k, dict) or not _num(k.get("t")) or not _num(k.get("v")) or not math.isfinite(k["v"]):
                    raise SceneError(f"{pw}[{ki}]: needs numeric t and finite v")
                if k["t"] <= last:
                    raise SceneError(f"{pw}[{ki}]: t must strictly increase")
                if k["t"] < 0 or k["t"] > dur:
                    raise SceneError(f"{pw}[{ki}]: t outside 0..duration_ms")
                if k.get("ease", "linear") not in EASINGS:
                    raise SceneError(f"{pw}[{ki}]: unknown easing {k.get('ease')!r}")
                last = k["t"]
    return scene


def frame_state(scene: dict[str, Any], frame: int) -> dict[str, dict[str, float]]:
    """Every animated property of every layer at a frame: {layer_id: {prop: value}}."""
    t_ms = frame * 1000.0 / scene["fps"]
    return {l["id"]: {p: evaluate(k, t_ms) for p, k in (l.get("props") or {}).items()} for l in scene["layers"]}


def snap_to_beats(times_ms: list[float], beats_ms: list[float], max_shift_ms: float = 120.0) -> list[int]:
    """Move each cut to the nearest beat if one lies within `max_shift_ms`; otherwise keep it. Output stays sorted
    and de-duplicated (two cuts snapping to one beat collapse into one)."""
    if not beats_ms:
        return sorted({round(t) for t in times_ms})
    bs = sorted(beats_ms)
    out = set()
    for t in times_ms:
        i = bisect.bisect_left(bs, t)
        near = min(bs[max(0, i - 1):i + 1], key=lambda b: abs(b - t))
        out.add(round(near if abs(near - t) <= max_shift_ms else t))
    return sorted(out)


def beat_cuts(beats_ms: list[float], duration_ms: float, every_beats: int = 4, offset_beats: int = 0) -> list[int]:
    """Cut list on every Nth beat (a bar, by default), strictly inside the clip."""
    if every_beats < 1:
        raise SceneError("every_beats must be >= 1")
    return [round(b) for b in beats_ms[offset_beats::every_beats] if 0 < b < duration_ms]


def punch_zoom(beats_ms: list[float], strength: float = 0.08, decay_ms: float = 220.0) -> list[dict[str, Any]]:
    """Scale keyframes: each beat snaps to 1+strength and eases back to 1 (a zoom punch on the kick)."""
    kfs: list[dict[str, Any]] = [{"t": 0, "v": 1.0}]
    for b in beats_ms:
        b = round(b)
        if b <= kfs[-1]["t"]:
            continue
        if b - 1 > kfs[-1]["t"]:
            kfs.append({"t": b - 1, "v": 1.0})          # hold at rest, so the punch is a snap and not a ramp
        kfs.append({"t": b, "v": 1.0 + strength})
        kfs.append({"t": b + round(decay_ms), "v": 1.0, "ease": "easeOutExpo"})
    return kfs
