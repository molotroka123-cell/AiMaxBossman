"""Измеренное продвижение навыка или шаблона: baseline против кандидата.

`objective_improvement.may_promote` — ГЕЙТ: он потребляет готовое измерение и
говорит «можно ли этому кандидату в канареечный выпуск». Измерения не делал
никто. Из-за этого «measured skill promotion» в карточке V5 стояло NOT_RUN, а
`PromotionEvidence` можно было собрать руками из чисел, которые никто не считал.

Здесь измерение и происходит, и у него есть три свойства, без которых оно не
доказательство:

  ОТЛОЖЕННАЯ ВЫБОРКА. Улучшение, показанное на тех же задачах, на которых
  кандидата и подбирали, не улучшение, а подгонка. Набор делится ДЕТЕРМИНИРОВАННО
  (sha256 по идентификатору задачи и материалу разбиения — никакого генератора
  случайных чисел, разбиение обязано повторяться в другом процессе и в отчёте), и
  кандидат обязан выиграть на измеряемой части И НЕ ПРОИГРАТЬ на отложенной.

  ПРИВЯЗКА К ПРИМЕНИМОСТИ. Измерение верно для той области и той версии
  применимости, на которой снято. Кандидат, измеренный на `python.refactor`
  версии 3, ничего не доказал ни про другую область, ни про версию 4. Версия
  проверяется при авторизации, а не при измерении: между ними проходит время.

  ОДНОРАЗОВОСТЬ. Одно измерение продвигает ОДНОГО кандидата. Без памяти о
  потраченном никакой предикат от (кандидат, улики) не отличит две версии,
  которым подсунули байт в байт одинаковые числа — это AUDIT001-F5-REPLAY.
  Журнал подключается портом: канонический durable-журнал живёт в
  `bossman.learning_guard.evidence_ledger`, а базовый слой не имеет права
  зависеть от ядра.

Модуль ЧИСТЫЙ: ни ввода-вывода, ни часов, ни случайности. Прогон задач —
вызываемый объект от вызывающего; время входит параметром.

Расширения прав здесь не бывает: решение делегируется `may_promote`, который
отказывает структурно, до чтения любых чисел.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

from .objective_improvement import CandidateImprovement, PromotionEvidence, may_promote

BASELINE = "baseline"
CANDIDATE = "candidate"

# Ниже этого отложенная выборка ничего не показывает: одна-две задачи совпадут
# по случайности. Это не «настройка», а нижняя граница осмысленности.
MIN_HOLDOUT_TASKS = 5
MIN_MEASURED_TASKS = 15


class PromotionError(ValueError):
    """Измерение отказалось от входных данных; результата нет."""


@runtime_checkable
class LedgerPort(Protocol):
    """Одноразовый журнал улик. `None` — потрачено этим потребителем впервые
    или повторно им же; строка — причина отказа."""

    def consume(self, key: str, consumer: str) -> str | None: ...


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class Task:
    """Одна задача набора и область, которую она проверяет."""
    task_id: str
    applicability: str

    def __post_init__(self) -> None:
        if not self.task_id or not isinstance(self.task_id, str):
            raise PromotionError("task_id is required")
        if not self.applicability or not isinstance(self.applicability, str):
            raise PromotionError("applicability is required")


@dataclass(frozen=True, slots=True)
class Outcome:
    """Итог одного прогона. `score` сравним только внутри одной задачи."""
    task_id: str
    passed: bool
    score: float

    def __post_init__(self) -> None:
        if type(self.passed) is not bool:
            raise PromotionError("passed must be a boolean")
        if type(self.score) not in (int, float) or not math.isfinite(self.score):
            raise PromotionError("score must be a finite number")


@dataclass(frozen=True, slots=True)
class Split:
    """Детерминированное разбиение набора. Повторяемо в любом процессе."""
    measured: tuple[str, ...]
    holdout: tuple[str, ...]
    material: str

    @property
    def digest(self) -> str:
        return _digest({"measured": list(self.measured), "holdout": list(self.holdout)})


@dataclass(frozen=True, slots=True)
class LaneResult:
    variant: str
    passed: int
    total: int
    mean_score: float

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class MeasuredPromotion:
    """Готовое измерение. Ничего не разрешает — это только числа и их привязка."""
    candidate_id: str
    candidate_version: str
    baseline_version: str
    applicability: tuple[str, ...]
    applicability_version: str
    split: Split
    measured: dict[str, LaneResult]
    holdout: dict[str, LaneResult]
    task_set_digest: str
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def evidence_key(self) -> str:
        """Ключ ОДНОГО измерения: числа обеих полос, разбиение и набор задач.

        В ключ входит и версия применимости: то же измерение под другой версией
        — другое утверждение, и тратить под него старую улику нельзя.
        """
        return _digest({
            "task_set": self.task_set_digest, "split": self.split.digest,
            "applicability": list(self.applicability),
            "applicability_version": self.applicability_version,
            "measured": {k: [v.passed, v.total, round(v.mean_score, 9)]
                         for k, v in sorted(self.measured.items())},
            "holdout": {k: [v.passed, v.total, round(v.mean_score, 9)]
                        for k, v in sorted(self.holdout.items())},
        })

    @property
    def consumer(self) -> str:
        """Кто тратит улику: кандидат ВМЕСТЕ С ВЕРСИЕЙ. Две версии — два
        потребителя, и второй получит отказ."""
        return f"{self.candidate_id}@{self.candidate_version}"


def split_tasks(tasks: Iterable[Task], *, material: str,
                holdout_fraction: float = 0.3) -> Split:
    """Разбить набор на измеряемую и отложенную части — детерминированно.

    Порядок задаёт sha256(material + task_id), а не генератор случайных чисел:
    разбиение обязано воспроизводиться в другом процессе, иначе отчёт нельзя
    перепроверить, а «отложенная выборка» становится словом.
    """
    if not isinstance(material, str) or not material:
        raise PromotionError("split material is required")
    if not (0.0 < holdout_fraction < 1.0):
        raise PromotionError("holdout_fraction must be strictly between 0 and 1")
    rows = list(tasks)
    ids = [t.task_id for t in rows]
    if len(set(ids)) != len(ids):
        raise PromotionError("duplicate task_id in the task set")
    ordered = sorted(ids, key=lambda task_id: _digest({"m": material, "t": task_id}))
    cut = int(len(ordered) * (1.0 - holdout_fraction))
    return Split(tuple(sorted(ordered[:cut])), tuple(sorted(ordered[cut:])), material)


def _lane(variant: str, outcomes: dict[str, Outcome], ids: tuple[str, ...]) -> LaneResult:
    rows = []
    for task_id in ids:
        outcome = outcomes.get(task_id)
        if outcome is None:
            raise PromotionError(f"{variant} did not report task {task_id}")
        rows.append(outcome)
    total = len(rows)
    return LaneResult(variant, sum(1 for r in rows if r.passed), total,
                      sum(r.score for r in rows) / total if total else 0.0)


def measure(tasks: Iterable[Task], *, candidate_id: str, candidate_version: str,
            baseline_version: str, applicability_version: str,
            run: Callable[[str, Task], Outcome], split: Split | None = None,
            holdout_fraction: float = 0.3) -> MeasuredPromotion:
    """Прогнать обе полосы по одному и тому же набору и вернуть числа.

    `run(variant, task)` — единственная нечистая часть, и она снаружи. Обе
    полосы видят ОДИН набор и одно разбиение: сравнивать кандидата на своих
    задачах с базой на чужих — не сравнение.
    """
    rows = list(tasks)
    if not rows:
        raise PromotionError("an empty task set measures nothing")
    ids = [task.task_id for task in rows]
    if len(ids) != len(set(ids)):
        raise PromotionError("duplicate task_id in the task set")
    if candidate_version == baseline_version:
        raise PromotionError("candidate and baseline are the same version")
    split = split or split_tasks(rows, material=f"{candidate_id}:{applicability_version}",
                                 holdout_fraction=holdout_fraction)
    if not isinstance(split, Split):
        raise PromotionError("a Split is required")
    known = {t.task_id: t for t in rows}
    measured_ids, holdout_ids = set(split.measured), set(split.holdout)
    if len(measured_ids) != len(split.measured) or len(holdout_ids) != len(split.holdout):
        raise PromotionError("duplicate task_id within a split lane")
    if measured_ids & holdout_ids:
        raise PromotionError("measured and holdout lanes must be disjoint")
    if measured_ids | holdout_ids != set(ids):
        raise PromotionError("split must partition the declared task set exactly")
    for task_id in split.measured + split.holdout:
        if task_id not in known:
            raise PromotionError(f"split names a task outside the set: {task_id}")
    outcomes: dict[str, dict[str, Outcome]] = {BASELINE: {}, CANDIDATE: {}}
    for variant in (BASELINE, CANDIDATE):
        for task in rows:
            outcome = run(variant, task)
            if not isinstance(outcome, Outcome):
                raise PromotionError(f"{variant} returned a non-Outcome for {task.task_id}")
            if outcome.task_id != task.task_id:
                raise PromotionError(f"{variant} answered {outcome.task_id} for {task.task_id}")
            outcomes[variant][task.task_id] = outcome
    return MeasuredPromotion(
        candidate_id=candidate_id, candidate_version=candidate_version,
        baseline_version=baseline_version,
        applicability=tuple(sorted({t.applicability for t in rows})),
        applicability_version=applicability_version, split=split,
        measured={v: _lane(v, outcomes[v], split.measured) for v in (BASELINE, CANDIDATE)},
        holdout={v: _lane(v, outcomes[v], split.holdout) for v in (BASELINE, CANDIDATE)},
        task_set_digest=_digest(sorted((t.task_id, t.applicability) for t in rows)),
        facts={"tasks": len(rows)})


@dataclass(frozen=True, slots=True)
class PromotionVerdict:
    authorized: bool
    reason: str
    evidence_key: str = ""
    consumer: str = ""
    facts: dict[str, Any] = field(default_factory=dict)


def authorize(measurement: MeasuredPromotion, candidate: CandidateImprovement, *,
              ledger: LedgerPort, applicability_version: str,
              applicability_scope: Iterable[str], retention: float,
              retention_evidence_ref: str, security_pass: bool,
              rollback_available: bool) -> PromotionVerdict:
    """Разрешить канареечный выпуск — или назвать причину отказа.

    Порядок проверок не косметика: сначала структурные и дешёвые, и только
    последним — трата улики. Отказ по форме не должен сжигать измерение.
    """
    if not isinstance(measurement, MeasuredPromotion):
        raise PromotionError("a measurement is required")
    if not isinstance(ledger, LedgerPort):
        raise PromotionError("a single-use evidence ledger is required")

    facts = {"measured": {k: [v.passed, v.total] for k, v in measurement.measured.items()},
             "holdout": {k: [v.passed, v.total] for k, v in measurement.holdout.items()},
             "applicability": list(measurement.applicability)}

    def refuse(reason: str) -> PromotionVerdict:
        return PromotionVerdict(False, reason, facts=facts)

    if candidate.candidate_version != measurement.candidate_version:
        return refuse("evidence_measured_on_another_version")
    if candidate.current_version != measurement.baseline_version:
        return refuse("evidence_measured_on_another_baseline")
    # Применимость проверяется ЗДЕСЬ, а не при измерении: между ними проходит
    # время, и версия могла смениться под уже снятыми числами.
    if applicability_version != measurement.applicability_version:
        return refuse("applicability_version_changed")
    outside = sorted(set(measurement.applicability) - set(applicability_scope))
    if outside:
        facts["outside_scope"] = outside
        return refuse("measured_outside_declared_applicability")

    measured_b, measured_c = measurement.measured[BASELINE], measurement.measured[CANDIDATE]
    holdout_b, holdout_c = measurement.holdout[BASELINE], measurement.holdout[CANDIDATE]
    if measured_c.total < MIN_MEASURED_TASKS:
        return refuse("insufficient_measured_tasks")
    if holdout_c.total < MIN_HOLDOUT_TASKS:
        return refuse("insufficient_holdout_tasks")
    # Выигрыш только на подобранных задачах — подгонка, а не улучшение.
    if holdout_c.pass_rate < holdout_b.pass_rate:
        facts["holdout_pass_rate"] = [holdout_b.pass_rate, holdout_c.pass_rate]
        return refuse("holdout_regression")

    evidence = PromotionEvidence(
        baseline_score=measured_b.pass_rate, candidate_score=measured_c.pass_rate,
        intelligence_retention=retention, retention_evidence_ref=retention_evidence_ref,
        security_pass=security_pass, rollback_available=rollback_available,
        sample_count=measured_c.total + holdout_c.total)
    ok, reason = may_promote(candidate, evidence)
    if not ok:
        return refuse(reason)

    # Улика тратится ПОСЛЕДНЕЙ и ровно один раз: всё, что могло отказать по
    # форме, уже отказало и измерение не сожгло.
    refusal = ledger.consume(measurement.evidence_key, measurement.consumer)
    if refusal is not None:
        facts["ledger"] = refusal
        return refuse("evidence_already_spent")
    return PromotionVerdict(True, "eligible_for_controlled_canary",
                            evidence_key=measurement.evidence_key,
                            consumer=measurement.consumer, facts=facts)
