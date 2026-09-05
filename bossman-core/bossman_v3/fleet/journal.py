"""Durable Fleet event journal (§25) с дедупликацией по event_id.

Только операционные факты. Событие ≠ доказательство исполнения: журнал —
история и аудит, подтверждение даёт нижний слой. Секретов здесь нет по
построению: payload проходит ту же редакцию, что и контекст V3.1.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from ..memory.assembler import redact
from .models import FleetEventType
from .store import FleetStore


class FleetEventJournal:
    def __init__(self, store: FleetStore) -> None:
        self.store = store
        self.deduplicated = 0

    @staticmethod
    def event_id(type_: FleetEventType, *, mission_id: str, work_id: str, node_id: str, payload: dict, ts: float) -> str:
        raw = json.dumps([type_.value, mission_id, work_id, node_id, payload, round(ts, 3)], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def emit(self, type_: FleetEventType, *, mission_id: str = "", work_id: str = "", node_id: str = "",
             payload: dict[str, Any] | None = None, ts: float | None = None, event_id: str | None = None) -> bool:
        ts = time.time() if ts is None else ts
        clean = json.loads(redact(json.dumps(dict(payload or {}), ensure_ascii=False, default=str)))
        eid = event_id or self.event_id(type_, mission_id=mission_id, work_id=work_id, node_id=node_id, payload=clean, ts=ts)
        ok = self.store.append_event(eid, type_.value, ts, clean, mission_id=mission_id, work_id=work_id, node_id=node_id)
        if not ok:
            self.deduplicated += 1
        return ok

    def events(self, *, mission_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        return self.store.events(mission_id=mission_id, limit=limit)
