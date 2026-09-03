"""Четыре раздельных слоя памяти и гейт повышения.

Почему слои разделены физически, а не «по тегу»: единая память — это способ,
которым мнение автора однажды окажется в правиле входа. Здесь правило может
попасть в PROCEDURAL только через явное повышение, а повышение требует
доказательств, которых у мнения по определению нет.

Инвариант, который проверяется тестом: AUTHOR_CLAIM не попадает в PROCEDURAL
ни при каких значениях остальных полей.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .metrics import MIN_OUT_OF_SAMPLE, QualityReport
from .models import Claim, Episode, MemoryLayer, NEVER_PROCEDURAL
from .safety import EvidenceClass, utcnow
from .strategy import StrategyRule

MIN_INDEPENDENT_EPISODES = 3       # один эпизод — это анекдот, а не правило


class PromotionDenied(RuntimeError):
    """Повышение отклонено. Причина всегда называется явно."""


class MemoryPoisoned(RuntimeError):
    """Запись пытается попасть в слой, которому она по типу не принадлежит."""


@dataclass
class LessonRecord:
    """Урок: правило + доказательства + цепочка происхождения."""

    lesson_id: str
    rule: StrategyRule
    layer: MemoryLayer
    episode_ids: tuple[str, ...]
    provenance: tuple[str, ...]
    report: QualityReport | None = None
    verified_by: str = ""
    created_at: datetime = field(default_factory=utcnow)
    evidence_class: EvidenceClass = EvidenceClass.SIMULATED
    notes: str = ""
    contradicted_by: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"lesson_id": self.lesson_id, "layer": self.layer.value,
                "rule": self.rule.as_dict(), "episode_ids": list(self.episode_ids),
                "provenance": list(self.provenance),
                "report": self.report.as_dict() if self.report else None,
                "verified_by": self.verified_by,
                "created_at": self.created_at.isoformat(),
                "evidence_class": self.evidence_class.value, "notes": self.notes,
                "contradicted_by": list(self.contradicted_by)}


@dataclass
class TradingMemory:
    """Четыре слоя. Между ними нет прямых переходов, кроме promote()."""

    working_state: dict[str, Any] = field(default_factory=dict)
    episodic: list[Episode] = field(default_factory=list)
    procedural: list[LessonRecord] = field(default_factory=list)
    quarantine: list[LessonRecord] = field(default_factory=list)

    # ------------------------------------------------------------- working
    def set_working(self, key: str, value: Any) -> None:
        """Текущая задача и только актуальный контекст. Не история."""
        self.working_state[key] = value

    def clear_working(self) -> None:
        self.working_state.clear()

    # ------------------------------------------------------------ episodic
    def record_episode(self, episode: Episode) -> None:
        if any(e.episode_id == episode.episode_id for e in self.episodic):
            return                     # идемпотентность: повтор не удваивает выборку
        self.episodic.append(episode)

    # ---------------------------------------------------------- quarantine
    def quarantine_lesson(self, lesson: LessonRecord, reason: str = "") -> LessonRecord:
        """Всё новое приходит сюда. Прямой записи в PROCEDURAL не существует."""
        lesson.layer = MemoryLayer.QUARANTINE
        if reason:
            lesson.notes = (lesson.notes + "; " + reason).strip("; ")
        self.quarantine.append(lesson)
        return lesson

    # ------------------------------------------------------------ promote
    def promote(self, lesson: LessonRecord, *, claims: Iterable[Claim],
                verifier_id: str, extraction_model: str,
                lookahead_clean: bool) -> LessonRecord:
        """QUARANTINE → PROCEDURAL. Каждый отказ — отдельная причина."""
        claims = list(claims)
        reasons: list[str] = []

        # 1. Мнение автора не становится процедурой ни при каких условиях.
        opinion_only = claims and all(c.claim_type in NEVER_PROCEDURAL for c in claims)
        if opinion_only or lesson.rule.author_opinion_only:
            reasons.append("rule is built from author opinion only (AUTHOR_CLAIM/HYPOTHESIS)")
        # 2. Несколько независимых эпизодов.
        if len(set(lesson.episode_ids)) < MIN_INDEPENDENT_EPISODES:
            reasons.append(
                f"independent episodes {len(set(lesson.episode_ids))} < {MIN_INDEPENDENT_EPISODES}")
        # 3. Отсутствие подглядывания в будущее.
        if not lookahead_clean:
            reasons.append("lookahead check did not pass")
        # 4. Положительный EV вне выборки на достаточной out-of-sample выборке.
        report = lesson.report
        if report is None:
            reasons.append("no quality report attached")
        else:
            if report.out_of_sample_size < MIN_OUT_OF_SAMPLE:
                reasons.append(
                    f"out-of-sample {report.out_of_sample_size} < {MIN_OUT_OF_SAMPLE}")
            if report.out_of_sample_ev <= 0:
                reasons.append("out-of-sample EV is not positive")
            if report.blockers:
                reasons.append("quality blockers: " + "; ".join(report.blockers))
        # 5. Независимая проверка: верификатор не равен экстрактору.
        if not verifier_id or verifier_id.strip().lower() == extraction_model.strip().lower():
            reasons.append("verification is not independent from extraction")
        # 6. Явная цепочка происхождения.
        if not lesson.provenance:
            reasons.append("empty provenance chain")
        if lesson.contradicted_by:
            reasons.append("lesson is contradicted by: " + ",".join(lesson.contradicted_by))

        if reasons:
            self.quarantine_lesson(lesson, "promotion denied: " + "; ".join(reasons))
            raise PromotionDenied("; ".join(reasons))

        if lesson in self.quarantine:
            self.quarantine.remove(lesson)
        lesson.layer = MemoryLayer.PROCEDURAL_MEMORY
        lesson.verified_by = verifier_id
        lesson.evidence_class = EvidenceClass.HISTORICAL_REPLAY
        self.procedural.append(lesson)
        return lesson

    # ----------------------------------------------------------- snapshot
    def snapshot(self) -> dict[str, Any]:
        return {"working_state_keys": sorted(self.working_state),
                "episodic": len(self.episodic),
                "procedural": len(self.procedural),
                "quarantine": len(self.quarantine),
                "procedural_rules": [l.lesson_id for l in self.procedural],
                "quarantined_rules": [
                    {"lesson_id": l.lesson_id, "notes": l.notes} for l in self.quarantine]}

    def dump(self, path: str | Path) -> Path:
        """Снимок памяти на диск. Только для отчёта — не второй стор."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"snapshot": self.snapshot(),
                   "procedural": [l.as_dict() for l in self.procedural],
                   "quarantine": [l.as_dict() for l in self.quarantine]}
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
