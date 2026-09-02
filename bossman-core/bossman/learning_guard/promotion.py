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


class _NoSecurityEvidence:
    """Сентинел «снимок не передан».

    Существует ровно для того, чтобы дефолт в сигнатуре читался как
    ``NO_SECURITY_EVIDENCE``, а не как безобидный ``None``. Это НЕ пустой
    baseline: сравнивать с ним нечего, любой гейт трактует его как отсутствие
    доказательства. ``None`` принимается как синоним (обратная совместимость).
    """
    __slots__ = ()

    def __repr__(self) -> str:
        return "NO_SECURITY_EVIDENCE"

    def __bool__(self) -> bool:
        return False


NO_SECURITY_EVIDENCE = _NoSecurityEvidence()


class SecurityRegression(PermissionError):
    """Security-гейт просел — продвижение запрещено, даже при росте efficiency."""


class MissingSecurityEvidence(SecurityRegression):
    """Доказательства security-непрегрессии нет или оно неполно.

    Подкласс `SecurityRegression`: уже существующие обработчики (`autonomy_trainer.
    promote_candidate`) ловят его как security-отказ, т.е. поведение fail-closed
    достаётся бесплатно и типизировано.
    """


def _supplied(snapshot) -> bool:
    return snapshot is not None and snapshot is not NO_SECURITY_EVIDENCE


def security_evidence_gap(before, after) -> str:
    """``''`` — пара снимков пригодна как доказательство; иначе причина отказа."""
    have_before, have_after = _supplied(before), _supplied(after)
    if have_before and have_after:
        return ""
    if have_before or have_after:
        # Половина доказательства — не доказательство: при before=None измеренный
        # пробой в `after` просто выбрасывался бы молча.
        return ("incomplete security evidence: both before/after snapshots are required "
                "(a half-supplied measurement is not proof)")
    return "no security evidence: before/after snapshots are required"


def assert_security_evidence(before, after) -> None:
    """Fail-fast проверка полноты доказательства (типизированный отказ)."""
    gap = security_evidence_gap(before, after)
    if gap:
        raise MissingSecurityEvidence(gap)


def assert_no_security_regression(before: SecuritySnapshot, after: SecuritySnapshot) -> None:
    """Security hard gates нельзя «оптимизировать» ради benchmark score.

    Любое ухудшение security-метрики блокирует продвижение независимо от прочего.
    """
    if before.scope_ref != after.scope_ref:
        # Срезы с разных корпусов несравнимы: «улучшение» может быть просто
        # сменой корпуса на более лёгкий (F5-PROMOTION-CROSS-CORPUS).
        raise SecurityRegression(
            f"incomparable security snapshots: corpus {before.scope_ref!r} vs {after.scope_ref!r}")
    if (after.leaks > before.leaks
            or after.bypasses > before.bypasses
            or after.containment_rate < before.containment_rate):
        raise SecurityRegression(
            "security gate regressed; promotion blocked regardless of efficiency gain")


def advance(candidate: Candidate, *, ab: ABVerdict,
            security_before: SecuritySnapshot | None = NO_SECURITY_EVIDENCE,
            security_after: SecuritySnapshot | None = NO_SECURITY_EVIDENCE,
            shadow_runs: int = 0, min_shadow_runs: int = MIN_SHADOW_RUNS) -> Candidate:
    """Продвинуть кандидата по конвейеру.

    Доказательство — ТОЛЬКО `ab` (verified-успех), не self-score (req.6). Каждая
    стадия требует прохождения анти-деградационных гейтов. OWNER_PROMOTED здесь
    НЕ достигается — только через `promote()` (req.7).

    Security-доказательство (AUDIT-ONLY-001 / F4):
    * пара снимков передана НАПОЛОВИНУ — отказ на ЛЮБОЙ стадии (иначе измеренный
      пробой молча теряется);
    * SHADOW → VERIFIED — единственный переход, дающий право на OWNER_PROMOTED:
      без ПОЛНОЙ пары снимков он fail-closed;
    * CANDIDATE → VALIDATION и VALIDATION → SHADOW — бухгалтерия без прав в
      продакшне: baseline там не требуется (SHADOW — только наблюдение).
    """
    complete = not security_evidence_gap(security_before, security_after)
    half_supplied = _supplied(security_before) != _supplied(security_after)
    if half_supplied:
        try:
            assert_security_evidence(security_before, security_after)
        except MissingSecurityEvidence as exc:      # контракт advance() — вернуть причины
            return replace(candidate, reasons=(str(exc),))
    # Security-регрессия блокирует всё (даже если ab «прошёл»).
    if complete:
        assert_no_security_regression(security_before, security_after)
    proven = candidate.security_proven or complete

    if not ab.passing:
        return replace(candidate, reasons=ab.reasons, security_proven=proven)

    stage = candidate.stage
    if stage is PromotionStage.CANDIDATE:
        stage = PromotionStage.VALIDATION
    elif stage is PromotionStage.VALIDATION:
        stage = PromotionStage.SHADOW
    elif stage is PromotionStage.SHADOW:
        if not complete:
            try:
                assert_security_evidence(security_before, security_after)
            except MissingSecurityEvidence as exc:
                return replace(candidate, reasons=(f"SHADOW->VERIFIED refused: {exc}",),
                               security_proven=proven)
        if shadow_runs >= min_shadow_runs:
            stage = PromotionStage.VERIFIED
    # VERIFIED дальше двигает только владелец через promote().
    return replace(candidate, stage=stage, reasons=(), security_proven=proven)


def promote(candidate: Candidate, *, owner_approved: bool, rollback: RollbackInfo) -> Candidate:
    """Финальное продвижение в продакшн — ТОЛЬКО по явному решению владельца (req.7),
    и только из VERIFIED, и только с rollback-метаданными (req.10).

    Стадия VERIFIED — метка, а не доказательство: дополнительно требуется, чтобы
    по кандидату уже была сравнена полная пара SecuritySnapshot (`security_proven`,
    выставляется только в `advance`). AUDIT-ONLY-001 / F4.
    """
    if not owner_approved:
        return replace(candidate, reasons=("owner approval required for promotion",))
    if candidate.stage < PromotionStage.VERIFIED:
        return replace(candidate, reasons=("candidate is not verified yet",))
    if not candidate.security_proven:
        return replace(candidate, reasons=(
            "no security non-regression evidence on record for this candidate",))
    return replace(candidate, stage=PromotionStage.OWNER_PROMOTED, reasons=(), rollback=rollback)
