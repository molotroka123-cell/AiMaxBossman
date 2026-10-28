"""Motion finishing: kinetic ASS subtitles and LUT grading, checked on rendered pixels (synthetic only)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bcc.art_v2 import motion_finish as mf
from bcc.art_v2 import motion_render as mr
from bcc.art_v2.motion_timeline import SceneError
from bcc.features import music_studio as music

FFMPEG = music._find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required")
WORDS = [{"w": "hey", "start_ms": 0, "end_ms": 400}, {"w": "this", "start_ms": 500, "end_ms": 800},
         {"w": "is", "start_ms": 800, "end_ms": 1000}, {"w": "phonk", "start_ms": 1000, "end_ms": 1600},
         {"w": "drift", "start_ms": 1700, "end_ms": 2200}]


def test_ass_has_karaoke_per_word_and_groups_lines():
    ass = mf.words_to_ass(WORDS, max_words=4)
    dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogue) == 2                                   # 4 + 1 words
    assert dialogue[0].startswith("Dialogue: 0,0:00:00.00,0:00:01.60,")
    assert "{\\kf40}hey" in dialogue[0] and "{\\k10}" in dialogue[0]       # 400 ms word, 100 ms gap before "this"
    assert "{\\kf60}phonk" in dialogue[0]


def test_transcript_cannot_inject_ass_overrides():
    ass = mf.words_to_ass([{"w": "{\\fs500}boom\\N", "start_ms": 0, "end_ms": 300}])
    assert "\\fs500" not in ass and "boom" in ass


@pytest.mark.parametrize("words", [
    [{"w": "a", "start_ms": 100, "end_ms": 100}],
    [{"w": "a", "start_ms": 500, "end_ms": 600}, {"w": "b", "start_ms": 100, "end_ms": 200}],
    [{"w": 5, "start_ms": 0, "end_ms": 10}],
])
def test_bad_word_timings_rejected(words):
    with pytest.raises(SceneError):
        mf.words_to_ass(words)


def _cube(path: Path, size: int = 2, swap_rb: bool = False) -> Path:
    rows = []
    for b in range(size):
        for g in range(size):
            for r in range(size):
                v = [r / (size - 1), g / (size - 1), b / (size - 1)]
                rows.append(" ".join(f"{x:.6f}" for x in (v[::-1] if swap_rb else v)))
    path.write_text(f"LUT_3D_SIZE {size}\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_validate_cube_accepts_good_and_rejects_corrupt(tmp_path: Path):
    assert mf.validate_cube(_cube(tmp_path / "ok.cube", 3)) == 3
    bad = tmp_path / "bad.cube"
    bad.write_text("LUT_3D_SIZE 2\n0 0 0\n", encoding="utf-8")
    with pytest.raises(SceneError, match="rows"):
        mf.validate_cube(bad)


def _red_clip(tmp: Path) -> Path:
    out = tmp / "red.mp4"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=160x90:d=1:r=10", "-pix_fmt", "yuv420p", str(out)], check=True, capture_output=True)
    return out


def _mean_rgb(video: Path, tmp: Path):
    from PIL import Image
    png = tmp / "m.png"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vframes", "1", str(png)],
                   check=True, capture_output=True)
    return Image.open(png).convert("RGB").resize((1, 1)).getpixel((0, 0))


@needs_ffmpeg
def test_lut_really_changes_colour_and_identity_does_not(tmp_path: Path):
    pytest.importorskip("PIL")
    src = _red_clip(tmp_path)
    _cube(tmp_path / "swap.cube", 2, swap_rb=True)
    _cube(tmp_path / "ident.cube", 2)
    r0 = _mean_rgb(src, tmp_path)
    swapped = _mean_rgb(mf.apply_grade(src, tmp_path / "s.mp4", tmp_path, FFMPEG, lut="swap.cube"), tmp_path)
    ident = _mean_rgb(mf.apply_grade(src, tmp_path / "i.mp4", tmp_path, FFMPEG, lut="ident.cube"), tmp_path)
    assert r0[0] > 200 and r0[2] < 60                           # source is red
    assert swapped[2] > 200 and swapped[0] < 60                 # red/blue swapped by the LUT
    assert max(abs(a - b) for a, b in zip(ident, r0)) < 12      # negative control: identity LUT leaves it alone


@needs_ffmpeg
def test_burned_subtitles_put_text_pixels_on_a_black_clip(tmp_path: Path):
    PIL = pytest.importorskip("PIL.Image")
    sc = {"fps": 10, "duration_ms": 2300, "width": 1280, "height": 720,
          "layers": [{"id": "x", "kind": "kinetic_text", "text": ".", "size": 8, "props": {"opacity": [{"t": 0, "v": 0}]}}]}
    base = tmp_path / "base.mp4"
    mr.render_scene(sc, base, tmp_path, FFMPEG)
    (tmp_path / "s.ass").write_text(mf.words_to_ass(WORDS), encoding="utf-8")
    try:
        out = mf.apply_grade(base, tmp_path / "sub.mp4", tmp_path, FFMPEG, ass="s.ass")
    except RuntimeError as e:
        pytest.skip(f"ffmpeg without libass: {e}")
    png = tmp_path / "f.png"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", "0.3", "-i", str(out), "-vframes", "1", str(png)],
                   check=True, capture_output=True)
    im = PIL.open(png).convert("L")
    assert sum(1 for p in im.getdata() if p > 128) > 300, "no subtitle pixels burned"


@needs_ffmpeg
def test_grade_refuses_escape_and_empty(tmp_path: Path):
    src = _red_clip(tmp_path)
    with pytest.raises(SceneError, match="inside"):
        mf.apply_grade(src, tmp_path / "o.mp4", tmp_path, FFMPEG, lut="../x.cube")
    with pytest.raises(SceneError, match="nothing"):
        mf.apply_grade(src, tmp_path / "o.mp4", tmp_path, FFMPEG)
