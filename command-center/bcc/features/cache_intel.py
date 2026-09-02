"""PASS3 — Provider Cache Economics + Cognitive Reuse Intelligence (API for the
existing dashboard). Читает нормализованные события `cache.observation` из
EventBus (числа/хэши), агрегирует measured/estimated/unknown РАЗДЕЛЬНО и
показывает observe-only сигналы детектора и advisory-only советы.

Флаги (все OFF по умолчанию, кроме безопасной числовой телеметрии):
  BOSSMAN_CACHE_TELEMETRY_V2   — эмиссия наблюдений (engine), default ON
  BOSSMAN_CONTEXT_WASTE_OBSERVE — детектор потерь контекста (observe only)
  BOSSMAN_CACHE_ADVISOR        — advisory-only советы
Hit rate — диагностика, не KPI: панель отдаёт VerifiedSuccess/false-success/
stale-degraded рядом с экономикой.
"""
from __future__ import annotations

import dataclasses
import os

import sqlalchemy as sa
from fastapi import APIRouter, Request

from ..db import tasks as tasks_t
from ..v2.tables import evaluations as evals_t
from . import Feature

router = APIRouter()
OBS_KEYS = {"event_version", "timestamp", "task_id_hash", "session_id_hash", "provider", "model", "route",
            "ttl", "cache_control_applied", "prefix_hash", "prefix_tokens", "fresh_input_tokens",
            "cache_read_tokens", "cache_write_tokens", "output_tokens", "state", "miss_reason",
            "actual_cost_usd", "baseline_cost_usd", "baseline_is_estimate", "verified_success",
            "environment_fingerprint", "security_context_hash"}


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


async def _observations(svc, limit: int = 500) -> list[dict]:
    rows = await svc.bus.recent(limit)
    out = []
    for r in rows:
        if r.get("kind") != "cache.observation":
            continue
        data = r.get("data") if isinstance(r.get("data"), dict) else r
        out.append({k: data.get(k) for k in OBS_KEYS if k in data})
    return out


async def economics(svc) -> dict:
    from .._shared import cache_observation as co
    obs = await _observations(svc)
    if co is None:
        return {"available": False, "reason": "shared contract unavailable", "observations": len(obs)}
    log = co.ObservationLog(capacity=1000)
    dropped = 0
    for o in obs:
        try:
            log.record(o)
        except ValueError:
            dropped += 1
    s = log.summary()
    measured = s["measured_actual_cost_usd"]
    baseline = s["estimated_baseline_cost_usd"]
    saved = None
    if measured is not None and baseline is not None and s["unknown_cost_requests"] == 0:
        saved = round(baseline - measured, 6)
    by_route: dict[str, int] = {}
    ttl_dist: dict[str, int] = {}
    for o in log.items:
        by_route[o["route"]] = by_route.get(o["route"], 0) + 1
        ttl_dist[str(o.get("ttl"))] = ttl_dist.get(str(o.get("ttl")), 0) + 1
    return {
        "available": True, "kind": "provider_cache_economics",
        "measured": {"counts": s["counts"], "eligible_requests": s["eligible_requests"],
                     "hit_rate_percent": s["hit_rate_percent"], "tokens": s["tokens"],
                     "actual_cost_usd": measured, "degraded_events": s["degraded_events"],
                     "unknown_events": s["unknown_events"]},
        "estimated": {"baseline_cost_usd": baseline, "saved_usd": saved,
                      "note": "baseline is a counterfactual all-fresh estimate; saved is null when any cost is unknown"},
        "unknown": {"cost_requests": s["unknown_cost_requests"],
                    "cache_control_without_usage": s["cache_control_without_usage"], "dropped_invalid": dropped},
        "by_route": by_route, "ttl_distribution": ttl_dist,
        "warning": ("cache_control applied but no provider usage evidence — savings cannot be claimed"
                    if s["cache_control_without_usage"] else None),
        "hit_rate_is_diagnostic_not_kpi": True,
    }


async def intelligence(svc) -> dict:
    """Cognitive Reuse Intelligence: verified success по evaluations, false-success,
    stale/degraded, learning-кандидаты — measured где есть данные, иначе unknown."""
    async with svc.db.session() as s:
        total = (await s.execute(sa.select(sa.func.count()).select_from(evals_t))).scalar() or 0
        passed = (await s.execute(sa.select(sa.func.count()).select_from(evals_t)
                                  .where(evals_t.c.passed.is_(True)))).scalar() or 0
        completed = (await s.execute(sa.select(sa.func.count()).select_from(tasks_t)
                                     .where(tasks_t.c.status == "completed"))).scalar() or 0
    obs = await _observations(svc)
    verified_success = (round(passed / total, 3) if total else None)
    panel = {"available": True, "kind": "cognitive_reuse_intelligence",
             "measured": {"evaluations": total, "verified_evaluations": passed,
                          "verified_success_rate": verified_success, "completed_tasks": completed,
                          "stale_or_degraded_cache_events": sum(1 for o in obs if o.get("state") == "DEGRADED"),
                          "fresh_observation_override_count": None},
             "estimated": {}, "unknown": {"false_success_rate": "requires re-verification data",
                                          "same_model_ab_reuse_on_vs_off": "no A/B run recorded",
                                          "time_to_resume": "not instrumented"},
             "learning_candidates": {"promoted": 0, "rolled_back": 0, "quarantined": 0,
                                     "source": "learning_guard (no candidates registered)"},
             "flags": {"BOSSMAN_CONTEXT_WASTE_OBSERVE": _flag("BOSSMAN_CONTEXT_WASTE_OBSERVE"),
                       "BOSSMAN_CACHE_ADVISOR": _flag("BOSSMAN_CACHE_ADVISOR"),
                       "BOSSMAN_COGNITIVE_REUSE_EXPERIMENT": _flag("BOSSMAN_COGNITIVE_REUSE_EXPERIMENT"),
                       "BOSSMAN_AUTONOMY_TRAINER_SHADOW": _flag("BOSSMAN_AUTONOMY_TRAINER_SHADOW")}}
    from .._shared import cache_intelligence as ci, cache_observation as co
    if ci is not None and _flag("BOSSMAN_CONTEXT_WASTE_OBSERVE"):
        panel["waste_signals"] = [dataclasses.asdict(s_) for s_ in ci.detect_context_waste(obs)]
    else:
        panel["waste_signals"] = None
    if ci is not None and co is not None and _flag("BOSSMAN_CACHE_ADVISOR"):
        log = co.ObservationLog(capacity=1000)
        for o in obs:
            try:
                log.record(o)
            except ValueError:
                pass
        panel["advice"] = [dataclasses.asdict(a) for a in ci.cache_advice(log.summary())]
    else:
        panel["advice"] = None
    return panel


@router.get("/cache/economics")
async def get_economics(request: Request):
    return await economics(request.app.state.svc)


@router.get("/cache/intelligence")
async def get_intelligence(request: Request):
    return await intelligence(request.app.state.svc)


FEATURE = Feature(name="cache_intel", router=router)
