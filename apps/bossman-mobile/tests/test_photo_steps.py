"""Photo section: per-model steps range + honest generation-time data (owner 17.10.2026)."""
import json
import pathlib

import pytest

import conftest
from bossmobile.timing import STEP_MIN, TABLE, estimate_seconds, from_history, step_range, timing_for

UI = pathlib.Path(__file__).resolve().parents[1] / "bossmobile" / "ui"

# default_steps / max_steps exactly as Bossman's direct-gen catalog (command-center/bcc/direct_gen/sdcli.py) reports them
CATALOG = {"z-image-turbo": (8, 20), "flux1-schnell": (4, 12), "flux2-klein-4b": (4, 12), "sdxl-base": (25, 60),
           "epicrealism-xl": (25, 60), "qwen-image-2.1": (20, 50), "qwen-image-2.1-uc": (20, 50),
           "qwen-image-2.1-uc-heretic": (20, 50), "qwen-image-viggle-turbo": (6, 12), "qwen-image-2.1-uc-edit": (20, 50),
           "qwen-image-edit-2509": (20, 50)}


def model(mid, d, mx):
    return {"id": mid, "label": mid, "kind": "photo", "modes": ["T2I"], "available": True, "reason": "",
            "requires_image": False, "accepts_image": False, "default_steps": d, "max_steps": mx,
            "default_resolution": "512x512", "min_side": 256, "max_side": 1536, "side_multiple": 16}


@pytest.fixture
def full(monkeypatch):
    monkeypatch.setattr(conftest, "DG_MODELS", {"models": [model(k, *v) for k, v in CATALOG.items()]})


def test_every_model_has_a_steps_range_and_a_time_model(authed, full):
    ms = authed.c.get("/m/api/photo/models").json()["models"]
    assert {m["id"] for m in ms} == set(CATALOG)
    for m in ms:
        d, mx = CATALOG[m["id"]]
        assert 1 <= m["min_steps"] <= m["default_steps"] == d <= m["max_steps"] == mx, m["id"]
        t = m["timing"]
        assert t["kind"] in ("measured", "estimate") and t["load_s"] > 0 and t["s_per_step"] > 0 and t["basis"], m["id"]
    by = {m["id"]: m for m in ms}
    # distilled / turbo 4..12, SDXL class 15..60, Qwen 10..50
    assert (by["flux1-schnell"]["min_steps"], by["flux1-schnell"]["max_steps"]) == (2, 12)
    assert (by["qwen-image-viggle-turbo"]["min_steps"], by["qwen-image-viggle-turbo"]["max_steps"]) == (4, 12)
    assert (by["sdxl-base"]["min_steps"], by["sdxl-base"]["max_steps"]) == (15, 60)
    assert by["qwen-image-2.1"]["min_steps"] == 10


def test_table_and_ranges_cover_the_whole_catalog():
    assert set(TABLE) == set(CATALOG) == set(STEP_MIN)
    measured = {k for k, v in TABLE.items() if v["kind"] == "measured"}
    assert {"z-image-turbo", "flux1-schnell", "qwen-image-2.1-uc-edit", "qwen-image-edit-2509"} <= measured
    assert all(TABLE[k]["kind"] == "estimate" and TABLE[k]["basis"] for k in set(TABLE) - measured)


def test_unknown_model_gets_range_and_labelled_estimate():
    r = step_range({"id": "new-model", "default_steps": 30, "max_steps": 40})
    assert r == {"min_steps": 15, "default_steps": 30, "max_steps": 40}
    t = timing_for("new-model")
    assert t["kind"] == "estimate" and "нет замеров" in t["basis"]


def test_time_formula_scales_with_steps_and_pixels():
    t = {"load_s": 10.0, "s_per_step": 2.0}
    assert estimate_seconds(t, 8, 512, 512) == 26.0
    assert estimate_seconds(t, 16, 512, 512) == 42.0
    assert estimate_seconds(t, 8, 1024, 1024) == 74.0


def job(model_id, steps, w, h, load, gen, status="completed"):
    base = "2026-10-17T10:00:{:06.3f}+00:00"
    return {"kind": "photo", "model": model_id, "status": status, "params": {"steps": steps, "width": w, "height": h},
            "timeline": [{"stage": "queued", "at": base.format(0)}, {"stage": "loading", "at": base.format(0)},
                         {"stage": "generating", "at": base.format(load)},
                         {"stage": "postprocessing", "at": base.format(load + gen)}]}


def test_history_measurement_overrides_table_and_ignores_unfinished():
    h = from_history([job("z-image-turbo", 8, 512, 512, 14, 16), job("z-image-turbo", 4, 1024, 1024, 20, 32),
                      job("z-image-turbo", 8, 512, 512, 1, 1, status="cancelled"),
                      {"kind": "video", "model": "wan", "status": "completed"}])
    t = h["z-image-turbo"]
    assert t["kind"] == "measured" and t["n"] == 2 and t["load_s"] == 17.0 and t["s_per_step"] == 2.0
    assert timing_for("z-image-turbo", h) is not TABLE["z-image-turbo"] and timing_for("z-image-turbo", h)["s_per_step"] == 2.0
    assert "wan" not in h


def test_live_history_reaches_the_model_list(authed, full, monkeypatch):
    j = job("flux1-schnell", 4, 512, 512, 30, 20)
    monkeypatch.setattr(conftest, "JOB", {**conftest.JOB, **j})
    by = {m["id"]: m for m in authed.c.get("/m/api/photo/models").json()["models"]}
    assert by["flux1-schnell"]["timing"]["load_s"] == 30.0 and by["flux1-schnell"]["timing"]["s_per_step"] == 5.0
    assert by["flux1-schnell"]["timing"]["n"] == 1


@pytest.mark.parametrize("mid", sorted(CATALOG))
def test_chosen_steps_reach_bossman_for_every_model(authed, full, mid):
    d, mx = CATALOG[mid]
    want = max(1, d - 1)
    r = authed.post("/m/api/photo/jobs", json={"model": mid, "prompt": "x", "resolution": "512x512", "steps": want})
    assert r.status_code == 202, r.text
    sent = json.loads([c for c in authed.fake.calls if c["path"] == "/api/direct-gen/jobs" and c["method"] == "POST"][-1]["body"])
    assert sent["steps"] == want and sent["model"] == mid


def test_steps_above_model_limit_refused_in_russian(authed, full):
    r = authed.post("/m/api/photo/jobs", json={"model": "flux1-schnell", "prompt": "x", "steps": 30})
    assert r.status_code == 422 and "от 1 до 12" in json.dumps(r.json(), ensure_ascii=False)
    assert not [c for c in authed.fake.calls if c["path"] == "/api/direct-gen/jobs" and c["method"] == "POST"]


def test_ui_has_visible_steps_control_and_time_box():
    html = (UI / "index.html").read_text(encoding="utf-8")
    form = html[html.index('id="photo-form"'):html.index('id="photo-go"')]
    steps_at = form.index('id="photo-steps"')
    # steps control is in the main form, not hidden inside the collapsed «Дополнительно»
    assert form.count("<details", 0, steps_at) == form.count("</details>", 0, steps_at)
    for ident in ("steps-minus", "steps-plus", "photo-time", "models-table"):
        assert f'id="{ident}"' in form
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "по замеру" in js and "оценка" in js and "showTime()" in js and "≈" in js
