"""Photo section: sensible step ranges and an HONEST generation-time model.

time = load_s + steps * s_per_step_512 * (pixels / 512^2)

* sd-cli is a fresh process for every job, so "load" (weights + text encoder) is paid on EVERY run, not just the first;
  VAE decode is inside the "generating" stage of Bossman's timeline, so there is no separate decode term.
* s_per_step_512 is seconds per sampler step at 512x512; other resolutions scale linearly with pixels (simple and a bit
  rough; the UI says "≈").
* kind == "measured": taken from real completed Bossman jobs (timeline: loading -> generating -> postprocessing) of
  that very model on the owner PC, 17.10.2026. Live history of the same model overrides the table (see from_history).
* kind == "estimate": no own run exists; derived from the most similar measured model and named in `basis`.
"""
from __future__ import annotations

from datetime import datetime
from statistics import mean
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


def from_history(jobs: list[dict]) -> dict[str, dict[str, Any]]:
    """Per-model timing from COMPLETED photo jobs in Bossman's own history (stage timestamps)."""
    acc: dict[str, list[tuple[float, float]]] = {}
    for j in jobs:
        p = j.get("params") or {}
        st = _stage_times(j)
        steps, w, h = p.get("steps"), p.get("width"), p.get("height")
        if (j.get("kind") != "photo" or j.get("status") != "completed" or not steps or not w or not h
                or not {"loading", "generating", "postprocessing"} <= st.keys()):
            continue
        load, gen = st["generating"] - st["loading"], st["postprocessing"] - st["generating"]
        if load <= 0 or gen <= 0:
            continue
        acc.setdefault(str(j.get("model")), []).append((load, gen / int(steps) / (int(w) * int(h) / REF_PIXELS)))
    return {m: {"kind": "measured", "n": len(v), "load_s": round(mean(a for a, _ in v), 1),
                "s_per_step": round(mean(b for _, b in v), 2),
                "spread": [round(min(b for _, b in v), 1), round(max(b for _, b in v), 1)] if len(v) > 1 else None,
                "basis": f"{len(v)} зап. из журнала Bossman"} for m, v in acc.items()}


def timing_for(model_id: str, history: dict[str, dict] | None = None) -> dict[str, Any]:
    t = dict((history or {}).get(model_id) or TABLE.get(model_id) or FALLBACK)
    t.setdefault("spread", None)
    return t


def estimate_seconds(t: dict, steps: int, width: int, height: int) -> float:
    return float(t["load_s"]) + steps * float(t["s_per_step"]) * (width * height) / REF_PIXELS


# ---------------------------------------------------------------------------------------------------------- queue view
ACTIVE = {"queued", "loading", "generating", "postprocessing", "running"}
DONE_OK = {"completed"}


def _job_total(job: dict, history: dict[str, dict] | None) -> float:
    p = job.get("params") or {}
    t = timing_for(str(job.get("model")), history)
    return estimate_seconds(t, int(p.get("steps") or 20), int(p.get("width") or 512), int(p.get("height") or 512))


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


def _total_any(job: dict, history: dict[str, dict] | None) -> float:
    if job.get("kind") in video_timing.VIDEO_KINDS:
        return video_timing.job_total(job, history)["total_s"]
    return _job_total(job, history)


def queue_view(jobs: list[dict], history: dict[str, dict] | None = None, now: float | None = None,
               recent: int = 5, kinds: frozenset[str] | set[str] = frozenset({"photo"}),
               done_kinds: frozenset[str] | set[str] | None = None) -> list[dict[str, Any]]:
    """Active jobs of `kinds` (running first, then queue order) + a few latest finished ones of `done_kinds`
    (default: the same kinds), each with a ready `ui` block. The video tab passes every kind as active, because
    photo and video share Bossman's one GPU queue, and only video kinds as finished."""
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
    for n, j in enumerate(queued, 1):
        total = _total_any(j, history)
        pos = j.get("queue_position") or n
        out.append({**j, "ui": {"state": "queued", "position": pos, "wait_s": ahead, "total_s": total, "real": False,
                                "percent": 0.0, "label": "оценка по замерам"}})
        if ahead is not None:
            ahead += total
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

