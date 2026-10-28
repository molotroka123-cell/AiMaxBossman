"""Motion render: a scene becomes a real video whose frames follow the keyframes (synthetic only, no owner media)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bcc.art_v2 import motion_render as mr
from bcc.art_v2 import motion_timeline as mt
from bcc.features import music_studio as music

FFMPEG = music._find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required")
Image = pytest.importorskip("PIL.Image")


def _scene(**extra):
    s = {"fps": 10, "duration_ms": 1000, "width": 320, "height": 180, "background": "#000000",
         "layers": [{"id": "box", "kind": "kinetic_text", "text": "MM", "color": "#ffffff", "size": 60,
                     "props": {"x": [{"t": 0, "v": 0.2}, {"t": 900, "v": 0.8}], "y": [{"t": 0, "v": 0.5}]}}]}
    s.update(extra)
    return s


def _frame_centroid_x(video: Path, idx: int, tmp: Path) -> float:
    png = tmp / f"f{idx}.png"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf", f"select=eq(n\\,{idx})",
                    "-vframes", "1", str(png)], check=True, capture_output=True)
    im = Image.open(png).convert("L")
    xs = [x for x in range(im.width) for y in range(im.height) if im.getpixel((x, y)) > 128]
    assert xs, "frame is black: nothing was drawn"
    return sum(xs) / len(xs) / im.width


def test_rendered_frames_follow_the_keyframes(tmp_path: Path):
    res = mr.render_scene(_scene(), tmp_path / "o.mp4", tmp_path, FFMPEG)
    assert res["frames"] == 10
    first, last = _frame_centroid_x(tmp_path / "o.mp4", 0, tmp_path), _frame_centroid_x(tmp_path / "o.mp4", 9, tmp_path)
    assert abs(first - 0.2) < 0.04 and abs(last - 0.8) < 0.04, (first, last)    # the text really moved left -> right


def test_opacity_zero_draws_nothing(tmp_path: Path):
    s = _scene()
    s["layers"][0]["props"]["opacity"] = [{"t": 0, "v": 0.0}]
    mr.render_scene(s, tmp_path / "o.mp4", tmp_path, FFMPEG)
    with pytest.raises(AssertionError, match="black"):
        _frame_centroid_x(tmp_path / "o.mp4", 3, tmp_path)


@pytest.mark.parametrize("layer,msg", [
    ({"id": "v", "kind": "video", "props": {}}, "not renderable"),
    ({"id": "i", "kind": "image", "src": "../secret.png", "props": {}}, "inside the scene directory"),
    ({"id": "t", "kind": "kinetic_text", "text": "x", "color": "red", "props": {}}, "color"),
])
def test_unrenderable_or_unsafe_layers_are_refused(tmp_path: Path, layer, msg):
    s = _scene()
    s["layers"] = [layer]
    with pytest.raises(mt.SceneError, match=msg):
        mr.render_scene(s, tmp_path / "o.mp4", tmp_path, FFMPEG)
    assert not (tmp_path / "o.mp4").exists()


def test_odd_size_refused(tmp_path: Path):
    with pytest.raises(mt.SceneError, match="even"):
        mr.render_scene(_scene(width=321), tmp_path / "o.mp4", tmp_path, FFMPEG)
