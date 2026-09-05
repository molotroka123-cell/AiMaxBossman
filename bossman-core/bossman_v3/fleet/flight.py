"""DistributedFlightRecorder — durable полётная запись работы во флоте со
строгим автоматом состояний.

Разделение состояний — единственный смысл этого модуля:

  PLANNED → QUEUED → PLACED → LEASED → DISPATCHED → EXECUTING → OBSERVED →
  VERIFYING → VERIFIED

`VERIFIED` достигается только через OBSERVED/VERIFYING и только со ссылками на
улики нижнего слоя из доверенных источников (журнал V3 / верификация V2).
Незаконный переход (PLACED → VERIFIED, текст → VERIFIED) — исключение, не
предупреждение.

MutationIdempotencyKey: каждая подтверждённая мутация записывается под
ключом (миссия, работа, шаг, действие) один раз. Второй VERIFIED под тем же
ключом — предотвращённый дубликат: он считается в метриках и НЕ считается
новым исполнением.
"""
from __future__ import annotations

import time
from typing import Iterable

from ..organization.contracts import TRUSTED_EVIDENCE_SOURCES
from .journal import FleetEventJournal
from .models import (FleetEventType, FlightRecord, FlightState, IllegalTransition, LEGAL_TRANSITIONS,
                     mutation_key)
from .store import FleetStore


class DistributedFlightRecorder:
    def __init__(self, store: FleetStore, journal: FleetEventJournal | None = None) -> None:
        self.store = store
        self.journal = journal
        self.duplicate_preventions = 0

    # ------------------------------------------------------------ records

    def open(self, work_id: str, mission_id: str) -> FlightRecord:
        f = self.store.flight(work_id)
        if f is None:
            f = FlightRecord(work_id=work_id, mission_id=mission_id, updated_ts=time.time())
            self.store.save_flight(f)
        return f

    def get(self, work_id: str) -> FlightRecord | None:
        return self.store.flight(work_id)

    def transition(self, f: FlightRecord, to: FlightState, *, reason: str = "", node_id: str | None = None,
                   lease_id: str | None = None, fence: int | None = None,
                   evidence_refs: Iterable[str] = ()) -> FlightRecord:
        if to not in LEGAL_TRANSITIONS[f.state]:
            raise IllegalTransition(f"{f.work_id}: {f.state.value} → {to.value} is not a legal flight transition")
        refs = list(evidence_refs)
        if to == FlightState.VERIFIED:
            trusted = [r for r in refs if r.startswith(TRUSTED_EVIDENCE_SOURCES)]
            if not trusted:
                raise IllegalTransition(f"{f.work_id}: VERIFIED requires evidence refs from a trusted lower layer "
                                        f"({', '.join(TRUSTED_EVIDENCE_SOURCES)}); got {refs!r}")
        f.history.append({"from": f.state.value, "to": to.value, "ts": time.time(), "reason": reason[:300],
                          "node_id": node_id or f.node_id})
        f.state = to
        f.reason = reason[:500]
        f.updated_ts = time.time()
        if node_id is not None:
            f.node_id = node_id
        if lease_id is not None:
            f.lease_id = lease_id
        if fence is not None:
            f.fence = fence
        for r in refs:
            if r not in f.evidence_refs:
                f.evidence_refs.append(r)
        if to in (FlightState.PLACED,):
            f.attempt += 1
        self.store.save_flight(f)
        if self.journal is not None:
            ev = _EVENT_FOR_STATE.get(to)
            if ev is not None:
                self.journal.emit(ev, mission_id=f.mission_id, work_id=f.work_id, node_id=f.node_id,
                                  payload={"state": to.value, "reason": reason[:200], "attempt": f.attempt})
        return f

    # ------------------------------------------------------- idempotency

    def record_verified_mutation(self, f: FlightRecord, *, step_id: str, action: dict | None,
                                 evidence_ref: str) -> bool:
        """True — новая подтверждённая мутация; False — дубликат предотвращён."""
        if not evidence_ref.startswith(TRUSTED_EVIDENCE_SOURCES):
            raise IllegalTransition(f"mutation evidence must come from a trusted layer, got {evidence_ref!r}")
        key = mutation_key(f.mission_id, f.work_id, step_id, action)
        fresh = self.store.record_verified_mutation(key, mission_id=f.mission_id, work_id=f.work_id, step_id=step_id,
                                                   node_id=f.node_id, evidence_ref=evidence_ref)
        if fresh:
            if step_id not in f.verified_steps:
                f.verified_steps.append(step_id)
                self.store.save_flight(f)
        else:
            self.duplicate_preventions += 1
            if self.journal is not None:
                self.journal.emit(FleetEventType.DUPLICATE_PREVENTED, mission_id=f.mission_id, work_id=f.work_id,
                                  node_id=f.node_id, payload={"step_id": step_id, "mutation_key": key})
        return fresh

    def verified_step_ids(self, mission_id: str, work_id: str) -> set[str]:
        return {m["step_id"] for m in self.store.verified_mutations(mission_id=mission_id) if m["work_id"] == work_id}


_EVENT_FOR_STATE = {
    FlightState.QUEUED: FleetEventType.TASK_QUEUED, FlightState.PLACED: FleetEventType.TASK_PLACED,
    FlightState.DISPATCHED: FleetEventType.TASK_DISPATCHED, FlightState.OBSERVED: FleetEventType.TASK_OBSERVED,
    FlightState.VERIFIED: FleetEventType.TASK_VERIFIED, FlightState.FAILED: FleetEventType.TASK_FAILED,
    FlightState.BLOCKED: FleetEventType.TASK_BLOCKED, FlightState.NODE_LOST: FleetEventType.TASK_NODE_LOST,
}
