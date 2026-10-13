"""Opt-in local time-series forecasting with Google Research TimesFM 2.5 (200M, CPU).

Nothing here runs unless the owner turns it on. Available only when ALL hold:
  * ``BOSSMAN_TIMESFM=1`` in the backend's environment;
  * the model directory exists and holds ``model.safetensors``
    (``BOSSMAN_TIMESFM_MODEL_DIR``, default ``<data_dir>/models/timesfm/timesfm-2.5-200m-pytorch``);
  * a runtime exists: either ``BOSSMAN_TIMESFM_PYTHON`` points at the isolated TimesFM
    interpreter (recommended; the model then lives in a separate worker process), or the
    ``timesfm`` package is importable in this interpreter (extra ``[forecast]``).

The model is loaded lazily on the first forecast, never at boot. Every forecast runs off the
event loop (a worker thread talking to a worker process, or a worker thread around the
in-process model), one at a time, with a hard timeout. Inputs and horizon are bounded.

Weights: google/timesfm-2.5-200m-pytorch, Apache-2.0 (checked 2026-10-13). TimesFM 3.0
weights are NON-commercial (timesfm-non-commercial-license-v1.0) and are deliberately not
supported here.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

MODEL_ID = "google/timesfm-2.5-200m-pytorch"
MODEL_REVISION = "1d952420fba87f3c6dee4f240de0f1a0fbc790e3"   # HF commit verified 2026-10-13
MODEL_LICENSE = "Apache-2.0"
QUANTILES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)

ENV_ENABLE = "BOSSMAN_TIMESFM"
ENV_MODEL_DIR = "BOSSMAN_TIMESFM_MODEL_DIR"
ENV_PYTHON = "BOSSMAN_TIMESFM_PYTHON"
ENV_TIMEOUT = "BOSSMAN_TIMESFM_TIMEOUT_S"

MIN_INPUT = 16          # shorter histories are not worth a 200M model; use a naive rule instead
MAX_INPUT = 4096        # bounded request size; the model only looks at the last MAX_CONTEXT anyway
MAX_CONTEXT = 1024      # compiled context (CPU latency/RAM measured at this size)
MAX_HORIZON = 256
MAX_ABS_VALUE = 1e12
DEFAULT_TIMEOUT_S = 120.0
WORKER_START_TIMEOUT_S = 180.0

DISCLAIMER = ("Statistical forecast from a pretrained model; not advice and not a guarantee. "
              "Check the quantile band, not only the point.")


class ForecastError(Exception):
    """Carries the HTTP status the API should answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def validate_request(series: Any, horizon: Any) -> tuple[list[float], int]:
    if not isinstance(series, list):
        raise ForecastError(422, "series must be a JSON array of numbers")
    if len(series) < MIN_INPUT:
        raise ForecastError(422, f"series needs at least {MIN_INPUT} points (got {len(series)})")
    if len(series) > MAX_INPUT:
        raise ForecastError(422, f"series is limited to {MAX_INPUT} points (got {len(series)})")
    values: list[float] = []
    for i, v in enumerate(series):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ForecastError(422, f"series[{i}] is not a number")
        f = float(v)
        if not math.isfinite(f) or abs(f) > MAX_ABS_VALUE:
            raise ForecastError(422, f"series[{i}] must be finite and |x| <= {MAX_ABS_VALUE:g}")
        values.append(f)
    if isinstance(horizon, bool) or not isinstance(horizon, int):
        raise ForecastError(422, "horizon must be an integer")
    if not 1 <= horizon <= MAX_HORIZON:
        raise ForecastError(422, f"horizon must be between 1 and {MAX_HORIZON}")
    return values, horizon


class Backend(Protocol):
    """Blocking forecaster; always called from a worker thread, never on the event loop."""

    name: str

    def predict(self, series: Sequence[float], horizon: int, timeout: float) -> dict: ...

    def info(self) -> dict: ...

    def kill(self) -> None: ...


class SubprocessBackend:
    """Model lives in a separate process of the isolated TimesFM interpreter (torch never enters Bossman)."""

    name = "subprocess"

    def __init__(self, python: Path, model_dir: Path):
        self.python, self.model_dir = python, model_dir
        self._proc: subprocess.Popen | None = None
        self._ready: dict = {}
        self._lock = threading.Lock()

    def _start(self) -> None:
        worker = Path(__file__).with_name("timesfm_worker.py")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            [str(self.python), str(worker), str(self.model_dir), str(MAX_CONTEXT), str(MAX_HORIZON)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, creationflags=flags)
        line = self._readline(WORKER_START_TIMEOUT_S)
        ready = json.loads(line) if line else {"ready": False, "error": "worker exited before reporting"}
        if not ready.get("ready"):
            self.kill()
            raise ForecastError(503, f"unavailable: worker failed to load the model: {ready.get('error')}")
        self._ready = ready

    def _readline(self, timeout: float) -> str:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        timer = threading.Timer(timeout, proc.kill)   # a hung worker is killed, which unblocks readline
        timer.start()
        try:
            return proc.stdout.readline()
        finally:
            timer.cancel()

    def predict(self, series: Sequence[float], horizon: int, timeout: float) -> dict:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            assert self._proc is not None and self._proc.stdin is not None
            try:
                self._proc.stdin.write(json.dumps({"series": list(series), "horizon": horizon}) + "\n")
                self._proc.stdin.flush()
            except OSError as exc:
                self.kill()
                raise ForecastError(502, f"forecast worker is gone: {exc}") from exc
            line = self._readline(timeout)
            if not line:
                self.kill()
                raise ForecastError(504, f"forecast worker did not answer within {timeout:g}s")
            reply = json.loads(line)
            if "error" in reply:
                raise ForecastError(502, f"forecast failed: {reply['error']}")
            return reply

    def info(self) -> dict:
        return {"loaded": self._proc is not None and self._proc.poll() is None, **self._ready}

    def kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()


class InProcessBackend:
    """``timesfm`` importable in this interpreter (extra [forecast]); loaded on first use."""

    name = "in-process"

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self._model = None
        self._ready: dict = {}
        self._lock = threading.Lock()

    def predict(self, series: Sequence[float], horizon: int, timeout: float) -> dict:
        with self._lock:
            import numpy as np
            if self._model is None:
                from importlib.metadata import version

                import timesfm
                t0 = time.perf_counter()
                model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(str(self.model_dir), torch_compile=False)
                model.compile(timesfm.ForecastConfig(
                    max_context=MAX_CONTEXT, max_horizon=MAX_HORIZON, normalize_inputs=True,
                    use_continuous_quantile_head=True, fix_quantile_crossing=True))
                self._model = model
                self._ready = {"load_s": round(time.perf_counter() - t0, 3), "timesfm": version("timesfm"),
                               "device": "cpu"}
            t0 = time.perf_counter()
            point, quant = self._model.forecast(horizon=horizon, inputs=[np.asarray(series, dtype=np.float32)])
            return {"point": point[0, :horizon].astype(float).tolist(),
                    "quantiles": quant[0, :horizon, 1:10].astype(float).tolist(),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}

    def info(self) -> dict:
        return {"loaded": self._model is not None, **self._ready}

    def kill(self) -> None:  # an in-process model cannot be interrupted; the timeout only frees the caller
        return None


@dataclass
class _Config:
    enabled: bool
    model_dir: Path
    python: Path | None
    timeout_s: float


class TimesFMService:
    def __init__(self, data_dir: Path, env: Mapping[str, str] | None = None, backend: Backend | None = None):
        self.data_dir = Path(data_dir)
        self._env = env
        self._backend = backend          # tests inject a fake; production builds one lazily
        self._built_for: tuple | None = None
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    def config(self) -> _Config:
        env = os.environ if self._env is None else self._env
        model_dir = Path(env.get(ENV_MODEL_DIR) or self.data_dir / "models" / "timesfm" / "timesfm-2.5-200m-pytorch")
        python = Path(env[ENV_PYTHON]) if env.get(ENV_PYTHON) else None
        try:
            timeout = min(max(float(env.get(ENV_TIMEOUT) or DEFAULT_TIMEOUT_S), 1.0), 600.0)
        except ValueError:
            timeout = DEFAULT_TIMEOUT_S
        return _Config(enabled=env.get(ENV_ENABLE, "").strip().lower() in ("1", "true", "yes", "on"),
                       model_dir=model_dir, python=python, timeout_s=timeout)

    def unavailable_reason(self, cfg: _Config | None = None) -> str | None:
        cfg = cfg or self.config()
        if not cfg.enabled:
            return f"disabled: set {ENV_ENABLE}=1 to enable local forecasting"
        if not (cfg.model_dir / "model.safetensors").is_file():
            return f"model not found: {cfg.model_dir} has no model.safetensors"
        if self._backend is not None:
            return None
        if cfg.python is not None:
            if not cfg.python.is_file():
                return f"{ENV_PYTHON} does not exist: {cfg.python}"
            return None
        if importlib.util.find_spec("timesfm") is None:
            return (f"timesfm is not installed here: set {ENV_PYTHON} to the isolated TimesFM venv's python "
                    "or install the [forecast] extra")
        return None

    def _get_backend(self, cfg: _Config) -> Backend:
        key = (str(cfg.model_dir), str(cfg.python))
        if self._backend is not None and (self._built_for is None or self._built_for == key):
            return self._backend
        if self._backend is not None:
            self._backend.kill()
        self._backend = (SubprocessBackend(cfg.python, cfg.model_dir) if cfg.python is not None
                         else InProcessBackend(cfg.model_dir))
        self._built_for = key
        return self._backend

    def status(self) -> dict:
        cfg = self.config()
        reason = self.unavailable_reason(cfg)
        backend = self._backend
        return {"available": reason is None, "reason": reason, "model": MODEL_ID, "revision": MODEL_REVISION,
                "license": MODEL_LICENSE, "model_dir": str(cfg.model_dir),
                "backend": backend.name if backend else ("subprocess" if cfg.python else "in-process"),
                "runtime": backend.info() if backend else {"loaded": False},
                "last_error": self.last_error,
                "limits": {"min_input": MIN_INPUT, "max_input": MAX_INPUT, "max_context": MAX_CONTEXT,
                           "max_horizon": MAX_HORIZON, "timeout_s": cfg.timeout_s},
                "quantiles": list(QUANTILES)}

    async def forecast(self, series: Any, horizon: Any) -> dict:
        values, h = validate_request(series, horizon)
        cfg = self.config()
        reason = self.unavailable_reason(cfg)
        if reason is not None:
            raise ForecastError(503, f"unavailable: {reason}")
        backend = self._get_backend(cfg)
        context = values[-MAX_CONTEXT:]
        t0 = time.perf_counter()
        try:
            async with self._lock:              # one forecast at a time; waiting does not hold a thread
                reply = await asyncio.wait_for(
                    asyncio.to_thread(backend.predict, context, h, cfg.timeout_s), timeout=cfg.timeout_s + 1.0)
        except asyncio.TimeoutError:
            backend.kill()
            self.last_error = f"timeout after {cfg.timeout_s:g}s"
            raise ForecastError(504, f"forecast timed out after {cfg.timeout_s:g}s") from None
        except ForecastError as exc:
            self.last_error = exc.message
            raise
        except Exception as exc:  # noqa: BLE001 — an unexpected runtime failure is a 502, not a crash
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise ForecastError(502, f"forecast failed: {self.last_error}") from exc
        point, quants = reply.get("point"), reply.get("quantiles")
        if (not isinstance(point, list) or len(point) != h or not isinstance(quants, list) or len(quants) != h
                or any(len(q) != len(QUANTILES) for q in quants)
                or not all(math.isfinite(x) for x in point + [v for q in quants for v in q])):
            self.last_error = "model returned a malformed forecast"
            raise ForecastError(502, self.last_error)
        self.last_error = None
        info = backend.info()
        return {"model": MODEL_ID, "revision": MODEL_REVISION, "license": MODEL_LICENSE,
                "package": {"timesfm": info.get("timesfm"), "torch": info.get("torch")},
                "backend": backend.name, "horizon": h, "input_points": len(values), "context_used": len(context),
                "point": point,
                "quantiles": {f"{q:.1f}": [step[i] for step in quants] for i, q in enumerate(QUANTILES)},
                "model_ms": reply.get("elapsed_ms"), "total_ms": round((time.perf_counter() - t0) * 1000, 1),
                "disclaimer": DISCLAIMER}

    def close(self) -> None:
        if self._backend is not None:
            self._backend.kill()
