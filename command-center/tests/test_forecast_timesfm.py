"""Opt-in TimesFM forecast surface (bcc.forecast.timesfm_local + /api/forecast).

CI has no torch: every test but the last uses a FAKE backend or the real worker's
failure path. The last test runs the real model and is skipped unless the owner's
isolated venv and weights are present.
"""
from __future__ import annotations

import asyncio
import importlib.util
import math
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from bcc.forecast import timesfm_local as tl
from bcc.forecast.timesfm_local import ForecastError, TimesFMService, validate_request

from .conftest import client_for, make_settings, start_app

SERIES = [float(10 + (i % 7)) for i in range(64)]


class FakeBackend:
    name = "fake"

    def __init__(self, *, delay: float = 0.0, malformed: bool = False):
        self.delay, self.malformed = delay, malformed
        self.calls: list[tuple[int, int]] = []
        self.killed = 0
        self.thread_ids: list[int] = []

    def predict(self, series, horizon, timeout):
        self.thread_ids.append(threading.get_ident())
        self.calls.append((len(series), horizon))
        if self.delay:
            time.sleep(self.delay)
        if self.malformed:
            return {"point": [float("nan")] * horizon, "quantiles": [[0.0] * 9] * horizon}
        last = series[-1]
        return {"point": [last] * horizon,
                "quantiles": [[last - 4 + i for i in range(9)] for _ in range(horizon)], "elapsed_ms": 1.0}

    def info(self):
        return {"loaded": bool(self.calls), "timesfm": "fake", "torch": None}

    def kill(self):
        self.killed += 1


def _model_dir(tmp_path: Path) -> Path:
    d = tmp_path / "models" / "timesfm" / "timesfm-2.5-200m-pytorch"
    d.mkdir(parents=True)
    (d / "model.safetensors").write_bytes(b"not-real-weights")
    return d


def _enabled_env(tmp_path: Path, **extra) -> dict:
    return {tl.ENV_ENABLE: "1", tl.ENV_MODEL_DIR: str(_model_dir(tmp_path)), **extra}


# ------------------------------------------------------------------ validation / bounds
@pytest.mark.parametrize("series,horizon,needle", [
    ("1,2,3", 5, "JSON array"),
    ([1.0] * (tl.MIN_INPUT - 1), 5, "at least"),
    ([1.0] * (tl.MAX_INPUT + 1), 5, "limited to"),
    ([1.0] * 20 + ["x"], 5, "series[20] is not a number"),
    ([1.0] * 20 + [True], 5, "series[20] is not a number"),
    ([1.0] * 20 + [float("nan")], 5, "finite"),
    ([1.0] * 20 + [float("inf")], 5, "finite"),
    ([1.0] * 20 + [1e13], 5, "finite"),
    ([1.0] * 20, 0, "between 1 and"),
    ([1.0] * 20, tl.MAX_HORIZON + 1, "between 1 and"),
    ([1.0] * 20, 3.5, "integer"),
    ([1.0] * 20, True, "integer"),
    ([1.0] * 20, None, "integer"),
])
def test_validation_rejects_bad_input_with_a_human_reason(series, horizon, needle):
    with pytest.raises(ForecastError) as err:
        validate_request(series, horizon)
    assert err.value.status == 422 and needle in err.value.message


def test_validation_accepts_the_edges():
    assert validate_request([1] * tl.MIN_INPUT, 1) == ([1.0] * tl.MIN_INPUT, 1)
    assert validate_request([0.5] * tl.MAX_INPUT, tl.MAX_HORIZON)[1] == tl.MAX_HORIZON


# ------------------------------------------------------------------ disabled / unavailable
def test_disabled_by_default_even_when_the_model_exists(tmp_path):
    svc = TimesFMService(tmp_path, env={tl.ENV_MODEL_DIR: str(_model_dir(tmp_path))}, backend=FakeBackend())
    st = svc.status()
    assert st["available"] is False and st["reason"].startswith("disabled")
    assert st["model"] == "google/timesfm-2.5-200m-pytorch" and st["license"] == "Apache-2.0"


def test_enabled_without_weights_says_model_not_found(tmp_path):
    svc = TimesFMService(tmp_path, env={tl.ENV_ENABLE: "1"}, backend=FakeBackend())
    assert svc.status()["reason"].startswith("model not found")


def test_enabled_with_weights_but_no_runtime_says_so(tmp_path, monkeypatch):
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(tl.importlib.util, "find_spec",
                        lambda name, *a: None if name == "timesfm" else real_find_spec(name, *a))
    svc = TimesFMService(tmp_path, env=_enabled_env(tmp_path))
    assert "timesfm is not installed" in svc.status()["reason"]
    svc2 = TimesFMService(tmp_path / "b", env=_enabled_env(tmp_path / "b", **{tl.ENV_PYTHON: str(tmp_path / "no.exe")}))
    assert "does not exist" in svc2.status()["reason"]


async def test_unavailable_forecast_is_503_and_never_touches_a_backend(tmp_path):
    fake = FakeBackend()
    svc = TimesFMService(tmp_path, env={}, backend=fake)
    with pytest.raises(ForecastError) as err:
        await svc.forecast(SERIES, 5)
    assert err.value.status == 503 and err.value.message.startswith("unavailable: disabled")
    assert fake.calls == []


# ------------------------------------------------------------------ schema / behaviour with a fake model
async def test_response_schema(tmp_path):
    fake = FakeBackend()
    svc = TimesFMService(tmp_path, env=_enabled_env(tmp_path), backend=fake)
    out = await svc.forecast(SERIES, 7)
    assert out["model"] == tl.MODEL_ID and out["revision"] == tl.MODEL_REVISION and out["license"] == "Apache-2.0"
    assert out["horizon"] == 7 and len(out["point"]) == 7
    assert list(out["quantiles"]) == ["0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9"]
    assert all(len(v) == 7 for v in out["quantiles"].values())
    assert out["quantiles"]["0.1"][0] <= out["quantiles"]["0.9"][0]
    assert out["input_points"] == 64 and out["context_used"] == 64 and "not advice" in out["disclaimer"]
    assert svc.status()["runtime"]["loaded"] is True


async def test_long_input_is_cut_to_the_compiled_context(tmp_path):
    fake = FakeBackend()
    svc = TimesFMService(tmp_path, env=_enabled_env(tmp_path), backend=fake)
    out = await svc.forecast([float(i) for i in range(3000)], 3)
    assert fake.calls == [(tl.MAX_CONTEXT, 3)]
    assert out["input_points"] == 3000 and out["context_used"] == tl.MAX_CONTEXT
    assert out["point"] == [2999.0] * 3            # the LAST points were kept, not the first


async def test_forecast_runs_off_the_event_loop(tmp_path):
    fake = FakeBackend(delay=0.6)
    svc = TimesFMService(tmp_path, env=_enabled_env(tmp_path), backend=fake)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    t = asyncio.create_task(ticker())
    try:
        await svc.forecast(SERIES, 5)
    finally:
        t.cancel()
    assert fake.thread_ids and fake.thread_ids[0] != threading.get_ident()
    assert ticks >= 20, f"event loop starved during the forecast: only {ticks} ticks in 0.6 s"


async def test_timeout_is_504_and_kills_the_backend(tmp_path):
    fake = FakeBackend(delay=3.0)
    svc = TimesFMService(tmp_path, env=_enabled_env(tmp_path, **{tl.ENV_TIMEOUT: "1"}), backend=fake)
    t0 = time.perf_counter()
    with pytest.raises(ForecastError) as err:
        await svc.forecast(SERIES, 5)
    assert err.value.status == 504 and time.perf_counter() - t0 < 2.9
    assert fake.killed == 1 and "timeout" in svc.status()["last_error"]


async def test_malformed_model_output_is_502_not_garbage(tmp_path):
    svc = TimesFMService(tmp_path, env=_enabled_env(tmp_path), backend=FakeBackend(malformed=True))
    with pytest.raises(ForecastError) as err:
        await svc.forecast(SERIES, 4)
    assert err.value.status == 502


@pytest.mark.skipif(importlib.util.find_spec("timesfm") is not None,
                    reason="timesfm is installed in this interpreter, so the worker would really load")
async def test_real_worker_process_reports_why_it_cannot_load(tmp_path):
    """The real subprocess protocol, failure path: this interpreter has no timesfm."""
    env = _enabled_env(tmp_path, **{tl.ENV_PYTHON: sys.executable})
    svc = TimesFMService(tmp_path, env=env)
    with pytest.raises(ForecastError) as err:
        await svc.forecast(SERIES, 3)
    assert err.value.status == 503 and "worker failed to load the model" in err.value.message
    assert "timesfm" in err.value.message.lower() or "numpy" in err.value.message.lower()
    assert svc.status()["runtime"]["loaded"] is False


# ------------------------------------------------------------------ HTTP surface
async def test_api_disabled_then_enabled_with_fake(tmp_path, monkeypatch):
    for name in (tl.ENV_ENABLE, tl.ENV_MODEL_DIR, tl.ENV_PYTHON, tl.ENV_TIMEOUT):
        monkeypatch.delenv(name, raising=False)
    app, svc = await start_app(make_settings(tmp_path))
    try:
        async with client_for(app, svc) as c:
            st = (await c.get("/api/forecast/status")).json()
            assert st["available"] is False and st["reason"].startswith("disabled")
            r = await c.post("/api/forecast", json={"series": SERIES, "horizon": 5})
            assert r.status_code == 503 and r.json()["available"] is False
            assert r.json()["error"].startswith("unavailable: disabled")

            fake = FakeBackend()
            app.state.timesfm = TimesFMService(tmp_path, env=_enabled_env(tmp_path), backend=fake)
            r = await c.post("/api/forecast", json={"series": SERIES, "horizon": 5})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["model"] == tl.MODEL_ID and len(body["point"]) == 5 and len(body["quantiles"]) == 9

            assert (await c.post("/api/forecast", json={"series": SERIES, "horizon": 999})).status_code == 422
            assert (await c.post("/api/forecast", json={"series": [1] * 5000, "horizon": 3})).status_code == 422
            assert (await c.post("/api/forecast", json=[1, 2, 3])).status_code == 422
            assert (await c.post("/api/forecast", content=b"{not json",
                                 headers={"content-type": "application/json"})).status_code == 422
            assert len(fake.calls) == 1                  # rejected requests never reached the model
    finally:
        await svc.stop()


async def test_api_requires_the_owner_token(tmp_path):
    app, svc = await start_app(make_settings(tmp_path))
    try:
        async with client_for(app, svc) as c:
            c.headers.clear()
            r = await c.post("/api/forecast", json={"series": SERIES, "horizon": 5})
            assert r.status_code in (401, 403)
    finally:
        await svc.stop()


# ------------------------------------------------------------------ local only: the real model
_REAL_PY = os.environ.get(tl.ENV_PYTHON, "")
_REAL_DIR = os.environ.get(tl.ENV_MODEL_DIR, "")
_REAL_READY = bool(_REAL_PY and Path(_REAL_PY).is_file() and _REAL_DIR
                   and (Path(_REAL_DIR) / "model.safetensors").is_file())


@pytest.mark.skipif(not _REAL_READY,
                    reason="local-only: needs BOSSMAN_TIMESFM_PYTHON (isolated TimesFM venv) and "
                           "BOSSMAN_TIMESFM_MODEL_DIR with the downloaded TimesFM 2.5 weights")
async def test_real_timesfm_forecast_local_only(tmp_path):
    svc = TimesFMService(tmp_path, env={tl.ENV_ENABLE: "1", tl.ENV_PYTHON: _REAL_PY, tl.ENV_MODEL_DIR: _REAL_DIR,
                                        tl.ENV_TIMEOUT: "300"})
    try:
        y = [50 + 10 * math.sin(i / 4.0) for i in range(256)]
        out = await svc.forecast(y, 12)
        truth = [50 + 10 * math.sin(i / 4.0) for i in range(256, 268)]
        mae = sum(abs(a - b) for a, b in zip(out["point"], truth)) / 12
        assert mae < 2.0, f"a clean sine should be easy, MAE={mae:.3f}"
        q = out["quantiles"]
        assert all(q["0.1"][i] <= q["0.5"][i] <= q["0.9"][i] for i in range(12))
        assert out["backend"] == "subprocess" and out["package"]["timesfm"]
    finally:
        svc.close()
