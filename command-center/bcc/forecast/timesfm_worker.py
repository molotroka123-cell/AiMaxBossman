"""TimesFM worker process: one JSON request per stdin line, one JSON reply per stdout line.

Runs inside the ISOLATED TimesFM interpreter (torch + timesfm), started by
bcc.forecast.timesfm_local with ``<python> <this file> <model_dir> <max_context> <max_horizon>``.
It imports nothing from Bossman (that venv does not have it): stdlib + numpy + timesfm.

First line written is ``{"ready": true, ...versions...}`` or ``{"ready": false, "error": ...}``.
Request:  {"series": [floats], "horizon": int}
Reply:    {"point": [...], "quantiles": [[q0.1..q0.9] per step], "elapsed_ms": float} or {"error": str}
"""
from __future__ import annotations

import json
import sys
import time


_PROTOCOL = sys.stdout
sys.stdout = sys.stderr   # anything a library prints must not corrupt the JSON-lines protocol


def _emit(obj) -> None:
    _PROTOCOL.write(json.dumps(obj) + "\n")
    _PROTOCOL.flush()


def main(argv: list[str]) -> int:
    model_dir, max_context, max_horizon = argv[0], int(argv[1]), int(argv[2])
    try:
        from importlib.metadata import version

        import numpy as np
        import timesfm
        import torch

        t0 = time.perf_counter()
        model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(model_dir, torch_compile=False)
        model.compile(timesfm.ForecastConfig(max_context=max_context, max_horizon=max_horizon,
                                             normalize_inputs=True, use_continuous_quantile_head=True,
                                             fix_quantile_crossing=True))
        _emit({"ready": True, "load_s": round(time.perf_counter() - t0, 3),
               "timesfm": version("timesfm"), "torch": torch.__version__, "device": "cpu"})
    except Exception as exc:  # noqa: BLE001 — reported to the parent, which shows "unavailable: <reason>"
        _emit({"ready": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            series = np.asarray(req["series"], dtype=np.float32)
            horizon = int(req["horizon"])
            t0 = time.perf_counter()
            point, quant = model.forecast(horizon=horizon, inputs=[series])
            _emit({"point": point[0, :horizon].astype(float).tolist(),
                   "quantiles": quant[0, :horizon, 1:10].astype(float).tolist(),  # index 0 is the mean
                   "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)})
        except Exception as exc:  # noqa: BLE001
            _emit({"error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
