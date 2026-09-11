#!/usr/bin/env python3
"""Deterministic Bossman application icon pipeline — standard library only.

One raster master (``icon-1024.png``) produces every shipped size and the
Windows ``.ico``. Nothing here depends on Pillow, because the check that the
committed assets still match the master has to run on every CI job and on the
owner's machine, not only where an image library happens to be installed.

Why premultiplied alpha: the tile has transparent corners, and transparent
pixels are stored as fully transparent BLACK. Averaging RGBA straight would
pull that black into every edge pixel and ring the icon with a dark halo —
the exact defect the cleaned artwork was made to remove. Averaging colour
weighted by coverage, then dividing the coverage back out, keeps the edge the
colour it actually is.

    python tools/app_icons.py --check    # committed assets still match master
    python tools/app_icons.py --write    # regenerate them from the master
"""
from __future__ import annotations

import argparse
import base64
import binascii
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ICONS = REPO / "command-center" / "ui" / "icons"
MASTER = "icon-1024.png"
# Sizes the product actually loads: browser/PWA, desktop window, launcher.
PNG_SIZES = (512, 256, 192, 128, 64, 48, 32, 16)
# Windows reads the closest entry it needs; 256 is the Explorer "extra large".
ICO_SIZES = (16, 32, 48, 64, 128, 256)
ICO_NAME = "bossman.ico"
# Браузер предпочитает SVG любому PNG в цепочке иконок, а до сих пор SVG были
# ОТДЕЛЬНЫМИ рисунками: у Command Center — своя плитка, у bossman-core/ui —
# зелёная буква «B». То есть фавикон и иконка приложения показывали разное.
# Теперь SVG — обёртка над тем же мастером, а не второй рисунок.
SVG_EMBED_SIZE = 256
SVG_TARGETS = (
    Path("command-center") / "ui" / "icon.svg",
    Path("bossman-core") / "ui" / "icon.svg",
)

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


# --------------------------------------------------------------------- decode

def _unfilter(raw: bytes, width: int, height: int, channels: int) -> bytearray:
    """Undo the per-scanline PNG filters (spec 9.2). Bytes, not pixels."""
    stride = width * channels
    out = bytearray(stride * height)
    previous = bytearray(stride)
    pos = 0
    for row in range(height):
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = previous[i]
                upper_left = previous[i - channels] if i >= channels else 0
                estimate = left + up - upper_left
                da, db, dc = (abs(estimate - left), abs(estimate - up),
                              abs(estimate - upper_left))
                nearest = left if (da <= db and da <= dc) else (up if db <= dc else upper_left)
                line[i] = (line[i] + nearest) & 0xFF
        elif filter_type != 0:
            raise ValueError(f"unsupported PNG filter {filter_type}")
        out[row * stride:(row + 1) * stride] = line
        previous = line
    return out


def decode_png(data: bytes) -> tuple[int, int, bytearray]:
    """Return ``(width, height, RGBA bytes)`` for an 8-bit non-interlaced PNG."""
    if data[:8] != _SIGNATURE:
        raise ValueError("not a PNG")
    width = height = depth = color = interlace = -1
    idat = bytearray()
    palette = b""
    transparency = b""
    pos = 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            transparency = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if depth != 8:
        raise ValueError(f"only 8-bit PNGs are supported, got depth {depth}")
    if interlace:
        raise ValueError("interlaced PNGs are not supported")
    if color not in _CHANNELS:
        raise ValueError(f"unsupported PNG colour type {color}")

    planes = _unfilter(zlib.decompress(bytes(idat)), width, height, _CHANNELS[color])
    rgba = bytearray(width * height * 4)
    if color == 6:
        rgba[:] = planes
    elif color == 2:
        for i in range(width * height):
            rgba[4 * i:4 * i + 3] = planes[3 * i:3 * i + 3]
            rgba[4 * i + 3] = 255
    elif color == 0:
        for i in range(width * height):
            grey = planes[i]
            rgba[4 * i:4 * i + 4] = bytes((grey, grey, grey, 255))
    elif color == 4:
        for i in range(width * height):
            grey, alpha = planes[2 * i], planes[2 * i + 1]
            rgba[4 * i:4 * i + 4] = bytes((grey, grey, grey, alpha))
    else:  # palette
        for i in range(width * height):
            index = planes[i]
            rgba[4 * i:4 * i + 3] = palette[3 * index:3 * index + 3]
            rgba[4 * i + 3] = transparency[index] if index < len(transparency) else 255
    return width, height, rgba


# --------------------------------------------------------------------- encode

def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """Write 8-bit RGBA with per-row adaptive filtering.

    Filter choice uses the standard minimum-sum-of-absolute-differences
    heuristic (PNG spec 12.8): a fixed rule, so the same pixels always pick the
    same filters. Storing rows unfiltered instead would roughly triple every
    shipped asset — a 512 tile went from ~150 KB to ~500 KB.
    """
    stride = width * 4
    raw = bytearray()
    previous = bytearray(stride)
    for row in range(height):
        line = rgba[row * stride:(row + 1) * stride]
        candidates = []
        for kind in range(5):
            out = bytearray(stride)
            for i in range(stride):
                left = line[i - 4] if i >= 4 else 0
                up = previous[i]
                upper_left = previous[i - 4] if i >= 4 else 0
                if kind == 0:
                    out[i] = line[i]
                elif kind == 1:
                    out[i] = (line[i] - left) & 0xFF
                elif kind == 2:
                    out[i] = (line[i] - up) & 0xFF
                elif kind == 3:
                    out[i] = (line[i] - ((left + up) >> 1)) & 0xFF
                else:
                    estimate = left + up - upper_left
                    da, db, dc = (abs(estimate - left), abs(estimate - up),
                                  abs(estimate - upper_left))
                    nearest = left if (da <= db and da <= dc) else (up if db <= dc else upper_left)
                    out[i] = (line[i] - nearest) & 0xFF
            # Signed magnitude: bytes near 0 or 255 are both cheap to deflate.
            cost = sum(b if b < 128 else 256 - b for b in out)
            candidates.append((cost, kind, out))
        _cost, kind, best = min(candidates, key=lambda c: (c[0], c[1]))
        raw.append(kind)
        raw += best
        previous = bytearray(line)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return b"".join((
        _SIGNATURE,
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
        chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
        chunk(b"IEND", b""),
    ))


# --------------------------------------------------------------------- resize

def resize_rgba(width: int, height: int, rgba: bytes, size: int) -> bytearray:
    """Area-average downscale over premultiplied alpha (no dark edge halo)."""
    out = bytearray(size * size * 4)
    x_edges = [round(i * width / size) for i in range(size + 1)]
    y_edges = [round(i * height / size) for i in range(size + 1)]
    for oy in range(size):
        y0, y1 = y_edges[oy], max(y_edges[oy + 1], y_edges[oy] + 1)
        for ox in range(size):
            x0, x1 = x_edges[ox], max(x_edges[ox + 1], x_edges[ox] + 1)
            r = g = b = a = 0
            count = 0
            for sy in range(y0, y1):
                base = (sy * width + x0) * 4
                for _ in range(x1 - x0):
                    alpha = rgba[base + 3]
                    r += rgba[base] * alpha
                    g += rgba[base + 1] * alpha
                    b += rgba[base + 2] * alpha
                    a += alpha
                    base += 4
                    count += 1
            index = (oy * size + ox) * 4
            if a:
                # Divide the coverage back out; round half up, deterministically.
                out[index] = min(255, (2 * r + a) // (2 * a))
                out[index + 1] = min(255, (2 * g + a) // (2 * a))
                out[index + 2] = min(255, (2 * b + a) // (2 * a))
                out[index + 3] = min(255, (2 * a + count) // (2 * count))
            # fully transparent stays transparent black
    return out


# ------------------------------------------------------------------------ ico

def _dib(size: int, rgba: bytes) -> bytes:
    """32-bit bottom-up BGRA DIB plus an empty AND mask."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         size * size * 4, 0, 0, 0, 0)
    rows = []
    for y in range(size - 1, -1, -1):
        row = bytearray()
        for x in range(size):
            i = (y * size + x) * 4
            row += bytes((rgba[i + 2], rgba[i + 1], rgba[i], rgba[i + 3]))
        rows.append(bytes(row))
    mask_stride = ((size + 31) // 32) * 4
    return header + b"".join(rows) + b"\x00" * (mask_stride * size)


def build_ico(master_w: int, master_h: int, master: bytes,
              sizes: tuple[int, ...] = ICO_SIZES) -> bytes:
    """Small entries as DIB (every Windows shell reads them), 256 as PNG."""
    entries, blobs = [], []
    offset = 6 + 16 * len(sizes)
    for size in sizes:
        scaled = resize_rgba(master_w, master_h, master, size)
        blob = encode_png(size, size, scaled) if size >= 256 else _dib(size, scaled)
        entries.append(struct.pack("<BBBBHHII", size & 0xFF, size & 0xFF, 0, 0, 1, 32,
                                   len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    return struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries) + b"".join(blobs)


# ------------------------------------------------------------------ compare

def ico_entries(blob: bytes) -> list[tuple[int, bytearray]]:
    """Parse an .ico into ``[(size, RGBA)]`` so assets compare by pixels."""
    reserved, kind, count = struct.unpack("<HHH", blob[:6])
    if reserved or kind != 1:
        raise ValueError("not an .ico")
    entries = []
    for i in range(count):
        width, _h, _c, _r, _p, _b, length, offset = struct.unpack(
            "<BBBBHHII", blob[6 + 16 * i:22 + 16 * i])
        size = width or 256
        body = blob[offset:offset + length]
        if body[:8] == _SIGNATURE:
            _w, _hh, rgba = decode_png(body)
        else:
            rgba = bytearray(size * size * 4)
            pixels = body[40:40 + size * size * 4]
            for y in range(size):
                src = (size - 1 - y) * size * 4
                for x in range(size):
                    j = src + x * 4
                    k = (y * size + x) * 4
                    rgba[k] = pixels[j + 2]
                    rgba[k + 1] = pixels[j + 1]
                    rgba[k + 2] = pixels[j]
                    rgba[k + 3] = pixels[j + 3]
        entries.append((size, rgba))
    return entries


def _svg_parts(blob: bytes) -> tuple[bytes, bytearray]:
    """Markup with the data URI removed, and the pixels that URI decodes to."""
    marker = b"base64,"
    start = blob.index(marker) + len(marker)
    end = blob.index(b'"', start)
    return blob[:start] + blob[end:], decode_png(base64.b64decode(blob[start:end]))[2]


def same_svg(committed: bytes, expected: bytes) -> bool:
    """Same markup and same drawn pixels.

    The SVG embeds a COMPRESSED PNG, so a byte comparison inherits the same
    zlib-build dependence as the rasters and fails on a different runner for a
    file that draws the identical image — which is exactly what happened on
    windows-latest. Compare what it draws, and require the markup around it to
    be untouched so this cannot become a hole.
    """
    try:
        committed_markup, committed_pixels = _svg_parts(committed)
    except (ValueError, IndexError, binascii.Error):
        return False
    expected_markup, expected_pixels = _svg_parts(expected)
    # .gitattributes keeps these files byte-identical, but an existing clone may
    # already hold a CRLF copy. Line endings are not a drawing.
    normalise = lambda markup: markup.replace(b"\r\n", b"\n")
    return (normalise(committed_markup) == normalise(expected_markup)
            and committed_pixels == expected_pixels)


def same_pixels(committed: bytes, expected: bytes, *, ico: bool) -> bool:
    """Compare what the asset DRAWS, not how zlib happened to pack it.

    Deflate output is not stable across zlib builds, so a byte comparison
    would fail on a different runner for a file that is pixel-for-pixel
    identical. Pixels are the contract; compression is an implementation
    detail of whoever ran --write.
    """
    if ico:
        return ico_entries(committed) == ico_entries(expected)
    return decode_png(committed)[:3] == decode_png(expected)[:3]


# ---------------------------------------------------------------------- build

def svg_wrapper(png: bytes) -> bytes:
    """An SVG that draws exactly the canonical raster and nothing of its own."""
    data = base64.b64encode(png).decode("ascii")
    size = SVG_EMBED_SIZE
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"'
        f' width="{size}" height="{size}" role="img" aria-label="BOSSMAN">\n'
        f'<title>BOSSMAN</title>\n'
        f'<image href="data:image/png;base64,{data}"'
        f' x="0" y="0" width="{size}" height="{size}"/>\n'
        f'</svg>\n'
    ).encode("utf-8")


def derive(master_bytes: bytes) -> dict[str, bytes]:
    """Every shipped asset, derived from the one master."""
    width, height, rgba = decode_png(master_bytes)
    if width != height:
        raise ValueError(f"master must be square, got {width}x{height}")
    assets = {name: encode_png(size, size, resize_rgba(width, height, rgba, size))
              for size, name in ((s, f"icon-{s}.png") for s in PNG_SIZES)}
    assets[ICO_NAME] = build_ico(width, height, rgba)
    return assets


def derive_svgs(assets: dict[str, bytes]) -> dict[Path, bytes]:
    """Relative path -> SVG bytes, all wrapping the same canonical raster."""
    wrapper = svg_wrapper(assets[f"icon-{SVG_EMBED_SIZE}.png"])
    return {target: wrapper for target in SVG_TARGETS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="regenerate assets from the master")
    group.add_argument("--check", action="store_true", help="verify committed assets")
    args = parser.parse_args(argv)

    master_path = ICONS / MASTER
    if not master_path.exists():
        print(f"APP_ICONS=FAIL missing master {master_path}", file=sys.stderr)
        return 2
    assets = derive(master_path.read_bytes())

    svgs = derive_svgs(assets)

    if args.write:
        for name, blob in assets.items():
            (ICONS / name).write_bytes(blob)
        for target, blob in svgs.items():
            (REPO / target).write_bytes(blob)
        print(f"APP_ICONS=WRITTEN master={MASTER} "
              f"assets={len(assets)} svg={len(svgs)}")
        return 0

    stale = []
    for name, blob in assets.items():
        path = ICONS / name
        if not path.exists():
            stale.append(f"{name}: missing")
        elif not same_pixels(path.read_bytes(), blob, ico=name.endswith(".ico")):
            stale.append(f"{name}: does not match the master")
    for target, blob in svgs.items():
        path = REPO / target
        if not path.exists():
            stale.append(f"{target.as_posix()}: missing")
        elif not same_svg(path.read_bytes(), blob):
            stale.append(f"{target.as_posix()}: is not the canonical master")
    if stale:
        print("APP_ICONS_CURRENT=FAIL", file=sys.stderr)
        for line in stale:
            print("  " + line, file=sys.stderr)
        print("  run: python tools/app_icons.py --write", file=sys.stderr)
        return 1
    print(f"APP_ICONS_CURRENT=PASS master={MASTER} assets={len(assets)} svg={len(svgs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
