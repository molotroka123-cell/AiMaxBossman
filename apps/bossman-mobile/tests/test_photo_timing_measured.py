"""ETA from Bossman's measured timing log (owner 19.10): medians per model, cold vs warm load, honest labels."""
from datetime import datetime, timezone

import conftest
from bossmobile.timing import (FALLBACK, MIN_SAMPLES, TABLE, estimate_seconds, from_history, from_measurements,
                               label_for, merge_rows, queue_view, timing_for, warm_load_s, warm_models)

T0 = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def row(model="z-image-turbo", load=40.0, gen=20.0, *, warm=False, steps=10, w=512, h=512, status="completed", jid=None):
    return {"job_id": jid or f"{model}-{load}-{gen}-{warm}", "model": model, "kind": "photo", "status": status,
            "steps": steps, "width": w, "height": h, "load": "warm" if warm else "cold", "load_s": load, "generate_s": gen}


def test_medians_not_means_cold_and_warm_apart():
    # one Defender-contended 697 s cold load must not drag the estimate (median, not mean)
    h = from_measurements([row(load=40), row(load=44), row(load=697), row(load=2, warm=True), row(load=3, warm=True)])
    t = h["z-image-turbo"]
    assert t["load_s"] == 44.0 and t["load_s_warm"] == 2.5 and t["n"] == 5 and t["n_cold"] == 3 and t["n_warm"] == 2
    assert t["s_per_step"] == 2.0 and t["label"] == "по замеру 5 генераций" and t["kind"] == "measured"


def test_steps_and_resolution_scale_the_estimate():
    t = from_measurements([row(gen=20, steps=10), row(gen=80, steps=10, w=1024, h=1024), row(gen=40, steps=20)])["z-image-turbo"]
    assert t["s_per_step"] == 2.0                                   # 2 s/step at 512x512 for all three
    assert estimate_seconds(t, 10, 512, 512) == 40.0 + 20.0
    assert estimate_seconds(t, 20, 1024, 1024) == 40.0 + 20 * 2.0 * 4
    assert estimate_seconds(t, 10, 512, 512, warm=True) == max(2.0, 0.1 * 40.0) + 20.0   # no warm run yet: ≈10 %


def test_fewer_than_three_runs_say_approximately():
    assert MIN_SAMPLES == 3
    two = from_measurements([row(load=30), row(load=50)])["z-image-turbo"]
    assert two["label"] == "примерно" and two["load_s"] == 40.0
    assert timing_for("z-image-turbo", {"z-image-turbo": two})["label"] == "примерно"
    # no own run at all: the old table / fallback numbers, also «примерно»
    assert timing_for("sdxl-base")["label"] == "примерно" and timing_for("sdxl-base")["load_s"] == TABLE["sdxl-base"]["load_s"]
    assert timing_for("brand-new")["load_s"] == FALLBACK["load_s"] and timing_for("brand-new")["label"] == "примерно"
    # the 17.10 table entry from 2 runs is real but still too few
    assert timing_for("z-image-turbo")["label"] == "примерно"
    three = from_measurements([row(load=30), row(load=50), row(load=40)])["z-image-turbo"]
    assert three["label"] == "по замеру 3 генераций"
    assert label_for("measured", 21) == "по замеру 21 генерации" and label_for("estimate", 50) == "примерно"


def test_only_warm_runs_keep_the_table_cold_load_and_failed_runs_are_ignored():
    t = from_measurements([row(load=1, warm=True), row(load=2, warm=True), row(load=1.5, warm=True),
                           row(load=999, status="failed"), row(load=5, gen=0)])["z-image-turbo"]
    assert t["load_s"] == TABLE["z-image-turbo"]["load_s"] and t["load_s_warm"] == 1.5 and t["n"] == 3
    assert warm_load_s(t) == 1.5


def test_log_rows_win_over_history_and_old_jobs_still_count():
    def job(jid, load, gen, timing=None):
        base = "2026-10-17T10:00:{:06.3f}+00:00"
        j = {"job_id": jid, "kind": "photo", "model": "z-image-turbo", "status": "completed",
             "params": {"steps": 10, "width": 512, "height": 512},
             "timeline": [{"stage": "loading", "at": base.format(0)}, {"stage": "generating", "at": base.format(load)},
                          {"stage": "postprocessing", "at": base.format(load + gen)}]}
        if timing:
            j["timing"] = timing
        return j
    log = [row(load=10, jid="a")]
    jobs = [job("a", 50, 20), job("b", 30, 20), job("c", 20, 20, timing={**row(load=3, warm=True), "job_id": None})]
    rows = merge_rows(log, jobs)
    assert [r["job_id"] for r in rows] == ["a", "b", "c"] and rows[0]["load_s"] == 10
    assert rows[2]["load"] == "warm"                                # Bossman's own timing block beats the timestamps
    h = from_history(jobs, log)["z-image-turbo"]
    assert h["n"] == 3 and h["load_s"] == 20.0 and h["load_s_warm"] == 3.0


def at(s):
    return datetime.fromtimestamp(T0 + s, timezone.utc).isoformat()


def qjob(jid, status, pos=None, model="z-image-turbo", image=False):
    return {"job_id": jid, "kind": "photo", "model": model, "status": status, "stage": status, "queue_position": pos,
            "progress": {"kind": "none"}, "created_at": at(0), "params": {"steps": 10, "width": 512, "height": 512},
            "inputs": {"image": 5} if image else {}, "timeline": [{"stage": status, "at": at(0)}]}


def test_queue_run_gets_the_warm_load_only_for_consecutive_same_model_jobs():
    hist = from_measurements([row(load=40), row(load=40), row(load=40), row(load=2, warm=True)])
    jobs = [qjob("r", "loading"), qjob("q1", "queued", 1), qjob("q2", "queued", 2), qjob("q3", "queued", 3, model="flux1-schnell"),
            qjob("q4", "queued", 4), qjob("q5", "queued", 5, image=True)]
    u = {j["job_id"]: j["ui"] for j in queue_view(jobs, hist, now=T0 + 5, warm=None)}
    assert u["q1"]["warm"] and u["q2"]["warm"] and u["q1"]["total_s"] == 2.0 + 20.0
    assert not u["q3"]["warm"] and not u["q4"]["warm"]               # flux in between: z must be loaded again
    assert not u["q5"]["warm"]                                       # a reference image = other engine flags
    assert u["q1"]["label"] == "по замеру 4 генераций, модель уже загружена"
    assert u["q4"]["total_s"] == 40.0 + 20.0 and u["q4"]["label"] == "по замеру 4 генераций"
    # without a resident engine (default before the live proof) every job is a cold load
    cold = {j["job_id"]: j["ui"] for j in queue_view(jobs, hist, now=T0 + 5)}
    assert not any(cold[k]["warm"] for k in ("q1", "q2", "q4")) and cold["q1"]["total_s"] == 60.0


def test_warm_models_from_bossman_status():
    assert warm_models({}) == frozenset() and warm_models({"resident": None}) == frozenset()
    assert warm_models({"resident": {"mode": "auto"}}) is None and warm_models({"resident": {"mode": "on"}}) is None
    assert warm_models({"resident": {"mode": "auto-if-proven", "proven_models": ["z-image-turbo"]}}) == {"z-image-turbo"}
    assert warm_models({"resident": {"mode": "off"}}) == frozenset()


def test_endpoints_use_the_timing_log(authed, monkeypatch):
    rows = [row("z-image-turbo", load=12, gen=10, steps=5, jid=f"x{i}") for i in range(4)]
    monkeypatch.setattr(conftest, "TIMINGS", {"records": rows, "summary": {}, "min_samples": 3})
    by = {m["id"]: m for m in authed.c.get("/m/api/photo/models").json()["models"]}
    t = by["z-image-turbo"]["timing"]
    assert t["load_s"] == 12.0 and t["s_per_step"] == 2.0 and t["label"] == "по замеру 4 генераций"
    q = authed.c.get("/m/api/photo/queue")
    assert q.status_code == 200
    assert any(c["path"] == "/api/direct-gen/timings" for c in authed.fake.calls)
