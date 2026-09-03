"""Сборка урока из эпизодов: что было решением, а что случайностью.

Урок — это не «сделка была прибыльной». Урок отвечает на три вопроса:
переносимо ли правило на другой день/актив/режим, отличается ли результат от
случайного, и какие данные для вывода не хватало. Урок всегда рождается в
карантине — это единственный вход в память.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .memory import LessonRecord, MemoryLayer, TradingMemory
from .metrics import QualityReport
from .models import Claim, Episode
from .safety import EvidenceClass
from .strategy import StrategyRule


@dataclass(frozen=True, slots=True)
class LessonDraft:
    """Черновик урока с явным перечислением того, чего не хватает."""

    lesson_id: str
    rule: StrategyRule
    report: QualityReport
    episode_ids: tuple[str, ...]
    provenance: tuple[str, ...]
    transferable_regimes: tuple[str, ...]
    randomness_note: str
    data_gaps: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"lesson_id": self.lesson_id, "rule_id": self.rule.rule_id,
                "episode_ids": list(self.episode_ids), "provenance": list(self.provenance),
                "transferable_regimes": list(self.transferable_regimes),
                "randomness_note": self.randomness_note,
                "data_gaps": list(self.data_gaps),
                "report": self.report.as_dict()}


def _randomness_note(report: QualityReport) -> str:
    """Честная формулировка о случайности исхода.

    Если нижняя граница доверительного интервала win rate не отделена от нуля,
    результат неотличим от везения — так и пишем, а не «стратегия работает».
    """
    lo, hi = report.win_rate_ci95
    if report.sample_size < 10:
        return (f"выборка {report.sample_size} сделок — исход неотличим от случайности; "
                "выводов о правиле делать нельзя")
    if lo <= 0.0:
        return (f"доверительный интервал win rate [{lo:.2f},{hi:.2f}] включает ноль — "
                "результат может быть случайным")
    return (f"win rate {report.win_rate:.2f}, 95% CI [{lo:.2f},{hi:.2f}] на "
            f"{report.sample_size} сделках")


def lesson_builder(rule: StrategyRule, report: QualityReport, episodes: list[Episode],
                   claims: list[Claim], memory: TradingMemory) -> LessonRecord:
    """Собрать урок и поместить его в КАРАНТИН. Не в процедурную память."""
    episode_ids = tuple(sorted({e.episode_id for e in episodes}))
    provenance = tuple(sorted({f"{c.source_id}@{c.timestamp_start:.1f}" for c in claims}))
    seed = f"{rule.rule_id}|{'|'.join(episode_ids)}|{report.sample_size}"
    lesson_id = f"lesson_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"

    gaps: list[str] = []
    if report.sample_size < 30:
        gaps.append("недостаточная выборка сделок")
    if report.out_of_sample_size == 0:
        gaps.append("нет результата вне выборки")
    if len(report.regimes_covered) < 2:
        gaps.append("правило проверено меньше чем в двух режимах")
    if not any(c.verification_status.value == "DATA_SUPPORTED" for c in claims):
        gaps.append("ни один claim не подтверждён рыночными данными")
    if rule.author_opinion_only:
        gaps.append("правило собрано только из мнений автора")

    draft = LessonDraft(
        lesson_id=lesson_id, rule=rule, report=report, episode_ids=episode_ids,
        provenance=provenance,
        transferable_regimes=tuple(report.regimes_covered),
        randomness_note=_randomness_note(report), data_gaps=tuple(gaps))

    record = LessonRecord(
        lesson_id=lesson_id, rule=rule, layer=MemoryLayer.QUARANTINE,
        episode_ids=episode_ids, provenance=provenance, report=report,
        evidence_class=EvidenceClass.HISTORICAL_REPLAY if episode_ids else EvidenceClass.MOCK,
        notes="; ".join([draft.randomness_note, *gaps]))
    memory.quarantine_lesson(record)
    return record
