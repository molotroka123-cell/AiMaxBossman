"""Video tab: LTX-2.5 (owner 31.10 «В пульте не вижу вариант лтх»). Bossman is a MockTransport; no generation runs.

The catalogue entry below is what Bossman's GET /api/direct-gen/models returns for ltx-2.5 (bcc.direct_gen.sdcli),
weights removed. Proven: the phone lists it as a selectable model with its own limits, refuses a frame above the
measured 576x1024 ceiling before sending, and submits through the same /api/direct-gen/jobs path as Wan.
"""
import base64
import json
import pathlib

import pytest

import conftest
from bossmobile import video, video_timing as vt

from test_video import FACESWAP, UP, VIDEO

UI = pathlib.Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
JPEG = b"\xff\xd8\xff\xe0" + b"1" * 64
LTX = {"id": "ltx-2.5", "label": "LTX-2.5 22B distilled (текст/картинка→видео, 8 шагов)", "kind": "video",
       "family": "ltx", "runtime": "sdcpp", "modes": ["T2V", "I2V"], "available": True, "reason": "", "fps": 24,
       "max_seconds": 5, "min_side": 256, "max_side": 1024, "side_multiple": 32, "max_pixels": 576 * 1024,
       "requires_image": False, "requires_audio": False, "accepts_image": True, "max_refs": 1,
       "default_steps": 8, "max_steps": 8, "default_resolution": "512x288", "license": "UNKNOWN",
       "notes": "Тот же запуск, что в Motion Studio (sd-cli Vulkan, distilled Q6_K, 8 шагов, cfg 1)."}


@pytest.fixture
def cat(monkeypatch):
    monkeypatch.setattr(conftest, "DG_MODELS", {"models": conftest.DG_MODELS["models"][:2] + VIDEO + [LTX]})
    monkeypatch.setattr(conftest, "DG_STATUS", UP)
    monkeypatch.setattr(conftest, "FACESWAP", FACESWAP)
    monkeypatch.setattr(conftest, "FAIL_NEXT", [])


def posted(env, path="/api/direct-gen/jobs"):
    return [json.loads(c["body"]) for c in env.fake.calls if c["path"] == path and c["method"] == "POST"]


def test_ltx25_is_a_selectable_ready_model_with_its_own_limits(authed, cat):
    body = authed.c.get("/m/api/video/models").json()
    ms = {m["id"]: m for m in body["models"]}
    m = ms["ltx-2.5"]
    assert m["available"] and m["ready"] and m["reason"] == "" and m["runtime"] == "sdcpp"
    assert (m["fps"], m["frame_step"], m["max_seconds"], m["min_side"], m["max_side"], m["side_multiple"]) == \
        (24, 8, 5.0, 256, 1024, 32)
    assert m["max_pixels"] == 576 * 1024 and m["default_aspect"] == "16:9"
    assert m["steps"]["max_steps"] == 8 and m["steps"]["default_steps"] == 8 and m["fixed_steps"] is None
    assert m["accepts_image"] and not m["requires_image"] and not m["accepts_audio"]
    assert m["timing"]["kind"] == "estimate" and "Motion Studio" in m["timing"]["basis"]
    assert [x["id"] for x in body["models"]].index("ltx-2.5") < [x["id"] for x in body["models"]].index("wan21-vace-1.3b")


def test_ltx25_time_estimate_matches_the_measured_runs():
    t = vt.timing_for("ltx-2.5")
    full = vt.wan_seconds(t, 8, 576, 1024, 121)["total_s"]
    assert 15 * 60 <= full <= 24 * 60                       # 883-1438 s measured for 576x1024x121, 8 steps
    small = vt.wan_seconds(t, 8, 512, 288, vt.frames_for(24, 8, 4))["total_s"]
    assert small < full


def test_ltx25_t2v_submit_reaches_bossman_on_the_direct_gen_path(authed, cat):
    r = authed.post("/m/api/video/jobs", data={"model": "ltx-2.5", "prompt": "a paper boat floating on a calm lake",
                                               "duration": "4", "resolution": "512x288", "steps": "8", "seed": "3"},
                    files={})
    assert r.status_code == 202, r.text
    sent = posted(authed)[-1]
    assert (sent["model"], sent["resolution"], sent["duration"], sent["steps"], sent["seed"], sent["mode"]) == \
        ("ltx-2.5", "512x288", 4.0, 8, 3, "DIRECT")
    assert "image_b64" not in sent and "audio_b64" not in sent


def test_ltx25_i2v_submit_carries_the_start_picture(authed, cat):
    r = authed.post("/m/api/video/jobs", data={"model": "ltx-2.5", "prompt": "x", "duration": "2",
                                               "resolution": "576x1024"},
                    files={"image": ("s.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 202, r.text
    assert base64.b64decode(posted(authed)[-1]["image_b64"]) == JPEG


@pytest.mark.parametrize("data,needle", [
    ({"resolution": "1024x1024"}, "не больше 589824 точек"),       # above the measured memory ceiling
    ({"resolution": "1280x720"}, "до 1024"),
    ({"resolution": "480x272"}, "кратна 32"),
    ({"duration": "6"}, "не больше 5 с"),
    ({"steps": "9"}, "от 1 до 8"),
])
def test_ltx25_values_bossman_rejects_are_refused_before_sending(authed, cat, data, needle):
    r = authed.post("/m/api/video/jobs", data={"model": "ltx-2.5", "prompt": "x", "duration": "2",
                                               "resolution": "512x288", **data}, files={})
    assert r.status_code == 422, r.text
    assert needle in r.json()["detail"]["message"]
    assert not posted(authed)


def test_pixel_ceiling_check_is_only_for_models_that_declare_it():
    wan = {"ready": True, "min_side": 256, "max_side": 1280, "side_multiple": 16, "max_seconds": 5, "steps": None,
           "requires_image": False, "accepts_image": True, "requires_audio": False, "accepts_audio": False}
    assert video.check_job(wan, resolution="1280x1280", duration=2, steps=None, has_image=False, has_audio=False) is None
    ltx = {**wan, "max_side": 1024, "side_multiple": 32, "max_pixels": 576 * 1024}
    assert video.check_job(ltx, resolution="576x1024", duration=2, steps=None, has_image=False, has_audio=False) is None
    assert "точек" in video.check_job(ltx, resolution="1024x1024", duration=2, steps=None, has_image=False,
                                      has_audio=False)


def test_ui_keeps_the_frame_under_the_pixel_ceiling():
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "m.max_pixels && W * H > m.max_pixels" in js
