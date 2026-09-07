"""Канареечная активация ревизии: сперва малая когорта, потом все.

`prepare_rollback` уже отдаёт ПОРЯДОК отката как данные, но канареечного шага
не было вовсе: новая ревизия либо действовала для всех, либо ни для кого. Из-за
этого в карточке V5 «Canary admission and a live rollback rehearsal» стояло
NOT_RUN, а ошибка ревизии обнаруживалась сразу на всём парке целей.

Три правила, и все три — fail-closed.

  КОГОРТА ВЫБИРАЕТСЯ ДЕТЕРМИНИРОВАННО. sha256 по цифре ревизии и идентификатору
  цели, никакого генератора случайных чисел: тот же выпуск обязан дать ту же
  когорту в другом процессе, иначе «канарейка» невоспроизводима, а её отчёт
  непроверяем. Когорта ограничена и снизу, и сверху: одна цель ничего не
  показывает, а «канарейка» на девяноста процентах парка — это не канарейка.

  МОЛЧАНИЕ — НЕ УСПЕХ. Пока не отчитался КАЖДЫЙ член когорты, вердикт PENDING, а
  не PASSED. Отсутствие плохих новостей не является хорошей новостью: именно так
  зависший канареечный прогон превращается в общий выпуск.

  ОДНОГО ПАДЕНИЯ ДОСТАТОЧНО. Любой нездоровый член — FAILED, и широкая
  активация закрыта до отката или новой ревизии. Не «доля здоровых»: если
  ревизия ломает одну цель из десяти, она ломает цель.

Модуль ЧИСТЫЙ: ни ввода-вывода, ни часов, ни случайности; время входит `now`.
Здесь ничего не активируется и не откатывается — это решение, а не действие.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

PENDING = "PENDING"
PASSED = "PASSED"
FAILED = "FAILED"

# Меньше трёх целей — не наблюдение, а совпадение.
MIN_COHORT = 3


class CanaryError(ValueError):
    """Канареечный план отказался от входных данных; вердикта нет."""


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class CanaryPolicy:
    fraction: float = 0.2
    min_cohort: int = MIN_COHORT
    max_cohort: int = 25

    def __post_init__(self) -> None:
        if not (0.0 < self.fraction < 1.0):
            raise CanaryError("fraction must be strictly between 0 and 1")
        if type(self.min_cohort) is not int or self.min_cohort < MIN_COHORT:
            raise CanaryError(f"min_cohort must be an integer >= {MIN_COHORT}")
        if type(self.max_cohort) is not int or self.max_cohort < self.min_cohort:
            raise CanaryError("max_cohort must be an integer >= min_cohort")


@dataclass(frozen=True, slots=True)
class CanaryPlan:
    """Кто именно получает новую ревизию первым. Построение ничего не активирует."""
    revision_digest: str
    cohort: tuple[str, ...]
    population: tuple[str, ...]
    started_at: float

    @property
    def rest(self) -> tuple[str, ...]:
        held = set(self.cohort)
        return tuple(o for o in self.population if o not in held)


@dataclass(frozen=True, slots=True)
class CanaryOutcome:
    """Отчёт одного члена когорты. `healthy=None` означает «ещё не известно»."""
    objective_id: str
    healthy: bool | None
    evidence_ref: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CanaryVerdict:
    state: str
    reason: str
    revision_digest: str
    reported: tuple[str, ...] = ()
    unhealthy: tuple[str, ...] = ()
    silent: tuple[str, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.state == PASSED


def plan_canary(population: Iterable[str], *, revision_digest: str, now: float,
                policy: CanaryPolicy | None = None) -> CanaryPlan:
    """Выбрать когорту детерминированно по цифре ревизии."""
    policy = policy or CanaryPolicy()
    if not isinstance(revision_digest, str) or not revision_digest:
        raise CanaryError("revision_digest is required")
    ids = list(population)
    if len(set(ids)) != len(ids):
        raise CanaryError("duplicate objective in the population")
    if len(ids) < policy.min_cohort:
        raise CanaryError(f"a population of {len(ids)} cannot host a canary "
                          f"of at least {policy.min_cohort}")
    ordered = sorted(ids, key=lambda oid: _digest({"r": revision_digest, "o": oid}))
    size = min(policy.max_cohort, max(policy.min_cohort, int(len(ordered) * policy.fraction)))
    return CanaryPlan(revision_digest, tuple(sorted(ordered[:size])), tuple(sorted(ids)), now)


def evaluate_canary(plan: CanaryPlan, outcomes: Iterable[CanaryOutcome]) -> CanaryVerdict:
    """Вердикт по отчётам когорты. Молчание — PENDING, одно падение — FAILED."""
    if not isinstance(plan, CanaryPlan):
        raise CanaryError("a canary plan is required")
    rows: dict[str, CanaryOutcome] = {}
    for outcome in outcomes:
        if not isinstance(outcome, CanaryOutcome):
            raise CanaryError("outcomes must be CanaryOutcome")
        if outcome.objective_id not in plan.cohort:
            # Отчёт не от члена когорты — не улика об этой ревизии.
            raise CanaryError(f"{outcome.objective_id} is not in the cohort")
        if outcome.healthy is not None and type(outcome.healthy) is not bool:
            raise CanaryError("healthy must be bool or None")
        previous = rows.get(outcome.objective_id)
        # Failure is sticky within one revision/cohort. A later healthy report
        # cannot erase the failure or manufacture order-dependent eligibility.
        if previous is not None and previous.healthy is False:
            continue
        rows[outcome.objective_id] = outcome
    unhealthy = tuple(sorted(o for o, r in rows.items() if r.healthy is False))
    silent = tuple(sorted(o for o in plan.cohort if rows.get(o) is None
                          or rows[o].healthy is None))
    facts = {"cohort": len(plan.cohort), "population": len(plan.population),
             "reported": len(rows)}
    # Порядок важен: одно падение решает даже при неполном отчёте. Ждать
    # остальных, зная, что ревизия уже сломала цель, незачем.
    if unhealthy:
        return CanaryVerdict(FAILED, "canary_member_unhealthy", plan.revision_digest,
                             tuple(sorted(rows)), unhealthy, silent, facts)
    if silent:
        return CanaryVerdict(PENDING, "canary_incomplete", plan.revision_digest,
                             tuple(sorted(rows)), (), silent, facts)
    return CanaryVerdict(PASSED, "canary_clear", plan.revision_digest,
                         tuple(sorted(rows)), (), (), facts)


def may_activate_broadly(verdict: CanaryVerdict) -> tuple[bool, str]:
    """Можно ли выпускать ревизию на остальной парк. (можно, причина)."""
    if not isinstance(verdict, CanaryVerdict):
        return False, "no_canary_verdict"
    if verdict.state == FAILED:
        return False, "canary_failed"
    if verdict.state != PASSED:
        return False, "canary_incomplete"
    return True, "canary_passed"
