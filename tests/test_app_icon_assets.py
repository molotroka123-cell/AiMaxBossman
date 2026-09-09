"""The shipped Bossman icon must be ONE artwork, derived from ONE master.

Standard library only, on purpose: this runs on every CI job and on the owner's
machine, not just where Pillow happens to be installed.

What went wrong before, and what each check now holds onto:

* the 1024 master was lost from this line and the derived assets were replaced
  by a crude generated logo — so the master is required to exist and every
  derived asset must still be reproducible from it;
* the favicon chain preferred a hand-drawn ``icon.svg`` that was a different
  drawing from the app icon, and ``bossman-core/ui`` shipped a green letter "B"
  — so both SVG surfaces must be wrappers around the canonical raster;
* naive downscaling of an icon with transparent corners rings it with a dark
  halo — so corners must stay fully transparent and the edge must not go black.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import app_icons  # noqa: E402

ICONS = REPO / "command-center" / "ui" / "icons"


@pytest.fixture(scope="module")
def master() -> bytes:
    path = ICONS / app_icons.MASTER
    assert path.exists(), f"canonical raster master is missing: {path}"
    return path.read_bytes()


@pytest.fixture(scope="module")
def derived(master: bytes) -> dict[str, bytes]:
    return app_icons.derive(master)


def test_the_master_is_a_square_rgba_raster(master: bytes) -> None:
    width, height, rgba = app_icons.decode_png(master)
    assert width == height == 1024
    assert len(rgba) == width * height * 4
    assert any(rgba[3::4][i] != 255 for i in range(0, width * height, 97)), (
        "master has no transparency at all — it is not the cut-out tile"
    )


@pytest.mark.parametrize("size", app_icons.PNG_SIZES)
def test_every_shipped_size_exists_and_matches_the_master(
    size: int, derived: dict[str, bytes]
) -> None:
    name = f"icon-{size}.png"
    path = ICONS / name
    assert path.exists(), f"{name} is not shipped"
    committed = path.read_bytes()
    width, height, rgba = app_icons.decode_png(committed)
    assert (width, height) == (size, size)
    # Pixels, not bytes: deflate output differs between zlib builds, but what
    # the asset draws must not.
    assert app_icons.decode_png(committed)[2] == app_icons.decode_png(derived[name])[2], (
        f"{name} was not produced from {app_icons.MASTER}; "
        "run python tools/app_icons.py --write"
    )


@pytest.mark.parametrize("size", app_icons.PNG_SIZES)
def test_corners_stay_transparent_and_the_centre_stays_visible(size: int) -> None:
    width, height, rgba = app_icons.decode_png((ICONS / f"icon-{size}.png").read_bytes())

    def alpha(x: int, y: int) -> int:
        return rgba[(y * width + x) * 4 + 3]

    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        assert alpha(x, y) == 0, f"icon-{size}: corner ({x},{y}) is not transparent"
    assert alpha(width // 2, height // 2) == 255, f"icon-{size}: centre is not opaque"


@pytest.mark.parametrize("size", [s for s in app_icons.PNG_SIZES if s >= 48])
def test_the_edge_has_no_dark_halo(size: int) -> None:
    """Premultiplied resampling, or a black ring around a coloured tile."""
    width, height, rgba = app_icons.decode_png((ICONS / f"icon-{size}.png").read_bytes())
    row = height // 2
    lit = [x for x in range(width) if rgba[(row * width + x) * 4 + 3] > 24]
    assert lit, f"icon-{size}: middle row is empty"
    for x in (lit[0], lit[-1]):
        i = (row * width + x) * 4
        r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
        # Un-premultiply back to the colour a compositor will actually show.
        assert max(r, g, b) * 255 // max(a, 1) > 40, (
            f"icon-{size}: edge pixel x={x} is near-black (rgba={r},{g},{b},{a}) — "
            "transparent black was averaged into the edge"
        )


def test_the_windows_ico_carries_every_size_windows_asks_for(derived: dict[str, bytes]) -> None:
    blob = (ICONS / app_icons.ICO_NAME).read_bytes()
    entries = app_icons.ico_entries(blob)
    assert sorted(size for size, _ in entries) == sorted(app_icons.ICO_SIZES)
    assert entries == app_icons.ico_entries(derived[app_icons.ICO_NAME]), (
        "bossman.ico was not produced from the master"
    )
    for size, rgba in entries:
        assert rgba[3] == 0, f".ico {size}: top-left corner is not transparent"
        centre = ((size // 2) * size + size // 2) * 4
        assert rgba[centre + 3] == 255, f".ico {size}: centre is not opaque"


def test_both_svg_surfaces_wrap_the_same_canonical_raster(derived: dict[str, bytes]) -> None:
    expected = app_icons.derive_svgs(derived)
    for target, blob in expected.items():
        path = REPO / target
        assert path.exists(), f"{target.as_posix()} is missing"
        assert app_icons.same_svg(path.read_bytes(), blob), (
            f"{target.as_posix()} is a second, different drawing; "
            "run python tools/app_icons.py --write"
        )


def test_every_icon_the_product_references_actually_exists() -> None:
    """A manifest entry pointing at a missing file is a broken icon, silently."""
    manifest_path = REPO / "command-center" / "ui" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["icons"]:
        assert (manifest_path.parent / entry["src"]).exists(), entry["src"]
        # maskable crops to a circle-ish safe zone; this tile is already a
        # rounded square with transparent corners and would be cut twice.
        assert entry.get("purpose") != "maskable", entry["src"]

    html = (REPO / "command-center" / "ui" / "index.html").read_text(encoding="utf-8")
    for line in html.splitlines():
        if "rel=\"icon\"" in line or "apple-touch-icon" in line:
            href = line.split('href="', 1)[1].split('"', 1)[0]
            assert (REPO / "command-center" / "ui" / href).exists(), href


def test_the_desktop_launcher_uses_the_canonical_assets() -> None:
    source = (REPO / "command-center" / "bcc" / "desktop_install.py").read_text(encoding="utf-8")
    assert 'ICON_WINDOWS = ICON_DIR / "bossman.ico"' in source
    assert 'ICON_PNG = ICON_DIR / "icon-512.png"' in source
    assert (ICONS / "bossman.ico").exists() and (ICONS / "icon-512.png").exists()


def test_the_svg_check_tolerates_repacking_but_not_a_different_drawing(
    derived: dict[str, bytes]
) -> None:
    """The looser SVG comparison must stay a comparison.

    windows-latest rejected both SVG surfaces for a file that drew the identical
    image, because deflate packs differently there. Accepting a repack is
    correct; accepting a different picture, or edited markup, would turn the
    check into decoration.
    """
    import zlib
    canonical = app_icons.derive_svgs(derived)[app_icons.SVG_TARGETS[0]]
    width, height, rgba = app_icons.decode_png(derived["icon-256.png"])

    original_compress = zlib.compress
    try:  # same pixels, deliberately different compression
        zlib.compress = lambda data, level=6: original_compress(data, 1)
        repacked = app_icons.svg_wrapper(app_icons.encode_png(width, height, rgba))
    finally:
        zlib.compress = original_compress
    assert repacked != canonical, "the repack must really differ, or this proves nothing"
    assert app_icons.same_svg(repacked, canonical)

    assert not app_icons.same_svg(app_icons.svg_wrapper(derived["icon-128.png"]), canonical)
    assert not app_icons.same_svg(
        canonical.replace(b'aria-label="BOSSMAN"', b'aria-label="OTHER"'), canonical)
    assert not app_icons.same_svg(b"<svg/>", canonical)
