"""Procedural (CSG-style) high-poly props: dense closed primitives + noise displacement + paint.

Each model is a list of `Part`s. A part is one closed (watertight) triangle mesh in metres, Y up,
plus a paint function (position, normal -> sRGB colour), a material key ("matte", "gloss" or
"glow:#rrggbb") and a shading mode. Models are described declaratively by part specs (hpspec.py,
examples in examples/hipoly/*.json). Parts overlap like a sculptor's blockout (no boolean union:
the OSS boolean kernel manifold3d is blocked by Smart App Control on the owner PC), so every part
stays individually watertight. `hipoly.build` decimates the dense parts to a triangle budget.

Dense primitives come from trimesh (MIT): icosphere, revolve (lathe), cylinder, torus, box +
subdivision. Noise is a small deterministic value-noise fBm in numpy (no extra dependency).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import trimesh

Paint = Callable[[np.ndarray, np.ndarray], np.ndarray]  # (N,3) pos, (N,3) normal -> (N,3) sRGB 0..1


@dataclass
class Part:
    name: str
    mesh: trimesh.Trimesh
    paint: Paint
    material: str = "matte"      # "matte" | "gloss" | "glow:#rrggbb"
    shading: str = "smooth"      # "smooth" | "flat"
    decimate: bool = True        # False: keep as built (already low-poly, e.g. crystal prisms)


@dataclass
class Model:
    name: str
    label: str
    height: float                # metres (1 m = one BossBlocks block)
    parts: list[Part] = field(default_factory=list)
    target: int = 8000           # triangle budget after decimation


# ----------------------------------------------------------------------------------- noise

def _hash(ix: np.ndarray, iy: np.ndarray, iz: np.ndarray, seed: int) -> np.ndarray:
    h = (ix * 73856093) ^ (iy * 19349663) ^ (iz * 83492791) ^ np.int64(seed * 2654435761 & 0x7FFFFFFF)
    h = h & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF).astype(np.float64) / 65535.0


def value_noise(p: np.ndarray, seed: int = 0) -> np.ndarray:
    """Smooth 3D value noise in [0, 1] (trilinear with smoothstep), deterministic."""
    f = np.floor(p)
    t = p - f
    t = t * t * (3.0 - 2.0 * t)
    i = f.astype(np.int64)
    out = np.zeros(len(p))
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                w = (t[:, 0] if dx else 1 - t[:, 0]) * (t[:, 1] if dy else 1 - t[:, 1]) * (t[:, 2] if dz else 1 - t[:, 2])
                out += w * _hash(i[:, 0] + dx, i[:, 1] + dy, i[:, 2] + dz, seed)
    return out


def fbm(p: np.ndarray, freq: float | tuple = 1.0, octaves: int = 4, seed: int = 0) -> np.ndarray:
    """Fractal noise centred on 0, roughly in [-0.5, 0.5]."""
    q = np.asarray(p, float) * np.asarray(freq, float)
    total, amp, norm = np.zeros(len(q)), 1.0, 0.0
    for o in range(octaves):
        total += amp * (value_noise(q, seed + 17 * o) - 0.5)
        norm += amp
        amp *= 0.5
        q = q * 2.03 + 11.7
    return total / norm


# ------------------------------------------------------------------------------ primitives

def _yup(m: trimesh.Trimesh) -> trimesh.Trimesh:
    """trimesh builds revolve/cylinder along +Z; turn +Z into +Y (right-handed)."""
    m.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    return m


def _densify_profile(profile: list[tuple[float, float]], step: float) -> np.ndarray:
    pts = [np.array(profile[0], float)]
    for a, b in zip(profile[:-1], profile[1:]):
        a, b = np.array(a, float), np.array(b, float)
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        for k in range(1, n + 1):
            pts.append(a + (b - a) * k / n)
    return np.array(pts)


def lathe(profile: list[tuple[float, float]], sections: int = 48, step: float = 0.05,
          at: tuple = (0, 0, 0)) -> trimesh.Trimesh:
    """Revolve an (radius, y) profile around the Y axis. Profile must start and end at radius 0."""
    prof = _densify_profile(profile, step) if step else np.array(profile, float)
    m = _yup(trimesh.creation.revolve(prof, sections=sections))
    m.apply_translation(at)
    return m


def ellipsoid(center, radii, level: int = 4) -> trimesh.Trimesh:
    m = trimesh.creation.icosphere(subdivisions=level)
    m.apply_scale(np.asarray(radii, float))
    m.apply_translation(center)
    return m


def rounded_box(center, size, radius: float = 0.05, level: int = 4) -> trimesh.Trimesh:
    """Box subdivided `level` times, then edges/corners rounded by clamp-and-offset."""
    half = np.asarray(size, float) / 2.0
    m = trimesh.creation.box(extents=np.ones(3) * 2.0)
    for _ in range(level):
        m = m.subdivide()
    u = m.vertices  # in [-1, 1]
    # bunch grid points toward the edges so the rounded band gets vertices
    u = np.sign(u) * np.abs(u) ** 0.7
    inner = np.maximum(half - radius, 1e-4)
    p = u * half
    core = np.clip(p, -inner, inner)
    d = p - core
    ln = np.linalg.norm(d, axis=1, keepdims=True)
    safe = np.where(ln > 1e-9, ln, 1.0)
    p = np.where(ln > 1e-9, core + d / safe * radius, p)
    out = trimesh.Trimesh(p + np.asarray(center, float), m.faces, process=False)
    return out


def tube(p0, p1, radius: float, sections: int = 16, r1: float | None = None, ring_step: float = 0.05) -> trimesh.Trimesh:
    """Closed cylinder (optionally tapered to r1) between two points, as a ring grid with capped
    ends: well-shaped quads along the axis, so it both displaces and decimates cleanly."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    length = float(np.linalg.norm(p1 - p0))
    r_end = radius if r1 is None else r1
    rings = max(1, int(np.ceil(length / ring_step)))
    prof = [(0.0, 0.0)] + [(radius + (r_end - radius) * k / rings, length * k / rings) for k in range(rings + 1)] + [(0.0, length)]
    m = lathe(prof, sections=sections, step=0)
    m.apply_transform(trimesh.geometry.align_vectors([0, 1, 0], (p1 - p0) / length))
    m.apply_translation(p0)
    return m


def torus(center, major: float, minor: float, axis=(0, 1, 0), sections=(48, 16)) -> trimesh.Trimesh:
    m = trimesh.creation.torus(major_radius=major, minor_radius=minor,
                               major_sections=sections[0], minor_sections=sections[1])
    m.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], np.asarray(axis, float)))
    m.apply_translation(center)
    return m


def prism(base, radius: float, height: float, tip: float, sides: int = 6, direction=(0, 1, 0),
          twist: float = 0.0) -> trimesh.Trimesh:
    """Crystal: n-sided prism with a pyramid tip, low-poly by design."""
    m = lathe([(0, 0), (radius * 0.8, 0), (radius, height * 0.15), (radius, height), (0, height + tip)],
              sections=sides, step=0)
    if twist:
        m.apply_transform(trimesh.transformations.rotation_matrix(twist, [0, 1, 0]))
    m.apply_transform(trimesh.geometry.align_vectors([0, 1, 0], np.asarray(direction, float)))
    m.apply_translation(base)
    return m


def refine(m: trimesh.Trimesh, max_edge: float) -> trimesh.Trimesh:
    """Uniform midpoint subdivision until the longest edge is below max_edge (keeps watertight)."""
    for _ in range(6):
        if m.edges_unique_length.max() <= max_edge:
            break
        m = m.subdivide()
    return m


def displace(m: trimesh.Trimesh, amp: float, freq, octaves: int = 4, seed: int = 0,
             ridged: bool = False) -> trimesh.Trimesh:
    n = m.vertex_normals.copy()
    d = fbm(m.vertices, freq, octaves, seed)
    if ridged:
        d = 0.5 - np.abs(d) * 2.0
    v = m.vertices + n * (amp * d)[:, None]
    return trimesh.Trimesh(v, m.faces, process=False)


def grooves(m: trimesh.Trimesh, depth: float, period: float, axis: int, width: float = 0.12) -> trimesh.Trimesh:
    """Cut plank grooves: push vertices inward where the coordinate along `axis` hits a seam."""
    n = m.vertex_normals.copy()
    x = m.vertices[:, axis] / period
    dist = np.abs(x - np.round(x))
    g = np.clip(1.0 - dist / width, 0.0, 1.0)
    v = m.vertices - n * (depth * g)[:, None]
    return trimesh.Trimesh(v, m.faces, process=False)


# ----------------------------------------------------------------------------------- paint

def hexrgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[k:k + 2], 16) / 255.0 for k in (0, 2, 4)])


def paint(base: str, vary: float = 0.08, freq: float = 3.0, seed: int = 1,
          top: str | None = None, top_from: float = 0.0, top_to: float = 1.0,
          up: str | None = None, up_amount: float = 0.0, dark: float = 0.0, dark_freq: float = 6.0) -> Paint:
    """Base colour * brightness noise, optional vertical gradient (y from..to -> top colour),
    optional 'up'-facing tint (moss, snow), optional dark cavities (from a second noise)."""
    b = hexrgb(base)
    t = hexrgb(top) if top else None
    u = hexrgb(up) if up else None

    def f(p: np.ndarray, n: np.ndarray) -> np.ndarray:
        c = np.tile(b, (len(p), 1))
        if t is not None:
            k = np.clip((p[:, 1] - top_from) / max(top_to - top_from, 1e-6), 0, 1)[:, None]
            c = c * (1 - k) + t * k
        if u is not None and up_amount > 0:
            k = np.clip((n[:, 1] - 0.45) * 2.5, 0, 1) * np.clip(0.5 + fbm(p, 2.5, 3, seed + 5) * 2.2, 0, 1)
            c = c * (1 - up_amount * k[:, None]) + u * (up_amount * k[:, None])
        c = c * (1.0 + vary * 2.0 * fbm(p, freq, 3, seed))[:, None]
        if dark > 0:
            c = c * (1.0 - dark * np.clip(fbm(p, dark_freq, 3, seed + 9) * 2.5, 0, 1))[:, None]
        return np.clip(c, 0, 1)

    return f


def solid(base: str) -> Paint:
    b = hexrgb(base)
    return lambda p, n: np.tile(b, (len(p), 1))


# ------------------------------------------------------------------------------ examples

EXAMPLES_DIR = __import__("pathlib").Path(__file__).resolve().parent / "examples" / "hipoly"


def example_specs() -> dict[str, dict]:
    """The hand-made part specs (tree, crystal golem, treasure chest, lantern, mushroom house):
    game models and few-shot examples for the local model. Built by `hpspec.build`."""
    import json

    return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(EXAMPLES_DIR.glob("*.json"))}
