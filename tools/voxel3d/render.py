"""Software preview: two isometric views of the exposed voxel faces (Pillow, painter's algorithm).

No GPU, no OpenGL: every visible unit face is projected and painted far-to-near with fixed
directional shading, so the PNG is deterministic for a given grid.
"""
from __future__ import annotations

import math
from pathlib import Path

from .spec import Grid

FACES = [  # (neighbour offset, corner offsets, shade)
    ((0, 1, 0), ((0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)), 1.00),
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)), 0.45),
    ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)), 0.80),
    ((-1, 0, 0), ((0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)), 0.70),
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)), 0.62),
    ((0, 0, -1), ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)), 0.88),
]


def _view(grid: Grid, azimuth_deg: float, size: int, elevation_deg: float = 30.0):
    from PIL import Image, ImageDraw

    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    X, Y, Z = grid.size
    cx, cz = X / 2, Z / 2
    # camera looks along -view; view points from object towards camera
    view = (math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el))
    right = (math.cos(az), 0.0, -math.sin(az))
    up = (-math.sin(az) * math.sin(el), math.cos(el), -math.cos(az) * math.sin(el))

    def proj(p):
        q = (p[0] - cx, p[1], p[2] - cz)
        return (sum(q[i] * right[i] for i in range(3)), sum(q[i] * up[i] for i in range(3)),
                sum(q[i] * view[i] for i in range(3)))

    polys = []
    cells = grid.cells
    for (x, y, z), k in cells.items():
        # orthographic view of equal grid cubes: ordering whole cubes by centre depth is a valid
        # painter's order (face-centre ordering is not: it lets far side faces cover near cubes)
        depth = proj((x + 0.5, y + 0.5, z + 0.5))[2]
        for off, corners, shade in FACES:
            if sum(off[i] * view[i] for i in range(3)) <= 0:
                continue  # back face
            if (x + off[0], y + off[1], z + off[2]) in cells:
                continue
            pts = [proj((x + c[0], y + c[1], z + c[2])) for c in corners]
            polys.append((depth, [(p[0], p[1]) for p in pts], grid.color(k), shade))
    if not polys:
        return Image.new("RGB", (size, size), (235, 238, 242))
    xs = [p[0] for _, pts, _, _ in polys for p in pts]
    ys = [p[1] for _, pts, _, _ in polys for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    scale = (size * 0.86) / span
    mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    img = Image.new("RGB", (size, size), (235, 238, 242))
    draw = ImageDraw.Draw(img)
    polys.sort(key=lambda t: t[0])  # far (small depth along view) first
    for _, pts, col, shade in polys:
        r, g, b = (int(col[i:i + 2], 16) for i in (1, 3, 5))
        fill = (int(r * shade), int(g * shade), int(b * shade))
        edge = (int(r * shade * 0.7), int(g * shade * 0.7), int(b * shade * 0.7))
        screen = [(size / 2 + (px - mx) * scale, size / 2 - (py - my) * scale) for px, py in pts]
        draw.polygon(screen, fill=fill, outline=edge)
    return img


def render_preview(grid: Grid, path: Path, size: int = 512) -> Path:
    from PIL import Image

    a = _view(grid, 35.0, size)
    b = _view(grid, 215.0, size)
    out = Image.new("RGB", (size * 2, size), (235, 238, 242))
    out.paste(a, (0, 0))
    out.paste(b, (size, 0))
    out.save(path)
    return Path(path)
