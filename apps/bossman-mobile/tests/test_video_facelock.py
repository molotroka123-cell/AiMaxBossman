"""Phone: the video form offers FaceLock exactly like the photo form, and the request reaches Bossman unchanged."""
import pathlib

from bossmobile import video_timing as vt

from test_video import JPEG, cat, form, posted  # noqa: F401  (fixture re-export)

UI = pathlib.Path(__file__).resolve().parents[1] / "bossmobile" / "ui"


def test_video_facelock_with_a_start_picture_is_sent(authed, cat):  # noqa: F811
    r = form(authed, "/m/api/video/jobs", {"model": "wan2.2-ti2v-5b", "prompt": "@1 улыбается", "duration": "2",
                                           "resolution": "480x272", "facelock": "true"},
             {"image": ("s.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 202, r.text
    sent = posted(authed)[-1]
    assert sent["facelock"] is True and sent["image_b64"]


def test_video_facelock_is_off_by_default(authed, cat):  # noqa: F811
    r = form(authed, "/m/api/video/jobs", {"model": "wan2.2-ti2v-5b", "prompt": "волны", "duration": "2",
                                           "resolution": "480x272"}, {"image": ("s.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 202 and "facelock" not in posted(authed)[-1]


def test_video_facelock_without_a_start_picture_is_refused_before_bossman(authed, cat):  # noqa: F811
    before = len(posted(authed))
    r = form(authed, "/m/api/video/jobs", {"model": "wan2.2-ti2v-5b", "prompt": "волны", "duration": "2",
                                           "resolution": "480x272", "facelock": "1"})
    assert r.status_code == 422 and "FaceLock" in r.text and "BM_FACELOCK_NEEDS_REF" in r.text
    assert len(posted(authed)) == before


def test_running_card_shows_the_facelock_stage_with_real_frames():
    d = {"known": True, "stage": "facelock", "phase": "facelock", "pct": 40.0, "frame": 33, "frames": 81}
    v = vt.detail_view(d, 120.0, 600.0, True)
    assert v["phase"].startswith("FaceLock") and (v["step"], v["steps"], v["unit"]) == (33, 81, "кадр")
    assert v["real"] is True and v["percent"] == 40.0


def test_form_has_the_toggle_and_shows_bossmans_label():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'id="video-facelock"' in html and 'id="vfacelock-row"' in html and "минимум на 8 кадрах" in html
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "fd.append('facelock', 'true')" in js and "$('vfacelock-row').hidden = !fl" in js
    assert "if (f.label) return" in js and "facelock: 'FaceLock'" in js
