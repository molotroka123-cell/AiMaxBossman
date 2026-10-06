"""Repeatable architecture benchmark for Bossman (no network, no models, temp data dir).

Measures what an owner feels and what a release gate can compare between two builds:

* ``import_s``      - cold ``import bcc.api`` in a fresh interpreter (median of N)
* ``start_s``       - ``create_app`` + ``Services.start`` (workers off)
* ``stop_s``        - ``Services.stop``
* ``rss_mb``        - resident memory of the process after start (median of N is not meaningful; last value)
* ``endpoints``     - p50/p95 milliseconds of every read-only GET route without path parameters that answers 200
* ``summary``       - one number per axis so two runs can be diffed by ``--compare``

Usage (from command-center/, PYTHONPATH must point at this checkout):
    python ../tools/bench_bossman.py --out bench.json
    python ../tools/bench_bossman.py --compare before.json after.json
The tool never writes outside --out and its own temp directory.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def measure_import(repeats: int) -> float:
    times = []
    for _ in range(repeats):
        started = time.perf_counter()
        subprocess.run([sys.executable, "-c", "import bcc.api"], check=True, capture_output=True)
        times.append(time.perf_counter() - started)
    return statistics.median(times)


async def measure_app(calls: int) -> dict:
    import httpx
    import psutil

    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings

    with tempfile.TemporaryDirectory(prefix="bossman-bench-") as tmp:
        data = Path(tmp) / "data"
        settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}",
                            ui_dir=Path(tmp) / "no-ui")
        started = time.perf_counter()
        app = create_app(settings, announce_token=False, start_workers=False)
        svc = app.state.svc
        await svc.start()
        start_s = time.perf_counter() - started
        rss_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
        endpoints: dict[str, dict] = {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bench",
                                     headers={HEADER: svc.auth.token}) as client:
            paths = (await client.get("/openapi.json")).json().get("paths", {})
            routes = sorted(p for p, ops in paths.items()
                            if "get" in ops and "{" not in p and p.startswith("/api/") and "stream" not in p)
            for path in routes:
                probe = await client.get(path)
                if probe.status_code != 200:
                    continue
                samples = []
                for _ in range(calls):
                    t = time.perf_counter()
                    response = await client.get(path)
                    samples.append((time.perf_counter() - t) * 1000.0)
                    if response.status_code != 200:
                        break
                endpoints[path] = {"p50_ms": round(_pct(samples, 0.5), 2), "p95_ms": round(_pct(samples, 0.95), 2),
                                   "bytes": len(probe.content)}
        stopped = time.perf_counter()
        await svc.stop()
        stop_s = time.perf_counter() - stopped
    return {"start_s": round(start_s, 3), "stop_s": round(stop_s, 3), "rss_mb": round(rss_mb, 1), "endpoints": endpoints}


def summarize(result: dict) -> dict:
    eps = result["endpoints"]
    p50s = [v["p50_ms"] for v in eps.values()]
    p95s = [v["p95_ms"] for v in eps.values()]
    return {"import_s": result["import_s"], "start_s": result["start_s"], "stop_s": result["stop_s"],
            "rss_mb": result["rss_mb"], "endpoints_measured": len(eps),
            "endpoint_p50_median_ms": round(statistics.median(p50s), 2) if p50s else 0.0,
            "endpoint_p95_median_ms": round(statistics.median(p95s), 2) if p95s else 0.0,
            "endpoint_p95_worst_ms": max(p95s) if p95s else 0.0}


def compare(before: dict, after: dict) -> str:
    lines = [f"{'axis':28s} {'before':>10s} {'after':>10s} {'change':>9s}"]
    for key, old in before["summary"].items():
        new = after["summary"].get(key)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)) or old == 0:
            continue
        change = (new - old) / old * 100.0
        lines.append(f"{key:28s} {old:10.3f} {new:10.3f} {change:+8.1f}%")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="")
    parser.add_argument("--calls", type=int, default=15)
    parser.add_argument("--import-repeats", type=int, default=5)
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    if args.compare:
        before, after = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args.compare)
        print(compare(before, after))
        return 0
    result = {"import_s": round(measure_import(args.import_repeats), 3)}
    result.update(asyncio.run(measure_app(args.calls)))
    result["summary"] = summarize(result)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
