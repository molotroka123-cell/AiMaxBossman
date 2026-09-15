"""V5 N5: продвижение навыка становится ИЗМЕРЕННЫМ, а не заявленным.

`may_promote` был гейтом без измерителя: он потреблял `PromotionEvidence`,
которую можно было собрать руками из чисел, не полученных ни в одном прогоне.
Отсюда `Measured skill promotion = NOT_RUN` в карточке V5.

Проверяются четыре вещи и по каждой — отрицательный контроль: отложенная
выборка (а не подгонка), привязка к применимости и её версии, одноразовость
улики, и структурный отказ на расширении прав, который никакими числами не
покупается.
"""
from __future__ import annotations

import pytest

from bossman_shared.objective_improvement import (CORE_INTELLIGENCE_RETENTION,
                                                  CandidateImprovement, REQUIRED_STAGES)
from bossman_shared.objective_promotion import (BASELINE, CANDIDATE, MIN_HOLDOUT_TASKS,
                                                LedgerPort, MeasuredPromotion, Outcome,
                                                PromotionError, Task, authorize, measure,
                                                split_tasks)

SCOPE = ("python.refactor", "python.test")
REF = "intelligence_preservation/paired/" + "a" * 64


class FakeLedger:
    """Тот же контракт, что у канонического durable-журнала AUDIT001-F5-REPLAY:
    одна улика — один потребитель; повтор тем же потребителем разрешён."""

    def __init__(self) -> None:
        self.spent: dict[str, str] = {}

    def consume(self, key: str, consumer: str) -> str | None:
        held = self.spent.get(key)
        if held is None:
            self.spent[key] = consumer
            return None
        return None if held == consumer else f"already spent by {held}"


def tasks(n=40):
    return [Task(f"t-{i:03d}", SCOPE[i % len(SCOPE)]) for i in range(n)]


def candidate(**kw):
    base = dict(kind="skill", current_version="v1", candidate_version="v2",
                hypothesis="a better refactor template", completed_stages=REQUIRED_STAGES)
    base.update(kw)
    return CandidateImprovement(**base)


def runner(*, candidate_wins_on, baseline_rate=0.5):
    """Прогон, который выигрывает только на названных задачах."""
    def run(variant, task):
        index = int(task.task_id.split("-")[1])
        if variant == BASELINE:
            passed = (index % 2 == 0) if baseline_rate == 0.5 else index < 0
        else:
            passed = (index % 2 == 0) or task.task_id in candidate_wins_on
        return Outcome(task.task_id, passed, 1.0 if passed else 0.0)
    return run


def measured(run, *, version="v2", applicability_version="app-3", n=40):
    return measure(tasks(n), candidate_id="skill.refactor", candidate_version=version,
                   baseline_version="v1", applicability_version=applicability_version, run=run)


def authorized(measurement, ledger, *, cand=None, applicability_version="app-3",
               scope=SCOPE, retention=0.995):
    return authorize(measurement, cand or candidate(), ledger=ledger,
                     applicability_version=applicability_version, applicability_scope=scope,
                     retention=retention, retention_evidence_ref=REF,
                     security_pass=True, rollback_available=True)


# --------------------------------------------------------- разбиение

def test_the_split_is_deterministic_and_reproducible():
    """Отложенная выборка, которую нельзя воспроизвести, — это слово, а не метод."""
    a = split_tasks(tasks(), material="m")
    b = split_tasks(list(reversed(tasks())), material="m")
    assert a == b and a.digest == b.digest
    assert set(a.measured) | set(a.holdout) == {t.task_id for t in tasks()}
    assert not set(a.measured) & set(a.holdout)
    assert split_tasks(tasks(), material="other") != a       # материал меняет разбиение


@pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1, 1.5])
def test_a_degenerate_split_is_refused(fraction):
    with pytest.raises(PromotionError):
        split_tasks(tasks(), material="m", holdout_fraction=fraction)


def test_a_duplicate_task_is_refused():
    with pytest.raises(PromotionError):
        split_tasks([Task("t", "a"), Task("t", "a")], material="m")


# --------------------------------------------------------- измерение

def test_both_lanes_are_measured_on_the_same_tasks():
    result = measured(runner(candidate_wins_on=set()))
    assert result.measured[BASELINE].total == result.measured[CANDIDATE].total
    assert result.holdout[BASELINE].total == result.holdout[CANDIDATE].total
    assert result.measured[CANDIDATE].total + result.holdout[CANDIDATE].total == 40
    assert result.applicability == tuple(sorted(SCOPE))


def test_a_lane_that_answers_the_wrong_task_is_refused():
    def liar(variant, task):
        return Outcome("t-999", True, 1.0)
    with pytest.raises(PromotionError, match="answered"):
        measured(liar)


def test_measuring_the_same_version_against_itself_is_refused():
    with pytest.raises(PromotionError, match="same version"):
        measure(tasks(), candidate_id="s", candidate_version="v1", baseline_version="v1",
                applicability_version="app-3", run=runner(candidate_wins_on=set()))


def test_an_empty_task_set_measures_nothing():
    with pytest.raises(PromotionError, match="empty task set"):
        measure([], candidate_id="s", candidate_version="v2", baseline_version="v1",
                applicability_version="app-3", run=runner(candidate_wins_on=set()))


# --------------------------------------------------------- отложенная выборка

def test_a_real_improvement_is_authorized():
    """Положительный контроль: кандидат выигрывает ВЕЗДЕ — и на измеряемой
    части, и на отложенной."""
    everywhere = {t.task_id for t in tasks()}
    verdict = authorized(measured(runner(candidate_wins_on=everywhere)), FakeLedger())
    assert verdict.authorized, verdict.reason
    assert verdict.reason == "eligible_for_controlled_canary"
    assert verdict.evidence_key and verdict.consumer == "skill.refactor@v2"


def test_winning_only_on_the_tuned_tasks_is_refused():
    """ГЛАВНЫЙ отрицательный контроль: улучшение, показанное на тех же задачах,
    на которых кандидата подбирали, — подгонка. На отложенной он проигрывает."""
    split = split_tasks(tasks(), material="skill.refactor:app-3")
    def overfit(variant, task):
        index = int(task.task_id.split("-")[1])
        base = index % 2 == 0
        if variant == BASELINE:
            return Outcome(task.task_id, base, 1.0 if base else 0.0)
        # выигрывает на измеряемых, ломает часть отложенных
        passed = True if task.task_id in split.measured else (base and index % 4 == 0)
        return Outcome(task.task_id, passed, 1.0 if passed else 0.0)
    verdict = authorized(measured(overfit), FakeLedger())
    assert not verdict.authorized and verdict.reason == "holdout_regression"


def test_a_holdout_too_small_to_mean_anything_is_refused():
    result = measured(runner(candidate_wins_on={t.task_id for t in tasks(12)}), n=12)
    assert result.holdout[CANDIDATE].total < MIN_HOLDOUT_TASKS
    verdict = authorized(result, FakeLedger())
    assert not verdict.authorized
    assert verdict.reason in ("insufficient_measured_tasks", "insufficient_holdout_tasks")


# --------------------------------------------------------- применимость

def test_a_changed_applicability_version_invalidates_the_measurement():
    """Числа сняты под версией 3. Под версией 4 они ничего не утверждают."""
    result = measured(runner(candidate_wins_on={t.task_id for t in tasks()}))
    verdict = authorized(result, FakeLedger(), applicability_version="app-4")
    assert not verdict.authorized and verdict.reason == "applicability_version_changed"


def test_measuring_outside_the_declared_scope_is_refused():
    result = measured(runner(candidate_wins_on={t.task_id for t in tasks()}))
    verdict = authorized(result, FakeLedger(), scope=("python.refactor",))
    assert not verdict.authorized
    assert verdict.reason == "measured_outside_declared_applicability"
    assert verdict.facts["outside_scope"] == ["python.test"]


def test_evidence_for_another_version_is_refused():
    result = measured(runner(candidate_wins_on={t.task_id for t in tasks()}), version="v7")
    verdict = authorized(result, FakeLedger(), cand=candidate(candidate_version="v2"))
    assert not verdict.authorized and verdict.reason == "evidence_measured_on_another_version"


# --------------------------------------------------------- одноразовость

def test_one_measurement_promotes_exactly_one_candidate():
    """AUDIT001-F5-REPLAY на пути целей: те же числа, поданные другой версии,
    не продвигают её."""
    ledger = FakeLedger()
    everywhere = {t.task_id for t in tasks()}
    first = measured(runner(candidate_wins_on=everywhere))
    assert authorized(first, ledger).authorized

    # Тот же кандидат, повторная попытка — не реплей, а повтор: разрешено.
    assert authorized(first, ledger).authorized

    # Другая версия с байт в байт теми же числами — отказ.
    replay = MeasuredPromotion(
        candidate_id=first.candidate_id, candidate_version="v9",
        baseline_version=first.baseline_version, applicability=first.applicability,
        applicability_version=first.applicability_version, split=first.split,
        measured=first.measured, holdout=first.holdout,
        task_set_digest=first.task_set_digest)
    assert replay.evidence_key == first.evidence_key          # улика ТА ЖЕ
    verdict = authorized(replay, ledger, cand=candidate(candidate_version="v9"))
    assert not verdict.authorized and verdict.reason == "evidence_already_spent"


def test_a_structural_refusal_does_not_burn_the_measurement():
    """Отказ по форме не должен сжигать улику: иначе одна опечатка в заявке
    навсегда лишает честного кандидата его измерения."""
    ledger = FakeLedger()
    result = measured(runner(candidate_wins_on={t.task_id for t in tasks()}))
    widening = authorized(result, ledger, cand=candidate(widens_permissions=True))
    assert not widening.authorized and widening.reason == "permission_widening_refused"
    assert ledger.spent == {}                                  # ничего не потрачено
    assert authorized(result, ledger).authorized               # улика ещё цела


def test_an_unusable_ledger_refuses_rather_than_promoting():
    class Broken:
        def consume(self, key, consumer):
            return "evidence ledger unavailable (OSError); promotion refused"
    result = measured(runner(candidate_wins_on={t.task_id for t in tasks()}))
    verdict = authorized(result, Broken())
    assert not verdict.authorized and verdict.reason == "evidence_already_spent"
    assert "unavailable" in verdict.facts["ledger"]


def test_a_ledger_is_required():
    result = measured(runner(candidate_wins_on={t.task_id for t in tasks()}))
    with pytest.raises(PromotionError, match="single-use"):
        authorize(result, candidate(), ledger=object(), applicability_version="app-3",
                  applicability_scope=SCOPE, retention=0.99, retention_evidence_ref=REF,
                  security_pass=True, rollback_available=True)


# --------------------------------------------------------- права и удержание

@pytest.mark.parametrize("kw,expected", [
    ({"widens_permissions": True}, "permission_widening_refused"),
    ({"touches_trust_kernel": True}, "trust_kernel_rewrite_refused"),
    ({"kind": "policy_kernel"}, "trust_critical_kind_never_auto_promoted"),
    ({"kind": "finalizer"}, "trust_critical_kind_never_auto_promoted"),
    ({"completed_stages": ()}, "pipeline_stage_skipped:observe"),
])
def test_no_measurement_buys_a_structural_refusal(kw, expected):
    result = measured(runner(candidate_wins_on={t.task_id for t in tasks()}))
    verdict = authorized(result, FakeLedger(), cand=candidate(**kw))
    assert not verdict.authorized and verdict.reason == expected


def test_an_intelligence_regression_is_refused_even_with_a_better_score():
    """Навык, который лучше решает задачи и при этом теряет общий интеллект,
    не продвигается: гейт удержания стоит раньше выгоды."""
    result = measured(runner(candidate_wins_on={t.task_id for t in tasks()}))
    verdict = authorized(result, FakeLedger(), retention=CORE_INTELLIGENCE_RETENTION - 0.01)
    assert not verdict.authorized and verdict.reason == "intelligence_regression"


def test_an_unbound_retention_figure_is_not_evidence():
    result = measured(runner(candidate_wins_on={t.task_id for t in tasks()}))
    verdict = authorize(result, candidate(), ledger=FakeLedger(), applicability_version="app-3",
                        applicability_scope=SCOPE, retention=0.999,
                        retention_evidence_ref="trust me", security_pass=True,
                        rollback_available=True)
    assert not verdict.authorized
    assert verdict.reason == "retention_not_bound_to_paired_measurement"


def test_no_measured_improvement_is_refused():
    result = measured(runner(candidate_wins_on=set()))
    verdict = authorized(result, FakeLedger())
    assert not verdict.authorized and verdict.reason == "no_measured_improvement"


def test_the_fake_ledger_matches_the_canonical_port():
    assert isinstance(FakeLedger(), LedgerPort)
