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
                 retry: RetryPolicy | None = None) -> None:
        self.store, self.scheduler, self.journal = store, scheduler, journal
        self.retry = retry or RetryPolicy()

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

    def release(self, work_id: str, node_id: str) -> bool:
        return self.store.release_claim(work_id, node_id)

    def complete(self, work_id: str) -> bool:
        return self.store.dequeue(work_id)

    # ------------------------------------------------------------ retries

    def on_failure(self, work_id: str, mission_id: str, *, reason: str, attempts: int,
                   payload: dict[str, Any] | None = None) -> tuple[FailureClass, str]:
        """Решение после провала: REQUEUE (с задержкой), WAIT_HUMAN, DEAD_LETTER."""
        fc = classify_failure(reason)
        if fc == FailureClass.NEVER_RETRY:
            self._dead(work_id, mission_id, reason, fc, attempts, payload)
            return fc, "DEAD_LETTER"
        if fc == FailureClass.HUMAN_REQUIRED:
            self.store.release_claim(work_id)
            return fc, "WAIT_HUMAN"
        if attempts >= self.retry.max_attempts:
            self._dead(work_id, mission_id, reason, fc, attempts, payload)
            return fc, "DEAD_LETTER"
        self.store.release_claim(work_id)
        return fc, f"REQUEUE_AFTER_{self.retry.delay_for(attempts):.0f}s"

    def _dead(self, work_id: str, mission_id: str, reason: str, fc: FailureClass, attempts: int, payload) -> None:
        self.store.dead_letter(work_id, mission_id, reason=reason, failure_class=fc.value, attempts=attempts,
                               payload=dict(payload or {}))
        self.store.dequeue(work_id)
        if self.journal:
            self.journal.emit(FleetEventType.TASK_DEAD_LETTERED, mission_id=mission_id, work_id=work_id,
                              payload={"reason": reason[:200], "class": fc.value, "attempts": attempts})

    def dead_letters(self) -> list[dict[str, Any]]:
        return self.store.dead_letters()

    def requeue_dead_letter(self, work_id: str, *, by: str) -> bool:
        """Явное решение человека/политики; модель не может вернуть работу из карантина."""
        if not (by.startswith("human:") or by.startswith("policy:")):
            raise PermissionError("dead-letter requeue requires a human:* or policy:* principal")
        item = next((d for d in self.store.dead_letters() if d["work_id"] == work_id), None)
        if item is None:
            return False
        self.store.mark_requeued(work_id)
        return self.store.enqueue(work_id, item["mission_id"], 5, item["payload"].get("requirement", {}), item["payload"])


def _req(raw: dict[str, Any]) -> PlacementRequirement:
    return PlacementRequirement(
        capabilities=tuple(raw.get("capabilities") or ()), pools=tuple(raw.get("pools") or ()),
        min_ram_gb=float(raw.get("min_ram_gb", 0.0)), min_gpu_memory_gb=float(raw.get("min_gpu_memory_gb", 0.0)),
        required_models=tuple(raw.get("required_models") or ()), allowed_os=tuple(raw.get("allowed_os") or ()),
        privacy=str(raw.get("privacy", "private")), contains_secrets=bool(raw.get("contains_secrets", False)),
        artifacts=tuple(raw.get("artifacts") or ()), artifact_bytes=int(raw.get("artifact_bytes", 0)),
        max_load=float(raw.get("max_load", 0.9)), anti_affinity_domains=tuple(raw.get("anti_affinity_domains") or ()),
        prefer_node=str(raw.get("prefer_node", "")))
