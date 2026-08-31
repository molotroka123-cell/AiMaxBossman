"""Конвейер продвижения кандидата с анти-деградационными гейтами (req.5,6,7,10).

candidate → validation → shadow → verified → owner. Автопродвижения в продакшн
НЕТ: OWNER_PROMOTED достижим только по явному решению владельца и требует
rollback-метаданных. Security hard gates неоптимизируемы ради score (req.).
"""
from __future__ import annotations

from dataclasses import replace

from .ab import ABVerdict
from .models import Candidate, PromotionStage, RollbackInfo, SecuritySnapshot

MIN_SHADOW_RUNS = 20             # запрет single-episode на shadow-стадии (req.5)


class SecurityRegression(PermissionError):
    """Security-гейт просел — продвижение запрещено, даже при росте efficiency."""


def assert_no_security_regression(before: SecuritySnapshot, after: SecuritySnapshot) -> None:
    """Security hard gates нельзя «оптимизировать» ради benchmark score.

    Любое ухудшение security-метрики блокирует продвижение независимо от прочего.
    """
    if (after.leaks > before.leaks
            or after.bypasses > before.bypasses
            or after.containment_rate < before.containment_rate):
        raise SecurityRegression(
            "security gate regressed; promotion blocked regardless of efficiency gain")


def advance(candidate: Candidate, *, ab: ABVerdict,
            security_before: SecuritySnapshot | None = None,
            security_after: SecuritySnapshot | None = None,
            shadow_runs: int = 0, min_shadow_runs: int = MIN_SHADOW_RUNS) -> Candidate:
    """Продвинуть кандидата по конвейеру.

    Доказательство — ТОЛЬКО `ab` (verified-успех), не self-score (req.6). Каждая
    стадия требует прохождения анти-деградационных гейтов. OWNER_PROMOTED здесь
    НЕ достигается — только через `promote()` (req.7).
    """
    # Security-регрессия блокирует всё (даже если ab «прошёл»).
    if security_before is not None and security_after is not None:
        assert_no_security_regression(security_before, security_after)

    if not ab.passing:
        return replace(candidate, reasons=ab.reasons)

    stage = candidate.stage
    if stage is PromotionStage.CANDIDATE:
        stage = PromotionStage.VALIDATION
    elif stage is PromotionStage.VALIDATION:
        stage = PromotionStage.SHADOW
    elif stage is PromotionStage.SHADOW and shadow_runs >= min_shadow_runs:
        stage = PromotionStage.VERIFIED
    # VERIFIED дальше двигает только владелец через promote().
    return replace(candidate, stage=stage, reasons=())


def promote(candidate: Candidate, *, owner_approved: bool, rollback: RollbackInfo) -> Candidate:
    """Финальное продвижение в продакшн — ТОЛЬКО по явному решению владельца (req.7),
    и только из VERIFIED, и только с rollback-метаданными (req.10)."""
    if not owner_approved:
        return replace(candidate, reasons=("owner approval required for promotion",))
    if candidate.stage < PromotionStage.VERIFIED:
        return replace(candidate, reasons=("candidate is not verified yet",))
    return replace(candidate, stage=PromotionStage.OWNER_PROMOTED, reasons=(), rollback=rollback)
