"""Phone video queue reads Bossman's live ``progress_detail`` (bcc.direct_gen.video_progress, 20.10).

Before: a Wan job said «загрузка модели» until sd-cli printed its first step bar (41 min on the A14B run of 19.10).
Now the card shows the engine's real stage, steps and an ETA whose source is named; unknown stays unknown.
"""
from __future__ import annotations

from datetime import datetime, timezone

import conftest
from bossmobile import video_timing as vt
from bossmobile.timing import queue_view

T = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc).timestamp()


def at(s):
    return datetime.fromtimestamp(T + s, timezone.utc).isoformat()


def detail(stage, step=None, total=30, pct=0.0, eta=None, source=None, scope=None, known=True):
    return {"stage": stage, "phase": stage, "known": known, "step": step, "total": total, "steps": total, "pct": pct,
            "eta_s": eta, "eta_source": source, "eta_scope": scope, "s_per_it": None, "basis_n": None,
            "source": "engine output", "cap": 0.97}


def vjob(status, d, progress=None):
    return {"job_id": "v" * 32, "kind": "video", "model": "wan2.2-ti2v-5b", "status": status, "stage": status,
            "progress": progress or {"kind": "none"}, "progress_detail": d, "created_at": at(0), "started_at": at(0),
            "elapsed_s": 0, "params": {"steps": 30, "width": 480, "height": 272, "frames": 17},
            "timeline": [{"stage": "queued", "at": at(0)}, {"stage": "loading", "at": at(0)}]}


def ui(job, now=600):
    return queue_view([job], {}, now=T + now, kinds={"video"})[0]["ui"]


def test_sampling_shows_real_steps_and_the_tqdm_eta_with_its_source():
    u = ui(vjob("generating", detail("sample", 11, pct=34.3, eta=596.0, source="tqdm", scope="sampling")))
    assert u["real"] is True and u["percent"] == 34.3 and (u["step"], u["steps"], u["unit"]) == (11, 30, "шаг")
    assert u["phase"] == "генерация" and u["remaining_s"] == 596.0
    assert u["label"] == "осталось — по счётчику движка, без сборки кадров" and u["eta_source"] == "tqdm"


def test_stage_names_follow_the_engine_not_the_job_stage():
    # the job record still says "loading" while the engine already encodes the prompt: the engine wins
    u = ui(vjob("loading", detail("encode", 0, pct=2.9)))
    assert u["phase"] == "разбор промпта" and u["real"] is True and u["remaining_s"] is None
    assert u["label"] == "осталось неизвестно"
    u = ui(vjob("generating", detail("decode", 30, pct=83.4)))
    assert u["phase"] == "сборка кадров (VAE)" and u["step"] is None and u["percent"] == 83.4
    u = ui(vjob("postprocessing", detail("verify", 30, pct=97.0, eta=0.0, source="done", scope="total")))
    assert u["phase"] == "проверка файла" and u["percent"] == 97.0


def test_loading_stays_indeterminate_unless_timing_medians_exist():
    u = ui(vjob("loading", detail("load", 0, pct=0.0)), now=300)
    assert u["indeterminate"] and u["percent"] is None and u["remaining_s"] is None and u["phase"] == "загрузка модели"
    u = ui(vjob("loading", detail("load", 0, eta=900.0, source="timings_median", scope="total")), now=300)
    assert u["indeterminate"] and u["remaining_s"] is None and u["typical_s"] == 1200.0     # «обычно ≈ 20 мин»


def test_unknown_or_broken_detail_falls_back_to_the_old_honest_view():
    j = vjob("loading", detail("sample", 3, known=False))
    assert ui(j)["indeterminate"] is True                         # known False: ignored
    j = vjob("generating", detail("sample", 3, pct="x", eta=-5, source="tqdm"))
    u = ui(j)
    assert u["percent"] == 0.0 and u["remaining_s"] is None and u["label"] == "осталось неизвестно"
    j = vjob("generating", "garbage", progress={"kind": "steps", "value": 5, "max": 20, "percent": 25.0})
    assert ui(j)["step"] == 5 and ui(j)["label"] == "по шагам генератора"      # old path untouched


def test_queue_payload_carries_progress_detail_to_the_phone(authed, monkeypatch):
    running = vjob("generating", detail("sample", 11, pct=34.3, eta=596.0, source="tqdm", scope="sampling"))
    monkeypatch.setattr(conftest, "JOB", running)                 # the fake Bossman lists JOB (+ a copy) as its jobs
    r = authed.c.get("/m/api/video/queue")
    assert r.status_code == 200, r.text
    job = next(j for j in r.json()["jobs"] if j["job_id"] == "v" * 32)
    assert job["progress_detail"]["stage"] == "sample" and job["progress_detail"]["eta_source"] == "tqdm"
    assert job["ui"]["step"] == 11 and job["ui"]["remaining_s"] == 596.0 and job["ui"]["phase"] == "генерация"
    jobs = authed.c.get("/m/api/video/jobs").json()["jobs"]
    assert next(j for j in jobs if j["job_id"] == "v" * 32)["progress_detail"]["pct"] == 34.3


def test_stage_labels_cover_every_engine_stage():
    assert set(vt.STAGE_RU) == {"load", "encode", "sample", "decode", "save", "verify"}
