"""Keeps the MEASURED health of the models that are in use from aging out silently.

A model's health record is a measurement with a shelf life (``model_health.STALE_AFTER_SECONDS``, 30 min): a success
older than that is history, so after a restart or half an hour of silence ``models`` / ``providers`` fall to STALE and
``ready`` turns false on an installation that works (12.10: every record dated 22.09-08.10, local ports down after a
reboot). Nothing re-measured them until someone pressed «проверить» by hand.

This tick re-measures through the EXISTING mechanism - ``Registry.test_model``, the very call behind
``POST /api/models/{id}/test`` - and nothing else:

* only models that are LOCAL or FREE (price known and zero): a paid request is never made here;
* only models that matter: referenced by an agent (model / fallback) or last measured healthy within two weeks;
* at most ``MAX_PER_CYCLE`` per cycle, ``PROBE_TIMEOUT_S`` each, ``CYCLE_BUDGET_S`` in total;
* a model inside its cooldown, one whose key was rejected (``unauthorized``) and one measured less than
  ``REFRESH_AFTER_S`` ago are left alone;
* a failure is recorded as what it is (silent / provider_down / timeout / throttled ...), never as health, and the
  readiness snapshot keeps saying so.

``BOSSMAN_MODEL_FRESHNESS=0`` switches it off.  ``GET /api/model-freshness`` shows the last cycle.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request

from .. import model_health as mh
from ..db import agents as agents_t, models as models_t
from ..providers import ProviderError
from . import Feature

router = APIRouter()

TICK_SECONDS = 600.0
FIRST_DELAY_S = 20.0
MAX_PER_CYCLE = 3
PROBE_TIMEOUT_S = 40.0
CYCLE_BUDGET_S = 100.0
#: Re-measure before the 30 min shelf life ends: the tick is 10 min, so a record never reaches 30 min.
REFRESH_AFTER_S = 900.0
KNOWN_GOOD_DAYS = 14

_state: dict[str, Any] = {"first_seen": None, "cycles": 0, "last_cycle_at": None, "last": [], "error": None}


def enabled() -> bool:
    return os.environ.get("BOSSMAN_MODEL_FRESHNESS", "1").strip().lower() not in {"0", "false", "off", "no"}


def _free_or_local(row: dict) -> bool:
    if (row.get("kind") or "local") == "local":
        return True
    return bool(row.get("pricing_known")) and not (row.get("price_in") or 0) and not (row.get("price_out") or 0)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _candidates(svc, now: datetime) -> tuple[list[tuple[dict, mh.HealthRecord]], dict[str, int]]:
    """Models worth re-measuring, oldest measurement first, plus why the others were skipped (counts)."""
    async with svc.db.session() as s:
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(models_t))).fetchall()]
        used: set[int] = set()
        for r in (await s.execute(sa.select(agents_t.c.model_id, agents_t.c.fallback_model_id))).fetchall():
            used.update(int(v) for v in r if v is not None)
    skipped: dict[str, int] = {}
    picked: list[tuple[dict, mh.HealthRecord]] = []
    horizon = now - timedelta(days=KNOWN_GOOD_DAYS)
    for row in rows:
        record = mh.HealthRecord.from_dict(row.get("health"))
        known_good = (record.last_success_at is not None and _aware(record.last_success_at) >= horizon)
        if row["id"] not in used and not known_good:
            skipped["not_in_use"] = skipped.get("not_in_use", 0) + 1
            continue
        if not _free_or_local(row):
            skipped["paid_model_not_probed"] = skipped.get("paid_model_not_probed", 0) + 1
            continue
        if record.status == mh.UNAUTHORIZED:
            skipped["key_rejected_owner_must_fix"] = skipped.get("key_rejected_owner_must_fix", 0) + 1
            continue
        if record.in_cooldown(now):
            skipped["cooldown"] = skipped.get("cooldown", 0) + 1
            continue
        checked = _aware(record.checked_at)
        if checked is not None and (now - checked).total_seconds() < REFRESH_AFTER_S:
            skipped["fresh"] = skipped.get("fresh", 0) + 1
            continue
        picked.append((row, record))
    picked.sort(key=lambda p: _aware(p[1].checked_at) or datetime.min.replace(tzinfo=timezone.utc))
    return picked, skipped


async def run_cycle(svc, *, now: datetime | None = None) -> dict[str, Any]:
    """One bounded re-measurement cycle. Returns what was probed and what happened."""
    now = now or datetime.now(timezone.utc)
    picked, skipped = await _candidates(svc, now)
    started = time.monotonic()
    probed: list[dict[str, Any]] = []
    for row, _record in picked[:MAX_PER_CYCLE]:
        remaining = CYCLE_BUDGET_S - (time.monotonic() - started)
        if remaining <= 1.0:
            skipped["cycle_budget"] = skipped.get("cycle_budget", 0) + 1
            continue
        model_id = int(row["id"])
        entry: dict[str, Any] = {"id": model_id, "alias": row.get("alias"), "kind": row.get("kind")}
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(svc.registry.test_model(model_id), min(PROBE_TIMEOUT_S, remaining))
            entry["outcome"] = mh.HEALTHY
        except asyncio.TimeoutError:
            detail = f"проверка не уложилась в {int(PROBE_TIMEOUT_S)} с"
            await svc.registry.record_model_health(model_id, mh.TIMEOUT, detail)
            entry.update(outcome=mh.TIMEOUT, detail=detail)
        except asyncio.CancelledError:
            raise
        except ProviderError as exc:
            record = await svc.registry.model_health(model_id)    # test_model already recorded the real kind
            entry.update(outcome=record.status, detail=str(exc)[:200])
        except Exception as exc:  # noqa: BLE001 - one model never stops the cycle
            entry.update(outcome="probe_error", detail=f"{type(exc).__name__}: {exc}"[:200])
        entry["seconds"] = round(time.monotonic() - t0, 2)
        probed.append(entry)
    result = {"at": now.isoformat(), "probed": probed, "skipped": skipped, "candidates": len(picked)}
    _state.update(cycles=_state["cycles"] + 1, last_cycle_at=result["at"], last=probed, error=None)
    return result


async def _tick(svc) -> None:
    if not enabled():
        return
    if _state["first_seen"] is None:
        _state["first_seen"] = time.monotonic()
    if time.monotonic() - _state["first_seen"] < FIRST_DELAY_S:
        return                                   # let the backend finish starting before the first probe
    try:
        await run_cycle(svc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        _state["error"] = f"{type(exc).__name__}: {exc}"[:200]
        raise


@router.get("/model-freshness")
async def freshness(request: Request):
    svc = request.app.state.svc
    picked, skipped = await _candidates(svc, datetime.now(timezone.utc))
    async with svc.db.session() as s:
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(models_t.c.id, models_t.c.health))).fetchall()]
    records = [mh.HealthRecord.from_dict(r.get("health")) for r in rows]
    return {
        "enabled": enabled(), "tick_seconds": TICK_SECONDS, "stale_after_seconds": mh.STALE_AFTER_SECONDS,
        "limits": {"max_per_cycle": MAX_PER_CYCLE, "probe_timeout_s": PROBE_TIMEOUT_S,
                   "cycle_budget_s": CYCLE_BUDGET_S, "refresh_after_s": REFRESH_AFTER_S,
                   "paid_models": "never probed here"},
        "cycles": _state["cycles"], "last_cycle_at": _state["last_cycle_at"], "last_probed": _state["last"],
        "last_error": _state["error"],
        "models": {"total": len(records), "fresh_healthy": sum(1 for r in records if r.usable()),
                   "stale": sum(1 for r in records if r.stale), "due_for_probe": len(picked)},
        "skipped_reasons": skipped,
    }


FEATURE = Feature(name="model_freshness", router=router, tick=_tick, tick_seconds=TICK_SECONDS)
