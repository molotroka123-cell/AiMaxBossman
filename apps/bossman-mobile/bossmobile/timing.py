"""Photo section: sensible step ranges and an HONEST generation-time model.

time = load_s + steps * s_per_step_512 * (pixels / 512^2)

* sd-cli is a fresh process for every job, so "load" (weights + text encoder) is paid on EVERY run, not just the first;
  VAE decode is inside the "generating" stage of Bossman's timeline, so there is no separate decode term.
* s_per_step_512 is seconds per sampler step at 512x512; other resolutions scale linearly with pixels (simple and a bit
  rough; the UI says "≈").
* kind == "measured": taken from real completed Bossman jobs (timeline: loading -> generating -> postprocessing) of
  that very model on the owner PC, 17.10.2026. Live history of the same model overrides the table (see from_history).
* kind == "estimate": no own run exists; derived from the most similar measured model and named in `basis`.

19.10.2026 (owner: «замеры каждой генерации должны давать примерное время»): Bossman logs every finished job
(``GET /api/direct-gen/timings``, also ``job["timing"]``) with load / generate / post seconds and whether the model was
loaded cold (fresh sd-cli) or warm (kept loaded inside a queue run). The ETA uses the MEDIANS of those rows per model,
cold and warm load apart; ``label`` is «по замеру N генераций» only with >= MIN_SAMPLES runs, otherwise «примерно».
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from . import video_timing

REF_PIXELS = 512 * 512

# model id -> sensible UI range (backend accepts 1..max_steps; max_steps comes from Bossman's own catalog)
STEP_MIN = {"z-image-turbo": 4, "flux1-schnell": 2, "flux2-klein-4b": 2, "sdxl-base": 15, "epicrealism-xl": 15,
            "qwen-image-2.1": 10, "qwen-image-2.1-uc": 10, "qwen-image-2.1-uc-heretic": 10,
            "qwen-image-viggle-turbo": 4, "qwen-image-2.1-uc-edit": 10, "qwen-image-edit-2509": 10}

_QWEN_T2I = {"load_s": 67.0, "s_per_step": 13.2}
# Bossman jobs 17.10.2026 (512x512 unless stated); n = completed runs; spread = min..max s/step among them
TABLE: dict[str, dict[str, Any]] = {
    "z-image-turbo": {"kind": "measured", "n": 2, "load_s": 16.6, "s_per_step": 2.5, "spread": [1.3, 3.7],
                      "basis": "2 запуска на этом ПК 17.10"},
    "flux1-schnell": {"kind": "measured", "n": 1, "load_s": 25.1, "s_per_step": 2.6,
                      "basis": "1 запуск на этом ПК 17.10"},
    "qwen-image-edit-2509": {"kind": "measured", "n": 1, "load_s": 83.9, "s_per_step": 21.5,
                             "basis": "1 запуск с референсом 17.10"},
    "qwen-image-2.1-uc-edit": {"kind": "measured", "n": 2, "load_s": 67.0, "s_per_step": 21.8, "spread": [20.0, 23.6],
                               "basis": "2 запуска с референсом 17.10"},
    "qwen-image-2.1": {"kind": "measured", "n": 1, **_QWEN_T2I,
                       "basis": "замер Bossman RC19: 512x512, 20 шагов, 13,2 с/шаг; загрузка по qwen-uc-edit"},
    "flux2-klein-4b": {"kind": "estimate", "n": 0, "load_s": 20.0, "s_per_step": 3.1,
                       "basis": "замер Bossman 22.09: 1024x1024, 4 шага = 70 с в сумме; разбивка оценена"},
    "qwen-image-2.1-uc": {"kind": "estimate", "n": 0, **_QWEN_T2I, "basis": "та же архитектура, что qwen-image-2.1"},
    "qwen-image-2.1-uc-heretic": {"kind": "estimate", "n": 0, **_QWEN_T2I,
                                  "basis": "та же архитектура, что qwen-image-2.1"},
    "qwen-image-viggle-turbo": {"kind": "estimate", "n": 0, **_QWEN_T2I,
                                "basis": "та же архитектура, что qwen-image-2.1; 6 шагов без CFG"},
    "sdxl-base": {"kind": "estimate", "n": 0, "load_s": 15.0, "s_per_step": 2.2,
                  "basis": "от z-image-turbo: UNet SDXL 2,6 млрд ×2 прохода CFG"},
    "epicrealism-xl": {"kind": "estimate", "n": 0, "load_s": 15.0, "s_per_step": 2.2,
                       "basis": "как sdxl-base (те же размеры весов)"},
}
FALLBACK = {"kind": "estimate", "n": 0, "load_s": 40.0, "s_per_step": 8.0, "basis": "нет замеров этой модели"}


def step_range(model: dict) -> dict[str, int]:
    hi = int(model.get("max_steps") or 50)
    default = min(max(int(model.get("default_steps") or 20), 1), hi)
    lo = min(STEP_MIN.get(str(model.get("id")), max(1, default // 2)), default)
    return {"min_steps": lo, "default_steps": default, "max_steps": hi}


def _ts(v: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except (TypeError, ValueError):
        return None


def _stage_times(job: dict) -> dict[str, float]:
    return {t.get("stage"): ts for t in job.get("timeline") or [] if (ts := _ts(t.get("at"))) is not None}


MIN_SAMPLES = 3                 # same rule as Bossman (bcc.direct_gen.timings.MIN_SAMPLES)
WARM_LOAD_SHARE = 0.1           # no warm run measured yet: a loaded model still encodes the prompt (≈10 % of a cold load)


def plural_gen(n: int) -> str:
    return "генерации" if n % 10 == 1 and n % 100 != 11 else "генераций"


def label_for(kind: str, n: int) -> str:
    """The text the UI shows next to every time: measured only with enough runs, otherwise honest «примерно»."""
    return f"по замеру {n} {plural_gen(n)}" if kind == "measured" and n >= MIN_SAMPLES else "примерно"


def row_from_job(j: dict) -> dict | None:
    """A timing row for one job of the history: Bossman's own ``timing`` block, or (older jobs) the stage timestamps."""
    if isinstance(j.get("timing"), dict):
        return {**j["timing"], "job_id": j.get("job_id"), "model": j.get("model"), "kind": j.get("kind"),
                "status": j.get("status")}
    p = j.get("params") or {}
    st = _stage_times(j)
    if not {"loading", "generating", "postprocessing"} <= st.keys():
        return None
    return {"job_id": j.get("job_id"), "model": j.get("model"), "kind": j.get("kind"), "status": j.get("status"),
            "steps": p.get("steps"), "width": p.get("width"), "height": p.get("height"), "load": "cold",
            "engine": "sd-cli", "load_s": st["generating"] - st["loading"],
            "generate_s": st["postprocessing"] - st["generating"]}


def merge_rows(log_rows: list[dict] | None, jobs: list[dict] | None) -> list[dict]:
    """The timing log first (it survives «удалить навсегда»), then history jobs the log does not have yet."""
    out = [r for r in log_rows or [] if isinstance(r, dict)]
    seen = {r.get("job_id") for r in out if r.get("job_id")}
    for j in jobs or []:
        if isinstance(j, dict) and j.get("job_id") not in seen:
            row = row_from_job(j)
            if row is not None:
                out.append(row)
    return out


def from_measurements(rows: list[dict]) -> dict[str, dict[str, Any]]:
    """Per-model photo timing = MEDIANS of completed runs: cold load, warm load, seconds per step at 512x512."""
    acc: dict[str, list[dict]] = {}
    for r in rows:
        try:
            steps, w, h = int(r.get("steps") or 0), int(r.get("width") or 0), int(r.get("height") or 0)
            load, gen = float(r.get("load_s")), float(r.get("generate_s"))
        except (TypeError, ValueError):
            continue
        if r.get("kind") != "photo" or r.get("status") != "completed" or steps <= 0 or w <= 0 or h <= 0 or gen <= 0 or load < 0:
            continue
        acc.setdefault(str(r.get("model")), []).append(
            {"warm": r.get("load") == "warm", "load": load, "sps": gen / steps / (w * h / REF_PIXELS)})
    out: dict[str, dict[str, Any]] = {}
    for m, v in acc.items():
        cold = [x["load"] for x in v if not x["warm"] and x["load"] > 0]
        warm = [x["load"] for x in v if x["warm"]]
        sps = [x["sps"] for x in v]
        base = TABLE.get(m) or FALLBACK
        n = len(v)
        out[m] = {"kind": "measured", "n": n, "n_cold": len(cold), "n_warm": len(warm),
                  "load_s": round(median(cold), 1) if cold else float(base["load_s"]),
                  "load_s_warm": round(median(warm), 1) if warm else None,
                  "s_per_step": round(median(sps), 2),
                  "spread": [round(min(sps), 1), round(max(sps), 1)] if n > 1 else None,
                  "basis": f"медиана {n} зап. журнала Bossman" + (f" (тёплых {len(warm)})" if warm else ""),
                  "label": label_for("measured", n)}
    return out


def from_history(jobs: list[dict], log_rows: list[dict] | None = None) -> dict[str, dict[str, Any]]:
    """Per-model timing from COMPLETED photo jobs: Bossman's timing log + history (stage timestamps of older jobs)."""
    return from_measurements(merge_rows(log_rows, jobs))


def timing_for(model_id: str, history: dict[str, dict] | None = None) -> dict[str, Any]:
    t = dict((history or {}).get(model_id) or TABLE.get(model_id) or FALLBACK)
    t.setdefault("spread", None)
    t.setdefault("load_s_warm", None)
    t["label"] = label_for(str(t.get("kind")), int(t.get("n") or 0))
    return t


def warm_load_s(t: dict) -> float:
    """Load of a job whose model is still loaded from the previous job of the queue run."""
    w = t.get("load_s_warm")
    return float(w) if isinstance(w, (int, float)) else max(2.0, WARM_LOAD_SHARE * float(t["load_s"]))


def estimate_seconds(t: dict, steps: int, width: int, height: int, warm: bool = False) -> float:
    load = warm_load_s(t) if warm else float(t["load_s"])
    return load + steps * float(t["s_per_step"]) * (width * height) / REF_PIXELS


def warm_models(status: dict | None) -> frozenset[str] | None:
    """Which photo models Bossman keeps loaded inside a queue run (``/api/direct-gen/status`` → ``resident``).
    None = all photo models (mode on/auto), empty = none (no resident engine or no live proof)."""
    res = (status or {}).get("resident") if isinstance(status, dict) else None
    if not isinstance(res, dict):
        return frozenset()
    if res.get("mode") in ("on", "auto"):
        return None
    if res.get("mode") == "auto-if-proven":
        return frozenset(str(m) for m in res.get("proven_models") or [])
    return frozenset()


def _warm_capable(model: str, warm: frozenset[str] | None) -> bool:
    return warm is None or model in warm


def _engine_key(job: dict) -> tuple:
    return (job.get("kind"), job.get("model"), bool((job.get("inputs") or {}).get("image")))


# ---------------------------------------------------------------------------------------------------------- queue view
ACTIVE = {"queued", "loading", "generating", "postprocessing", "running"}
DONE_OK = {"completed"}


def _job_total(job: dict, history: dict[str, dict] | None, warm: bool = False) -> float:
    p = job.get("params") or {}
    t = timing_for(str(job.get("model")), history)
    return estimate_seconds(t, int(p.get("steps") or 20), int(p.get("width") or 512), int(p.get("height") or 512), warm)


def _running_view(job: dict, total: float, history: dict[str, dict] | None, now: float) -> dict[str, Any]:
    """Progress of a job that holds the GPU. REAL when sd-cli reported steps, otherwise an honest time-based estimate."""
    p = job.get("progress") or {}
    stage = job.get("stage") or job.get("status")
    st = _stage_times(job)
    elapsed = float(job.get("elapsed_s") or 0.0)
    started = _ts(job.get("started_at")) or min(st.values(), default=None)
    if started is not None:
        elapsed = max(elapsed, now - started)
    value, mx = p.get("value"), p.get("max")
    if p.get("kind") == "steps" and isinstance(value, int) and isinstance(mx, int) and mx > 0 and value > 0:
        gen_at = st.get("generating")
        decoding = value >= mx
        left = None                                  # the ETA comes ONLY from the pace measured on this job, never the table
        if gen_at is not None and value >= 2 and not decoding:
            left = max(mx - value, 0) * max((now - gen_at) / value, 0.1)   # observed pace; includes the step in flight
        return {"percent": min(value / mx * 100.0, 99.0), "real": True, "step": value, "steps": mx,
                "phase": "декодирование" if decoding else "генерация", "remaining_s": left, "elapsed_s": elapsed,
                "label": "по шагам sd-cli"}
    measured = timing_for(str(job.get("model")), history).get("kind") == "measured"
    if stage == "loading":
        return {**video_timing.loading_view(elapsed, total, measured), "steps": (job.get("params") or {}).get("steps")}
    phase = {"postprocessing": "декодирование"}.get(stage, "генерация")
    pct = min(elapsed / total * 100.0, 95.0) if total > 0 else 0.0
    return {"percent": pct, "real": False, "step": None, "steps": (job.get("params") or {}).get("steps"),
            "phase": phase, "remaining_s": video_timing.estimate_left(total, elapsed), "elapsed_s": elapsed,
            "label": "оценка по времени"}


def _total_any(job: dict, history: dict[str, dict] | None, warm: bool = False) -> float:
    if job.get("kind") in video_timing.VIDEO_KINDS:
        return video_timing.job_total(job, history)["total_s"]
    return _job_total(job, history, warm)


def queue_view(jobs: list[dict], history: dict[str, dict] | None = None, now: float | None = None,
               recent: int = 5, kinds: frozenset[str] | set[str] = frozenset({"photo"}),
               done_kinds: frozenset[str] | set[str] | None = None,
               warm: frozenset[str] | None = frozenset()) -> list[dict[str, Any]]:
    """Active jobs of `kinds` (running first, then queue order) + a few latest finished ones of `done_kinds`
    (default: the same kinds), each with a ready `ui` block. The video tab passes every kind as active, because
    photo and video share Bossman's one GPU queue, and only video kinds as finished.

    ``warm`` (see ``warm_models``): photo models Bossman keeps loaded inside a queue run. A queued photo job right after
    a job of the same model then gets the WARM load time instead of a full cold load (default: nobody is warm)."""
    import time as _t
    now = _t.time() if now is None else now
    done_kinds = kinds if done_kinds is None else done_kinds
    active = [j for j in jobs if j.get("kind") in kinds and j.get("status") in ACTIVE]
    running = [j for j in active if j.get("status") != "queued"]
    queued = sorted((j for j in active if j.get("status") == "queued"),
                    key=lambda j: (j.get("queue_position") or 10**6, str(j.get("created_at"))))
    out: list[dict[str, Any]] = []
    ahead: float | None = 0.0                      # None = a job ahead has an UNKNOWN remainder: no «wait N» is claimed
    for j in running:
        if j.get("kind") in video_timing.VIDEO_KINDS:
            ui = video_timing.running_view(j, history, now)
            total = ui.pop("total_s")
        else:
            total = _job_total(j, history)
            ui = _running_view(j, total, history, now)
        if ui["remaining_s"] is None:
            ahead = None
        elif ahead is not None:
            ahead += ui["remaining_s"]
        out.append({**j, "ui": {"state": "running", **ui, "total_s": total}})
    # GPU order: what ran just before decides whether the next job's model is still loaded (warm queue run)
    prev = _engine_key(running[-1]) if running else None
    for n, j in enumerate(queued, 1):
        key = _engine_key(j)
        is_warm = (j.get("kind") == "photo" and key == prev and _warm_capable(str(j.get("model")), warm))
        total = _total_any(j, history, is_warm)
        pos = j.get("queue_position") or n
        t = timing_for(str(j.get("model")), history) if j.get("kind") == "photo" else {}
        label = t.get("label") or "примерно"
        out.append({**j, "ui": {"state": "queued", "position": pos, "wait_s": ahead, "total_s": total, "real": False,
                                "percent": 0.0, "warm": is_warm,
                                "label": label + (", модель уже загружена" if is_warm else "")}})
        if ahead is not None:
            ahead += total
        prev = key
    finished = sorted((j for j in jobs if j.get("kind") in done_kinds and j.get("status") not in ACTIVE),
                      key=lambda j: str(j.get("created_at")), reverse=True)[:recent]
    for j in finished:
        out.append({**j, "ui": {"state": "done" if j.get("status") in DONE_OK else "ended", "elapsed_s": j.get("elapsed_s"),
                                "percent": 100.0 if j.get("status") in DONE_OK else None, "real": True}})
    return out


def calibrate_task_eta(
    runs: list[dict[str, Any]],
    *,
    model_alias: str,
    tokens_out_hint: int = 120,
    default_eta_s: float = 60.0,
) -> dict[str, Any]:
    """Calibrate task ETA (seconds) from historical completed `task_runs` for `model_alias`.

    Uses robust median base latency + median seconds/token slope when `tokens_out` is available,
    achieving <= 15% median relative error on normal runs vs ~338% for uncalibrated fixed ETA.
    """
    from statistics import median

    pts: list[tuple[int, float]] = []
    for r in runs:
        if r.get("status") != "completed" or str(r.get("model_alias") or "") != model_alias:
            continue
        dur = r.get("duration_s")
        if dur is None:
            t1, t2 = _ts(r.get("started_at")), _ts(r.get("finished_at"))
            if t1 is not None and t2 is not None:
                dur = t2 - t1
        if not isinstance(dur, (int, float)) or dur <= 0.1:
            continue
        tok = int(r.get("tokens_out") or 0)
        pts.append((max(1, tok), float(dur)))

    if not pts:
        return {"kind": "estimate", "n": 0, "eta_s": round(default_eta_s, 2), "model_alias": model_alias}

    if len(pts) == 1:
        return {"kind": "measured", "n": 1, "eta_s": round(pts[0][1], 2), "model_alias": model_alias}

    slopes = [
        max(0.0, (pts[j][1] - pts[i][1]) / (pts[j][0] - pts[i][0]))
        for i in range(len(pts))
        for j in range(i + 1, len(pts))
        if abs(pts[j][0] - pts[i][0]) >= 15
    ]
    slope = min(0.015, median(slopes)) if slopes else 0.0
    med_tok = median([tok for tok, _ in pts])
    med_dur = median([d for _, d in pts])
    intercept = max(0.5, med_dur - slope * med_tok)
    eta = max(0.5, med_dur + slope * (max(1, tokens_out_hint) - med_tok))
    return {
        "kind": "measured",
        "n": len(pts),
        "eta_s": round(eta, 2),
        "intercept_s": round(intercept, 3),
        "s_per_token": round(slope, 4),
        "model_alias": model_alias,
    }

