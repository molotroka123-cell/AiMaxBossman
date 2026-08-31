"""Learning pipeline: episode → evidence → proposal → benchmark → shadow → verify → promote.

Эталон из ZIP помечал `eligible_for_shadow = verified`, а `verified` в движке
вычислялся как «действие защиты входит в список всех возможных действий» — то
есть ВСЕГДА True. Это fake-green: любой эпизод немедленно становился кандидатом.

Здесь стадии разделены и у каждой своё условие. Автопродвижения в продакшн нет:
`PROMOTED` достижим только через явное решение владельца.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Stage(IntEnum):
    PROPOSED = 0
    BENCHMARKED = 1
    SHADOW = 2
    VERIFIED = 3
    PROMOTED = 4        # только по решению владельца, не автоматически


@dataclass(frozen=True)
class LearningProposal:
    scenario_id: str
    pattern: str
    mitigation: str
    stage: Stage = Stage.PROPOSED
    reasons: tuple[str, ...] = ()

    @property
    def eligible_for_shadow(self) -> bool:
        return self.stage >= Stage.BENCHMARKED


def propose(scenario_id: str, pattern: str, mitigation: str, *,
            contained: bool, evidence_ref: str) -> LearningProposal:
    """Предложение появляется, только если эпизод РЕАЛЬНО сдержан и есть улики."""
    reasons: list[str] = []
    if not contained:
        reasons.append("episode was not contained; nothing proven to learn")
    if not evidence_ref:
        reasons.append("no evidence reference")
    # Стадия всегда PROPOSED: сам факт эпизода ничего не доказывает.
    # Причины (reasons) блокируют дальнейшее продвижение в `advance`.
    return LearningProposal(scenario_id, pattern, mitigation, Stage.PROPOSED, tuple(reasons))


def advance(p: LearningProposal, *, benchmark_passing: bool,
            shadow_runs: int = 0, min_shadow_runs: int = 5,
            security_regression: bool = False) -> LearningProposal:
    """Продвинуть предложение по конвейеру. Каждая стадия — своё доказательство."""
    if p.reasons:
        return p                                    # незакрытые причины блокируют всё
    if security_regression:
        return LearningProposal(p.scenario_id, p.pattern, p.mitigation, Stage.PROPOSED,
                                ("security regression detected",))
    stage = p.stage
    if stage is Stage.PROPOSED and benchmark_passing:
        stage = Stage.BENCHMARKED
    if stage is Stage.BENCHMARKED and shadow_runs > 0:
        stage = Stage.SHADOW
    if stage is Stage.SHADOW and shadow_runs >= min_shadow_runs:
        stage = Stage.VERIFIED
    return LearningProposal(p.scenario_id, p.pattern, p.mitigation, stage, ())


def promote(p: LearningProposal, *, owner_approved: bool) -> LearningProposal:
    """Продвижение в продакшн — ТОЛЬКО по явному решению владельца."""
    if not owner_approved:
        return LearningProposal(p.scenario_id, p.pattern, p.mitigation, p.stage,
                                ("owner approval required for promotion",))
    if p.stage < Stage.VERIFIED:
        return LearningProposal(p.scenario_id, p.pattern, p.mitigation, p.stage,
                                ("proposal is not verified yet",))
    return LearningProposal(p.scenario_id, p.pattern, p.mitigation, Stage.PROMOTED, ())
