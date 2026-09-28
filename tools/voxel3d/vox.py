"""MagicaVoxel .vox (version 150) writer — SIZE, XYZI and RGBA chunks inside MAIN.

Format: https://github.com/ephtracy/voxel-model/blob/master/MagicaVoxel-file-format-vox.txt
MagicaVoxel is right-handed Z-up; our grid is right-handed Y-up (glTF convention), so a cell
(x, y, z) is written as (x, Z-1-z, y). Palette entry k (1-based) is RGBA slot k.
Verification reads files back with py-vox-io (BSD) as an independent parser.
"""
from __future__ import annotations

import struct
from pathlib import Path

from .spec import Grid


def _chunk(tag: bytes, content: bytes, children: bytes = b"") -> bytes:
    return tag + struct.pack("<ii", len(content), len(children)) + content + children


def to_vox_coords(grid: Grid, c: tuple[int, int, int]) -> tuple[int, int, int]:
    x, y, z = c
    return x, grid.size[2] - 1 - z, y


def encode(grid: Grid) -> bytes:
    if len(grid.palette) > 255:
        raise ValueError("MagicaVoxel palettes hold at most 255 colors")
    X, Y, Z = grid.size
    size = _chunk(b"SIZE", struct.pack("<iii", X, Z, Y))
    body = bytearray(struct.pack("<i", len(grid.cells)))
    for c in sorted(grid.cells):
        vx, vy, vz = to_vox_coords(grid, c)
        body += struct.pack("<BBBB", vx, vy, vz, grid.cells[c])
    xyzi = _chunk(b"XYZI", bytes(body))
    rgba = bytearray()
    for i in range(256):
        if i < len(grid.palette):
            h = grid.palette[i][1].lstrip("#")
            rgba += bytes((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255))
        else:
            rgba += bytes((0, 0, 0, 255))
    main = _chunk(b"MAIN", b"", size + xyzi + _chunk(b"RGBA", bytes(rgba)))
    return b"VOX " + struct.pack("<i", 150) + main


def write(grid: Grid, path: Path) -> Path:
    path = Path(path)
    path.write_bytes(encode(grid))
    return path


def read_basic(path: Path) -> dict:
    """Tiny reader used by tests when py-vox-io is not installed: returns size, voxels, rgba."""
    data = Path(path).read_bytes()
    if data[:4] != b"VOX ":
        raise ValueError("not a .vox file")
    pos = 8 + 12  # header + MAIN chunk header
    out: dict = {"voxels": [], "rgba": []}
    while pos < len(data):
        tag = data[pos:pos + 4]
        n, m = struct.unpack_from("<ii", data, pos + 4)
        content = data[pos + 12:pos + 12 + n]
        if tag == b"SIZE":
            out["size"] = struct.unpack("<iii", content)
        elif tag == b"XYZI":
            count = struct.unpack_from("<i", content)[0]
            out["voxels"] = [struct.unpack_from("<BBBB", content, 4 + 4 * i) for i in range(count)]
        elif tag == b"RGBA":
            out["rgba"] = [tuple(content[4 * i:4 * i + 4]) for i in range(256)]
        pos += 12 + n + m
    return out
