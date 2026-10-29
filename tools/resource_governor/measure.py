"""TZ 2.5 T3: memory meter — run one Direct Gen configuration N times and record each peak as a governor measurement.

    python tools/resource_governor/measure.py --consumer sdcli_image --model flux1-schnell --resolution 768x768 --steps 4 --runs 10

Peak = max(used memory during the job) - used memory right before it (psutil, sampled every 0.25 s), so a model that
was already resident is not counted twice. The first run is marked cold. Every run is posted to
`POST /api/resources/measurement` with the config_hash; after `min_samples` (10) the ledger replaces the seed estimate
with the measured nearest-rank P95. Synthetic prompts only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]


def used_gb() -> float:
    import psutil
    vm = psutil.virtual_memory()
    return (vm.total - vm.available) / 1e9


class PeakSampler:
    def __init__(self, read: Callable[[], float] = used_gb, every_s: float = 0.25):
        self.read, self.every_s = read, every_s
        self.peak = 0.0
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def __enter__(self) -> "PeakSampler":
        self.base = self.read()
        self.peak = self.base
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, self.read())
            self._stop.wait(self.every_s)

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=2)
        self.peak = max(self.peak, self.read())
        return False

    @property
    def delta_gb(self) -> float:
        return round(max(0.0, self.peak - self.base), 3)


def config_hash(model: str, resolution: str, steps: int) -> str:
    return hashlib.sha256(f"{model}|{resolution}|{steps}".encode()).hexdigest()[:16]


def run_once(api, model: str, resolution: str, steps: int, seed: int, wait_s: float = 1800) -> str:
    j = api("POST", "/api/direct-gen/jobs", {"model": model, "prompt": "measurement run: grey studio backdrop, single cube",
                                             "resolution": resolution, "steps": steps, "seed": seed}, timeout=120)
    jid = j.get("job_id") or (j.get("job") or {}).get("job_id")
    t0 = time.time()
    while time.time() - t0 < wait_s:
        st = next((x for x in api("GET", "/api/direct-gen/jobs", timeout=60).get("jobs") or [] if x.get("job_id") == jid), {})
        if st.get("status") in ("completed", "failed", "cancelled"):
            return st["status"]
        time.sleep(1)
    return "timeout"


def measure(api, consumer: str, model: str, resolution: str, steps: int, runs: int,
            sampler_factory: Callable[[], PeakSampler] = PeakSampler) -> dict:
    h = config_hash(model, resolution, steps)
    rows = []
    for i in range(runs):
        with sampler_factory() as s:
            status = run_once(api, model, resolution, steps, seed=1000 + i)
        row = {"run": i + 1, "status": status, "peak_gb": s.delta_gb, "cold": i == 0}
        if status == "completed":
            rec = api("POST", "/api/resources/measurement", {"consumer": consumer, "peak_gb": s.delta_gb,
                                                             "config_hash": h, "cold": i == 0}, timeout=30)
            row["recorded"] = bool(rec)
        rows.append(row)
        print(json.dumps(row), flush=True)
    ok = [r["peak_gb"] for r in rows if r["status"] == "completed"]
    srt = sorted(ok)
    p95 = srt[max(0, -(-95 * len(srt) // 100) - 1)] if srt else None       # nearest-rank, same rule as the ledger
    return {"consumer": consumer, "model": model, "resolution": resolution, "steps": steps, "config_hash": h,
            "runs": rows, "completed": len(ok), "p95_gb": p95, "max_gb": max(ok) if ok else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--consumer", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--resolution", default="768x768")
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT / "tools"))
    import prove_chat_attachments as p
    res = measure(p.api, a.consumer, a.model, a.resolution, a.steps, a.runs)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "runs"}, ensure_ascii=False))
    return 0 if res["completed"] == a.runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
