"""Durable work queue with single-owner claim, retry classes and dead letter
(§20, §21).

Claim — атомарный CAS в SQLite (`UPDATE … WHERE claimed_by IS NULL`): при гонке
двух узлов ровно один получает строку. Claim не обходит ни способность, ни
приватность, ни разрешения — перед claim проверяется eligibility узла тем же
планировщиком, а после claim работа всё равно идёт через V3/V2 политику.

Retry: PERMISSION/UNSAFE — никогда; APPROVAL — ждать человека; NODE_OFFLINE —
перенос, если шаг безопасен; TIMEOUT — ограниченный backoff. После исчерпания —
dead letter (durable), а не вечный цикл.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .journal import FleetEventJournal
from .models import FailureClass, FleetEventType, NodeState, PlacementRequirement, RetryPolicy, classify_failure
from .scheduler import FleetScheduler
from .store import FleetStore


@dataclass(frozen=True)
class Claim:
    work_id: str
    node_id: str
    claim_fence: int
    payload: dict[str, Any]


class WorkQueue:
    def __init__(self, store: FleetStore, scheduler: FleetScheduler, journal: FleetEventJournal | None = None,
                 retry: RetryPolicy | None = None, authorize_requeue=None) -> None:
        self.store, self.scheduler, self.journal = store, scheduler, journal
        self.retry = retry or RetryPolicy()
        self.authorize_requeue = authorize_requeue or (lambda principal, work_id: False)

    def enqueue(self, work_id: str, mission_id: str, *, priority: int, requirement: PlacementRequirement,
                payload: dict[str, Any] | None = None) -> bool:
        ok = self.store.enqueue(work_id, mission_id, priority, requirement.__dict__ | {
            "capabilities": list(requirement.capabilities), "pools": list(requirement.pools),
            "required_models": list(requirement.required_models), "allowed_os": list(requirement.allowed_os),
            "artifacts": list(requirement.artifacts), "anti_affinity_domains": list(requirement.anti_affinity_domains)},
            dict(payload or {}))
        if ok and self.journal:
            self.journal.emit(FleetEventType.TASK_QUEUED, mission_id=mission_id, work_id=work_id,
                              payload={"priority": priority})
        return ok

    def eligible_for(self, node: NodeState) -> list[dict[str, Any]]:
        out = []
        for row in self.store.queue(unclaimed_only=True):
            req = _req(row["requirement"])
            if not self.scheduler.reject_reasons(node, req):
                out.append(row)
        return out

    def claim(self, node: NodeState, *, now: float | None = None) -> Claim | None:
        """Узел забирает первую пригодную ЕМУ работу. Проигравший гонку получает
        None и идёт к следующей строке — двух владельцев не бывает."""
        now = time.time() if now is None else now
        for row in self.eligible_for(node):
            fence = self.store.claim(row["work_id"], node.node_id, now)
            if fence is None:
                continue
            if self.journal:
                self.journal.emit(FleetEventType.TASK_CLAIMED, mission_id=row["mission_id"], work_id=row["work_id"],
                                  node_id=node.node_id, payload={"claim_fence": fence})
            return Claim(row["work_id"], node.node_id, fence, row["payload"])
        return None

    @staticmethod
    def _identity(claim: Claim):
        if not isinstance(claim, Claim):
            raise TypeError("a fenced Claim is required")
        return claim.work_id, claim.node_id, claim.claim_fence

    def release(self, claim: Claim) -> bool:
        with self.store.connect() as con:
            return con.execute("UPDATE fleet_work_queue SET claimed_by=NULL, claimed_ts=NULL "
                               "WHERE work_id=? AND claimed_by=? AND claim_fence=?",
                               self._identity(claim)).rowcount == 1

    def complete(self, claim: Claim) -> bool:
        with self.store.connect() as con:
            return con.execute("DELETE FROM fleet_work_queue WHERE work_id=? AND claimed_by=? AND claim_fence=?",
                               self._identity(claim)).rowcount == 1

    def on_failure(self, work_id: str, mission_id: str, *, claim: Claim, reason: str,
                   attempts: int = 0, payload: dict[str, Any] | None = None,
                   now: float | None = None) -> tuple[FailureClass, str]:
        import json
        now = time.time() if now is None else now
        identity = self._identity(claim)
        if claim.work_id != work_id:
            raise PermissionError("failure work identity mismatch")
        fc = classify_failure(reason)
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT * FROM fleet_work_queue WHERE work_id=? AND claimed_by=? "
                                  "AND claim_fence=? AND mission_id=?", (*identity, mission_id)).fetchone()
                if row is None:
                    raise PermissionError("stale queue claim")
                attempts = int(row["attempts"])  # persisted authority, never caller-controlled
                if fc == FailureClass.NEVER_RETRY or attempts >= self.retry.max_attempts:
                    body = json.loads(row["payload"])
                    body["requirement"] = json.loads(row["requirement"])
                    con.execute("INSERT OR REPLACE INTO fleet_dead_letter VALUES(?,?,?,?,?,?,?,0)",
                                (work_id, mission_id, reason[:2000], fc.value, attempts, json.dumps(body), now))
                    con.execute("DELETE FROM fleet_work_queue WHERE work_id=? AND claimed_by=? AND claim_fence=?", identity)
                    decision = "DEAD_LETTER"
                else:
                    waiting = fc == FailureClass.HUMAN_REQUIRED
                    delay = 0 if waiting else self.retry.delay_for(attempts)
                    con.execute("UPDATE fleet_work_queue SET claimed_by=NULL, claimed_ts=NULL, queue_state=?, "
                                "not_before=? WHERE work_id=? AND claimed_by=? AND claim_fence=?",
                                ("waiting_human" if waiting else "ready", now + delay, *identity))
                    decision = "WAIT_HUMAN" if waiting else f"REQUEUE_AFTER_{delay:.0f}s"
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return fc, decision

    def resume_waiting(self, work_id: str, *, by: str) -> bool:
        if not self.authorize_requeue(by, work_id):
            raise PermissionError("authenticated approval is required")
        with self.store.connect() as con:
            return con.execute("UPDATE fleet_work_queue SET queue_state='ready',not_before=0 "
                               "WHERE work_id=? AND queue_state='waiting_human'", (work_id,)).rowcount == 1

    def dead_letters(self) -> list[dict[str, Any]]:
        return self.store.dead_letters()

    def requeue_dead_letter(self, work_id: str, *, by: str) -> bool:
        """Явное решение человека/политики; модель не может вернуть работу из карантина."""
        if not self.authorize_requeue(by, work_id):
            raise PermissionError("dead-letter requeue requires authenticated authorization")
        import json
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                item = con.execute("SELECT * FROM fleet_dead_letter WHERE work_id=? AND requeued=0",
                                   (work_id,)).fetchone()
                if item is None:
                    con.execute("ROLLBACK")
                    return False
                body = json.loads(item["payload"])
                changed = con.execute(
                    "INSERT OR IGNORE INTO fleet_work_queue(work_id,mission_id,priority,requirement,payload,enqueued_ts) "
                    "VALUES(?,?,?,?,?,?)", (work_id, item["mission_id"], 5,
                                           json.dumps(body.get("requirement", {})), item["payload"], time.time())).rowcount
                if not changed:
                    con.execute("ROLLBACK")
                    return False
                con.execute("UPDATE fleet_dead_letter SET requeued=1 WHERE work_id=?", (work_id,))
                con.execute("COMMIT")
                return True
            except BaseException:
                con.execute("ROLLBACK")
                raise



def _req(raw: dict[str, Any]) -> PlacementRequirement:
    return PlacementRequirement(
        capabilities=tuple(raw.get("capabilities") or ()), pools=tuple(raw.get("pools") or ()),
        min_ram_gb=float(raw.get("min_ram_gb", 0.0)), min_gpu_memory_gb=float(raw.get("min_gpu_memory_gb", 0.0)),
        required_models=tuple(raw.get("required_models") or ()), allowed_os=tuple(raw.get("allowed_os") or ()),
        privacy=str(raw.get("privacy", "private")), contains_secrets=bool(raw.get("contains_secrets", False)),
        artifacts=tuple(raw.get("artifacts") or ()), artifact_bytes=int(raw.get("artifact_bytes", 0)),
        max_load=float(raw.get("max_load", 0.9)), anti_affinity_domains=tuple(raw.get("anti_affinity_domains") or ()),
        prefer_node=str(raw.get("prefer_node", "")))
