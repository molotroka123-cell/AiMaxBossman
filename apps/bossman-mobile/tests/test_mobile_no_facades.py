"""The mobile pult must not invent facts (audit 28.10, «функции доказаны, а не обёрнуты»).

Five routes were facades: a 1x1 blank PNG as «the screen», an invented element in the tap preview, a fabricated transcript and
«выполняю в контуре» for any voice input, «rolled_back» on every failure, «no files, no network, $0» for a route Bossman does
not have. Each test pins one of them to the honest behaviour. Synthetic only: the screen grab is a generated picture.
"""
from __future__ import annotations

import base64
import io
import shutil
import subprocess

import pytest
from PIL import Image

from bossmobile import speech

needs_ffmpeg = pytest.mark.skipif(speech.find_ffmpeg() is None and not shutil.which("ffmpeg"), reason="ffmpeg required")


def _hdr(env):
    return {"X-BM-CSRF": env.csrf}


def _upstream(env, path_part):
    return [c for c in env.fake.calls if path_part in c["path"]]


# ---------------------------------------------------------------- screen

def test_screen_frame_is_the_real_grab_downscaled_not_a_blank_pixel(authed, monkeypatch):
    from PIL import ImageGrab
    monkeypatch.setattr(ImageGrab, "grab", lambda *a, **k: Image.new("RGB", (2560, 1440), (200, 30, 30)))
    monkeypatch.setattr("bossmobile.api.PRIVACY_BLACKLIST", ("zzz-no-such-window-title-zzz",))
    r = authed.c.get("/m/api/pc/screen-stream")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["privacy_filtered"] is False and d["mime"] == "image/jpeg"
    img = Image.open(io.BytesIO(base64.b64decode(d["frame_base64"])))
    assert img.size == (1280, 720) and (d["frame_width"], d["frame_height"]) == (1280, 720)
    assert (d["width"], d["height"]) == (2560, 1440) and d["scale_factor"] == 2.0       # real source size, not a hardcoded 1920x1080
    r_, g_, b_ = img.getpixel((640, 360))
    assert r_ > 150 and g_ < 80 and b_ < 80, "the frame is the grabbed picture"
    assert d["touch_to_act_enabled"] is False, "nothing executes a tap from the phone, so it must not claim to"


def test_screen_that_cannot_be_captured_is_a_503_not_a_blank_frame(authed, monkeypatch):
    from PIL import ImageGrab

    def boom(*a, **k):
        raise OSError("screen grab failed")
    monkeypatch.setattr(ImageGrab, "grab", boom)
    monkeypatch.setattr("bossmobile.api.PRIVACY_BLACKLIST", ("zzz-no-such-window-title-zzz",))
    r = authed.c.get("/m/api/pc/screen-stream")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "BM_SCREEN_UNAVAILABLE"
    assert "frame_base64" not in r.text


def test_a_blacklisted_window_blocks_the_whole_frame(authed, monkeypatch):
    from PIL import ImageGrab

    def must_not_grab(*a, **k):
        raise AssertionError("the screen must not be captured while a blacklisted window is visible")
    monkeypatch.setattr(ImageGrab, "grab", must_not_grab)
    monkeypatch.setattr("bossmobile.api.PRIVACY_BLACKLIST", ("",))             # every visible titled window matches
    r = authed.c.get("/m/api/pc/screen-stream")
    if r.status_code == 503 or (r.status_code == 200 and r.json().get("privacy_filtered") is not True):
        pytest.skip("no visible titled window on this desktop")
    assert r.json()["privacy_filtered"] is True and r.json()["frame_base64"] == ""


def test_screen_frame_of_the_real_desktop_is_not_a_blank_picture(authed):
    """Real ImageGrab, no patches: only statistics are checked (size, pixel variance), the picture itself is never looked at."""
    import statistics
    r = authed.c.get("/m/api/pc/screen-stream")
    if r.status_code == 503:
        pytest.skip("no interactive desktop in this session")
    d = r.json()
    if d.get("privacy_filtered"):
        pytest.skip("a privacy-blacklisted window is visible right now")
    img = Image.open(io.BytesIO(base64.b64decode(d["frame_base64"]))).convert("L")
    assert img.size[0] >= 640 and img.size[1] >= 360 and img.size[0] <= 1280
    px = list(img.resize((64, 36)).getdata())
    assert statistics.pstdev(px) > 1.0, "a blank frame (the old facade was a 1x1 white pixel) would have no variance"
    assert d["width"] >= img.size[0] and d["scale_factor"] >= 1.0


# ---------------------------------------------------------------- touch preview

def test_touch_preview_invents_no_element_and_executes_nothing(authed):
    r = authed.c.post("/m/api/pc/touch-preview", json={"x": 100.4, "y": 200.6}, headers=_hdr(authed))
    d = r.json()
    assert r.status_code == 200 and d["executed"] is False and d["requires_approval"] is True
    assert d["target"] == {"x": 100.4, "y": 200.6, "element": None, "element_resolved": False}
    assert "InteractiveControl" not in r.text
    named = authed.c.post("/m/api/pc/touch-preview", json={"x": 1, "y": 2, "element": "Кнопка OK"}, headers=_hdr(authed)).json()
    assert named["target"]["element"] == "Кнопка OK" and named["target"]["element_resolved"] is False
    assert authed.c.post("/m/api/pc/touch-preview", json={"x": "a", "y": 2}, headers=_hdr(authed)).status_code == 422


# ---------------------------------------------------------------- voice turn

def test_voice_turn_text_passes_through_and_never_claims_execution(authed):
    r = authed.c.post("/m/api/voice/turn", json={"text": "  удали все файлы  "}, headers=_hdr(authed))
    d = r.json()
    assert r.status_code == 200 and d["transcript"] == "удали все файлы"
    assert d["executed"] is False and d["task_id"] is None and d["reply"] == ""
    assert "Выполняю" not in r.text and "Принял" not in r.text
    assert not _upstream(authed, "speech"), "text needs no recognition"


def test_voice_turn_without_input_or_with_broken_audio_is_refused(authed):
    assert authed.c.post("/m/api/voice/turn", json={}, headers=_hdr(authed)).status_code == 422
    bad = authed.c.post("/m/api/voice/turn", json={"audio_b64": "!!!not base64!!!"}, headers=_hdr(authed))
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "BM_VOICE_B64"
    junk = base64.b64encode(b"this is not audio at all" * 40).decode()
    r = authed.c.post("/m/api/voice/turn", json={"audio_b64": junk}, headers=_hdr(authed))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "BM_SPEECH_FORMAT"
    assert not _upstream(authed, "speech")


@needs_ffmpeg
def test_voice_turn_audio_goes_through_the_real_recognition_chain_not_a_canned_transcript(authed):
    ff = speech.find_ffmpeg()
    webm = subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=2", "-c:a", "libopus", "-f", "webm",
                           "pipe:1"], capture_output=True, check=True).stdout
    r = authed.c.post("/m/api/voice/turn", json={"audio_b64": base64.b64encode(webm).decode()}, headers=_hdr(authed))
    d = r.json()
    assert r.status_code == 200 and d["transcript"] == "привет, Босман"            # what the (fake) Whisper answered
    assert "Голосовая команда владельца" not in r.text and d["executed"] is False
    assert len(_upstream(authed, "/api/oss/speech/transcribe")) == 1 and authed.fake.speech_bytes[:4] == b"RIFF"


# ---------------------------------------------------------------- rollback and blast radius

def test_rollback_passes_the_real_answer_through(authed):
    r = authed.c.post("/m/api/tasks/7/rollback", headers=_hdr(authed))
    assert r.status_code == 200 and r.json() == {"ok": True, "restored": ["a.txt"], "task_id": 7}
    assert [c["path"] for c in _upstream(authed, "rollback")] == ["/api/pc-agent/tasks/7/rollback"]


def test_a_failed_rollback_is_never_reported_as_rolled_back(authed):
    r = authed.c.post("/m/api/tasks/99/rollback", headers=_hdr(authed))
    assert r.status_code == 404, "an upstream failure must stay a failure"
    assert "rolled_back" not in r.text and "completed" not in r.text


@pytest.mark.parametrize("bad", ["abc", "7;rm", "1.5", "-3"])
def test_rollback_and_blast_radius_refuse_non_numeric_task_ids(authed, bad):
    assert authed.c.post(f"/m/api/tasks/{bad}/rollback", headers=_hdr(authed)).status_code in (404, 422)
    assert authed.c.get(f"/m/api/tasks/{bad}/blast-radius").status_code in (404, 422)
    assert not _upstream(authed, "rollback") and not _upstream(authed, "blast")


def test_rollback_needs_a_session_and_csrf(env):
    assert env.c.post("/m/api/tasks/7/rollback").status_code in (401, 403)
    env.login()
    assert env.c.post("/m/api/tasks/7/rollback").status_code == 403
    assert not _upstream(env, "rollback")


def test_blast_radius_is_501_instead_of_invented_zeros(authed):
    r = authed.c.get("/m/api/tasks/7/blast-radius")
    assert r.status_code == 501 and r.json()["detail"]["code"] == "BM_BLAST_UNAVAILABLE"
    for invented in ("network_used", "cost_usd", "affected_files", '"diff"'):
        assert invented not in r.text, f"{invented} must not be invented for a route Bossman does not have"


def test_a_failed_window_enumeration_is_a_503_not_zero_open_windows(authed, monkeypatch):
    import ctypes
    monkeypatch.setattr(ctypes, "windll", None, raising=False)         # EnumWindows cannot run
    r = authed.c.get("/m/api/pc-agent/windows")                        # the fake Bossman has no such route -> the local fallback
    assert r.status_code == 503 and r.json()["detail"]["code"] == "BM_WINDOWS_UNAVAILABLE"
    assert '"count"' not in r.text
