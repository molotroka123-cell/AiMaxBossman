"""V5 N8: канареечная активация и РЕПЕТИЦИЯ ОТКАТА с целью в полёте.

`prepare_rollback` отдавал порядок отката как данные, но откат никто не
репетировал, а канареечного шага не было вовсе: ревизия действовала либо для
всех, либо ни для кого. Обе строки карточки V5 стояли NOT_RUN.

Репетиция здесь настоящая: цель живёт, у неё НЕОБРАТИМЫЙ эффект в полёте с
неизвестным исходом, ревизия сменилась под ней, канарейка упала. Проверяются
ровно четыре обещания отката:

  * цель не потеряна;
  * необратимый эффект не повторён;
  * ревизия и улики пережили откат;
  * упавшая канарейка ЗАКРЫВАЕТ широкую активацию.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from bossman_shared.objective_canary import (FAILED, MIN_COHORT, PASSED, PENDING, CanaryError,
                                             CanaryEvidenceLedger, CanaryOutcome, CanaryPlan,
                                             CanaryPolicy, authorize_broad_activation,
                                             evaluate_canary, may_activate_broadly, plan_canary)
from bossman_shared.objective_recovery import (IRREVERSIBLE, NOT_APPLIED, PARKED,
                                               REASON_AMBIGUOUS, REASON_IRREVERSIBLE, UNKNOWN,
                                               prepare_rollback, recover, resume_point)
from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import ObjectiveStore
from test_v5_admission import (CAPABILITY, EFFECT, NOW, OBJECTIVE, OWNER, AdmissionKernel,
                               CostEstimate, FakeConflicts, FakePolicy, FakeTreasury,
                               build_proposal, observation, spec_dict)

from bossman_shared import evidence as _evidence

POPULATION = tuple(f"obj-{i:02d}" for i in range(20))
DIGEST = "a" * 64


@pytest.fixture(autouse=True)
def _evidence_key(tmp_path, monkeypatch):
    """Ключ подписи улик — во временном каталоге, а не в домашнем."""
    monkeypatch.setenv(_evidence.ENV_KEY_FILE, str(tmp_path / "evidence.key"))
    monkeypatch.setenv(_evidence.ENV_DATA_DIR, str(tmp_path / "data"))
    _evidence.reset_cache()
    yield
    _evidence.reset_cache()


# ------------------------------------------------------------ когорта

def test_the_cohort_is_deterministic_for_a_revision():
    """Невоспроизводимая канарейка не проверяема: тот же выпуск — та же когорта."""
    first = plan_canary(POPULATION, revision_digest=DIGEST, now=NOW)
    second = plan_canary(reversed(POPULATION), revision_digest=DIGEST, now=NOW + 999.0)
    assert first.cohort == second.cohort
    assert plan_canary(POPULATION, revision_digest="b" * 64, now=NOW).cohort != first.cohort


def test_the_cohort_is_a_bounded_slice_of_the_population():
    plan = plan_canary(POPULATION, revision_digest=DIGEST, now=NOW,
                       policy=CanaryPolicy(fraction=0.2))
    assert len(plan.cohort) == 4 and set(plan.cohort) <= set(POPULATION)
    assert set(plan.cohort) & set(plan.rest) == set()
    assert set(plan.cohort) | set(plan.rest) == set(POPULATION)
    # Малый парк всё равно получает осмысленную когорту, а не одну цель.
    small = plan_canary(POPULATION[:5], revision_digest=DIGEST, now=NOW,
                        policy=CanaryPolicy(fraction=0.1))
    assert len(small.cohort) == MIN_COHORT
    # И «канарейка» не растёт до всего парка.
    capped = plan_canary(POPULATION, revision_digest=DIGEST, now=NOW,
                         policy=CanaryPolicy(fraction=0.9, max_cohort=5))
    assert len(capped.cohort) == 5


def test_a_population_too_small_for_a_canary_is_refused():
    with pytest.raises(CanaryError, match="cannot host a canary"):
        plan_canary(POPULATION[:2], revision_digest=DIGEST, now=NOW)


@pytest.mark.parametrize("kwargs", [{"fraction": 0.0}, {"fraction": 1.0}, {"min_cohort": 1},
                                    {"min_cohort": 5, "max_cohort": 4}])
def test_an_unusable_canary_policy_is_refused(kwargs):
    with pytest.raises(CanaryError):
        CanaryPolicy(**kwargs)


def test_a_duplicate_objective_in_the_population_is_refused():
    with pytest.raises(CanaryError, match="duplicate"):
        plan_canary(POPULATION + POPULATION[:1], revision_digest=DIGEST, now=NOW)


# ------------------------------------------------------------ вердикт

def _plan():
    return plan_canary(POPULATION, revision_digest=DIGEST, now=NOW)


# Улика здоровья — не строка, а разрешаемая ссылка (IV5-CAN-002). Ключ живёт в
# тесте: модуль канарейки остаётся чистым и ключей сам не читает.
CANARY_KEY = b"canary-evidence-key-32-bytes-!!!"


def _attested(plan, ledger, *, issued_at=NOW + 1.0):
    """Настоящие, выпущенные ключом улики для каждого члена когорты."""
    return [CanaryOutcome(o, True, ledger.issue(plan, o, issued_at=issued_at))
            for o in plan.cohort]


def test_silence_is_never_success():
    """Отсутствие плохих новостей не является хорошей новостью: именно так
    зависший прогон превращался бы в общий выпуск."""
    plan = _plan()
    verdict = evaluate_canary(plan, [CanaryOutcome(plan.cohort[0], True)])
    assert verdict.state == PENDING and verdict.reason == "canary_incomplete"
    assert set(verdict.silent) == set(plan.cohort[1:])
    allowed, reason = may_activate_broadly(verdict)
    assert not allowed and reason == "canary_incomplete"


def test_an_unknown_answer_is_silence_not_health():
    plan = _plan()
    verdict = evaluate_canary(plan, [CanaryOutcome(o, None) for o in plan.cohort])
    assert verdict.state == PENDING and set(verdict.silent) == set(plan.cohort)


def test_one_unhealthy_member_fails_the_whole_canary():
    """Не доля здоровых: ревизия, ломающая одну цель из десяти, ломает цель."""
    plan = _plan()
    outcomes = [CanaryOutcome(o, True) for o in plan.cohort[:-1]]
    outcomes.append(CanaryOutcome(plan.cohort[-1], False, detail="condition regressed"))
    verdict = evaluate_canary(plan, outcomes)
    assert verdict.state == FAILED and verdict.reason == "canary_member_unhealthy"
    assert verdict.unhealthy == (plan.cohort[-1],)
    allowed, reason = may_activate_broadly(verdict)
    assert not allowed and reason == "canary_failed"


def test_a_failure_decides_even_before_everyone_reports():
    plan = _plan()
    verdict = evaluate_canary(plan, [CanaryOutcome(plan.cohort[0], False)])
    assert verdict.state == FAILED and verdict.silent      # ждать остальных незачем
    assert not may_activate_broadly(verdict)[0]


def test_a_clear_canary_opens_broad_activation():
    """Положительный контроль: выпуск открывают ПРИВЯЗАННЫЕ улики, а не строки.

    Раньше здесь стояло `f"ev-{o}"` — выдуманная строка, которую никто не
    резолвил, и тест утверждал, что она открывает парк. Он проходил ровно
    потому, что дефекта IV5-CAN-002 не видел.
    """
    plan, ledger = _plan(), CanaryEvidenceLedger(CANARY_KEY)
    allowed, reason, verdict = authorize_broad_activation(
        plan, _attested(plan, ledger), resolve=ledger.resolve, now=NOW + 2.0)
    assert verdict.state == PASSED and verdict.reason == "canary_clear"
    assert (allowed, reason) == (True, "canary_passed")


def test_the_same_cohort_with_unresolvable_evidence_does_not_open_activation():
    """Отрицательный контроль к предыдущему тесту: та же когорта, те же
    `healthy=True`, отличается ТОЛЬКО происхождение улик."""
    plan, ledger = _plan(), CanaryEvidenceLedger(CANARY_KEY)
    outcomes = [CanaryOutcome(o, True, f"ev-{o}") for o in plan.cohort]
    allowed, reason, _ = authorize_broad_activation(
        plan, outcomes, resolve=ledger.resolve, now=NOW + 2.0)
    assert (allowed, reason) == (False, "canary_evidence_unattested")


def test_a_report_from_outside_the_cohort_is_not_evidence():
    plan = _plan()
    outsider = next(o for o in POPULATION if o not in plan.cohort)
    with pytest.raises(CanaryError, match="not in the cohort"):
        evaluate_canary(plan, [CanaryOutcome(outsider, True)])


def test_no_verdict_at_all_blocks_activation():
    assert may_activate_broadly(None) == (False, "no_canary_verdict")


# ------------------------------------------------- репетиция отката

def _live_objective(tmp_path):
    """Живая цель с НАСТОЯЩИМ допуском в полёте — не подделанная строка брони.

    Класс эффекта в полезной нагрузке допуска не записан, и восстановление
    считает такой эффект НЕОБРАТИМЫМ: безопасное значение по умолчанию для
    отсутствующего факта — то, которое нельзя отменить.
    """
    store = ObjectiveStore(tmp_path / "objectives.db")
    spec = ObjectiveSpec.from_dict(spec_dict())
    state = store.create(spec)
    state = store.enroll_sources(OBJECTIVE, ("src:build",), owner_id=OWNER,
                                 expected_version=state.version)
    store.transition(OBJECTIVE, "ACTIVE", now=NOW, owner_id=OWNER,
                     expected_version=state.version)
    proposal = build_proposal(
        store, OBJECTIVE, [observation(spec.digest, observed_at=NOW - 10.0)], now=NOW,
        trigger="scheduled", requested_capabilities=(CAPABILITY,), expected_effects=(EFFECT,),
        cost_estimate=CostEstimate(0.5, 1000, 30.0))
    kernel = AdmissionKernel(FakePolicy({"perm:repo.write"}, {CAPABILITY}),
                             FakeTreasury(), FakeConflicts())
    decision = kernel.admit(store, proposal, now=NOW)
    assert decision.admitted, decision.reason
    return store, spec, decision


def _revision_two(store, previous):
    """Ревизия привязывается к ДОВЕРЕННОМУ предшественнику, а не к числу в поле."""
    state = store.get(OBJECTIVE)
    spec = ObjectiveSpec.from_dict({**spec_dict(revision=2, previous_digest=previous.digest)},
                                   previous=previous)
    return store.revise(OBJECTIVE, spec, owner_id=OWNER, expected_version=state.version)


def test_a_rollback_rehearsal_with_an_objective_in_flight(tmp_path):
    """Полная репетиция: необратимый эффект в полёте, ревизия сменилась под
    целью, канарейка упала, откат отрепетирован — и все четыре обещания
    проверены на настоящем хранилище."""
    store, spec, decision = _live_objective(tmp_path)

    # Улика последнего подтверждённого состояния — то, из чего резюмируется работа.
    verified_ref = store.record_condition_evidence(OBJECTIVE, condition="SATISFIED",
                                                   run_id=decision.reservation_id)
    state = store.set_condition(OBJECTIVE, "SATISFIED", evidence_ref=verified_ref,
                                expected_version=store.get(OBJECTIVE).version)
    assert state.last_verified_evidence_ref == verified_ref

    before = resume_point(store, OBJECTIVE)
    assert before.open_reservations == (decision.reservation_id,)

    # Ревизия под целью, у которой эффект уже в полёте.
    revised = _revision_two(store, spec)
    assert revised.revision == 2

    # Канарейка на парке, куда эта цель входит, — падает на ней самой.
    population = (OBJECTIVE,) + POPULATION[:9]
    plan = plan_canary(population, revision_digest=revised.spec_digest, now=NOW,
                       policy=CanaryPolicy(fraction=0.3))
    verdict = evaluate_canary(plan, [CanaryOutcome(o, o != plan.cohort[0]) for o in plan.cohort])
    assert verdict.state == FAILED

    # ОБЕЩАНИЕ 4: упавшая канарейка закрывает широкую активацию.
    assert may_activate_broadly(verdict) == (False, "canary_failed")

    # Откат репетируется: построение плана ничего не исполняет и не разрушает.
    rollback = prepare_rollback(store, OBJECTIVE, now=NOW + 1.0)
    assert not rollback.executed
    assert [s.action for s in rollback.steps] == [
        "pause_observers", "stop_admissions", "fence_effects",
        "drain_park_missions", "snapshot_objective_state"]
    assert rollback.open_reservations == (decision.reservation_id,)
    assert rollback.snapshot()["objective_id"] == OBJECTIVE

    # Исполняем то, что план описывает последним шагом: разбор незакрытой брони.
    # Наблюдатель ЧЕСТНО не знает, приземлился ли эффект.
    report = recover(store, OBJECTIVE, now=NOW + 1.0, is_effect_applied=lambda r: UNKNOWN,
                     expected_version=store.get(OBJECTIVE).version)

    # ОБЕЩАНИЕ 2: необратимый эффект неизвестного исхода ПАРКУЕТСЯ, не повторяется.
    assert [o.disposition for o in report.outcomes] == [PARKED]
    assert report.outcomes[0].effect_class == IRREVERSIBLE
    assert report.outcomes[0].reason == REASON_AMBIGUOUS
    assert report.blocked

    # ОБЕЩАНИЕ 1 и 3: цель на месте, ревизия и накопленный расход целы, и всё
    # это переживает перезапуск процесса.
    restarted = ObjectiveStore(tmp_path / "objectives.db")
    survived = restarted.get(OBJECTIVE)
    assert survived.objective_id == OBJECTIVE
    assert survived.revision == 2
    assert survived.condition == "UNKNOWN"      # улики прежней ревизии не переносятся
    after = resume_point(restarted, OBJECTIVE)
    assert after.open_reservations == (decision.reservation_id,)
    assert after.observations_used == before.observations_used
    assert after.missions_used == before.missions_used
    assert after.cost_usd_used == before.cost_usd_used

    # Повторное восстановление не удваивает ни эффект, ни разбор.
    again = recover(restarted, OBJECTIVE, now=NOW + 2.0, is_effect_applied=lambda r: UNKNOWN,
                    expected_version=restarted.get(OBJECTIVE).version)
    assert [o.disposition for o in again.outcomes] == [PARKED]
    assert [r["reservation_id"] for r in restarted.open_reservations(OBJECTIVE)] == [
        decision.reservation_id]


def test_a_passing_canary_still_leaves_the_in_flight_effect_parked(tmp_path):
    """Отрицательный контроль к репетиции: зелёная канарейка открывает выпуск,
    но НЕ разрешает повторить необратимый эффект неизвестного исхода."""
    store, spec, decision = _live_objective(tmp_path)
    ledger = CanaryEvidenceLedger(CANARY_KEY)
    plan = plan_canary((OBJECTIVE,) + POPULATION[:9], revision_digest=DIGEST, now=NOW,
                       process_identity="rehearsal/pid-1")
    allowed, reason, _ = authorize_broad_activation(
        plan, _attested(plan, ledger), resolve=ledger.resolve, now=NOW + 2.0)
    assert (allowed, reason) == (True, "canary_passed")

    report = recover(store, OBJECTIVE, now=NOW + 1.0, is_effect_applied=lambda r: UNKNOWN,
                     expected_version=store.get(OBJECTIVE).version)
    assert [o.disposition for o in report.outcomes] == [PARKED] and report.blocked
    assert [r["reservation_id"] for r in store.open_reservations(OBJECTIVE)] == [
        decision.reservation_id]


def test_even_a_confident_not_applied_parks_an_irreversible_effect(tmp_path):
    """Второй отрицательный контроль: наблюдатель УВЕРЕН, что эффект не
    приземлился. Для необратимого эффекта это всё равно парковка, а не
    автоматический повтор — уверенность наблюдателя не является правом
    отправить необратимое действие второй раз."""
    store, spec, decision = _live_objective(tmp_path)
    report = recover(store, OBJECTIVE, now=NOW + 1.0, is_effect_applied=lambda r: NOT_APPLIED,
                     expected_version=store.get(OBJECTIVE).version)
    assert [o.disposition for o in report.outcomes] == [PARKED]
    assert report.outcomes[0].reason == REASON_IRREVERSIBLE
    assert report.blocked
    assert [r["reservation_id"] for r in store.open_reservations(OBJECTIVE)] == [
        decision.reservation_id]
