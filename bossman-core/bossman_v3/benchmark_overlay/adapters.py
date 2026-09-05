"""Адаптеры durable-истины → события бенчмарка. ТОЛЬКО чтение.

Ничего из этого не является доказательством исполнения: доказательство живёт в
подписанных уликах журнала и вердиктах верификатора; здесь они лишь пересчитываются.
"""
from __future__ import annotations

import time
from typing import Any, Iterable

from .models import BenchmarkEvent


def _ev(kind: str, mission_id: str, source: str, **data: Any) -> BenchmarkEvent:
    return BenchmarkEvent(kind, mission_id, time.time(), dict(data), source=source)


def events_from_organization(store, mission_id: str) -> list[BenchmarkEvent]:
    """OrganizationStore → события: интерпретация контрактов, выбор команды,
    верификация результата, исполненные эффекты (по подписанным уликам),
    завершение миссии/родителя, запросы владельцу."""
    src = "adapter:organization"
    out: list[BenchmarkEvent] = []
    works = store.works(mission_id)
    mission = store.mission(mission_id)
    if not works:
        return out
    contracts = [w["contract"] for w in works]
    out.append(_ev("mission.interpreted", mission_id, src,
                   constraints_preserved=all(not c.problems() for c in contracts), contracts=len(contracts)))
    escalations = sum(len((c.metadata.get("runtime") or {}).get("failed_agents") or []) for c in contracts)
    teams = store.teams(mission_id)
    for i, t in enumerate(teams):
        slots = t.get("slots") or {}
        out.append(_ev("organization.selected", mission_id, src, team_fit="good" if slots.get("executor") else "missing_executor",
                       team_size=len(set(slots.values())), executors=1 if slots.get("executor") else 0,
                       attempts=1, escalations=escalations if i == 0 else 0, risk=t.get("risk")))
    failed_required: list[str] = []
    unverified_required: list[str] = []
    side_effect_required = any(c.side_effect for c in contracts)
    all_verified = True
    for w in works:
        c = w["contract"]
        r = store.result(w["work_id"])
        state = w["state"]
        if r is not None:
            out.append(_ev("verification.completed", mission_id, src, work_id=w["work_id"], verified=bool(r.verified),
                           executed=bool(r.executed), reviewed_by=r.reviewed_by))
            for e in r.evidence:
                if e.verified and e.signature_valid():
                    out.append(_ev("side_effect.executed", mission_id, src, work_id=w["work_id"],
                                   idempotency_key=e.source, evidence_kind=e.kind, ref=e.ref))
                    out.append(_ev("verification.accepted", mission_id, src, work_id=w["work_id"],
                                   signature_valid=True, evidence_age_s=0.0))
                elif e.verified:
                    out.append(_ev("verification.accepted", mission_id, src, work_id=w["work_id"],
                                   signature_valid=False, evidence_age_s=0.0))
            if r.cost is not None:
                out.append(_ev("resource.usage", mission_id, src, work_id=w["work_id"],
                               cost_usd=float(getattr(r.cost, "usd", 0.0) or 0.0),
                               tokens=float(getattr(r.cost, "tokens", 0) or 0),
                               gpu_seconds=float(getattr(r.cost, "gpu_seconds", 0) or 0)))
            if r.metadata.get("review") and r.metadata["review"].get("independent") is False:
                out.append(_ev("review.bypass", mission_id, src, work_id=w["work_id"]))
        if state == "failed":
            failed_required.append(w["work_id"]); all_verified = False
        elif state != "completed" or r is None or not r.verified:
            unverified_required.append(w["work_id"]); all_verified = False
    for row in store.tail(500, mission_id=mission_id):
        if row.get("event") == "work.blocked":
            out.append(_ev("approval.requested", mission_id, src, work_id=row.get("work_id"), unnecessary=False,
                           reason=str(row.get("detail") or "")[:200]))
    if mission and mission.get("state") == "completed":
        out.append(_ev("mission.completed", mission_id, src, side_effect_required=side_effect_required,
                       verified_side_effect=all_verified))
        out.append(_ev("parent.completed", mission_id, src, failed_required_children=failed_required,
                       unverified_required_children=unverified_required))
    return out


def events_from_fleet(plane, store, mission_id: str) -> list[BenchmarkEvent]:
    """FleetControlPlane + OrganizationStore → размещения и privacy-факты.
    privacy.violation = PRIVATE/LOCAL_ONLY работа, размещённая на узле уровня cloud."""
    src = "adapter:fleet"
    out: list[BenchmarkEvent] = []
    for w in store.works(mission_id):
        f = plane.flights.get(w["work_id"])
        if f is None or not f.node_id:
            continue
        node = plane.registry.node(f.node_id)
        node_class = "cloud" if node is not None and str(getattr(node, "privacy_level", "")).lower() == "cloud" else "local"
        privacy = str(getattr(w["contract"], "privacy", "private")).lower()
        cloud_eligible = privacy in ("cloud_ok", "public", "cloud")
        out.append(_ev("fleet.placed", mission_id, src, work_id=w["work_id"], node_id=f.node_id, node_class=node_class,
                       placement_fit="good", cloud_eligible=cloud_eligible, state=str(f.state.value if hasattr(f.state, "value") else f.state)))
        if node_class == "cloud" and not cloud_eligible:
            out.append(_ev("privacy.violation", mission_id, src, work_id=w["work_id"], node_id=f.node_id, privacy=privacy))
    for e in plane.journal.events(mission_id=mission_id, limit=2000):
        if e.get("type") == "DUPLICATE_PREVENTED":
            out.append(_ev("duplicate.prevented", mission_id, src, work_id=e.get("work_id")))
    return out


def events_from_task_journal(journal, mission_id: str, *, replayed_steps: Iterable[str] = ()) -> list[BenchmarkEvent]:
    """TaskJournal → side_effect.executed по подписанным закрытым шагам; повтор
    шага (замеченный тестом/рантаймом) → второй эффект с тем же ключом."""
    src = "adapter:task_journal"
    out: list[BenchmarkEvent] = []
    for s in journal.finished():
        ok = s.signature_valid(journal.task_id)
        key = f"{journal.task_id}/{s.step_id}"
        out.append(_ev("side_effect.executed", mission_id, src, idempotency_key=key, signed=ok))
        out.append(_ev("verification.accepted", mission_id, src, step_id=s.step_id, signature_valid=ok, evidence_age_s=0.0))
    for sid in replayed_steps:
        out.append(_ev("side_effect.executed", mission_id, src, idempotency_key=f"{journal.task_id}/{sid}", replay=True))
    return out
