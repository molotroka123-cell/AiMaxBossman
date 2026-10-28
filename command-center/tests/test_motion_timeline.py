"""Motion Design Engine 2.0 core: easing, keyframes, validation, beat sync (synthetic data only)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bcc.art_v2 import motion_timeline as mt
from bcc.features import music_studio as music
from .test_music_studio_master2 import _clicks, needs_ffmpeg


@pytest.mark.parametrize("name", sorted(mt.EASINGS))
def test_every_easing_starts_at_0_and_ends_at_1(name):
    assert mt.ease(name, 0.0) == pytest.approx(0.0, abs=1e-9)
    assert mt.ease(name, 1.0) == pytest.approx(1.0, abs=1e-9)


def test_known_easing_shapes():
    assert mt.ease("easeOutExpo", 0.5) > 0.9                 # fast start
    assert mt.ease("easeInQuad", 0.5) == pytest.approx(0.25)
    assert min(mt.ease("easeInOutBack", t / 100) for t in range(100)) < 0      # anticipation dips below 0
    assert max(mt.ease("spring", t / 100) for t in range(101)) > 1.0           # underdamped spring overshoots
    with pytest.raises(mt.SceneError):
        mt.ease("nope", 0.5)


def test_evaluate_interpolates_holds_and_eases():
    kf = [{"t": 0, "v": 0.0}, {"t": 1000, "v": 10.0}, {"t": 2000, "v": 0.0, "ease": "easeInQuad"}]
    assert mt.evaluate(kf, -50) == 0.0 and mt.evaluate(kf, 9999) == 0.0
    assert mt.evaluate(kf, 500) == pytest.approx(5.0)
    assert mt.evaluate(kf, 1500) == pytest.approx(10.0 - 10.0 * 0.25)      # easeInQuad on the ending segment


def _scene():
    return {"fps": 30, "duration_ms": 2000,
            "layers": [{"id": "t", "kind": "kinetic_text", "props": {"x": [{"t": 0, "v": 0}, {"t": 1000, "v": 1}]}}]}


def test_valid_scene_and_frame_state():
    s = mt.validate_scene(_scene())
    assert mt.frame_state(s, 15)["t"]["x"] == pytest.approx(0.5)          # frame 15 @30fps = 500 ms


@pytest.mark.parametrize("mutate,msg", [
    (lambda s: s.update(fps=0), "fps"),
    (lambda s: s.update(duration_ms=-1), "duration"),
    (lambda s: s.update(layers=[]), "layers"),
    (lambda s: s["layers"][0].update(kind="hologram"), "kind"),
    (lambda s: s["layers"][0].update(props={"hue": [{"t": 0, "v": 1}]}), "animatable"),
    (lambda s: s["layers"][0]["props"]["x"].append({"t": 500, "v": 3}), "increase"),
    (lambda s: s["layers"][0]["props"]["x"].append({"t": 1500, "v": float("nan")}), "finite"),
    (lambda s: s["layers"][0]["props"]["x"].append({"t": 1500, "v": 1, "ease": "wobble"}), "easing"),
    (lambda s: s["layers"][0]["props"]["x"].append({"t": 9999, "v": 1}), "outside"),
    (lambda s: s["layers"].append(dict(s["layers"][0])), "duplicate"),
])
def test_negative_controls_reject_bad_scenes(mutate, msg):
    s = _scene()
    mutate(s)
    with pytest.raises(mt.SceneError, match=msg):
        mt.validate_scene(s)


def test_snap_to_beats_moves_close_cuts_and_keeps_far_ones():
    beats = [0, 500, 1000, 1500, 2000]
    assert mt.snap_to_beats([480, 1090, 1260], beats, max_shift_ms=120) == [500, 1000, 1260]
    assert mt.snap_to_beats([490, 510], beats) == [500]                    # two cuts, one beat: collapse
    assert mt.snap_to_beats([300], []) == [300]


def test_beat_cuts_and_punch_zoom():
    beats = [0, 500, 1000, 1500, 2000, 2500]
    assert mt.beat_cuts(beats, 2400, every_beats=2) == [1000, 2000]        # 0 excluded, 2500 beyond the clip
    z = mt.punch_zoom([500, 1000], strength=0.1)
    assert [k["t"] for k in z] == sorted({k["t"] for k in z})
    assert mt.evaluate(z, 500) == pytest.approx(1.1) and mt.evaluate(z, 1000 - 1) < 1.01
    mt.validate_scene({"fps": 30, "duration_ms": 2000, "layers": [{"id": "c", "kind": "video", "props": {"scale": z}}]})


@needs_ffmpeg
@pytest.mark.parametrize("bpm", [128.0, 90.0, 110.0])
def test_measured_beat_grid_matches_a_known_click_track_within_15ms(tmp_path: Path, bpm):
    res = music.measure_beats(_clicks(tmp_path / "c.wav", bpm))
    assert res is not None and abs(res["bpm"] - bpm) <= 0.5
    per = 60000.0 / bpm
    errs = [abs(b - round(b / per) * per) for b in res["beat_timestamps_ms"]]
    assert len(errs) >= 20 and max(errs) <= 15, max(errs)


@needs_ffmpeg
def test_negative_control_silence_has_no_beat_grid(tmp_path: Path):
    p = tmp_path / "s.wav"
    subprocess.run([music._find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=mono", "-t", "10", str(p)], check=True, capture_output=True)
    assert music.measure_beats(p) is None
