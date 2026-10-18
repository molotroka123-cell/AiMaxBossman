"""Video section: an HONEST generation-time model for the owner's video models and FaceFusion face swap.

Wan (sd-cli TI2V and ComfyUI S2V) - one diffusion pass over all frames at once:

    time = load_s + steps * s_per_step * scale(r) + decode_s,     r = width * height * frames / ref

    scale(r) = r            for r <= 1   (smaller than the measured clip: overheads keep it roughly linear)
             = r ** alpha   for r >  1   (attention over more tokens grows faster than linear)

  alpha is FITTED to two runs of the same model on this PC (see `basis`), not assumed.

Face swap (FaceFusion) - frame by frame, then a pose gate + audio/frame restore over the whole clip:

    time = load_s + frames / gen_fps(preset) + frames * post_s_per_frame

Labels follow the rule of the photo section, stricter: kind == "measured" ONLY when the numbers come from completed
jobs in Bossman's own direct-gen journal (GET /api/direct-gen/jobs). Wan runs measured outside that journal (Genjutsu
notes, concert 06.10, podcast 16.10) are real, but they ran other scripts/graphs, so they stay "estimate" with the
source named in `basis`. On 17.10.2026 the journal holds no Wan video job; it holds 2 face-swap jobs (preset quality).
"""
from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Any

VIDEO_KINDS = frozenset({"video", "faceswap"})
FACESWAP_ID = "faceswap-facefusion"
FRAME_STEP = {"wan": 4, "ltx": 8}            # Bossman: frames = step * k + 1 (bcc/direct_gen/catalog.py, sdcli.py)
ASSUMED_FPS = 30                             # a phone clip; the browser cannot read the frame rate before upload
BYTES_PER_FRAME = 28_543_980 / 823           # the measured face-swap clip (13.7 s, 60 fps, 1320x1040) - queued jobs only

# Wan: reference clip + fitted exponent. All "estimate": no Wan job in the direct-gen journal yet.
TABLE: dict[str, dict[str, Any]] = {
    "wan2.2-ti2v-5b": {
        "kind": "estimate", "n": 0, "load_s": 90.0, "decode_s": 30.0, "s_per_step": 16.3,
        "ref": 640 * 1152 * 17, "alpha": 2.9, "fixed_steps": None,
        "basis": "замеры на этом ПК вне журнала: 640×1152, 17 кадров, 24 шага ≈ 510 с (Genjutsu); "
                 "832×480, 121 кадр ≈ 14 мин на шаг (каталог Bossman); рост выше эталона подогнан по ним"},
    "wan2.2-s2v-14b": {
        "kind": "estimate", "n": 0, "load_s": 120.0, "decode_s": 0.0, "s_per_step": 423.0,
        "ref": 480 * 832 * 77, "alpha": 1.47, "fixed_steps": 4,
        "basis": "замеры на этом ПК вне журнала: 480×832, 77 кадров (4,8 с) = 30 мин (концерт 06.10: 28–34 мин, "
                 "подкаст 16.10: 1812 с); 1280×720: 1446 с на шаг (16.10)"},
}
FALLBACK = {"kind": "estimate", "n": 0, "load_s": 120.0, "decode_s": 30.0, "s_per_step": 30.0, "ref": 480 * 272 * 33,
            "alpha": 2.0, "fixed_steps": None, "basis": "нет замеров этой модели — грубая оценка"}

# Face swap: 2 completed jobs in the journal, 14.10.2026, preset quality, 823 frames (13.7 s, 60 fps, 1320x1040):
# loading 22.9/24.2 s, frames 502.6/490.4 s (1.64/1.68 fps), pose gate + restore 815.8/673.7 s (0.99/0.82 s per frame).
FACESWAP: dict[str, Any] = {
    "load_s": 23.5, "post_s_per_frame": 0.905, "assumed_fps": ASSUMED_FPS,
    "presets": {
        "quality": {"kind": "measured", "n": 2, "gen_fps": 1.66, "basis": "2 запуска в журнале Bossman 14.10 (823 кадра)"},
        "fast": {"kind": "estimate", "n": 0, "gen_fps": 7.0,
                 "basis": "7 кадров/с из описания пресета (замер 10.10); проверка позы и сборка — по замеру «Качество»"},
        "lively": {"kind": "estimate", "n": 0, "gen_fps": 1.6,
                   "basis": "как «Качество» + перенос мимики; проверка позы и сборка — по замеру «Качество»"},
    },
}


def _ts(v: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except (TypeError, ValueError):
        return None


def _stages(job: dict) -> dict[str, float]:
    return {t.get("stage"): ts for t in job.get("timeline") or [] if (ts := _ts(t.get("at"))) is not None}


def loading_view(elapsed: float, total: float, measured: bool, phase: str = "загрузка модели") -> dict[str, Any]:
    """The engine is loading the model: the real progress is UNKNOWN (the job may take 10+ minutes), so no percent and
    no «remaining» - only the elapsed time and, if the timing is measured, what such a job usually takes."""
    return {"percent": None, "real": False, "indeterminate": True, "step": None, "steps": None, "unit": None,
            "phase": phase, "remaining_s": None, "elapsed_s": elapsed,
            "typical_s": total if measured and total > 0 else None, "label": "прогресс неизвестен"}


def estimate_left(total: float, elapsed: float) -> float | None:
    """Time left by the TABLE estimate; None once the estimate is used up (the job is slower than usual: unknown)."""
    left = total - elapsed
    return left if left > 1.0 else None


def frames_for(fps: int, step: int, seconds: float) -> int:
    raw = max(1, round(seconds * fps))
    return max(1, round((raw - 1) / step)) * step + 1


def scale(r: float, alpha: float) -> float:
    return r if r <= 1 else r ** alpha


def wan_seconds(t: dict, steps: int, width: int, height: int, frames: int) -> dict[str, float]:
    r = width * height * frames / float(t["ref"])
    run = steps * float(t["s_per_step"]) * scale(r, float(t["alpha"]))
    return {"load_s": float(t["load_s"]), "run_s": run, "decode_s": float(t.get("decode_s") or 0.0),
            "total_s": float(t["load_s"]) + run + float(t.get("decode_s") or 0.0), "ratio": r}


def faceswap_seconds(preset: str, frames: int, fs: dict | None = None) -> dict[str, float]:
    fs = fs or FACESWAP
    p = fs["presets"].get(preset) or fs["presets"]["quality"]
    gen, post = frames / float(p["gen_fps"]), frames * float(fs["post_s_per_frame"])
    return {"load_s": float(fs["load_s"]), "gen_s": gen, "post_s": post, "total_s": float(fs["load_s"]) + gen + post}


def from_history(jobs: list[dict]) -> dict[str, dict[str, Any]]:
    """Measurements from COMPLETED video / face-swap jobs of Bossman's own journal; they override the tables."""
    wan: dict[str, list[tuple[float, float, float]]] = {}
    swap: dict[str, list[tuple[float, float, float]]] = {}
    for j in jobs:
        if j.get("status") != "completed" or j.get("kind") not in VIDEO_KINDS:
            continue
        st = _stages(j)
        if not {"loading", "generating", "postprocessing", "completed"} <= st.keys():
            continue
        load, gen, post = (st["generating"] - st["loading"], st["postprocessing"] - st["generating"],
                           st["completed"] - st["postprocessing"])
        if load <= 0 or gen <= 0 or post < 0:
            continue
        p = j.get("params") or {}
        if j.get("kind") == "faceswap":
            frames = (j.get("progress") or {}).get("max")
            if isinstance(frames, int) and frames > 0:
                swap.setdefault(str(p.get("preset") or "fast"), []).append((load, frames / gen, post / frames))
            continue
        base = TABLE.get(str(j.get("model")))
        steps = p.get("steps") or (base or {}).get("fixed_steps")
        w, h, fr = p.get("width"), p.get("height"), p.get("frames")
        if not base or not steps or not w or not h or not fr:
            continue
        r = int(w) * int(h) * int(fr) / float(base["ref"])
        wan.setdefault(str(j.get("model")), []).append((load, gen / int(steps) / scale(r, float(base["alpha"])), post))
    out: dict[str, dict[str, Any]] = {}
    for m, v in wan.items():
        out[m] = {**TABLE[m], "kind": "measured", "n": len(v), "load_s": round(mean(a for a, _, _ in v), 1),
                  "s_per_step": round(mean(b for _, b, _ in v), 2), "decode_s": round(mean(c for _, _, c in v), 1),
                  "basis": f"{len(v)} зап. из журнала Bossman"}
    if swap:
        presets = {k: dict(v) for k, v in FACESWAP["presets"].items()}
        rows = [x for v in swap.values() for x in v]
        for k, v in swap.items():
            presets[k] = {"kind": "measured", "n": len(v), "gen_fps": round(mean(b for _, b, _ in v), 2),
                          "basis": f"{len(v)} зап. из журнала Bossman"}
        out[FACESWAP_ID] = {**FACESWAP, "load_s": round(mean(a for a, _, _ in rows), 1),
                            "post_s_per_frame": round(mean(c for _, _, c in rows), 3), "presets": presets}
    return out


def timing_for(model_id: str, history: dict[str, dict] | None = None) -> dict[str, Any]:
    if model_id == FACESWAP_ID:
        return dict((history or {}).get(FACESWAP_ID) or FACESWAP)
    return dict((history or {}).get(model_id) or TABLE.get(model_id) or FALLBACK)


# ------------------------------------------------------------------------------------------------- queue (video jobs)
def job_total(job: dict, history: dict[str, dict] | None = None) -> dict[str, float]:
    p = job.get("params") or {}
    if job.get("kind") == "faceswap":
        frames = (job.get("progress") or {}).get("max")
        if not isinstance(frames, int) or frames <= 0:
            frames = max(1, round(int((job.get("inputs") or {}).get("video") or 0) / BYTES_PER_FRAME))
        return {**faceswap_seconds(str(p.get("preset") or "fast"), frames, timing_for(FACESWAP_ID, history)), "frames": frames}
    t = timing_for(str(job.get("model")), history)
    steps = int(p.get("steps") or t.get("fixed_steps") or 20)
    return wan_seconds(t, steps, int(p.get("width") or 480), int(p.get("height") or 272), int(p.get("frames") or 17))


def running_view(job: dict, history: dict[str, dict] | None, now: float) -> dict[str, Any]:
    """Progress of a running video job. REAL where Bossman reports steps/frames, else a time estimate; never 100 %."""
    tot = job_total(job, history)
    total = tot["total_s"]
    st = _stages(job)
    stage = job.get("stage") or job.get("status")
    started = _ts(job.get("started_at")) or min(st.values(), default=None)
    elapsed = max(float(job.get("elapsed_s") or 0.0), now - started if started is not None else 0.0)
    p = job.get("progress") or {}
    value, mx = p.get("value"), p.get("max")
    has = isinstance(value, int) and isinstance(mx, int) and mx > 0 and value > 0
    if job.get("kind") == "faceswap":
        if stage == "postprocessing":
            post_at = st.get("postprocessing", now)
            left = max(tot["post_s"] - (now - post_at), 0.0)
            pct = min(elapsed / max(elapsed + left, 1.0) * 100.0, 99.0)
            return {"percent": pct, "real": False, "step": None, "steps": None, "unit": "кадр",
                    "phase": "проверка позы и сборка", "remaining_s": left if left > 1.0 else None, "elapsed_s": elapsed,
                    "label": "оценка по замеру", "total_s": total}
        if has and stage == "generating":
            gen_at = st.get("generating")
            measured_pace = gen_at is not None and value >= 2
            pace = (now - gen_at) / value if measured_pace else tot["gen_s"] / mx
            left = max(mx - value, 0) * max(pace, 0.01) + tot["post_s"]
            pct = min(elapsed / max(elapsed + left, 1.0) * 100.0, 99.0)
            return {"percent": pct, "real": True, "step": value, "steps": mx, "unit": "кадр", "phase": "замена лица",
                    "remaining_s": left if measured_pace else None, "elapsed_s": elapsed,
                    "label": "кадры — по счётчику FaceFusion, сборка — оценка", "total_s": total}
    elif has and p.get("kind") == "steps":
        gen_at = st.get("generating")
        decoding = value >= mx
        if gen_at is not None and value >= 2:        # the ETA comes from the pace MEASURED on this job, never the table
            per_step = max((now - gen_at) / value, 0.1)
            left = (0.0 if decoding else max(mx - value, 0) * per_step) + tot.get("decode_s", 0.0)
        else:
            left = None
        return {"percent": min(value / mx * 100.0, 99.0), "real": True, "step": value, "steps": mx, "unit": "шаг",
                "phase": "декодирование и сборка" if decoding else "генерация", "remaining_s": left,
                "elapsed_s": elapsed, "label": "по шагам генератора", "total_s": total}
    measured = timing_for(FACESWAP_ID if job.get("kind") == "faceswap" else str(job.get("model")), history).get("kind") == "measured"
    if stage == "loading":
        return {**loading_view(elapsed, total, measured), "total_s": total}
    phase = {"postprocessing": "сборка файла", "queued": "ожидание"}.get(stage, "генерация")
    return {"percent": min(elapsed / total * 100.0, 95.0) if total > 0 else 0.0, "real": False, "step": None,
            "steps": None, "unit": None, "phase": phase, "remaining_s": estimate_left(total, elapsed),
            "elapsed_s": elapsed, "label": "оценка по времени", "total_s": total}
