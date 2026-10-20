"""Measured timings of every finished Direct-Generator job (owner 19.10: «замеры каждой генерации -> время в приложении»).

One row per finished job is appended to ``<BCC data dir>/direct-gen/timings.jsonl`` (append-only, never in git) and
the same dict is stored in the job record as ``job["timing"]`` (so ``GET /api/direct-gen/jobs`` carries it too).

Stage split, from the job's own timeline (the same stages the UI shows):
  queue_wait_s  created_at -> started_at (waiting for the GPU)
  load_s        loading    -> generating (weights + text encoder up to the first sampler step; ~0 on a warm engine)
  generate_s    generating -> postprocessing (sampler steps + VAE decode)
  post_s        postprocessing -> finished (file check, FaceLock, pose gate ...)
  total_s       started_at -> finished_at
When the engine reported no step at all (no ``generating`` stage), the split falls back to the engine's own clock
(``engine_ready_s`` / ``engine_seconds`` in provenance) and ``split`` says so; unknown parts stay ``None``.

``engine``: ``sd-cli`` | ``sd-server`` | ``comfyui`` | ``facefusion``; ``load``: ``cold`` (weights read for this job)
or ``warm`` (they were already loaded by the previous job of a queue run). No prompt, no participant, no paths.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

FILE = "timings.jsonl"
SCHEMA = 1
REF_PIXELS = 512 * 512
MIN_SAMPLES = 3          # fewer completed runs of a model -> the ETA says «примерно», not «по замеру»
_lock = threading.Lock()


def _ts(v: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except (TypeError, ValueError):
        return None


def _first_stages(job: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in job.get("timeline") or []:
        ts = _ts(t.get("at"))
        if ts is not None and t.get("stage") not in out:
            out[t.get("stage")] = ts
    return out


def _r(v: float | None) -> float | None:
    return None if v is None else round(max(v, 0.0), 2)


def engine_of(job: dict) -> tuple[str, str]:
    prov = job.get("provenance") or {}
    runtime = prov.get("runtime")
    if runtime == "comfyui":
        return "comfyui", "cold"
    if runtime == "facefusion":
        return "facefusion", "cold"
    if str(prov.get("engine") or "").startswith("sd-server"):
        return "sd-server", "warm" if prov.get("engine_warm") else "cold"
    return "sd-cli", "cold"          # every sd-cli run reads the weights again


def is_resumed(job: dict) -> bool:
    if int(job.get("resumed_count") or 0) > 0:
        return True
    for t in job.get("timeline") or []:
        if isinstance(t, dict) and t.get("note") in ("resumed_after_restart", "recovered_after_restart"):
            return True
    return False


def measure(job: dict) -> dict[str, Any]:
    """The timing row of one finished job (pure)."""
    st = _first_stages(job)
    p = job.get("params") or {}
    prov = job.get("provenance") or {}
    started, finished, created = _ts(job.get("started_at")), _ts(job.get("finished_at")), _ts(job.get("created_at"))
    loading, gen, post = st.get("loading"), st.get("generating"), st.get("postprocessing")
    load_s = generate_s = post_s = None
    split = "timeline"
    if loading is not None and gen is not None:
        load_s = gen - loading
        if post is not None:
            generate_s = post - gen
    elif loading is not None and post is not None:
        run, ready = prov.get("engine_seconds"), prov.get("engine_ready_s")
        if isinstance(run, (int, float)):
            before = max((post - loading) - float(run), 0.0)      # preflight + input files, before the engine started
            if isinstance(ready, (int, float)):
                load_s, generate_s = before + float(ready), float(run) - float(ready)
                split = "engine_clock"
            else:
                split = "no_step_report"
        else:
            split = "no_step_report"
    if post is not None and finished is not None:
        post_s = finished - post
    engine, load = engine_of(job)
    inputs = job.get("inputs") or {}
    refs = inputs.get("ref_images_count") if isinstance(inputs.get("ref_images_count"), int) else (
        inputs.get("images") or (1 if inputs.get("image") else 0)
    )
    frames = p.get("frames")
    if job.get("kind") == "faceswap":
        frames = (job.get("progress") or {}).get("max")
    resumed = is_resumed(job)
    return {
        "v": SCHEMA, "ts": job.get("finished_at"), "job_id": job.get("job_id"), "model": job.get("model"),
        "kind": job.get("kind"), "status": job.get("status"),
        "width": p.get("width"), "height": p.get("height"), "steps": p.get("steps"),
        "frames": frames if isinstance(frames, int) else None, "preset": p.get("preset"),
        "refs": refs if isinstance(refs, int) else 0,
        "facelock": bool((job.get("facelock") or {}).get("requested")),
        "resumed": resumed,
        "engine": engine, "load": load, "split": split,
        "load_s": _r(load_s), "generate_s": _r(generate_s), "post_s": _r(post_s),
        "total_s": _r(finished - started) if started is not None and finished is not None else None,
        "queue_wait_s": _r(started - created) if started is not None and created is not None else None,
        "error_code": (job.get("error") or {}).get("code"),
    }


class TimingLog:
    """Append-only JSONL of finished jobs under the Bossman data dir (BCC_DATA_DIR is respected by the caller)."""

    def __init__(self, data_root: Path):
        self.path = Path(data_root) / "direct-gen" / FILE

    def append(self, row: dict) -> None:
        if row.get("resumed"):
            return
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def read(self, limit: int = 1000) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for raw in lines[-max(1, int(limit)):]:
            try:
                row = json.loads(raw)
            except ValueError:
                continue                      # a torn last line after a crash is skipped, never fatal
            if isinstance(row, dict):
                out.append(row)
        return out


def s_per_step_512(row: dict) -> float | None:
    """Seconds per sampler step at 512x512 for a photo row (generate_s / steps / megapixel ratio)."""
    try:
        steps, w, h, gen = int(row["steps"]), int(row["width"]), int(row["height"]), float(row["generate_s"])
    except (KeyError, TypeError, ValueError):
        return None
    if steps <= 0 or w <= 0 or h <= 0 or gen <= 0:
        return None
    return gen / steps / (w * h / REF_PIXELS)


def summary(rows: list[dict]) -> dict[str, dict[str, Any]]:
    """Per-model medians of COMPLETED non-resumed rows, cold and warm load apart. ``measured`` only with >= MIN_SAMPLES runs."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("status") == "completed" and r.get("model") and not r.get("resumed"):
            by.setdefault(str(r["model"]), []).append(r)
    out: dict[str, dict[str, Any]] = {}
    for model, rs in by.items():
        def med(values):
            vals = [float(v) for v in values if isinstance(v, (int, float))]
            return round(median(vals), 2) if vals else None
        cold = [r["load_s"] for r in rs if r.get("load") != "warm" and isinstance(r.get("load_s"), (int, float))]
        warm = [r["load_s"] for r in rs if r.get("load") == "warm" and isinstance(r.get("load_s"), (int, float))]
        sps = [x for x in (s_per_step_512(r) for r in rs) if x is not None]
        out[model] = {"kind": rs[-1].get("kind"), "n": len(rs), "measured": len(rs) >= MIN_SAMPLES,
                      "cold": {"n": len(cold), "load_s": med(cold)}, "warm": {"n": len(warm), "load_s": med(warm)},
                      "s_per_step_512": round(median(sps), 3) if sps else None,
                      "generate_s": med(r.get("generate_s") for r in rs), "post_s": med(r.get("post_s") for r in rs),
                      "total_s": med(r.get("total_s") for r in rs)}
    return out
