"""Lane-authored behaviour test (capability leaf reg-motion_epic): the offline Pillow trailer renderer.

Real renderer, real pixels, no mocks. The ffmpeg muxing path is not exercised (needs ffmpeg + minutes);
this proves the scene-clock frame renderer and the preview writer.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "tools" / "motion_studio"
sys.path.insert(0, str(ROOT))

PIL = pytest.importorskip("PIL")
np = pytest.importorskip("numpy")
import epic  # noqa: E402


def _font_available():
    try:
        epic.font(20)
        return True
    except RuntimeError:
        return False


pytestmark = pytest.mark.skipif(not _font_available(), reason="no TTF font (set BOSSMAN_MOTION_FONT)")

SCENES = [
    {"type": "title", "start": 0, "end": 2, "title": "BOSSMAN", "kicker": "K", "typed": "typed", "chip": "chip"},
    {"type": "cards", "start": 2, "end": 4, "heading": "CAPS",
     "items": [{"t": 2.1, "icon": "memory", "title": "Memory", "sub": "s"},
               {"t": 2.2, "icon": "play", "title": "Video", "sub": "s"},
               {"t": 2.3, "icon": "mic", "title": "Voice", "sub": "s"},
               {"t": 2.4, "icon": "other", "title": "Core", "sub": "s"}]},
    {"type": "grid", "start": 4, "end": 6, "value": 32, "label": "days", "caption": "c"},
    {"type": "voice", "start": 6, "end": 8, "name": "Jeff", "sub": "sub", "status": [{"text": "ok"}]},
    {"type": "bars", "start": 8, "end": 10, "values": [1, 3, 2, 5], "headline": "11", "counter_label": "n",
     "x_from": "a", "x_to": "b"},
    {"type": "roadmap", "start": 10, "end": 12, "heading": "NEXT", "disclaimer": "plans may change",
     "items": [{"t": 10.1, "version": "2.1", "when": "soon", "lines": ["one", "two"]}]},
    {"type": "logo", "start": 12, "end": 14, "name": "BOSSMAN", "tagline": "tag", "vo": [{"t": 12.2, "text": "subtitle line"}]},
    {"type": "end_card", "start": 14, "end": 16, "name": "END", "tagline": "bye"},
]
SPEC = {"meta": {"duration": 16.0, "hud": "TEST"}, "scenes": SCENES}


def test_unsupported_scene_type_is_refused_before_rendering():
    bad = {"meta": {"duration": 2.0}, "scenes": [{"type": "hologram", "start": 0, "end": 2}]}
    with pytest.raises(ValueError, match="classic"):
        epic.Renderer(bad)


def test_every_supported_scene_kind_renders_a_full_hd_ready_frame():
    assert {s["type"] for s in SCENES} == epic.SUPPORTED
    r = epic.Renderer(SPEC)
    for sc in SCENES:
        t = (sc["start"] + sc["end"]) / 2
        im = r.frame(t)
        assert im.size == (epic.W, epic.H) and im.mode == "RGB", sc["type"]
        arr = np.asarray(im)
        assert arr.std() > 3 and arr.max() > 150, f"{sc['type']} frame is blank"   # real content, not a flat fill


def test_frames_are_deterministic_and_follow_the_clock():
    r = epic.Renderer(SPEC)
    a, b = r.frame(3.3).tobytes(), epic.Renderer(SPEC).frame(3.3).tobytes()
    assert a == b                                                   # no random frame jitter
    assert r.frame(3.3).tobytes() != r.frame(3.9).tobytes()         # motion advances with t
    assert r.frame(1.0).tobytes() != r.frame(5.0).tobytes()         # different scenes differ


def test_final_frames_fade_to_black_and_subtitle_follows_vo_clock():
    r = epic.Renderer(SPEC)
    mid = np.asarray(r.frame(15.0)).mean()
    last = np.asarray(r.frame(15.99)).mean()
    assert last < mid * 0.25                                        # fade-out at the end of the duration
    r2 = epic.Renderer(SPEC)
    before = np.asarray(r2.frame(12.1))[609:651].astype(int)
    after = np.asarray(r2.frame(13.0))[609:651].astype(int)
    assert (before != after).any()                                  # the subtitle box appears once vo.t is reached


def test_preview_mode_writes_pngs_and_skips_video(tmp_path):
    from PIL import Image
    assert epic.render(SPEC, tmp_path, tmp_path / "none.wav", previews=(1.0, 5.5)) is None
    for name in ("epic-preview-1.png", "epic-preview-5.5.png"):
        with Image.open(tmp_path / name) as im:
            assert im.size == (1280, 720)
    assert not (tmp_path / "video.mp4").exists()
