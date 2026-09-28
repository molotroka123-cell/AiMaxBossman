"""Voxel spec: the JSON a local model writes, its validator, and the deterministic builder.

Coordinates are integer voxel cells, x = width, y = UP, z = depth, all inclusive and
0-based inside `size` = [X, Y, Z] (each 1..32). The ground is y = 0.

    {"name": "oak_tree", "size": [9, 12, 9],
     "palette": {"trunk": "#6b4a2b", "leaves": "#3f8f3a"},
     "ops": [{"op": "box", "from": [4, 0, 4], "to": [4, 6, 4], "mat": "trunk"},
             {"op": "sphere", "center": [4, 8, 4], "radius": 3, "mat": "leaves"}]}

Ops (applied in order; later ops overwrite; mat "air" carves):
  box       from, to, mat [, hollow]          axis-aligned block (hollow = walls only)
  sphere    center, radius (n or [rx,ry,rz]), mat
  cylinder  base, radius, height, mat [, axis x|y|z]   base = centre of the first disc
  cone      base, radius, height, mat [, direction up|down]   y-axis, tapers to a tip
  line      from, to, mat                     3-D line of voxels
  voxels    at: [[x,y,z], ...], mat
  layer     y, rows: ["..tt..", ...], key: {"t": "trunk"}  row i = z i, char j = x j, "." = skip
  roof      from, to, mat [, style gable|pyramid, axis x|z]  stepped roof over the from..to footprint,
            first layer at from.y; gable ridge runs along `axis`
  mirror    axis x|z|y                        copy everything so far to the mirrored side

`validate(spec)` returns human-readable errors (empty = valid); the same messages are sent back
to the model on retry. `build(spec)` returns a Grid plus a list of deterministic repairs it made
(clipping, grounding, dropping tiny floating bits) so nothing is silently changed.
"""
from __future__ import annotations

import difflib
import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from . import MAX_DIM

MAX_OPS = 80
MAX_COLORS = 16
AIR = "air"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
OPS: dict[str, dict[str, list[str]]] = {
    "box": {"required": ["from", "to", "mat"], "optional": ["hollow"]},
    "sphere": {"required": ["center", "radius", "mat"], "optional": []},
    "cylinder": {"required": ["base", "radius", "height", "mat"], "optional": ["axis"]},
    "cone": {"required": ["base", "radius", "height", "mat"], "optional": ["direction"]},
    "line": {"required": ["from", "to", "mat"], "optional": []},
    "voxels": {"required": ["at", "mat"], "optional": []},
    "layer": {"required": ["y", "rows", "key"], "optional": []},
    "roof": {"required": ["from", "to", "mat"], "optional": ["style", "axis"]},
    "mirror": {"required": ["axis"], "optional": []},
}
MAX_VOXELS_PER_OP = 24
COMMON = {"op", "note", "comment", "part"}
FLOATER_MAX_SHARE = 0.05  # disconnected bits up to 5 % of the object are dropped by repair

Cell = tuple[int, int, int]


@dataclass
class Grid:
    size: tuple[int, int, int]
    palette: list[tuple[str, str]]  # [(name, "#rrggbb")], voxel value k -> palette[k - 1]
    cells: dict[Cell, int] = field(default_factory=dict)

    def inside(self, c: Cell) -> bool:
        return all(0 <= c[i] < self.size[i] for i in range(3))

    def color(self, k: int) -> str:
        return self.palette[k - 1][1]

    def bbox(self) -> tuple[Cell, Cell] | None:
        if not self.cells:
            return None
        xs, ys, zs = zip(*self.cells)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def exposed_faces(self) -> int:
        n = 0
        for (x, y, z) in self.cells:
            for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                if (x + d[0], y + d[1], z + d[2]) not in self.cells:
                    n += 1
        return n


def components(cells) -> list[set[Cell]]:
    """6-connected components (face contact = physically attached block), largest first."""
    todo, out = set(cells), []
    while todo:
        seed = todo.pop()
        comp, queue = {seed}, deque([seed])
        while queue:
            x, y, z = queue.popleft()
            for n in ((x + 1, y, z), (x - 1, y, z), (x, y + 1, z), (x, y - 1, z), (x, y, z + 1), (x, y, z - 1)):
                if n in todo:
                    todo.remove(n)
                    comp.add(n)
                    queue.append(n)
        out.append(comp)
    out.sort(key=lambda c: (-len(c), min(c)))
    return out


# ---------------------------------------------------------------- validation

def normalize_color(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    m = HEX_RE.match(value.strip())
    if not m:
        return None
    h = m.group(1).lower()
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return "#" + h


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) or (isinstance(v, float) and v.is_integer())


def _vec(errors: list[str], where: str, key: str, v: Any) -> bool:
    if not (isinstance(v, list) and len(v) == 3 and all(_is_int(a) for a in v)):
        errors.append(f"{where}: '{key}' must be [x, y, z] integers")
        return False
    return True


def _num(errors: list[str], where: str, key: str, v: Any, lo: float, hi: float) -> bool:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not (lo <= v <= hi):
        errors.append(f"{where}: '{key}' must be a number in [{lo}, {hi}]")
        return False
    return True


def validate(spec: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]
    size = spec.get("size")
    if not (isinstance(size, list) and len(size) == 3 and all(_is_int(a) and 1 <= a <= MAX_DIM for a in size)):
        errors.append(f"size: must be [X, Y, Z] integers, each 1..{MAX_DIM} (Y is height)")
        size = [MAX_DIM] * 3
    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name: non-empty string required (e.g. 'oak_tree')")
    palette = spec.get("palette")
    names: set[str] = set()
    if not isinstance(palette, dict) or not palette:
        errors.append("palette: object {material_name: '#rrggbb'} with 1..16 entries required")
    else:
        if len(palette) > MAX_COLORS:
            errors.append(f"palette: at most {MAX_COLORS} materials, got {len(palette)}")
        for k, v in palette.items():
            if not NAME_RE.match(str(k)) or k == AIR:
                errors.append(f"palette: material name {k!r} must be lowercase letters/digits/_ and not 'air'")
            if normalize_color(v) is None:
                errors.append(f"palette.{k}: color {v!r} must be '#rrggbb'")
            names.add(k)
    ops = spec.get("ops")
    if not isinstance(ops, list) or not ops:
        errors.append("ops: non-empty list of shape operations required")
        return errors
    if len(ops) > MAX_OPS:
        errors.append(f"ops: at most {MAX_OPS} operations, got {len(ops)}")
    for n, op in enumerate(ops):
        where = f"ops[{n}]"
        if not isinstance(op, dict):
            errors.append(f"{where}: object required")
            continue
        kind = op.get("op")
        if kind not in OPS:
            errors.append(f"{where}: unknown op {kind!r}; use one of {sorted(OPS)}")
            continue
        where = f"ops[{n}] ({kind})"
        for key in OPS[kind]["required"]:
            if key not in op:
                errors.append(f"{where}: '{key}' is required")
        allowed = set(OPS[kind]["required"]) | set(OPS[kind]["optional"]) | COMMON
        for key in op:
            if key not in allowed:
                errors.append(f"{where}: unknown field '{key}'")
        mat = op.get("mat")
        if "mat" in OPS[kind]["required"] and "mat" in op and mat != AIR and mat not in names:
            errors.append(f"{where}: mat {mat!r} is not in the palette (or 'air' to carve)")
        big = 2 * MAX_DIM
        if kind in ("box", "line", "roof"):
            for key in ("from", "to"):
                if key in op:
                    _vec(errors, where, key, op[key])
        elif kind == "sphere":
            if "center" in op:
                _vec(errors, where, "center", op["center"])
            r = op.get("radius")
            if isinstance(r, list):
                if not (len(r) == 3 and all(isinstance(a, (int, float)) and 0.5 <= a <= MAX_DIM for a in r)):
                    errors.append(f"{where}: 'radius' list must be [rx, ry, rz], each 0.5..{MAX_DIM}")
            elif "radius" in op:
                _num(errors, where, "radius", r, 0.5, MAX_DIM)
        if kind == "roof":
            if op.get("style", "gable") not in ("gable", "pyramid"):
                errors.append(f"{where}: 'style' must be gable or pyramid")
            if op.get("axis", "x") not in ("x", "z"):
                errors.append(f"{where}: 'axis' must be x or z (direction of the ridge)")
        elif kind in ("cylinder", "cone"):
            if "base" in op:
                _vec(errors, where, "base", op["base"])
            if "radius" in op:
                _num(errors, where, "radius", op["radius"], 0, MAX_DIM)
            if "height" in op:
                _num(errors, where, "height", op["height"], 1, big)
            if kind == "cylinder" and op.get("axis", "y") not in ("x", "y", "z"):
                errors.append(f"{where}: 'axis' must be x, y or z")
            if kind == "cone" and op.get("direction", "up") not in ("up", "down"):
                errors.append(f"{where}: 'direction' must be up or down")
        elif kind == "voxels":
            at = op.get("at")
            if not isinstance(at, list) or not at:
                errors.append(f"{where}: 'at' must be a non-empty list of [x, y, z]")
            elif len(at) > MAX_VOXELS_PER_OP:
                errors.append(f"{where}: {len(at)} cells; at most {MAX_VOXELS_PER_OP} per voxels op - "
                              "use box, line, layer or mirror for runs and walls")
            else:
                for i, c in enumerate(at[:50]):
                    if not _vec(errors, f"{where}.at[{i}]", "cell", c):
                        break
        elif kind == "layer":
            if "y" in op and not (_is_int(op["y"])):
                errors.append(f"{where}: 'y' must be an integer")
            rows, key = op.get("rows"), op.get("key")
            if not isinstance(rows, list) or not rows or not all(isinstance(r, str) for r in rows):
                errors.append(f"{where}: 'rows' must be a list of strings (row i = z i, char j = x j)")
            if not isinstance(key, dict) or not key:
                errors.append(f"{where}: 'key' must map single characters to materials")
            else:
                for ch, m in key.items():
                    if len(ch) != 1 or ch == ".":
                        errors.append(f"{where}: key {ch!r} must be one character other than '.'")
                    if m != AIR and m not in names:
                        errors.append(f"{where}: key {ch!r} -> {m!r} is not in the palette")
                if isinstance(rows, list):
                    used = {ch for r in rows if isinstance(r, str) for ch in r} - {".", " "}
                    missing = sorted(used - set(key))
                    if missing:
                        errors.append(f"{where}: characters {missing} are used in rows but missing from 'key'")
        elif kind == "mirror":
            if op.get("axis") not in ("x", "y", "z"):
                errors.append(f"{where}: 'axis' must be x, y or z")
    if not errors:
        grid, fixes, build_errors = build(spec)
        errors.extend(build_errors)
    return errors


# ---------------------------------------------------------------- building

def _ints(v) -> Cell:
    return int(round(v[0])), int(round(v[1])), int(round(v[2]))


def _line(a: Cell, b: Cell) -> list[Cell]:
    n = max(abs(b[i] - a[i]) for i in range(3))
    if n == 0:
        return [a]
    return [tuple(int(round(a[i] + (b[i] - a[i]) * t / n)) for i in range(3)) for t in range(n + 1)]  # type: ignore[misc]


def _op_cells(op: dict, size: tuple[int, int, int]) -> list[tuple[Cell, str]]:
    kind, mat = op["op"], op.get("mat")
    out: list[tuple[Cell, str]] = []
    if kind == "box":
        a, b = _ints(op["from"]), _ints(op["to"])
        lo = [min(a[i], b[i]) for i in range(3)]
        hi = [max(a[i], b[i]) for i in range(3)]
        hollow = bool(op.get("hollow"))
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    if hollow and lo[0] < x < hi[0] and lo[1] < y < hi[1] and lo[2] < z < hi[2]:
                        continue
                    out.append(((x, y, z), mat))
    elif kind == "sphere":
        c = _ints(op["center"])
        r = op["radius"]
        rx, ry, rz = (float(a) for a in r) if isinstance(r, list) else (float(r),) * 3
        for x in range(c[0] - math.ceil(rx), c[0] + math.ceil(rx) + 1):
            for y in range(c[1] - math.ceil(ry), c[1] + math.ceil(ry) + 1):
                for z in range(c[2] - math.ceil(rz), c[2] + math.ceil(rz) + 1):
                    d = ((x - c[0]) / (rx + 0.5)) ** 2 + ((y - c[1]) / (ry + 0.5)) ** 2 + ((z - c[2]) / (rz + 0.5)) ** 2
                    if d <= 1.0:
                        out.append(((x, y, z), mat))
    elif kind == "cylinder":
        b = _ints(op["base"])
        r, h = float(op["radius"]), int(round(op["height"]))
        axis = "xyz".index(op.get("axis", "y"))
        u, v = [i for i in range(3) if i != axis]
        ri = math.ceil(r)
        for t in range(h):
            for du in range(-ri, ri + 1):
                for dv in range(-ri, ri + 1):
                    if du * du + dv * dv <= (r + 0.5) ** 2:
                        c = list(b)
                        c[axis] += t
                        c[u] += du
                        c[v] += dv
                        out.append((tuple(c), mat))  # type: ignore[arg-type]
    elif kind == "cone":
        b = _ints(op["base"])
        r, h = float(op["radius"]), int(round(op["height"]))
        step = -1 if op.get("direction", "up") == "down" else 1
        for t in range(h):
            rt = r * (1 - t / h) if h > 1 else r
            ri = math.ceil(rt)
            for dx in range(-ri, ri + 1):
                for dz in range(-ri, ri + 1):
                    if dx * dx + dz * dz <= (rt + 0.5) ** 2:
                        out.append(((b[0] + dx, b[1] + step * t, b[2] + dz), mat))
    elif kind == "line":
        out = [(c, mat) for c in _line(_ints(op["from"]), _ints(op["to"]))]
    elif kind == "voxels":
        out = [(_ints(c), mat) for c in op["at"]]
    elif kind == "roof":
        a, b = _ints(op["from"]), _ints(op["to"])
        x0, x1 = sorted((a[0], b[0]))
        z0, z1 = sorted((a[2], b[2]))
        pyramid, along_x = op.get("style", "gable") == "pyramid", op.get("axis", "x") == "x"
        k = 0
        while True:
            lx, hx = (x0 + k, x1 - k) if pyramid or not along_x else (x0, x1)
            lz, hz = (z0 + k, z1 - k) if pyramid or along_x else (z0, z1)
            if lx > hx or lz > hz or k > MAX_DIM:
                break
            out += [((x, a[1] + k, z), mat) for x in range(lx, hx + 1) for z in range(lz, hz + 1)]
            k += 1
    elif kind == "layer":
        y, key = int(op["y"]), op["key"]
        for z, row in enumerate(op["rows"]):
            for x, ch in enumerate(row):
                if ch in key:
                    out.append(((x, y, z), key[ch]))
    return out


def _fit(spec: dict, size: tuple[int, int, int], fixes: list[str]) -> tuple[tuple[int, int, int], list[int]]:
    """Grow the grid and/or shift the object when ops reach past the declared size, as long as the
    object still fits in MAX_DIM. The model's `size` is a hint; its shapes are the intent.
    Axes that are mirrored keep their size (the mirror plane depends on it)."""
    raw = [c for op in spec["ops"] if op["op"] != "mirror" for c, m in _op_cells(op, size) if m != AIR]
    if not raw:
        return size, [0, 0, 0]
    mirrored = {"xyz".index(op["axis"]) for op in spec["ops"] if op["op"] == "mirror"}
    new, shift = list(size), [0, 0, 0]
    for i in range(3):
        if i in mirrored:
            continue
        lo, hi = min(c[i] for c in raw), max(c[i] for c in raw)
        if hi - lo + 1 > MAX_DIM:
            continue  # cannot fit: clipping below reports it
        if lo < 0:
            shift[i] = -lo
        new[i] = max(new[i], hi + shift[i] + 1)
    if new != list(size) or any(shift):
        fixes.append(f"grid fitted to the shapes: size {list(size)} -> {new}" + (f", shifted by {shift}" if any(shift) else ""))
    return tuple(new), shift  # type: ignore[return-value]


def lint(grid: Grid) -> list[str]:
    """Soft, heuristic quality hints for the model (never block a valid object on their own)."""
    bb = grid.bbox()
    if not bb:
        return []
    vol = 1
    for i in range(3):
        vol *= bb[1][i] - bb[0][i] + 1
    fill = len(grid.cells) / vol
    if vol >= 150 and fill >= 0.95:
        return [f"the object is an almost completely solid block ({fill:.0%} of its bounding box is filled). "
                "If the real object has open space (walkway, rooms, gaps between railings, legs, windows) "
                "carve it with mat 'air' or build only the parts; if it really is a solid block, reply with the same JSON"]
    return []


def build(spec: dict) -> tuple[Grid, list[str], list[str]]:
    """Deterministic spec -> Grid. Returns (grid, repairs made, errors the model must fix).

    Assumes the structural checks in validate() passed (call validate() or use generate)."""
    fixes: list[str] = []
    size, shift = _fit(spec, tuple(int(a) for a in spec["size"]), fixes)  # type: ignore[arg-type]
    palette_items = [(k, normalize_color(v) or "#808080") for k, v in spec["palette"].items()]
    index = {k: i + 1 for i, (k, _) in enumerate(palette_items)}
    grid = Grid(size=size, palette=palette_items)  # type: ignore[arg-type]
    errors: list[str] = []
    for n, op in enumerate(spec["ops"]):
        if op["op"] == "mirror":
            axis = "xyz".index(op["axis"])
            for c, k in list(grid.cells.items()):
                m = list(c)
                m[axis] = size[axis] - 1 - c[axis]
                grid.cells[tuple(m)] = k  # type: ignore[index]
            continue
        cells = _op_cells(op, size)
        clipped = 0
        for c, mat in cells:
            c = (c[0] + shift[0], c[1] + shift[1], c[2] + shift[2])
            if not grid.inside(c):
                clipped += 1
                continue
            if mat == AIR:
                grid.cells.pop(c, None)
            else:
                grid.cells[c] = index[mat]
        if cells and clipped == len(cells):
            errors.append(f"ops[{n}] ({op['op']}): entirely outside size {list(size)}; move it inside")
        elif clipped:
            fixes.append(f"ops[{n}] ({op['op']}): clipped {clipped} of {len(cells)} voxels outside the grid")
    if not grid.cells:
        errors.append("the object is empty: no voxels remain inside the grid")
        return grid, fixes, errors
    # ground the object: its lowest voxel must sit on y = 0
    lo_y = min(c[1] for c in grid.cells)
    if lo_y > 0 and not spec.get("allow_floating"):
        grid.cells = {(x, y - lo_y, z): k for (x, y, z), k in grid.cells.items()}
        fixes.append(f"moved the object down by {lo_y} so it stands on y=0")
    comps = components(grid.cells)
    if len(comps) > 1 and not spec.get("allow_floating"):
        total = len(grid.cells)
        loose = comps[1:]
        if sum(len(c) for c in loose) <= FLOATER_MAX_SHARE * total:
            for comp in loose:
                for c in comp:
                    del grid.cells[c]
            fixes.append(f"dropped {len(loose)} floating piece(s), {sum(len(c) for c in loose)} voxels, not attached to the main body")
        else:
            desc = ", ".join(f"{len(c)} voxels near {sorted(c)[0]}" for c in loose[:4])
            errors.append(f"the object falls apart into {len(comps)} disconnected parts ({desc}); "
                          "make every part touch the main body face-to-face (or set allow_floating: true)")
    return grid, fixes, errors


def check_grid(grid: Grid, allow_floating: bool = False) -> dict[str, Any]:
    """Deterministic post-build checks shared by the CLI verifier and the tests."""
    bb = grid.bbox()
    comps = components(grid.cells)
    return {
        "voxels": len(grid.cells),
        "non_empty": bool(grid.cells),
        "within_bounds": all(grid.inside(c) for c in grid.cells) and all(1 <= s <= MAX_DIM for s in grid.size),
        "grounded": bool(bb) and bb[0][1] == 0,
        "components": len(comps),
        "connected": len(comps) == 1 or allow_floating,
        "colors_used": len(set(grid.cells.values())),
        "exposed_faces": grid.exposed_faces(),
        "bbox": [list(bb[0]), list(bb[1])] if bb else None,
    }


def closest_material(name: str, names: list[str]) -> str | None:
    hit = difflib.get_close_matches(name, names, n=1, cutoff=0.5)
    return hit[0] if hit else None


def repair_spec(spec: dict) -> tuple[dict, list[str]]:
    """Cheap, safe repairs of common model slips before validation (logged, never silent)."""
    fixes: list[str] = []
    if not isinstance(spec, dict):
        return spec, fixes
    spec = dict(spec)
    size = spec.get("size")
    if isinstance(size, list) and len(size) == 3 and all(isinstance(a, (int, float)) for a in size):
        clamped = [max(1, min(MAX_DIM, int(round(a)))) for a in size]
        if clamped != size:
            fixes.append(f"size {size} clamped to {clamped}")
        spec["size"] = clamped
    pal = spec.get("palette")
    if isinstance(pal, list):  # [{"name":..,"color":..}] -> dict
        try:
            spec["palette"] = pal = {str(p["name"]).lower(): p["color"] for p in pal}
            fixes.append("palette list converted to {name: color}")
        except (KeyError, TypeError):
            pass
    if isinstance(pal, dict):
        new = {}
        for k, v in pal.items():
            nk = re.sub(r"[^a-z0-9_]", "_", str(k).strip().lower()) or "mat"
            if not nk[0].isalpha():
                nk = "m_" + nk
            col = normalize_color(v) if isinstance(v, str) else None
            if col is None:
                col = "#808080"
                fixes.append(f"palette.{k}: invalid color {v!r} replaced by #808080")
            if nk != k:
                fixes.append(f"palette name {k!r} -> {nk!r}")
            new[nk] = col
        spec["palette"] = pal = new
    ops = spec.get("ops")
    if isinstance(ops, list) and isinstance(pal, dict) and pal:
        names = list(pal)
        out = []
        for n, op in enumerate(ops):
            if not isinstance(op, dict):
                out.append(op)
                continue
            op = dict(op)
            if "type" in op and "op" not in op:
                op["op"] = op.pop("type")
            m = op.get("mat") or op.get("material")
            if "material" in op:
                op.pop("material")
                op["mat"] = m
            if isinstance(m, str):
                lm = m.strip().lower()
                if lm in ("air", "none", "empty"):
                    lm = AIR
                elif lm not in pal:
                    hit = closest_material(lm, names)
                    if hit:
                        fixes.append(f"ops[{n}]: unknown mat {m!r} -> {hit!r}")
                        lm = hit
                op["mat"] = lm
            out.append(op)
        spec["ops"] = out
    return spec, fixes
