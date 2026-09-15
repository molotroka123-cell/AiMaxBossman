"""Shadow decision telemetry: what was recommended, what happened, and the gap.

A shadow router that is never compared against production is not evidence, it
is an opinion nobody checked. This module records both halves so the comparison
is possible before anyone considers granting the router authority:

    recommendation + ranked alternatives + utility terms   (before)
    chosen production path + actual outcome + regret       (after)

`regret` is deliberately coarse. There is no counterfactual — the alternative
was not run, so its outcome is unknown, and a decimal "regret" would be a
fabricated measurement of a thing that did not happen. What CAN be stated
honestly is the relationship between the recommendation and the observed
result:

    agreed_ok        production followed the recommendation and it worked
    agreed_failed    production followed it and it failed — the recommendation
                     was wrong, which is the most useful record of all
    diverged_ok      production ignored it and succeeded — the router would
                     have been worse, or at least not better
    diverged_failed  production ignored it and failed — the router MIGHT have
                     been better, and this is a hypothesis, not a finding
    unknown          no outcome observed yet

Nothing here writes task state, and nothing here can be read back as
authorization.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa

from ..db import events as events_t, utcnow

KIND_SHADOW = "reality.shadow_decision"
KIND_OUTCOME = "reality.shadow_outcome"

AGREED_OK = "agreed_ok"
AGREED_FAILED = "agreed_failed"
DIVERGED_OK = "diverged_ok"
DIVERGED_FAILED = "diverged_failed"
UNKNOWN = "unknown"


@dataclass
class ShadowRecord:
    task_id: int | None
    run_id: int | None
    recommended: str | None
    ranked: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    production_path: str | None = None
    outcome: str | None = None
    regret: str = UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "run_id": self.run_id,
                "recommended": self.recommended, "ranked": self.ranked,
                "reason": self.reason, "production_path": self.production_path,
                "outcome": self.outcome, "regret": self.regret,
                "at": datetime.now(timezone.utc).isoformat()}


def regret_of(recommended: str | None, production_path: str | None,
              outcome: str | None) -> str:
    """Name the relationship. Never invents a counterfactual result."""
    if outcome not in ("ok", "failed"):
        return UNKNOWN
    if recommended is None or production_path is None:
        return UNKNOWN
    agreed = recommended == production_path
    if agreed:
        return AGREED_OK if outcome == "ok" else AGREED_FAILED
    return DIVERGED_OK if outcome == "ok" else DIVERGED_FAILED


async def _emit(svc, kind: str, payload: dict[str, Any]) -> None:
    """One journal, not two.

    `EventBus.emit` already persists into `events` (and redacts on the way), so
    writing the row here as well produced two records of a single decision —
    which the scoreboard then counted twice. Going through the bus keeps the
    live feed and the history telling the same story, which is the whole reason
    that table exists."""
    await svc.bus.emit(kind, **payload)


async def record_shadow(svc, decision, *, task_id: int | None = None,
                        run_id: int | None = None) -> ShadowRecord:
    """Store what the shadow router recommended, before production acts."""
    selected = getattr(decision, "selected", None)
    record = ShadowRecord(
        task_id=task_id, run_id=run_id,
        recommended=(selected.path if selected is not None else None),
        ranked=[s.terms() for s in getattr(decision, "ranked", ())],
        reason=str(getattr(decision, "reason", "")))
    await _emit(svc, KIND_SHADOW, record.to_dict())
    return record


async def record_outcome(svc, record: ShadowRecord, *, production_path: str,
                         outcome: str) -> ShadowRecord:
    """Close the loop with what production actually did and how it went."""
    record.production_path = production_path
    record.outcome = outcome
    record.regret = regret_of(record.recommended, production_path, outcome)
    await _emit(svc, KIND_OUTCOME, record.to_dict())
    return record


async def history(svc, *, limit: int = 100) -> list[dict[str, Any]]:
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(events_t).where(
            events_t.c.kind.in_((KIND_SHADOW, KIND_OUTCOME))).order_by(
            events_t.c.id.desc()).limit(max(1, min(int(limit), 500))))).fetchall()
    return [dict(r._mapping) for r in rows]


async def scoreboard(svc, *, limit: int = 500) -> dict[str, Any]:
    """How often the shadow router agreed with production, and how that went.

    Reported as counts, not as an accuracy percentage: a percentage over a
    handful of runs is a number that looks like evidence and is not."""
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(events_t.c.data).where(
            events_t.c.kind == KIND_OUTCOME).order_by(
            events_t.c.id.desc()).limit(max(1, min(int(limit), 2000))))).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        data = row[0]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                continue
        if not isinstance(data, dict):
            continue
        key = str(data.get("regret") or UNKNOWN)
        counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values())
    return {"observations": total, "by_regret": counts,
            "authoritative": False,
            "note": ("теневой маршрутизатор не управляет исполнением; "
                     "эти счётчики — основание для будущего решения, а не оно само")}
