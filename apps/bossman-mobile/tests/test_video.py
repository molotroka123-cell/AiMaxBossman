"""Video section: every Bossman video model, limits mirrored from Bossman, honest time, queue, face swap (owner 17.10.2026).

No real generation is started anywhere here: Bossman is a MockTransport and the submitted request is inspected."""
import base64
import json
import pathlib
import re
from datetime import datetime, timezone

import pytest

import conftest
from bossmobile import video, video_timing as vt
from bossmobile.timing import queue_view

UI = pathlib.Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
JPEG = b"\xff\xd8\xff\xe0" + b"1" * 64
WAV = b"RIFF" + b"\x00" * 60

# GET /api/direct-gen/models of the owner's Bossman on 17.10.2026 (video part, weights/sha removed)
VIDEO = [
    {"id": "wan2.2-ti2v-5b", "label": "Wan2.2 TI2V 5B", "kind": "video", "family": "wan", "runtime": "sdcpp",
     "modes": ["T2V", "I2V"], "available": True, "reason": "", "fps": 16, "max_seconds": 5, "min_side": 256,
     "max_side": 1280, "side_multiple": 16, "requires_image": False, "requires_audio": False, "accepts_image": True,
     "default_steps": 20, "max_steps": 40, "default_resolution": "480x272", "license": "Apache-2.0",
     "notes": "Один проход до ~5 с. Дольше на этой машине очень медленно (замер: ~14 мин на шаг при 832x480x121)."},
    {"id": "wan21-vace-1.3b", "label": "Wan2.1 VACE 1.3B", "kind": "video", "family": "wan", "runtime": "sdcpp",
     "modes": ["T2V", "I2V"], "available": False,
     "reason": "нет проверенного запуска (workflow-шаблона/набора флагов) для VACE; веса лежат на диске", "fps": 16,
     "max_seconds": 5, "min_side": 256, "max_side": 1536, "side_multiple": 16, "requires_image": False,
     "requires_audio": False, "accepts_image": True, "default_steps": 20, "max_steps": 40, "default_resolution": "480x272"},
    {"id": "wan21-vace-14b", "label": "Wan2.1 VACE 14B", "kind": "video", "family": "wan", "runtime": "sdcpp",
     "modes": ["T2V", "I2V"], "available": False,
     "reason": "нет проверенного запуска (workflow-шаблона/набора флагов) для VACE; веса лежат на диске", "fps": 16,
     "max_seconds": 5, "min_side": 256, "max_side": 1536, "side_multiple": 16, "requires_image": False,
     "requires_audio": False, "accepts_image": True, "default_steps": 20, "max_steps": 40, "default_resolution": "480x272"},
    {"id": "wan2.1-t2v-1.3b", "label": "Wan2.1 T2V 1.3B", "kind": "video", "family": "wan", "runtime": "comfyui",
     "modes": ["T2V"], "available": False,
     "reason": "weights missing on disk: diffusion (diffusion_models/wan2.1_t2v_1.3B*.safetensors); no verified workflow "
               "template installed (wan2.1-t2v-1.3b.json)", "fps": 16, "max_seconds": 8, "min_side": 256,
     "max_side": 832, "side_multiple": 16, "requires_image": False, "requires_audio": False, "accepts_image": False,
     "default_steps": None, "max_steps": None, "default_resolution": "480x320"},
    {"id": "wan2.2-s2v-14b", "label": "Wan2.2 S2V 14B", "kind": "video", "family": "wan", "runtime": "comfyui",
     "modes": ["S2V"], "available": True, "reason": "", "fps": 16, "max_seconds": 20, "min_side": 256, "max_side": 1280,
     "side_multiple": 16, "requires_image": True, "requires_audio": True, "accepts_image": True, "default_steps": None,
     "max_steps": None, "default_resolution": "480x320", "notes": "Речь/песня в видео: нужны референс-изображение и аудио."},
    {"id": "wan2.2-animate-14b", "label": "Wan2.2 Animate 14B", "kind": "video", "family": "wan", "runtime": "comfyui",
     "modes": ["ANIMATE"], "available": False,
     "reason": "ANIMATE needs a driving video and pose inputs; the Direct window has no such input yet", "fps": 16,
     "max_seconds": 10, "min_side": 256, "max_side": 1280, "side_multiple": 16, "requires_image": False,
     "requires_audio": False, "accepts_image": False, "default_steps": None, "max_steps": None,
     "default_resolution": "480x320"},
    {"id": "ltx-video", "label": "LTX-Video", "kind": "video", "family": "ltx", "runtime": "comfyui", "modes": ["T2V", "I2V"],
     "available": False,
     "reason": "weights missing on disk: checkpoint (checkpoints/ltx*.safetensors); text_encoder "
               "(text_encoders/t5xxl*.safetensors); no verified workflow template installed (ltx-video.json)",
     "fps": 24, "max_seconds": 8, "min_side": 256, "max_side": 1280, "side_multiple": 32, "requires_image": False,
     "requires_audio": False, "accepts_image": True, "default_steps": None, "max_steps": None,
     "default_resolution": "480x320"},
]
FACESWAP = {"engine": "facefusion", "available": True, "license": "OpenRAIL-AS", "reason": "", "presets": [
    {"id": "fast", "label": "Быстро: 7 кадров/с, похожесть 0.80 (замер 10.10)"},
    {"id": "quality", "label": "Качество: детализация 1024, маска рук/очков; 1.7 кадра/с, похожесть 0.80"},
    {"id": "lively", "label": "Живая мимика: как «Качество» + мимика исходника 40% (похожесть 0.79)"}]}
UP = {"queue": {"running": None, "waiting": 0}, "sd_cli": {"present": True},
      "runtime": {"name": "comfyui", "reachable": True, "reason": ""}, "memory": {"measured": True, "free_mb": 43356.3}}
DOWN = {**UP, "runtime": {"name": "comfyui", "reachable": False, "reason": "ConnectError"}}


def swap_job(jid, gen_at, post_at, done_at, frames=823):
    """The two real FaceFusion runs in the owner's journal, 14.10.2026 (timeline seconds from loading)."""
    t0 = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc).timestamp()
    iso = lambda s: datetime.fromtimestamp(t0 + s, timezone.utc).isoformat()  # noqa: E731
    return {"job_id": jid, "kind": "faceswap", "model": "faceswap-facefusion", "status": "completed",
            "params": {"preset": "quality", "frame": "original", "faces": 5},
            "progress": {"kind": "frames", "value": frames, "max": frames, "percent": 100.0},
            "result": {"mime": "video/mp4", "file": "video.mp4"},
            "timeline": [{"stage": "queued", "at": iso(0)}, {"stage": "loading", "at": iso(0)},
                         {"stage": "generating", "at": iso(gen_at)}, {"stage": "postprocessing", "at": iso(post_at)},
                         {"stage": "completed", "at": iso(done_at)}]}


REAL_SWAPS = [swap_job("1" * 32, 22.86, 525.43, 1341.19), swap_job("2" * 32, 24.23, 514.67, 1188.71)]


@pytest.fixture
def cat(monkeypatch):
    monkeypatch.setattr(conftest, "DG_MODELS", {"models": conftest.DG_MODELS["models"][:2] + VIDEO})
    monkeypatch.setattr(conftest, "DG_STATUS", UP)
    monkeypatch.setattr(conftest, "FACESWAP", FACESWAP)
    monkeypatch.setattr(conftest, "FAIL_NEXT", [])


def posted(env, path="/api/direct-gen/jobs"):
    return [json.loads(c["body"]) for c in env.fake.calls if c["path"] == path and c["method"] == "POST"]


def form(env, path, data, files=None):
    return env.post(path, data=data, files=files or {})


# ------------------------------------------------------------------------------------------------------- catalog
def test_every_video_model_listed_unavailable_disabled_with_russian_reason(authed, cat):
    body = authed.c.get("/m/api/video/models").json()
    ms = {m["id"]: m for m in body["models"]}
    assert set(ms) == {m["id"] for m in VIDEO}                       # all 7, nothing hidden, no photo model
    for m in body["models"]:
        assert m["kind"] == "video" and "weights" not in m
        if not m["available"]:
            assert not m["ready"] and m["reason"] and "missing" not in m["reason"] and "needs" not in m["reason"]
    assert ms["wan2.1-t2v-1.3b"]["reason"].startswith("нет весов на диске")
    assert "не установлен проверенный шаблон" in ms["ltx-video"]["reason"]
    assert ms["wan2.2-animate-14b"]["reason"].startswith("нужны ведущее видео и позы")
    assert "VACE" in ms["wan21-vace-1.3b"]["reason"]                  # Bossman's own Russian text kept
    assert [m["ready"] for m in body["models"]][:2] == [True, True]   # ready models first
    assert body["gpu"] == {"busy": False, "waiting": 0, "comfyui": True, "memory_free_gb": 42.3}


def test_per_model_inputs_follow_the_backend_contract(authed, cat):
    ms = {m["id"]: m for m in authed.c.get("/m/api/video/models").json()["models"]}
    ti = ms["wan2.2-ti2v-5b"]
    assert ti["steps"] == {"min_steps": 10, "default_steps": 20, "max_steps": 40} and ti["fixed_steps"] is None
    assert ti["accepts_image"] and not ti["requires_image"] and not ti["accepts_audio"]
    assert (ti["fps"], ti["frame_step"], ti["max_seconds"], ti["min_side"], ti["max_side"], ti["side_multiple"]) == \
        (16, 4, 5.0, 256, 1280, 16)
    assert ti["default_aspect"] == "16:9"
    s2 = ms["wan2.2-s2v-14b"]
    assert s2["steps"] is None and s2["fixed_steps"] == 4                    # KSampler steps=4 in the workflow template
    assert s2["requires_image"] and s2["requires_audio"] and s2["accepts_audio"] and s2["max_seconds"] == 20.0
    assert s2["default_aspect"] == "3:2" and ms["ltx-video"]["frame_step"] == 8


def test_comfyui_down_makes_s2v_not_ready_with_reason(authed, cat, monkeypatch):
    monkeypatch.setattr(conftest, "DG_STATUS", DOWN)
    s2 = {m["id"]: m for m in authed.c.get("/m/api/video/models").json()["models"]}["wan2.2-s2v-14b"]
    assert s2["available"] and not s2["ready"] and "ComfyUI сейчас не запущен" in s2["reason"]
    r = form(authed, "/m/api/video/jobs", {"model": "wan2.2-s2v-14b", "prompt": "поёт", "duration": "4",
                                           "resolution": "480x320"},
             {"image": ("a.jpg", JPEG, "image/jpeg"), "audio": ("a.wav", WAV, "audio/wav")})
    assert r.status_code == 422 and "ComfyUI" in r.json()["detail"]["message"] and not posted(authed)


def test_faceswap_exposed_by_bossman_is_offered(authed, cat):
    fs = authed.c.get("/m/api/video/models").json()["faceswap"]
    assert fs["available"] and [p["id"] for p in fs["presets"]] == ["fast", "quality", "lively"]
    assert fs["max_faces"] == 5 and fs["max_video_mb"] == 300 and {f["id"] for f in fs["frames"]} == {"original", "16:9"}


def test_no_faceswap_api_means_no_faceswap_mode(authed, cat, monkeypatch):
    monkeypatch.setattr(conftest, "FACESWAP", None)
    assert authed.c.get("/m/api/video/models").json()["faceswap"] is None
    r = form(authed, "/m/api/video/swap", {"consent": "true"}, {"video": ("c.mp4", conftest.MP4, "video/mp4")})
    assert r.status_code == 409 and not posted(authed, "/api/direct-gen/swap-jobs")


# ------------------------------------------------------------------------------------------------------- timing
def test_wan_reference_points_are_reproduced():
    ti, s2 = vt.TABLE["wan2.2-ti2v-5b"], vt.TABLE["wan2.2-s2v-14b"]
    assert abs(vt.wan_seconds(ti, 24, 640, 1152, 17)["total_s"] - 510) < 15            # Genjutsu: 24 steps = 510 s
    per_step = vt.wan_seconds(ti, 1, 832, 480, 121)["run_s"]
    assert 10 * 60 < per_step < 18 * 60                                                 # catalog note: ~14 min / step
    assert abs(vt.wan_seconds(s2, 4, 480, 832, 77)["total_s"] - 1812) < 5               # podcast pilot 16.10
    big = vt.wan_seconds(s2, 1, 1280, 720, 77)["run_s"]
    assert 20 * 60 < big < 30 * 60                                                      # 16.10: 1446 s per step
    assert all(t["kind"] == "estimate" and t["basis"] for t in vt.TABLE.values())     # not from the journal -> estimate


def test_video_is_honestly_slow_and_monotonic():
    ti = vt.TABLE["wan2.2-ti2v-5b"]
    small = vt.wan_seconds(ti, 20, 480, 272, vt.frames_for(16, 4, 2))["total_s"]
    big = vt.wan_seconds(ti, 20, 832, 480, vt.frames_for(16, 4, 5))["total_s"]
    assert 2 * 60 < small < 15 * 60 and big > 30 * 60 and big > small
    assert vt.frames_for(16, 4, 5) == 81 and vt.frames_for(16, 4, 1) == 17 and vt.frames_for(24, 8, 2) == 49


def test_faceswap_measured_from_the_real_journal_runs():
    h = vt.from_history(REAL_SWAPS)
    fs = h["faceswap-facefusion"]
    q = fs["presets"]["quality"]
    assert q["kind"] == "measured" and q["n"] == 2 and 1.6 < q["gen_fps"] < 1.7
    assert fs["presets"]["fast"]["kind"] == "estimate"
    total = vt.faceswap_seconds("quality", 823, fs)["total_s"]
    assert abs(total - (1341.19 + 1188.71) / 2) < 0.05 * 1265
    assert vt.FACESWAP["presets"]["quality"]["kind"] == "measured"                    # static table = same journal


def test_wan_job_in_journal_becomes_measured():
    job = swap_job("3" * 32, 60, 660, 690)
    job.update(kind="video", model="wan2.2-ti2v-5b", params={"steps": 20, "width": 640, "height": 1152, "frames": 17})
    t = vt.from_history([job])["wan2.2-ti2v-5b"]
    assert t["kind"] == "measured" and t["n"] == 1 and t["load_s"] == 60 and abs(t["s_per_step"] - 30) < 0.01


def test_live_journal_reaches_the_catalog(authed, cat, monkeypatch):
    monkeypatch.setattr(conftest, "JOB", REAL_SWAPS[0])
    fs = authed.c.get("/m/api/video/models").json()["faceswap"]
    assert fs["timing"]["presets"]["quality"]["n"] == 1 and "журнала" in fs["timing"]["presets"]["quality"]["basis"]


# ------------------------------------------------------------------------------------------------------- queue
T = datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc).timestamp()


def at(s):
    return datetime.fromtimestamp(T + s, timezone.utc).isoformat()


def vjob(jid, status, kind="video", model="wan2.2-ti2v-5b", progress=None, tl=(), pos=None, params=None, created=0):
    return {"job_id": jid, "kind": kind, "model": model, "status": status, "stage": status, "queue_position": pos,
            "progress": progress or {"kind": "none"}, "created_at": at(created), "elapsed_s": 0,
            "params": params or {"steps": 20, "width": 480, "height": 272, "frames": 33},
            "timeline": [{"stage": s, "at": at(t)} for s, t in tl]}


def test_video_steps_progress_is_real_and_never_100():
    j = vjob("r", "generating", progress={"kind": "steps", "value": 20, "max": 20, "percent": 100.0},
             tl=[("loading", 0), ("generating", 60)])
    u = queue_view([j], {}, now=T + 400, kinds={"video"})[0]["ui"]
    assert u["real"] and u["percent"] < 100 and u["phase"] == "декодирование и сборка" and u["unit"] == "шаг"


def test_faceswap_frames_are_real_but_assembly_keeps_it_below_frame_share():
    j = vjob("f", "generating", kind="faceswap", model="faceswap-facefusion",
             progress={"kind": "frames", "value": 800, "max": 823, "percent": 97.2}, params={"preset": "quality", "faces": 5},
             tl=[("loading", 0), ("generating", 23)])
    u = queue_view([j], vt.from_history(REAL_SWAPS), now=T + 520, kinds={"faceswap"})[0]["ui"]
    assert u["real"] and u["step"] == 800 and u["unit"] == "кадр" and u["percent"] < 60 and u["remaining_s"] > 600
    j["status"] = j["stage"] = "postprocessing"
    j["timeline"].append({"stage": "postprocessing", "at": at(520)})
    u = queue_view([j], {}, now=T + 10_000, kinds={"faceswap"})[0]["ui"]
    assert not u["real"] and u["percent"] <= 99 and u["phase"] == "проверка позы и сборка"


def test_video_loading_is_unknown_progress_and_estimate_caps_at_95_afterwards():
    j = vjob("l", "loading", kind="video", model="wan2.2-s2v-14b", tl=[("loading", 0)],
             params={"width": 480, "height": 320, "frames": 65})
    u = queue_view([j], {}, now=T + 10**6, kinds={"video"})[0]["ui"]
    assert u["indeterminate"] and u["percent"] is None and u["remaining_s"] is None and u["typical_s"] is None
    j["status"] = j["stage"] = "generating"
    u = queue_view([j], {}, now=T + 10**6, kinds={"video"})[0]["ui"]
    assert not u["real"] and u["percent"] <= 95 and u["remaining_s"] is None and "оценка" in u["label"]


def test_video_steps_eta_needs_a_measured_pace():
    j = vjob("r", "generating", progress={"kind": "steps", "value": 1, "max": 20, "percent": 5.0},
             tl=[("loading", 0), ("generating", 60)])
    assert queue_view([j], {}, now=T + 100, kinds={"video"})[0]["ui"]["remaining_s"] is None


def test_video_queue_counts_photo_jobs_ahead_and_lists_only_video_results(authed, cat, monkeypatch):
    photo = {**vjob("p", "generating", kind="photo", model="z-image-turbo", tl=[("generating", 0)]),
             "params": {"steps": 8, "width": 512, "height": 512}}
    q = vjob("q" * 32, "queued", pos=1)
    done_photo = {**conftest.JOB}
    jobs = [photo, q, done_photo, REAL_SWAPS[0]]
    view = queue_view(jobs, {}, now=T + 5, kinds={"photo", "video", "faceswap"}, done_kinds=vt.VIDEO_KINDS)
    ids = [j["job_id"] for j in view]
    assert ids == ["p", "q" * 32, REAL_SWAPS[0]["job_id"]]                  # finished photo not in the video list
    assert view[1]["ui"]["wait_s"] > 0 and view[2]["ui"]["state"] == "done"
    r = authed.c.get("/m/api/video/queue")
    assert r.status_code == 200 and all(j["kind"] in ("video", "faceswap") for j in r.json()["jobs"]
                                        if j["ui"]["state"] in ("done", "ended"))


# ------------------------------------------------------------------------------------------------------- submit
def test_ti2v_submit_shape_reaches_bossman(authed, cat):
    r = form(authed, "/m/api/video/jobs", {"model": "wan2.2-ti2v-5b", "prompt": "волны у скал", "negative": "текст",
                                           "duration": "2", "resolution": "480x272", "steps": "24", "seed": "7"},
             {"image": ("s.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 202, r.text
    sent = posted(authed)[-1]
    assert set(sent) == {"model", "prompt", "negative", "mode", "resolution", "duration", "steps", "seed", "image_b64"}
    assert (sent["model"], sent["prompt"], sent["mode"], sent["resolution"], sent["duration"], sent["steps"], sent["seed"]) == \
        ("wan2.2-ti2v-5b", "волны у скал", "DIRECT", "480x272", 2.0, 24, 7)
    assert base64.b64decode(sent["image_b64"]) == JPEG and "audio_b64" not in sent


def test_s2v_submit_has_image_audio_and_no_steps(authed, cat):
    r = form(authed, "/m/api/video/jobs", {"model": "wan2.2-s2v-14b", "prompt": "поёт", "duration": "6",
                                           "resolution": "480x320"},
             {"image": ("a.jpg", JPEG, "image/jpeg"), "audio": ("a.wav", WAV, "audio/wav")})
    assert r.status_code == 202, r.text
    sent = posted(authed)[-1]
    assert "steps" not in sent and sent["duration"] == 6.0
    assert base64.b64decode(sent["audio_b64"]) == WAV and base64.b64decode(sent["image_b64"]) == JPEG


@pytest.mark.parametrize("data,files,needle", [
    ({"model": "wan2.2-ti2v-5b", "steps": "41"}, {}, "от 1 до 40"),
    ({"model": "wan2.2-ti2v-5b", "duration": "6"}, {}, "не больше 5 с"),
    ({"model": "wan2.2-ti2v-5b", "duration": "0"}, {}, "больше 0"),
    ({"model": "wan2.2-ti2v-5b", "resolution": "480x270"}, {}, "кратна 16"),
    ({"model": "wan2.2-ti2v-5b", "resolution": "1296x720"}, {}, "до 1280"),
    ({"model": "wan2.2-ti2v-5b"}, {"audio": ("a.wav", WAV, "audio/wav")}, "не принимает звук"),
    ({"model": "wan2.2-ti2v-5b"}, {"image": ("a.gif", b"GIF89a....", "image/gif")}, "PNG или JPEG"),
    ({"model": "wan2.2-s2v-14b", "steps": "8"}, {"image": ("a.jpg", JPEG, "image/jpeg"),
                                                  "audio": ("a.wav", WAV, "audio/wav")}, "задано её графом"),
    ({"model": "wan2.2-s2v-14b"}, {"image": ("a.jpg", JPEG, "image/jpeg")}, "нужен звук"),
    ({"model": "wan2.2-s2v-14b"}, {"audio": ("a.wav", WAV, "audio/wav")}, "нужна стартовая картинка"),
    ({"model": "wan2.2-s2v-14b", "duration": "21"}, {"image": ("a.jpg", JPEG, "image/jpeg"),
                                                     "audio": ("a.wav", WAV, "audio/wav")}, "не больше 20 с"),
    ({"model": "wan21-vace-14b"}, {}, "VACE"),
    ({"model": "ltx-video"}, {}, "нет весов"),
])
def test_values_bossman_rejects_are_refused_before_sending(authed, cat, data, files, needle):
    base = {"prompt": "x", "duration": "2", "resolution": "480x320" if "s2v" in data["model"] else "480x272"}
    r = form(authed, "/m/api/video/jobs", {**base, **data}, files)
    assert r.status_code == 422, r.text
    assert needle in r.json()["detail"]["message"]
    assert not posted(authed)


def test_unknown_model_and_photo_model_refused(authed, cat):
    for mid in ("nope", "z-image-turbo"):
        r = form(authed, "/m/api/video/jobs", {"model": mid, "prompt": "x"})
        assert r.status_code == 404
    assert not posted(authed)


def test_json_body_refused_form_required(authed, cat):
    r = authed.post("/m/api/video/jobs", json={"model": "wan2.2-ti2v-5b", "prompt": "x"})
    assert r.status_code == 415 and not posted(authed)


def test_bossman_error_codes_come_back_in_russian(authed, cat, monkeypatch):
    monkeypatch.setattr(conftest, "FAIL_NEXT", [(409, {"code": "runtime_unreachable", "message": "ComfyUI is not reachable"})])
    r = form(authed, "/m/api/video/jobs", {"model": "wan2.2-ti2v-5b", "prompt": "x", "duration": "1",
                                           "resolution": "480x272"})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["message"].startswith("ComfyUI сейчас не запущен") and d["bossman"] == "ComfyUI is not reachable"


def test_csrf_required_for_video_submit(authed, cat):
    r = authed.c.post("/m/api/video/jobs", data={"model": "wan2.2-ti2v-5b", "prompt": "x"})
    assert r.status_code == 403 and not posted(authed)


# ------------------------------------------------------------------------------------------------------- face swap
def test_swap_submit_shape(authed, cat):
    r = form(authed, "/m/api/video/swap", {"preset": "quality", "frame": "16:9", "consent": "true"},
             [("video", ("c.mp4", conftest.MP4, "video/mp4")), ("faces", ("1.jpg", JPEG, "image/jpeg")),
              ("faces", ("2.png", conftest.PNG, "image/png"))])
    assert r.status_code == 202, r.text
    sent = posted(authed, "/api/direct-gen/swap-jobs")[-1]
    assert set(sent) == {"video_b64", "faces_b64", "preset", "frame", "consent"}
    assert sent["consent"] is True and sent["preset"] == "quality" and sent["frame"] == "16:9"
    assert base64.b64decode(sent["video_b64"]) == conftest.MP4
    assert [base64.b64decode(x) for x in sent["faces_b64"]] == [JPEG, conftest.PNG]


@pytest.mark.parametrize("data,files,code,needle", [
    ({"preset": "fast"}, [("video", ("c.mp4", b"v", "video/mp4")), ("faces", ("1.jpg", JPEG, "image/jpeg"))], 422, "согласие"),
    ({"preset": "turbo", "consent": "1"}, [("video", ("c.mp4", b"v", "video/mp4")), ("faces", ("1.jpg", JPEG, "image/jpeg"))],
     422, "пресета"),
    ({"consent": "1", "frame": "4:3"}, [("video", ("c.mp4", b"v", "video/mp4")), ("faces", ("1.jpg", JPEG, "image/jpeg"))],
     422, "16:9"),
    ({"consent": "1"}, [("faces", ("1.jpg", JPEG, "image/jpeg"))], 422, "исходное видео"),
    ({"consent": "1"}, [("video", ("c.mp4", b"v", "video/mp4"))], 422, "от 1 до 5"),
    ({"consent": "1"}, [("video", ("c.mp4", b"v", "video/mp4"))] + [("faces", (f"{i}.jpg", JPEG, "image/jpeg"))
                                                                    for i in range(5)] + [("faces", ("x.gif", b"GIF8", "image/gif"))],
     400, "форму"),
    ({"consent": "1"}, [("video", ("c.mp4", b"v", "video/mp4")), ("faces", ("1.gif", b"GIF89a", "image/gif"))], 422, "PNG"),
])
def test_swap_refusals_never_reach_bossman(authed, cat, data, files, code, needle):
    r = form(authed, "/m/api/video/swap", data, files)
    assert r.status_code == code, r.text
    assert needle in r.json()["detail"]["message"]
    assert not posted(authed, "/api/direct-gen/swap-jobs")


# ------------------------------------------------------------------------------------------------------- files + allowlist
def test_video_file_supports_range_for_iphone(authed, cat):
    f = authed.c.get(f"/m/api/video/jobs/{'c' * 32}/file", headers={"Range": "bytes=0-99"})
    assert f.status_code == 206 and f.content == conftest.MP4[:100]
    assert f.headers["content-range"] == f"bytes 0-99/{len(conftest.MP4)}" and f.headers["accept-ranges"] == "bytes"
    full = authed.c.get(f"/m/api/video/jobs/{'c' * 32}/file?download=1")
    assert full.status_code == 200 and full.content == conftest.MP4
    assert full.headers["content-disposition"].startswith("attachment") and full.headers["content-disposition"].endswith('.mp4"')
    assert authed.c.get("/m/api/video/jobs/..%2Fx/file").status_code == 404


@pytest.mark.parametrize("method,path", [("GET", "/m/api/video/genjutsu"), ("POST", "/m/api/video/jobs/" + "a" * 32 + "/retry"),
                                         ("GET", "/m/api/direct-gen/swap-jobs"), ("GET", "/m/api/video/vault")])
def test_video_allowlist_is_fixed(authed, cat, method, path):
    before = len(authed.fake.calls)
    r = authed.c.request(method, path, headers={"X-BM-CSRF": authed.csrf})
    assert r.status_code in (404, 405) and len(authed.fake.calls) == before


def test_video_endpoints_need_login(env, cat):
    for p in ("/m/api/video/models", "/m/api/video/queue", "/m/api/video/jobs"):
        assert env.c.get(p).status_code == 401


# ------------------------------------------------------------------------------------------------------- UI
def test_ui_has_video_tab_form_queue_and_honest_labels():
    html = (UI / "index.html").read_text(encoding="utf-8")
    for ident in ('data-tab="video"', 'id="view-video"', 'id="video-model"', 'id="video-time"', 'id="video-dur"',
                  'id="video-steps"', 'id="video-image"', 'id="video-audio"', 'id="swap-form"', 'id="swap-consent"',
                  'id="video-queue-list"', 'id="video-list"', 'id="vviewer-video"', "занимает видеокарту ПК"):
        assert ident in html, ident
    assert 'playsinline' in html
    js = (UI / "app.js").read_text(encoding="utf-8")
    for s in ("/video/models", "/video/queue", "/video/jobs", "/video/swap", "openVideo()", "(оценка)", "≈ осталось",
              "Math.min(99", "o.disabled = !m.available", "framesFor", "X-BM-CSRF"):
        assert s in js, s
    assert int(re.search(r"bossman-shell-v(\d+)", (UI / "sw.js").read_text(encoding="utf-8")).group(1)) >= 6
    css = (UI / "app.css").read_text(encoding="utf-8")
    assert "repeat(5, 1fr)" in css


def test_csp_allows_local_video_preview(authed):
    csp = authed.c.get("/m/").headers["content-security-policy"]
    assert "media-src 'self' blob:" in csp and "default-src 'self'" in csp


def test_reason_ru_keeps_russian_and_translates_english():
    assert video.reason_ru("нет весов") == "нет весов"
    assert video.reason_ru("something odd") == "недоступна (something odd)"
    assert video.reason_ru("") == ""
