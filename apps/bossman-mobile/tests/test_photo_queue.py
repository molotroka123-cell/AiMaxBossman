"""Photo queue: real sd-cli step progress when Bossman has it, honest time estimate otherwise (owner 17.10.2026)."""
import json
import pathlib
from datetime import datetime, timezone

import conftest
from bossmobile.timing import queue_view

UI = pathlib.Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
T0 = datetime(2026, 10, 17, 20, 0, 0, tzinfo=timezone.utc).timestamp()


def at(s):
    return datetime.fromtimestamp(T0 + s, timezone.utc).isoformat()


def job(jid, status, stage=None, *, model="qwen-image-edit-2509", steps=20, w=512, h=512, progress=None, tl=(), pos=None,
        elapsed=0.0, created=0):
    return {"job_id": jid, "kind": "photo", "model": model, "status": status, "stage": stage or status,
            "progress": progress or {"kind": "none"}, "queue_position": pos, "elapsed_s": elapsed, "created_at": at(created),
            "params": {"steps": steps, "width": w, "height": h}, "timeline": [{"stage": s, "at": at(t)} for s, t in tl]}


def by(view):
    return {j["job_id"]: j["ui"] for j in view}


def test_running_job_uses_real_steps_and_observed_pace():
    j = job("r", "generating", progress={"kind": "steps", "value": 5, "max": 20, "percent": 25.0},
            tl=[("queued", 0), ("loading", 0), ("generating", 100)], elapsed=200)
    u = by(queue_view([j], now=T0 + 200))["r"]
    assert u["real"] is True and u["step"] == 5 and u["steps"] == 20 and round(u["percent"]) == 25
    assert u["phase"] == "генерация" and abs(u["remaining_s"] - 15 * 20) < 1   # 100 s / 5 steps = 20 s/step


def test_loading_phase_is_unknown_progress_no_percent_no_remaining():
    j = job("l", "loading", tl=[("queued", 0), ("loading", 0)], elapsed=1e6)
    u = by(queue_view([j], now=T0 + 1e6))["l"]
    assert u["indeterminate"] is True and u["phase"] == "загрузка модели" and u["real"] is False
    assert u["percent"] is None and u["remaining_s"] is None and u["elapsed_s"] >= 1e6


def test_loading_shows_typical_time_only_for_a_measured_model():
    m = by(queue_view([job("m", "loading", model="z-image-turbo", tl=[("loading", 0)])], now=T0 + 5))["m"]
    e = by(queue_view([job("e", "loading", model="sdxl-base", tl=[("loading", 0)])], now=T0 + 5))["e"]
    assert m["typical_s"] and m["typical_s"] > 0 and e["typical_s"] is None


def test_steps_eta_comes_from_measured_pace_not_from_the_table():
    one = job("a", "generating", progress={"kind": "steps", "value": 1, "max": 20, "percent": 5.0},
              tl=[("loading", 0), ("generating", 100)])
    assert by(queue_view([one], now=T0 + 130))["a"]["remaining_s"] is None          # one step: no measured pace yet
    two = job("b", "generating", progress={"kind": "steps", "value": 4, "max": 20, "percent": 20.0},
              tl=[("loading", 0), ("generating", 100)])
    u = by(queue_view([two], now=T0 + 140))["b"]                                   # 40 s / 4 steps = 10 s/step
    assert abs(u["remaining_s"] - 160) < 1 and u["percent"] == 20.0


def test_estimate_used_up_is_unknown_not_one_second():
    j = job("g", "generating", tl=[("generating", 0)], elapsed=1e6)
    u = by(queue_view([j], now=T0 + 1e6))["g"]
    assert u["remaining_s"] is None and u["percent"] <= 95


def test_queue_wait_is_unknown_when_the_job_ahead_is_still_loading():
    r = job("r", "loading", tl=[("loading", 0)])
    q = job("q", "queued", pos=1, created=5)
    u = by(queue_view([r, q], now=T0 + 30))
    assert u["q"]["wait_s"] is None and u["q"]["total_s"] > 0


def test_last_step_is_decoding_and_capped_below_100():
    j = job("d", "generating", progress={"kind": "steps", "value": 20, "max": 20, "percent": 100.0},
            tl=[("generating", 0)])
    u = by(queue_view([j], now=T0 + 400))["d"]
    assert u["real"] and u["percent"] < 100 and u["phase"] == "декодирование"


def test_generating_without_step_info_falls_back_to_time_estimate():
    j = job("g", "generating", tl=[("generating", 0)], elapsed=30)
    u = by(queue_view([j], now=T0 + 30))["g"]
    assert u["real"] is False and 0 < u["percent"] <= 95 and u["remaining_s"] > 0


def test_queued_wait_accumulates_running_remainder_and_jobs_ahead():
    r = job("r", "generating", progress={"kind": "steps", "value": 10, "max": 20, "percent": 50.0},
            tl=[("generating", 0)], elapsed=100)
    q1 = job("q1", "queued", pos=1, created=5, w=512, h=512)
    q2 = job("q2", "queued", pos=2, created=6, w=512, h=512)
    u = by(queue_view([q2, q1, r], now=T0 + 100))
    assert u["q1"]["position"] == 1 and u["q2"]["position"] == 2
    assert u["q1"]["wait_s"] > 0 and u["q2"]["wait_s"] > u["q1"]["wait_s"] + u["q1"]["total_s"] - 1


def test_done_shows_elapsed_and_order_running_queued_done():
    d = job("d", "completed", elapsed=130.0, created=-50)
    v = queue_view([d, job("q", "queued", pos=1), job("r", "generating", tl=[("generating", 0)])], now=T0 + 5)
    assert [j["job_id"] for j in v] == ["r", "q", "d"]
    assert v[2]["ui"]["state"] == "done" and v[2]["ui"]["elapsed_s"] == 130.0


def test_video_jobs_are_not_in_the_photo_queue():
    j = {**job("v", "generating"), "kind": "video"}
    assert queue_view([j], now=T0) == []


def test_queue_endpoint_and_ui(authed, monkeypatch):
    r = authed.c.get("/m/api/photo/queue")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert jobs and jobs[0]["ui"]["state"] == "done" and "provenance" not in jobs[0]
    js = (UI / "app.js").read_text(encoding="utf-8")
    for s in ("/photo/queue", "${u.unit || unitDefault} ${u.step}/${u.steps}", "≈ осталось", "в очереди №", "готово за", "visibilitychange"):
        assert s in js
    assert 'id="photo-queue-list"' in (UI / "index.html").read_text(encoding="utf-8")
