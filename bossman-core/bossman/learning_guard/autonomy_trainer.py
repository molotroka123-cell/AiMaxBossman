"""PASS3 Autonomy Trainer — candidate/promotion loop ON TOP of Learning Guard.

Флаг BOSSMAN_AUTONOMY_TRAINER_SHADOW (OFF): при выключенном флаге record_candidate
возвращает None и ничего не учится. Ничего не дублирует: A/B и продвижение —
существующие evaluate_ab/advance/promote; holdout — SecretHoldout; записи —
learning corpus (learning/trace.py) вызывающего.

Обучающая единица: state → typed action → fresh observation → independent verification.
Запрещено учиться по: координатам UI без semantic anchor, self-reported success,
holdout, stale session, непроверенному trace, hidden chain-of-thought.
Promotion: ≥3 независимых verified эпизодов (risky — выше, configurable),
independent verifier (principal ≠ planner), zero security regression, нет
false-success, non-inferior VerifiedSuccess, явный scope/version/environment,
протестированный rollback. Недостаток выборки = INSUFFICIENT_EVIDENCE.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from .holdout import SecretHoldout
from .models import ABResult, Candidate, PromotionStage, RollbackInfo, SecuritySnapshot
from .promotion import SecurityRegression, promote
from .service import guard_promotion

FLAG = "BOSSMAN_AUTONOMY_TRAINER_SHADOW"
KINDS = ("context", "skill", "route", "budget", "cache", "verification", "weakness_patch")
STATUSES = ("CANDIDATE", "SHADOW", "PROMOTED", "QUARANTINED", "ROLLED_BACK", "REJECTED")
RISKY_KINDS = frozenset({"route", "budget", "weakness_patch"})

# Ключи scope, которые задают НЕИЗМЕНЯЕМУЮ идентичность корпуса (в отличие от
# имени task_class, которое можно молча перерезать). AUDIT-ONLY-001 / F5.
CORPUS_IDENTITY_KEYS = ("scope_ref", "corpus_ref", "dataset_hash", "policy_version")
LEGACY_UNSCOPED = "LEGACY_UNSCOPED"


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


def scope_fingerprint(scope: dict) -> str:
    """Непрозрачный отпечаток корпуса, к которому приколот кандидат.

    ``''`` — scope объявлен только ПО ИМЕНИ (task_class/environment/model_version):
    неизменяемой идентичности нет, требовать её от уже существующих записей нельзя
    (обратная совместимость). Как только scope объявляет ``dataset_hash`` /
    ``policy_version`` / ``scope_ref``, доказательства (ABResult, SecuritySnapshot)
    обязаны нести тот же отпечаток — иначе продвижение отклоняется.
    """
    if scope.get("scope_ref"):
        return str(scope["scope_ref"])
    ident = [f"{k}={scope.get(k)}" for k in CORPUS_IDENTITY_KEYS if scope.get(k)]
    if not ident:
        return ""
    base = "|".join([f"task_class={scope.get('task_class', '')}",
                     f"environment={scope.get('environment', '')}",
                     f"model_version={scope.get('model_version', '')}", *ident])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Episode:
    task_id: str
    state_hash: str
    action_type: str                       # typed action (click|type|route|...), не свободный текст
    semantic_anchor: str                   # для UI-действий обязателен (role/label/ref), не координаты
    fresh_observation: bool
    verified_success: bool
    planner_principal: str
    verifier_principal: str
    verifier_independence_class: str = "external_tool"   # cross_model | external_tool | human
    environment_fingerprint: str = ""
    model_version: str = ""
    stale_session: bool = False
    self_reported_only: bool = False
    contains_hidden_cot: bool = False
    false_success: bool = False
    security_regression: bool = False


@dataclass(frozen=True, slots=True)
class AutonomyCandidate:
    candidate_id: str
    kind: str
    scope: dict
    hypothesis: str
    rollback_ref: str
    sample_count: int = 0
    verified_success_delta: float | None = None
    independently_verified: bool = False
    holdout_touched: bool = False
    security_regression: bool = False
    status: str = "CANDIDATE"
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if not self.rollback_ref:
            raise ValueError("rollback_ref required")

    def as_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id, "kind": self.kind, "scope": dict(self.scope),
                "hypothesis": self.hypothesis, "sample_count": self.sample_count,
                "verified_success_delta": self.verified_success_delta,
                "independently_verified": self.independently_verified,
                "holdout_touched": self.holdout_touched, "security_regression": self.security_regression,
                "rollback_ref": self.rollback_ref, "status": self.status}


def episode_rejection(ep: Episode, holdout: SecretHoldout | None = None) -> str:
    """Пустая строка = эпизод пригоден для обучения; иначе причина."""
    if holdout is not None and holdout.is_holdout(ep.task_id):
        return "holdout episode"
    if ep.contains_hidden_cot:
        return "hidden chain-of-thought"
    if ep.stale_session:
        return "stale session"
    if ep.self_reported_only or not ep.fresh_observation:
        return "no fresh observation (self-reported)"
    if ep.action_type in ("click", "type", "drag", "scroll") and not ep.semantic_anchor.strip():
        return "UI action without semantic anchor (coordinates only)"
    if not ep.verifier_principal or ep.verifier_principal == ep.planner_principal:
        return "verifier is the planner (not independent)"
    if ep.verifier_independence_class not in ("cross_model", "external_tool", "human"):
        return "verifier independence class not independent"
    return ""          # verified_success=False — допустимый ИСХОД (считается как провал), не запрещённый источник


def evaluate_candidate(cand: AutonomyCandidate, episodes: Iterable[Episode], *,
                       holdout: SecretHoldout | None = None, min_samples: int = 3,
                       risky_min_samples: int = 10, baseline_success: float | None = None) -> AutonomyCandidate:
    """Shadow-оценка: только пригодные эпизоды считаются; недостаток выборки —
    INSUFFICIENT_EVIDENCE (status остаётся CANDIDATE), не promotion."""
    eps = list(episodes)
    if any(holdout is not None and holdout.is_holdout(e.task_id) for e in eps):
        return replace(cand, holdout_touched=True, status="QUARANTINED",
                       reasons=("holdout episode entered training set",))
    if any(e.security_regression for e in eps):
        return replace(cand, security_regression=True, status="QUARANTINED", reasons=("security regression observed",))
    if any(e.false_success for e in eps):
        return replace(cand, status="REJECTED", reasons=("false success observed",))
    usable = [e for e in eps if not episode_rejection(e, holdout)]
    rejected = [episode_rejection(e, holdout) for e in eps if episode_rejection(e, holdout)]
    envs = {e.environment_fingerprint for e in usable}
    versions = {e.model_version for e in usable}
    need = risky_min_samples if (cand.kind in RISKY_KINDS or cand.scope.get("risky")) else min_samples
    reasons: list[str] = []
    if len(usable) < need:
        reasons.append(f"INSUFFICIENT_EVIDENCE: {len(usable)} independent verified episodes < {need}")
    if len(envs) != 1 or len(versions) != 1 or not (envs and versions and next(iter(envs)) and next(iter(versions))):
        reasons.append("scope must be one explicit environment fingerprint and model version")
    else:
        # Эпизоды согласованы МЕЖДУ СОБОЙ — этого мало: они обязаны принадлежать
        # тому корпусу, к которому приколот кандидат (AUDIT-ONLY-001 / F5).
        env, ver = next(iter(envs)), next(iter(versions))
        want_env = str(cand.scope.get("environment") or "")
        want_ver = str(cand.scope.get("model_version") or "")
        if want_env and env != want_env:
            reasons.append(f"episodes from environment {env!r} are outside scope {want_env!r}")
        if want_ver and ver != want_ver:
            reasons.append(f"episodes from model version {ver!r} are outside scope {want_ver!r}")
    if not cand.scope.get("task_class"):
        reasons.append("scope.task_class missing")
    successes = sum(1 for e in usable if e.verified_success)
    success = (successes / len(usable)) if usable else None
    if baseline_success is None:
        reasons.append("SHADOW requires a measured baseline VerifiedSuccess (none supplied)")
    if successes < need:
        reasons.append(f"SHADOW requires >= {need} successful independently verified episodes, got {successes}")
    delta = None if (success is None or baseline_success is None) else round(success - baseline_success, 4)
    if delta is not None and delta < 0:
        reasons.append("VerifiedSuccess inferior to baseline")
    status = "SHADOW" if not reasons else "CANDIDATE"
    return replace(cand, sample_count=len(usable), verified_success_delta=delta,
                   independently_verified=bool(usable) and len(usable) >= need,
                   status=status, reasons=tuple(reasons) + tuple(f"rejected episode: {r}" for r in rejected))


def promote_candidate(cand: AutonomyCandidate, ab_results: Iterable[ABResult], *,
                      security_before: SecuritySnapshot, security_after: SecuritySnapshot,
                      shadow_runs: int, owner_approved: bool, rollback_tested: bool,
                      rollback: RollbackInfo) -> AutonomyCandidate:
    """Через Learning Guard: A/B (verified only) → анти-деградационные гейты →
    стадии → promote только владельцем и только с протестированным rollback."""
    if cand.status != "SHADOW":
        return replace(cand, reasons=("candidate is not in SHADOW; evaluate first",))
    if not rollback_tested:
        return replace(cand, reasons=("rollback not tested",))
    # --- scope binding (AUDIT-ONLY-001 / F5): доказательства обязаны принадлежать
    # тому корпусу, к которому приколот кандидат. До фикса cand.scope здесь не
    # читался вообще, и 20 чистых прогонов чужого класса продвигали кандидата.
    want_class = str(cand.scope.get("task_class") or "")
    if not want_class:
        # Легаси/десериализованная запись без scope. Ничего не удаляем и не
        # промоутим: возвращаем на переоценку (обратимо — добавьте scope и
        # прогоните evaluate_candidate заново).
        return replace(cand, status="CANDIDATE",
                       reasons=(f"{LEGACY_UNSCOPED}: candidate carries no scope.task_class; "
                                "re-evaluate before promotion",))
    ab = list(ab_results)
    seen = {r.task_class for r in ab}
    if seen != {want_class}:
        return replace(cand, reasons=(
            f"A/B evidence covers {sorted(seen)}, candidate is scoped to {want_class!r}",))
    want_ref = scope_fingerprint(cand.scope)
    if want_ref:
        foreign = sorted({r.scope_ref for r in ab if r.scope_ref != want_ref})
        if foreign:
            return replace(cand, reasons=(
                f"A/B evidence corpus {foreign} does not match candidate corpus {want_ref!r}",))
        if security_before.scope_ref != want_ref or security_after.scope_ref != want_ref:
            return replace(cand, reasons=(
                f"security snapshots are not bound to candidate corpus {want_ref!r}",))
    lg = Candidate(kind="config", ref=cand.candidate_id, stage=PromotionStage.SHADOW)
    try:
        moved, verdict = guard_promotion(lg, ab, security_before=security_before,
                                         security_after=security_after, shadow_runs=shadow_runs)
    except SecurityRegression as exc:
        return replace(cand, security_regression=True, status="QUARANTINED", reasons=(str(exc),))
    if moved.stage < PromotionStage.VERIFIED:
        why = tuple(moved.reasons) or (f"shadow runs {shadow_runs} below Learning Guard minimum "
                                       f"({__import__('bossman.learning_guard.promotion', fromlist=['MIN_SHADOW_RUNS']).MIN_SHADOW_RUNS})",)
        return replace(cand, reasons=why)
    final = promote(moved, owner_approved=owner_approved, rollback=rollback)
    if final.stage != PromotionStage.OWNER_PROMOTED:
        return replace(cand, reasons=tuple(final.reasons))
    return replace(cand, status="PROMOTED", reasons=())


def rollback_candidate(cand: AutonomyCandidate, reason: str) -> AutonomyCandidate:
    return replace(cand, status="ROLLED_BACK", reasons=(reason,))


def record_candidate(cand: AutonomyCandidate) -> dict | None:
    """Точка входа для рантайма: при выключенном флаге — ничего (None)."""
    if not enabled():
        return None
    return cand.as_dict()
