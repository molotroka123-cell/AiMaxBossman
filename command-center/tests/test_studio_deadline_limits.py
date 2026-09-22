"""Boundary behaviour of the work-proportional deadline (d059ac60) and its absolute ceiling.

d059ac60 made the engine deadline grow with the work so a 720p/81-frame/50-step clip is not
killed at one hour. It grew without a roof: the catalog's own maxima (1280x1280, 81 frames,
50 steps) scale the budget ~17x, and the `30s` preset chains six segments — over four days of
wall clock from one request. Proportional must never be a way around an absolute limit.

Pure functions only: no engine, no GPU, no subprocess.
"""
from __future__ import annotations

import inspect
import itertools

import pytest

from bcc.studio import catalog
from bcc.studio.providers import sdcpp

WAN = "sdcpp:wan2.2-ti2v-5b"
REF = {"width": 832, "height": 480, "frames": 49, "steps": 20}


def _model(model_id=WAN):
    return next(m for m in catalog.load()["models"] if m["id"] == model_id)


@pytest.mark.parametrize("bogus", [0, -1, -1e9, float("nan"), float("inf"), float("-inf"),
                                   "1280", "", None, True, False, [], {}])
def test_bogus_dimensions_never_buy_wall_clock(bogus):
    """0, negatives, NaN, infinities, strings and wrong types must not raise and must not
    stretch the budget: they fall back to the reference dimension."""
    for field in ("width", "height", "steps", "frames"):
        scale = sdcpp.workload_scale({**REF, field: bogus})
        assert isinstance(scale, float) and scale == scale, (field, bogus, scale)
        assert 1.0 <= scale <= 1.0 + 1e-9, (field, bogus, scale)


def test_the_reference_and_smaller_work_keep_the_catalog_deadline():
    assert sdcpp.workload_scale(REF) == 1.0
    assert sdcpp.workload_scale({**REF, "width": 640, "height": 352, "frames": 17, "steps": 16}) == 1.0
    assert sdcpp.workload_scale({"width": 1024, "height": 1024, "steps": 8}) == 1.0  # images: no frames


def test_proportional_growth_survives_the_ceiling_for_the_owners_real_run():
    """Negative control for the ceiling: the run d059ac60 exists for (720p, 81 frames, 50 steps)
    must still get its proportional budget — the cap bounds runaway work, it does not undo the fix."""
    hq = {"width": 1280, "height": 704, "frames": 81, "steps": 50}
    assert 9.0 < sdcpp.workload_scale(hq) < 9.5
    proportional = _model()["deadline_seconds"] * sdcpp.workload_scale(hq) + 60
    assert sdcpp.segment_deadline_s(_model(), hq) == pytest.approx(proportional)
    assert proportional > _model()["deadline_seconds"] + 60      # genuinely longer than the catalog


def test_the_proportional_deadline_cannot_outgrow_the_absolute_ceiling(monkeypatch):
    """Overflow case: the catalog's own maxima, then absurd values on top of them."""
    model = _model()
    ceiling = sdcpp.max_segment_deadline_s()
    worst = {"width": 1280, "height": 1280, "frames": 81, "steps": 50}
    assert model["deadline_seconds"] * sdcpp.workload_scale(worst) + 60 > ceiling, "no overflow to cap"
    assert sdcpp.segment_deadline_s(model, worst) == ceiling
    for absurd in ({"width": 10 ** 9, "height": 10 ** 9, "frames": 10 ** 9, "steps": 10 ** 9},
                   {"width": 1e308, "height": 1e308, "frames": 1e308, "steps": 1e308}):
        assert sdcpp.segment_deadline_s(model, absurd) == ceiling


def test_a_chain_of_segments_cannot_multiply_past_the_job_ceiling():
    model = _model()
    heavy = {"width": 1280, "height": 1280, "frames": 81, "steps": 50}
    per_segment = sdcpp.segment_deadline_s(model, heavy)
    assert sdcpp.job_budget_s(model, heavy, 6) == sdcpp.max_job_deadline_s()
    assert sdcpp.job_budget_s(model, heavy, 6) < per_segment * 6, "six segments would exceed the day"
    # a single light segment keeps its own (small) budget: the ceiling is a roof, not a floor
    assert sdcpp.job_budget_s(model, REF, 1) == pytest.approx(model["deadline_seconds"] + 60)


def test_every_catalog_combination_stays_under_both_ceilings():
    """Property over the whole declared grid, not over the paths the implementation happens to take."""
    model = _model()
    settings = model["settings"]
    widths = (settings["width"]["min"], settings["width"]["max"])
    heights = (settings["height"]["min"], settings["height"]["max"])
    steps = (settings["steps"]["min"], settings["steps"]["max"])
    seg_max = max(p["segments"] for p in sdcpp.DURATION_PRESETS.values())
    for w, h, f, st in itertools.product(widths, heights, settings["frames"]["values"], steps):
        s = {"width": w, "height": h, "frames": f, "steps": st}
        assert sdcpp.segment_deadline_s(model, s) <= sdcpp.max_segment_deadline_s()
        for n in range(1, seg_max + 1):
            assert sdcpp.job_budget_s(model, s, n) <= sdcpp.max_job_deadline_s()


def test_the_ceilings_are_configurable_and_reject_nonsense(monkeypatch):
    monkeypatch.setenv(sdcpp.MAX_SEGMENT_DEADLINE_ENV, "120")
    monkeypatch.setenv(sdcpp.MAX_JOB_DEADLINE_ENV, "300")
    assert sdcpp.max_segment_deadline_s() == 120
    assert sdcpp.segment_deadline_s(_model(), {"width": 1280, "height": 704, "frames": 81, "steps": 50}) == 120
    assert sdcpp.job_budget_s(_model(), REF, 6) == 300
    for junk in ("0", "-5", "nan", "inf", "soon", ""):
        monkeypatch.setenv(sdcpp.MAX_SEGMENT_DEADLINE_ENV, junk)
        assert sdcpp.max_segment_deadline_s() == sdcpp.DEFAULT_MAX_SEGMENT_DEADLINE_S, junk


def test_an_explicit_owner_limit_outranks_the_computed_budget(tmp_path):
    """The owner's own number is sovereign: it wins over the proportional value, in both
    directions, and the ceiling is not used to overrule it."""
    model = _model("sdcpp:z-image-turbo")

    class Explicit(sdcpp.SdCppProvider):
        hard_timeout_s = 7.0

    prov = Explicit({"root": tmp_path, "bin": tmp_path / "bin", "manifest": {}}, tmp_path, model)
    assert prov.hard_timeout_s == 7.0
    assert prov._catalog_timeout_s == model["deadline_seconds"] + 60

    plain = sdcpp.SdCppProvider({"root": tmp_path, "bin": tmp_path / "bin", "manifest": {}},
                                tmp_path, model)
    assert plain.hard_timeout_s == plain._catalog_timeout_s


def test_the_studio_watchdog_uses_the_same_ceiling_as_the_provider():
    """runtime.process_claimed computes its own budget for sdcpp jobs. It multiplied the catalog
    deadline by segments x workload_scale with no ceiling at all, so the outer watchdog would have
    waited days on a job the provider is willing to kill. Both must read the same limit."""
    from bcc.studio import runtime

    source = inspect.getsource(runtime.process_claimed)
    assert "job_budget_s" in source, "the studio watchdog does not use the capped budget"
    assert "workload_scale" not in source, "the studio watchdog still scales without a ceiling"

    model = _model()
    heavy = {"length": "30s", "width": 1280, "height": 1280, "frames": 81, "steps": 50}
    assert sdcpp.job_budget_s(model, heavy, sdcpp.segments_for(heavy)) == sdcpp.max_job_deadline_s()
    uncapped = model["deadline_seconds"] * sdcpp.segments_for(heavy) * sdcpp.workload_scale(heavy)
    assert uncapped > 4 * 24 * 3600, uncapped        # over four days, which is what it used to grant
