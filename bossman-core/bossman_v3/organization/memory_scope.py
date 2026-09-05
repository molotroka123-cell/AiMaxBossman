"""Organizational Memory & Knowledge Flow (§12) — скоупы поверх V3-памяти.

Второй памяти здесь НЕТ. Состояние шагов остаётся в TaskJournal, провалы — в
FailureMemory (по одному корню на отдел, чтобы Engineering не читал провалы
Trading). Этот модуль добавляет то, чего в V3.1 не было: скоуп знания,
происхождение, уверенность, срок годности и ЯВНУЮ политику обмена между
скоупами.

Скоупы — строки вида `organization`, `department:<id>`, `project:<id>`,
`mission:<id>`, `team:<id>`, `agent:<id>`. Чтение из скоупа возвращает только
его факты: контаминация «проект A → проект B» невозможна без `export`, а
экспорт проходит через allowlist видов у отдела-источника и сохраняет
происхождение (source_scope, provenance, confidence, timestamp, valid_until).

Секреты редактируются ДО записи тем же `redact`, что и контекст V3.1.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..memory.assembler import redact
from ..memory.failure_memory import FailureMemory
from .models import Department

ORG_SCOPE = "organization"


class ExportBlocked(PermissionError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Fact:
    fact_id: str
    scope: str
    kind: str
    payload: Mapping[str, Any]
    provenance: str
    confidence: float
    created_at: str
    source_scope: str = ""
    valid_until: str = ""

    def valid(self, now: str | None = None) -> bool:
        if not self.valid_until:
            return True
        return (now or _now()) <= self.valid_until


class ScopedKnowledge:
    def __init__(self, store, *, failure_root: str | Path | None = None) -> None:
        self.store = store
        self._failure_root = Path(failure_root) if failure_root else None

    # ------------------------------------------------------------ writing

    @staticmethod
    def _fact_id(scope: str, kind: str, payload: Mapping[str, Any]) -> str:
        raw = json.dumps([scope, kind, payload], sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def publish(self, scope: str, kind: str, payload: Mapping[str, Any], *, provenance: str,
                confidence: float = 1.0, valid_until: str = "", source_scope: str = "") -> Fact:
        clean = json.loads(redact(json.dumps(dict(payload), ensure_ascii=False, default=str)))
        fact = Fact(self._fact_id(scope, kind, clean), scope, kind, clean, redact(provenance),
                    max(0.0, min(1.0, confidence)), _now(), source_scope or scope, valid_until)
        self.store.save_fact(fact.fact_id, scope, kind, {
            "payload": clean, "provenance": fact.provenance, "confidence": fact.confidence,
            "created_at": fact.created_at, "source_scope": fact.source_scope, "valid_until": valid_until})
        return fact

    # ------------------------------------------------------------ reading

    def read(self, scope: str, *, kind: str | None = None, include_expired: bool = False) -> list[Fact]:
        out = []
        for row in self.store.facts(scope):
            f = Fact(row["fact_id"], row["scope"], row["kind"], row.get("payload") or {},
                     str(row.get("provenance", "")), float(row.get("confidence", 0.0)),
                     str(row.get("created_at", "")), str(row.get("source_scope", "")),
                     str(row.get("valid_until", "")))
            if kind is not None and f.kind != kind:
                continue
            if not include_expired and not f.valid():
                continue
            out.append(f)
        return out

    # ------------------------------------------------------------- export

    def export(self, fact: Fact, *, to_scope: str, source_department: Department) -> Fact:
        """Явный перенос между скоупами. Вид факта обязан быть в allowlist
        отдела-источника; происхождение сохраняется, уверенность не растёт."""
        if fact.kind not in source_department.allowed_exports:
            raise ExportBlocked(f"department {source_department.department_id!r} does not export {fact.kind!r}")
        return self.publish(to_scope, fact.kind, fact.payload,
                            provenance=f"{fact.provenance} | exported from {fact.scope}",
                            confidence=fact.confidence, valid_until=fact.valid_until,
                            source_scope=fact.source_scope or fact.scope)

    # ----------------------------------------------------- failure memory

    def failure_memory(self, department_id: str) -> FailureMemory | None:
        """Память провалов V3.1 — отдельный корень на отдел (изоляция)."""
        if self._failure_root is None:
            return None
        return FailureMemory(self._failure_root / department_id)
