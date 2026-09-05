"""Context Assembler (V3.1): журнал + память → ограниченный пакет контекста.

Ничего из бюджетирования здесь не изобретается: отбор, дедуп, сохранение обеих
сторон конфликта и приоритеты уже реализованы в bossman_v3/data_guardian.
Ассемблер отвечает за три вещи, которых там нет:

  1. ЧТО вообще является кандидатом в контекст — производится из журнала
     (сделанные шаги с их чеками, следующий шаг, провалившиеся подходы,
     наблюдения), а не из транскрипта модели;
  2. ЖЁСТКИЙ потолок вызывающего — guardian по своей конструкции может
     превысить номинальный бюджет ради критичного (и это правильно для него),
     а вызывающему здесь нужен предсказуемый лимит окна модели;
  3. СЕКРЕТЫ не попадают в контекст никогда — редакция идёт до сборки, а не
     после, чтобы секрет не успел просочиться ни в текст, ни в provenance.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..data_guardian.guardian import ContextDataGuardian
from ..data_guardian.models import ContextItem, GuardianConfig
from .failure_memory import FailureMemory
from .journal import TaskJournal

# Приоритеты: 0-2 у guardian считаются обязательными. Следующий шаг и уже
# сделанное — то, без чего возобновление невозможно; провалившиеся подходы —
# то, без чего оно пойдёт по кругу.
P_NEXT, P_DONE, P_FAILED, P_NOTE = 0, 1, 2, 6

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|$)"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\b\s*[:=]\s*\S+"),
)
REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    out = str(text)
    for pat in _SECRET_PATTERNS:
        out = pat.sub(REDACTED, out)
    return out


def redact_data(value: Any) -> Any:
    """Preserve JSON structure while removing sensitive field values and spans."""
    if isinstance(value, Mapping):
        return {str(k): (REDACTED if re.search(
            r"(?i)(?:password|secret|authorization|private.?key|api.?key|access.?token|refresh.?token)", str(k))
            else redact_data(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_data(v) for v in value]
    return redact(value) if isinstance(value, str) else value


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


@dataclass(frozen=True)
class ContextPack:
    text: str
    tokens: int
    dropped: int
    provenance: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


class ContextAssembler:
    def __init__(self, *, failure_memory: FailureMemory | None = None,
                 guardian: ContextDataGuardian | None = None):
        self.failure_memory = failure_memory
        self._guardian = guardian

    # ------------------------------------------------------------- candidates

    def _items(self, journal: TaskJournal) -> list[ContextItem]:
        items: list[ContextItem] = []

        nxt = journal.next_step()
        if nxt is not None:
            items.append(ContextItem(
                item_id=f"next:{nxt.step_id}", category="plan", priority=P_NEXT,
                content=redact(f"СЛЕДУЮЩИЙ ШАГ [{nxt.step_id}] {nxt.intent}"),
                token_count=estimate_tokens(nxt.intent) + 8,
                importance=1.0, protected=True, source=f"journal:{journal.task_id}"))

        for s in journal.finished():
            effect = ""
            if s.receipt:
                effect = redact(json.dumps(dict(s.receipt), ensure_ascii=False, sort_keys=True))
            line = redact(f"СДЕЛАНО [{s.step_id}] {s.intent} — подтверждено; чек: {effect}")
            items.append(ContextItem(
                item_id=f"done:{s.step_id}", category="progress", priority=P_DONE,
                content=line, token_count=estimate_tokens(line),
                importance=0.9, protected=True,
                source=f"journal:{journal.task_id}", version=s.by or "unknown",
                metadata={"step_id": s.step_id, "at": s.updated_at}))

        if self.failure_memory is not None:
            for s in journal.remaining():
                for row in self.failure_memory.query(s.step_id):
                    line = redact(f"УЖЕ ПРОВАЛИЛОСЬ на [{s.step_id}]: "
                                  f"{row.get('approach','')} → {row.get('error','')}")
                    items.append(ContextItem(
                        item_id=f"failed:{s.step_id}:{len(items)}", category="failure",
                        priority=P_FAILED, content=line, token_count=estimate_tokens(line),
                        importance=0.85, protected=True,
                        source="failure_memory", metadata={"signature": s.step_id}))

        for i, n in enumerate(journal.notes):
            line = redact(str(n.get("text", "")))
            items.append(ContextItem(
                item_id=f"note:{i}", category="observation", priority=P_NOTE,
                content=line, token_count=estimate_tokens(line), importance=0.2,
                source=str(n.get("source") or "run"), metadata={"at": n.get("at", "")}))
        return items

    # -------------------------------------------------------------- assembly

    def assemble(self, journal: TaskJournal, *, budget_tokens: int = 16000,
                 model: str = "") -> ContextPack:
        if budget_tokens < 0:
            raise ValueError("budget_tokens must be nonnegative")
        items = self._items(journal)
        guardian = self._guardian or ContextDataGuardian(GuardianConfig(token_budget=budget_tokens))
        report = guardian.select(items)

        # Жёсткий потолок вызывающего: guardian мог оставить критичное сверх
        # номинального бюджета — для окна модели это всё равно нужно обрезать,
        # но обрезать по приоритету, а не случайно.
        header = redact(f"ЗАДАЧА {journal.task_id}" + (f" · модель {model}" if model else ""))
        if estimate_tokens(header) > budget_tokens:
            header = ""
        kept = []
        text = header
        for item in sorted(report.selected, key=lambda x: (x.priority, -x.importance)):
            candidate = "\n".join(x for x in [text, redact(str(item.content))] if x)
            if estimate_tokens(candidate) > budget_tokens:
                continue
            kept.append(item)
            text = candidate

        dropped = len(items) - len(kept)
        provenance = {i.item_id: {"source": i.source, "category": i.category,
                                  "version": i.version, "at": str(i.metadata.get("at", ""))}
                      for i in kept}
        return ContextPack(text=redact(text), tokens=estimate_tokens(text),
                           dropped=dropped, provenance=redact_data(provenance))
