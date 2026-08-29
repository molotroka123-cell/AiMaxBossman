"""Stage 8 — Dataset Gate: траектории → кандидаты, но НИКОГДА напрямую в обучение.

Обязательный конвейер (non-negotiable #11 и #12):

    raw trajectory → sanitize → validate/evaluate → dataset candidate
                   → quality/human gate → training dataset

Запрещено: `raw logs -> fine-tune`. Здесь реализованы первые четыре звена;
последнее (human gate) — осознанно РУЧНОЕ: `approve()` требует явного решения
человека и не вызывается проходом конвейера.

Так же и с памятью: знания из песочницы становятся кандидатом и не попадают в
durable-память без валидации.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

from .. import obs

# Категории событий, которые вообще имеют смысл как обучающий сигнал.
_USEFUL_KINDS = frozenset({"tool_call", "shell", "test_result", "artifact", "failure"})

# Поля, которые никогда не попадают в датасет даже после редакции.
_DROP_FIELDS = frozenset({"sandbox_id", "ts", "grant_id", "lease_id"})


class CandidateState(str, Enum):
    CANDIDATE = "CANDIDATE"    # прошло sanitize+validate, ждёт человека
    APPROVED = "APPROVED"      # человек подтвердил
    REJECTED = "REJECTED"      # человек отклонил


@dataclass(slots=True)
class DatasetCandidate:
    id: str
    sandbox_id: str
    samples: list[dict]
    state: CandidateState = CandidateState.CANDIDATE
    created_at: float = field(default_factory=time.time)
    reasons: tuple[str, ...] = ()
    decided_by: str | None = None

    @property
    def approved(self) -> bool:
        return self.state is CandidateState.APPROVED


class DatasetGate:
    """Превращает сырую траекторию в кандидата. Никакого автопродвижения."""

    def __init__(self, *, min_samples: int = 1) -> None:
        self.min_samples = min_samples

    # --- 1. sanitize ---

    def sanitize(self, events: Iterable[dict]) -> list[dict]:
        """Вычистить секреты и служебные идентификаторы. Секреты уже вычищаются
        при записи траектории; здесь второй проход — defense in depth."""
        out: list[dict] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            clean = obs.redact_obj({k: v for k, v in ev.items() if k not in _DROP_FIELDS})
            out.append(clean)
        return out

    # --- 2. validate / evaluate ---

    def validate(self, samples: list[dict]) -> tuple[list[dict], tuple[str, ...]]:
        """Оставить только осмысленные обучающие примеры и объяснить отбраковку."""
        reasons: list[str] = []
        kept: list[dict] = []
        for s in samples:
            kind = s.get("kind")
            if kind not in _USEFUL_KINDS:
                continue
            payload = {k: v for k, v in s.items() if k != "kind"}
            if not payload:
                reasons.append(f"empty payload: {kind}")
                continue
            kept.append(s)
        if len(kept) < self.min_samples:
            reasons.append(f"too few samples: {len(kept)} < {self.min_samples}")
        return kept, tuple(reasons)

    # --- 3. candidate ---

    def build_candidate(self, sandbox_id: str, events: Iterable[dict]) -> DatasetCandidate:
        from .models import new_id
        samples, reasons = self.validate(self.sanitize(events))
        return DatasetCandidate(id=new_id("dsc"), sandbox_id=sandbox_id,
                                samples=samples, reasons=reasons)

    def from_trajectory_file(self, path: str | Path, sandbox_id: str) -> DatasetCandidate:
        events: list[dict] = []
        p = Path(path)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        return self.build_candidate(sandbox_id, events)

    # --- 4. human gate (НЕ автоматизируется) ---

    @staticmethod
    def approve(candidate: DatasetCandidate, *, by: str) -> DatasetCandidate:
        """Явное решение человека. Вызывается из UI/CLI, не конвейером."""
        if not by:
            raise ValueError("human approval requires an identity")
        if not candidate.samples:
            raise ValueError("cannot approve an empty candidate")
        candidate.state = CandidateState.APPROVED
        candidate.decided_by = by
        return candidate

    @staticmethod
    def reject(candidate: DatasetCandidate, *, by: str, reason: str = "") -> DatasetCandidate:
        candidate.state = CandidateState.REJECTED
        candidate.decided_by = by
        if reason:
            candidate.reasons = candidate.reasons + (reason,)
        return candidate

    @staticmethod
    def training_samples(candidate: DatasetCandidate) -> list[dict]:
        """Единственный путь к обучающим данным: только APPROVED-кандидат."""
        if not candidate.approved:
            raise PermissionError(
                "dataset candidate is not approved by a human; "
                "raw logs -> fine-tune is forbidden")
        return list(candidate.samples)
