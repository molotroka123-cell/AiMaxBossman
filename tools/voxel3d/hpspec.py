"""Part-based spec for high-poly props: the JSON a (local) model writes, validated and repaired here.

    {"name": "snake_case", "label": "Oak Tree", "height": 4.4, "target": 8000,
     "parts": [{"name": "trunk", "shape": "lathe", "profile": [[0.6, 0], [0.3, 0.5], [0.22, 2.7]],
                "color": "#7a5230", "surface": {"bumps": 0.05, "scale": 0.1, "ridged": true}}, ...]}

Units are metres, Y is up, x = width, z = depth (front = +z). 1 m = one game block. Shapes:
  ellipsoid  center [x,y,z], radii [rx,ry,rz]
  box        center, size [w,h,d], round (edge radius)
  lathe      profile [[radius, y], ...] revolved around a vertical axis through center (default
             [0,0,0]); sections 3..96 (4 = square, 6 = hexagonal, 32+ = round)
  tube       from [x,y,z], to [x,y,z], radius, radius_end (optional taper), sections
  torus      center, radius (ring), thickness (tube radius), axis [x,y,z] (ring normal)
  prism      base, radius, height, tip, sides, direction [x,y,z]   (crystals, spikes)
Per part (all optional): color "#rrggbb", color_top (vertical gradient over the part), moss
(tint on up-facing surfaces), variation 0..0.3, cavities 0..0.5 (dark noise patches), material
matte|gloss|glow, shading smooth|flat, detail high|low (high = dense and decimated to the budget,
low = kept as built: crisp hard-surface pieces), surface {bumps (m), scale (m), ridged, grooves
{axis x|y|z, every (m), depth (m)}}, mirror_x true (adds the mirrored copy), repeat {count, start_deg}
(copies rotated around the vertical axis x=z=0), scatter {on: part name, count, min_up, seed}
(copies placed on another part's surface, e.g. spots on a mushroom cap).
"""
from __future__ import annotations

import re
import zlib
from typing import Any

import numpy as np
import trimesh

from . import meshgen as mg

SHAPES = {"ellipsoid", "box", "lathe", "tube", "torus", "prism"}
MATERIALS = {"matte", "gloss", "glow"}
MAX_PARTS, MAX_EXPANDED = 40, 90
EXTENT = 5.0
TRI_MIN, TRI_MAX = 5000, 10000
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAMED = {"red": "#c0392b", "green": "#4e9a3a", "blue": "#2f6fd0", "brown": "#7a5230", "grey": "#8a8a8a",
          "gray": "#8a8a8a", "white": "#eeeeee", "black": "#222222", "yellow": "#f2c230", "orange": "#e67e22",
          "purple": "#8e44ad", "gold": "#e8b53a", "pink": "#f08cb0", "cyan": "#3fd0e8", "beige": "#e3cfa6"}


def _vec(v: Any, n: int = 3) -> list[float] | None:
    if isinstance(v, (int, float)):
        return [float(v)] * n
    if isinstance(v, (list, tuple)) and len(v) == n and all(isinstance(x, (int, float)) for x in v):
        return [float(x) for x in v]
    return None


def _color(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if s in _NAMED:
        return _NAMED[s]
    if re.fullmatch(r"#?[0-9a-f]{3}", s):
        s = s.lstrip("#")
        return "#" + "".join(c * 2 for c in s)
    if re.fullmatch(r"#?[0-9a-f]{6}", s):
        return "#" + s.lstrip("#")
    return None


def repair(spec: dict) -> tuple[dict, list[str]]:
    """Safe, logged repairs of common model slips. Never invents geometry."""
    fixes: list[str] = []
    spec = dict(spec)
    if not isinstance(spec.get("name"), str) or not spec["name"]:
        spec["name"] = "prop"
        fixes.append("name missing: used 'prop'")
    spec["name"] = re.sub(r"[^a-z0-9_]+", "_", spec["name"].lower()).strip("_") or "prop"
    spec.setdefault("label", spec["name"].replace("_", " ").title())
    try:
        spec["target"] = int(min(max(int(spec.get("target", 8000)), TRI_MIN + 500), TRI_MAX - 500))
    except (TypeError, ValueError):
        spec["target"] = 8000
    parts = spec.get("parts")
    if not isinstance(parts, list):
        return spec, fixes
    out = []
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            continue
        p = dict(p)
        if "type" in p and "shape" not in p:
            p["shape"] = p.pop("type")
            fixes.append(f"part {i}: 'type' renamed to 'shape'")
        if isinstance(p.get("shape"), str):
            p["shape"] = {"sphere": "ellipsoid", "cube": "box", "cylinder": "tube", "cone": "lathe_cone",
                          "crystal": "prism", "ring": "torus"}.get(p["shape"].lower(), p["shape"].lower())
        if p.get("shape") == "lathe_cone":  # cone given as base/radius/height
            base = _vec(p.get("base", p.get("center", [0, 0, 0]))) or [0.0, 0.0, 0.0]
            r, h = float(p.get("radius", 0.5)), float(p.get("height", 1.0))
            p.update(shape="lathe", center=[base[0], 0.0, base[2]], profile=[[r, base[1]], [0.0, base[1] + h]])
            fixes.append(f"part {i}: cone converted to a lathe")
        p.setdefault("name", f"part{i}")
        for key in ("color", "color_top", "moss"):
            if key in p:
                c = _color(p[key])
                if c is None:
                    fixes.append(f"part {p['name']}: bad {key} {p[key]!r} dropped")
                    p.pop(key)
                elif c != p[key]:
                    p[key] = c
        if p.get("material") not in MATERIALS:
            if "material" in p:
                fixes.append(f"part {p['name']}: unknown material {p['material']!r} -> matte")
            p["material"] = "matte"
        if p.get("shape") == "lathe" and isinstance(p.get("profile"), list):
            prof = [q for q in p["profile"] if _vec(q, 2)]
            prof = [[abs(float(q[0])), float(q[1])] for q in prof]
            if prof and prof[0][0] > 0:
                prof.insert(0, [0.0, prof[0][1]])
            if prof and prof[-1][0] > 0:
                prof.append([0.0, prof[-1][1]])
            if prof != p["profile"]:
                p["profile"] = prof
        for key in ("radius", "radius_end", "thickness", "height", "tip", "round"):
            if isinstance(p.get(key), (int, float)) and p[key] < 0:
                p[key] = abs(p[key])
                fixes.append(f"part {p['name']}: negative {key} made positive")
        out.append(p)
    spec["parts"] = out
    return spec, fixes


def validate(spec: dict) -> list[str]:
    errs: list[str] = []
    h = spec.get("height")
    if not isinstance(h, (int, float)) or not 0.3 <= h <= 8.0:
        errs.append("height must be a number of metres between 0.3 and 8 (one block = 1 m)")
    parts = spec.get("parts")
    if not isinstance(parts, list) or not parts:
        return errs + ["parts must be a non-empty list"]
    if len(parts) > MAX_PARTS:
        errs.append(f"too many parts ({len(parts)}); use at most {MAX_PARTS} (use repeat/mirror_x/scatter for copies)")
    names = [p.get("name") for p in parts]
    expanded = 0
    for p in parts:
        n, s = p.get("name"), p.get("shape")
        if s not in SHAPES:
            errs.append(f"part {n}: unknown shape {s!r}; use one of {sorted(SHAPES)}")
            continue
        need = {"ellipsoid": ("center", "radii"), "box": ("center", "size"), "lathe": ("profile",),
                "tube": ("from", "to", "radius"), "torus": ("center", "radius", "thickness"),
                "prism": ("base", "radius", "height")}[s]
        for k in need:
            if k not in p:
                errs.append(f"part {n} ({s}) needs '{k}'")
        for k in ("center", "from", "to", "base"):
            if k in p and _vec(p[k]) is None:
                errs.append(f"part {n}: '{k}' must be [x, y, z]")
            elif k in p and max(abs(x) for x in _vec(p[k])) > EXTENT:
                errs.append(f"part {n}: '{k}' is more than {EXTENT} m from the origin")
        if s == "ellipsoid" and _vec(p.get("radii")) is None:
            errs.append(f"part {n}: radii must be a number or [rx, ry, rz]")
        if s == "box" and _vec(p.get("size")) is None:
            errs.append(f"part {n}: size must be [w, h, d]")
        if s == "lathe":
            prof = p.get("profile")
            if not isinstance(prof, list) or len(prof) < 3:
                errs.append(f"part {n}: lathe profile needs at least one [radius, y] point")
        if s == "tube" and "from" in p and "to" in p and _vec(p["from"]) and _vec(p["to"]):
            if np.linalg.norm(np.subtract(p["to"], p["from"])) < 1e-3:
                errs.append(f"part {n}: tube 'from' and 'to' are the same point")
        if p.get("color") is None:
            errs.append(f"part {n}: needs a 'color' like '#7a5230'")
        sc = p.get("scatter")
        if sc is not None and (not isinstance(sc, dict) or sc.get("on") not in names):
            errs.append(f"part {n}: scatter.on must name another part")
        count = 1
        if isinstance(p.get("repeat"), dict):
            count *= max(1, min(int(p["repeat"].get("count", 1)), 16))
        if p.get("mirror_x"):
            count *= 2
        if isinstance(sc, dict):
            count *= max(1, min(int(sc.get("count", 1)), 24))
        expanded += count
    if expanded > MAX_EXPANDED:
        errs.append(f"{expanded} part copies after repeat/mirror/scatter; keep it under {MAX_EXPANDED}")
    return errs


# ---------------------------------------------------------------------------------- build

def _paint_of(p: dict, lo_y: float, hi_y: float) -> mg.Paint:
    scale = float((p.get("surface") or {}).get("scale", 0.3)) or 0.3
    return mg.paint(p.get("color", "#8a8a8a"), vary=float(p.get("variation", 0.08)), freq=1.0 / max(scale, 0.02),
                    seed=zlib.crc32(p["name"].encode()) % 997, top=p.get("color_top"), top_from=lo_y, top_to=hi_y,
                    up=p.get("moss"), up_amount=0.75 if p.get("moss") else 0.0,
                    dark=float(p.get("cavities", 0.0)), dark_freq=2.0 / max(scale, 0.02))


def _shape(p: dict, dense: int) -> trimesh.Trimesh:
    """dense: 0 = low detail, 1 = high detail, 2 = extra high."""
    s = p["shape"]
    hi = dense > 0
    if s == "ellipsoid":
        r = _vec(p["radii"])
        lvl = (2 if max(r) < 0.1 else 3) if not hi else (5 if max(r) > 0.45 else 4 if max(r) > 0.12 else 3)
        return mg.ellipsoid(_vec(p["center"]), r, level=min(lvl + max(dense - 1, 0), 6))
    if s == "box":
        size = _vec(p["size"])
        rnd = float(p.get("round", min(size) * 0.12))
        return mg.rounded_box(_vec(p["center"]), size, radius=min(rnd, min(size) * 0.49),
                              level=(4 + max(dense - 1, 0)) if hi else 2)
    if s == "lathe":
        c = _vec(p.get("center", [0, 0, 0]))
        sections = int(min(max(int(p.get("sections", 48 if hi else 16)), 3), 128))
        return mg.lathe([tuple(q) for q in p["profile"]], sections=sections, step=(0.04 / dense) if hi else 0, at=c)
    if s == "tube":
        return mg.tube(_vec(p["from"]), _vec(p["to"]), float(p["radius"]),
                       sections=int(min(max(int(p.get("sections", 16 if hi else 10)), 3), 96)),
                       r1=float(p["radius_end"]) if "radius_end" in p else None, ring_step=(0.04 / dense) if hi else 10.0)
    if s == "torus":
        return mg.torus(_vec(p["center"]), float(p["radius"]), float(p["thickness"]), axis=_vec(p.get("axis", [0, 1, 0])),
                        sections=(48, 14) if hi else (24, 8))
    return mg.prism(_vec(p["base"]), float(p["radius"]), float(p["height"]), float(p.get("tip", p["radius"] * 1.4)),
                    sides=int(min(max(int(p.get("sides", 6)), 3), 12)), direction=_vec(p.get("direction", [0, 1, 0])),
                    twist=float(p.get("twist", 0.0)))


def _surface(m: trimesh.Trimesh, p: dict, seed: int) -> trimesh.Trimesh:
    sf = p.get("surface") or {}
    g = sf.get("grooves")
    if isinstance(g, dict) and g.get("axis") in ("x", "y", "z"):
        m = mg.grooves(m, float(g.get("depth", 0.01)), float(g.get("every", 0.15)), "xyz".index(g["axis"]),
                       width=float(g.get("width", 0.1)))
    bumps = float(sf.get("bumps", 0.0))
    if bumps > 0:
        scale = float(sf.get("scale", 0.3)) or 0.3
        m = mg.displace(m, min(bumps, 0.4), 1.0 / max(scale, 0.02), octaves=int(sf.get("octaves", 4)), seed=seed,
                        ridged=bool(sf.get("ridged", False)))
    return m


def _copies(m: trimesh.Trimesh, p: dict, placed: dict[str, trimesh.Trimesh]) -> list[trimesh.Trimesh]:
    outs = [m]
    rep = p.get("repeat")
    if isinstance(rep, dict):
        n = max(1, min(int(rep.get("count", 1)), 16))
        start = np.radians(float(rep.get("start_deg", 0.0)))
        outs = []
        for k in range(n):
            c = m.copy()
            c.apply_transform(trimesh.transformations.rotation_matrix(start + 2 * np.pi * k / n, [0, 1, 0]))
            outs.append(c)
    if p.get("mirror_x"):
        mirrored = []
        for c in outs:
            d = c.copy()
            d.apply_transform(np.diag([-1.0, 1.0, 1.0, 1.0]))  # trimesh re-winds reflected faces itself
            mirrored.append(d)
        outs = outs + mirrored
    sc = p.get("scatter")
    if isinstance(sc, dict) and sc.get("on") in placed:
        host = placed[sc["on"]]
        n = max(1, min(int(sc.get("count", 8)), 24))
        rng = np.random.default_rng(int(sc.get("seed", 7)))
        pts, fidx = trimesh.sample.sample_surface_even(host, n * 12, seed=int(sc.get("seed", 7)))
        nrm = host.face_normals[fidx]
        ok = nrm[:, 1] >= float(sc.get("min_up", 0.2))
        pts, nrm = pts[ok], nrm[ok]
        centre = m.bounds.mean(axis=0)
        outs = []
        chosen: list[np.ndarray] = []
        min_gap = float(np.max(m.extents)) * 1.1
        perm = rng.permutation(len(pts))
        for q, nn in zip(pts[perm], nrm[perm]):
            if len(outs) >= n:
                break
            if any(np.linalg.norm(q - c) < min_gap for c in chosen):
                continue
            c = m.copy()
            c.apply_translation(-centre)
            c.apply_transform(trimesh.geometry.align_vectors([0, 1, 0], nn))
            c.apply_translation(q + nn * 0.005)
            outs.append(c)
            chosen.append(q)
    return outs


def to_model(spec: dict, dense: int = 1) -> mg.Model:
    model = mg.Model(spec["name"], spec.get("label", spec["name"]), float(spec["height"]), target=int(spec.get("target", 8000)))
    placed: dict[str, trimesh.Trimesh] = {}
    order = sorted(spec["parts"], key=lambda p: 1 if p.get("scatter") else 0)  # hosts first
    for idx, p in enumerate(order):
        hi = dense if p.get("detail", "high") != "low" else 0
        m = _shape(p, hi)
        m = _surface(m, p, seed=idx + 3)
        copies = _copies(m, p, placed)
        placed[p["name"]] = trimesh.util.concatenate(copies) if len(copies) > 1 else copies[0]
        for k, c in enumerate(copies):
            lo, hi_y = float(c.bounds[0][1]), float(c.bounds[1][1])
            mat = p.get("material", "matte")
            if mat == "glow":
                mat = "glow:" + p.get("color", "#ffffff")
            model.parts.append(mg.Part(f"{p['name']}{k if len(copies) > 1 else ''}", c, _paint_of(p, lo, hi_y),
                                       material=mat, shading=p.get("shading", "smooth"),
                                       decimate=p.get("detail", "high") != "low"))
    return model


def connectivity(model: mg.Model, gap: float = 0.04) -> list[str]:
    """Every part must touch the rest (bounding boxes within `gap`): no floating pieces."""
    boxes = [(p.name, p.mesh.bounds) for p in model.parts]
    n = len(boxes)
    adj = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i][1], boxes[j][1]
            if np.all(a[0] - gap <= b[1]) and np.all(b[0] - gap <= a[1]):
                adj[i].append(j)
                adj[j].append(i)
    seen, stack = {0}, [0]
    while stack:
        for j in adj[stack.pop()]:
            if j not in seen:
                seen.add(j)
                stack.append(j)
    return [f"part {boxes[i][0]} floats: it does not touch any other part" for i in range(n) if i not in seen][:8]


def build(spec: dict) -> tuple[bytes | None, dict, list[str]]:
    """spec -> (glb bytes, stats, errors). Raises density when the result is under the budget."""
    from . import hipoly

    errs = validate(spec)
    if errs:
        return None, {}, errs
    stats: dict = {}
    for dense in (1, 2, 3):
        model = to_model(spec, dense)
        errs = connectivity(model)
        if errs:
            return None, {}, errs
        glb, stats = hipoly.build_model(model)
        stats["density"] = dense
        if stats["triangles"] >= TRI_MIN:
            break
    if stats["triangles"] > TRI_MAX:
        return None, stats, [f"{stats['triangles']} triangles after decimation: too many 'detail: low' parts; "
                             "mark big organic parts as detail high"]
    return glb, stats, []
