"""P0-1: у канареечной двери появился ПРОИЗВОДСТВЕННЫЙ вызывающий.

`objective_canary` был верен и закрыт, но его никто не вызывал: широкой
активации в продакшене не существовало как кода, поэтому гейт нельзя было ни
обойти, ни пройти — его просто не было на пути. Здесь проверяется граница
`bossman_shared.objective_activation.BroadActivationGate`: единственный путь, которым
ревизия переходит с когорты на остальной парк.

Отрицательные проверки — главные. Для КАЖДОГО способа предъявить негодную улику
или негодную личность парк обязан остаться нетронутым: подделка, чужая цель,
чужая ревизия, прошлый прогон, просроченная улика, чужой процесс, молчащий
член, упавший член, обход двери и перезапуск без долговечного вердикта.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bossman_shared.objective_activation import (ABANDONED, ACTIVATED, OPEN, ActivationError,
                                                 BroadActivationGrant, BroadActivationGate,
                                                 GRANT_TTL_SECONDS, REASON_BAD_GRANT,
                                                 REASON_NOT_OPEN, REASON_NO_GRANT,
                                                 REASON_STALE_GRANT, REASON_WRONG_PROCESS,
                                                 revision_digest)
from bossman_shared.objective_canary import (CanaryEvidenceLedger, CanaryPlan, FAILED, PASSED,
                                             PENDING)
from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import ObjectiveStore

from test_v5_admission import NOW, OWNER, spec_dict

KEY = b"canary-evidence-key-of-sufficient-length"
OTHER_KEY = b"a-different-evidence-key-entirely-here!!"
PROCESS = "bossman-fleet-controller@rollout-1"
FLEET = tuple(f"objective:fleet-{i:02d}" for i in range(12))


# ------------------------------------------------------------------ фикстуры

def _spec(objective_id: str, *, priority: int = 5) -> ObjectiveSpec:
    raw = spec_dict()
    raw.update(objective_id=objective_id, priority=priority)
    return ObjectiveSpec.from_dict(raw)


def _candidate(previous: ObjectiveSpec, *, priority: int = 9) -> ObjectiveSpec:
    raw = previous.to_dict()
    raw.update(revision=raw["revision"] + 1, previous_digest=previous.digest, priority=priority)
    return ObjectiveSpec.from_dict(raw, previous=previous)


def _fleet(tmp_path, name: str = "objectives.db"):
    """Настоящее хранилище с настоящим парком целей."""
    store = ObjectiveStore(tmp_path / name)
    base = {}
    for objective_id in FLEET:
        spec = _spec(objective_id)
        store.create(spec)
        base[objective_id] = spec
    return store, base


def _rollout(store, *, key: bytes = KEY, process: str = PROCESS) -> BroadActivationGate:
    return BroadActivationGate(store, evidence_key=key, process_identity=process, owner_id=OWNER)


def _opened(tmp_path, *, priority: int = 9, name: str = "objectives.db"):
    store, base = _fleet(tmp_path, name)
    candidates = {o: _candidate(s, priority=priority) for o, s in base.items()}
    rollout = _rollout(store)
    plan = rollout.open(candidates, now=NOW)
    return store, base, candidates, rollout, plan


def _digests(store) -> dict[str, str]:
    return {o: store.get(o).spec_digest for o in FLEET}


def _body(spec) -> dict:
    """Тело редакции без полей цепочки: откат возвращает СОДЕРЖАНИЕ,
    а не переписывает историю, поэтому номер ревизии после отката растёт."""
    raw = spec.to_dict()
    raw.pop("revision")
    raw.pop("previous_digest")
    return raw


def _bodies(store) -> dict[str, dict]:
    return {o: _body(store.get_spec(o)) for o in FLEET}


def _rest_untouched(store, base, plan) -> bool:
    """Ни одна цель вне когорты не получила кандидата."""
    return all(store.get(o).spec_digest == base[o].digest for o in plan.rest)


def _bound_evidence(plan: CanaryPlan, objective_id: str, *, at: float, key: bytes = KEY,
                    **overrides) -> str:
    """Улика, выпущенная ТЕМ ЖЕ ключом, но привязанная к другому плану.

    Ровно одна привязка отличается — так проверяется именно она, а не «улика
    вообще не разрешилась».
    """
    fields = {"revision_digest": plan.revision_digest, "cohort": plan.cohort,
              "population": plan.population, "started_at": plan.started_at,
              "process_identity": plan.process_identity, "cohort_digest": plan.cohort_digest,
              "run_id": plan.run_id}
    fields.update(overrides)
    return CanaryEvidenceLedger(key).issue(CanaryPlan(**fields), objective_id, issued_at=at)


# ------------------------------------------------ граница вообще существует

def test_opening_a_rollout_touches_the_cohort_and_nobody_else(tmp_path):
    store, base, candidates, rollout, plan = _opened(tmp_path)
    assert len(plan.cohort) >= 3 and set(plan.cohort) < set(FLEET)
    for objective_id in plan.cohort:
        assert store.get(objective_id).spec_digest == candidates[objective_id].digest
    assert _rest_untouched(store, base, plan)
    # Прогон долговечен ДО того, как парк тронут дальше.
    run = store.canary_run(plan.run_id)
    assert run["state"] == OPEN and run["process_identity"] == PROCESS
    assert tuple(sorted(run["cohort"])) == plan.cohort


def test_the_broad_path_is_the_only_public_way_to_the_rest(tmp_path):
    """Единственные публичные входы к остальному парку — дверь и разрешение."""
    store, _, _, rollout, _ = _opened(tmp_path)
    public = {name for name in dir(rollout)
              if not name.startswith("_") and callable(getattr(rollout, name))}
    assert public == {"open", "plan_of", "issue_evidence", "report_healthy", "report_unhealthy",
                      "report_raw", "outcomes", "authorize", "activate_broadly",
                      "apply_broad_revision", "rollback"}


# ------------------------------------------------------- ОТРИЦАТЕЛЬНЫЕ


def _deny(rollout, store, base, plan, *, now: float = NOW + 5.0):
    outcome = rollout.activate_broadly(plan.run_id, now=now)
    assert outcome.allowed is False
    assert outcome.activated == ()
    assert _rest_untouched(store, base, plan)
    assert store.canary_run(plan.run_id)["state"] == OPEN
    return outcome


def test_forged_evidence_is_denied(tmp_path):
    """Выдуманная строка не разрешается ни в какую привязку."""
    store, base, _, rollout, plan = _opened(tmp_path)
    for objective_id in plan.cohort:
        rollout.report_raw(plan.run_id, objective_id, healthy=True,
                           evidence_ref="cev1.Zm9yZ2Vk." + "0" * 64, at=NOW + 1.0)
    outcome = _deny(rollout, store, base, plan)
    assert outcome.reason == "canary_evidence_unattested"
    assert {oid for oid, _ in outcome.verdict.unattested} == set(plan.cohort)
    assert {why for _, why in outcome.verdict.unattested} == {"unresolved"}


def test_evidence_for_another_objective_is_denied(tmp_path):
    """Улика соседа не является уликой об этой цели."""
    store, base, _, rollout, plan = _opened(tmp_path)
    donor, victim = plan.cohort[0], plan.cohort[1]
    stolen = rollout.issue_evidence(plan.run_id, donor, at=NOW + 1.0)
    rollout.report_healthy(plan.run_id, donor, at=NOW + 1.0)
    rollout.report_raw(plan.run_id, victim, healthy=True, evidence_ref=stolen, at=NOW + 1.0)
    for objective_id in plan.cohort[2:]:
        rollout.report_healthy(plan.run_id, objective_id, at=NOW + 1.0)
    outcome = _deny(rollout, store, base, plan)
    assert outcome.reason == "canary_evidence_unattested"
    assert (victim, "wrong_objective") in outcome.verdict.unattested


def test_evidence_for_another_revision_is_denied(tmp_path):
    """Улика о другой ревизии не открывает эту."""
    store, base, _, rollout, plan = _opened(tmp_path)
    for objective_id in plan.cohort:
        rollout.report_raw(plan.run_id, objective_id, healthy=True, at=NOW + 1.0,
                           evidence_ref=_bound_evidence(plan, objective_id, at=NOW + 1.0,
                                                        revision_digest="f" * 64))
    outcome = _deny(rollout, store, base, plan)
    assert {why for _, why in outcome.verdict.unattested} == {"wrong_revision"}


def test_evidence_from_a_previous_run_is_denied(tmp_path):
    """Тот же парк, та же ревизия, ДРУГОЙ прогон — улика не переносится."""
    store, base, candidates, rollout, first = _opened(tmp_path)
    second = rollout.open(candidates, now=NOW, run_nonce="second-attempt")
    assert second.run_id != first.run_id
    assert second.cohort == first.cohort and second.revision_digest == first.revision_digest
    for objective_id in first.cohort:
        stale = rollout.issue_evidence(first.run_id, objective_id, at=NOW + 1.0)
        rollout.report_raw(second.run_id, objective_id, healthy=True, evidence_ref=stale,
                           at=NOW + 1.0)
    outcome = _deny(rollout, store, base, second)
    assert {why for _, why in outcome.verdict.unattested} == {"wrong_run"}


def test_stale_evidence_is_denied(tmp_path):
    """Улика, выпущенная до начала прогона, — не улика этого прогона."""
    store, base, _, rollout, plan = _opened(tmp_path)
    ledger = CanaryEvidenceLedger(KEY)
    for objective_id in plan.cohort:
        rollout.report_raw(plan.run_id, objective_id, healthy=True, at=NOW + 1.0,
                           evidence_ref=ledger.issue(plan, objective_id, issued_at=NOW - 900.0))
    outcome = _deny(rollout, store, base, plan)
    assert {why for _, why in outcome.verdict.unattested} == {"stale"}


def test_evidence_from_another_process_is_denied(tmp_path):
    """Улика чужого процесса не открывает парк."""
    store, base, _, rollout, plan = _opened(tmp_path)
    for objective_id in plan.cohort:
        rollout.report_raw(plan.run_id, objective_id, healthy=True, at=NOW + 1.0,
                           evidence_ref=_bound_evidence(plan, objective_id, at=NOW + 1.0,
                                                        process_identity="someone-else"))
    outcome = _deny(rollout, store, base, plan)
    assert {why for _, why in outcome.verdict.unattested} == {"wrong_process"}


def test_another_process_cannot_decide_this_run(tmp_path):
    """И сам ВЫЗЫВАЮЩИЙ с чужой личностью не дорешивает чужой прогон."""
    store, base, _, rollout, plan = _opened(tmp_path)
    for objective_id in plan.cohort:
        rollout.report_healthy(plan.run_id, objective_id, at=NOW + 1.0)
    intruder = _rollout(store, process="attacker@elsewhere")
    outcome = intruder.activate_broadly(plan.run_id, now=NOW + 5.0)
    assert outcome.allowed is False and outcome.reason == REASON_WRONG_PROCESS
    assert outcome.activated == () and _rest_untouched(store, base, plan)


def test_a_silent_member_is_denied(tmp_path):
    """Молчание — не успех: отсутствие плохих новостей ничего не открывает."""
    store, base, _, rollout, plan = _opened(tmp_path)
    for objective_id in plan.cohort[:-1]:
        rollout.report_healthy(plan.run_id, objective_id, at=NOW + 1.0)
    outcome = _deny(rollout, store, base, plan)
    assert outcome.reason == "canary_incomplete"
    assert outcome.verdict.state == PENDING
    assert outcome.verdict.silent == (plan.cohort[-1],)


def test_an_unhealthy_member_is_denied(tmp_path):
    """Одного падения достаточно, и оно липкое: поздний «здоров» не отменяет."""
    store, base, _, rollout, plan = _opened(tmp_path)
    broken = plan.cohort[0]
    rollout.report_unhealthy(plan.run_id, broken, at=NOW + 1.0, detail="crash loop")
    for objective_id in plan.cohort[1:]:
        rollout.report_healthy(plan.run_id, objective_id, at=NOW + 1.0)
    rollout.report_healthy(plan.run_id, broken, at=NOW + 2.0)
    outcome = _deny(rollout, store, base, plan)
    assert outcome.reason == "canary_failed"
    assert outcome.verdict.state == FAILED and outcome.verdict.unhealthy == (broken,)


# --------------------------------------------------------- обход двери

def _healthy_run(tmp_path):
    store, base, candidates, rollout, plan = _opened(tmp_path)
    for objective_id in plan.cohort:
        rollout.report_healthy(plan.run_id, objective_id, at=NOW + 1.0)
    return store, base, candidates, rollout, plan


@pytest.mark.parametrize("forge", ["none", "fabricated", "foreign_key", "other_run", "expired"])
def test_reaching_the_rest_without_the_door_is_refused(tmp_path, forge):
    """Обход двери не даёт активации: он даёт исключение."""
    store, base, candidates, rollout, plan = _healthy_run(tmp_path)
    other = rollout.open(candidates, now=NOW, run_nonce="other")
    if forge == "none":
        grant, expected = None, REASON_NO_GRANT
    elif forge == "fabricated":
        grant = BroadActivationGrant(run_id=plan.run_id, revision_digest=plan.revision_digest,
                                     cohort_digest=plan.cohort_digest,
                                     process_identity=PROCESS, granted_at=NOW + 5.0,
                                     reason="canary_passed", token="0" * 64)
        expected = REASON_BAD_GRANT
    elif forge == "foreign_key":
        # Разрешение, подписанное другим ключом: подпись — это и есть дверь.
        # Разрешение, подписанное другим ключом: подпись — это и есть дверь.
        grant = _rollout(store, key=OTHER_KEY)._mint(rollout.plan_of(plan.run_id),
                                                     "canary_passed", NOW + 5.0)
        expected = REASON_BAD_GRANT
    elif forge == "other_run":
        allowed, _, _, grant = rollout.authorize(plan.run_id, now=NOW + 5.0)
        assert allowed
        grant, expected = grant, REASON_BAD_GRANT  # предъявим его ДРУГОМУ прогону
    else:
        allowed, _, _, grant = rollout.authorize(plan.run_id, now=NOW + 5.0)
        assert allowed
        expected = REASON_STALE_GRANT
    target = other.run_id if forge == "other_run" else plan.run_id
    when = NOW + 5.0 + GRANT_TTL_SECONDS + 1.0 if forge == "expired" else NOW + 5.0
    with pytest.raises(ActivationError, match=expected):
        rollout.apply_broad_revision(target, grant, now=when)
    assert _rest_untouched(store, base, plan)
    assert store.canary_run(plan.run_id)["state"] == OPEN


def test_a_decided_run_cannot_be_activated_twice(tmp_path):
    """Раскатать парк дважды по одному вердикту нельзя."""
    store, _, _, rollout, plan = _healthy_run(tmp_path)
    first = rollout.activate_broadly(plan.run_id, now=NOW + 5.0)
    assert first.allowed and first.activated == plan.rest
    again = rollout.activate_broadly(plan.run_id, now=NOW + 6.0)
    assert again.allowed is False and again.reason == REASON_NOT_OPEN


# ---------------------------------------------- перезапуск без вердикта

def test_a_restart_without_durable_evidence_denies(tmp_path):
    """Улики, оставшиеся в памяти процесса, не переживают перезапуск —
    и не открывают парк после него."""
    store, base, candidates, rollout, plan = _opened(tmp_path)
    # Процесс «проверил здоровье» и выпустил улики, но НЕ записал их.
    held = {o: rollout.issue_evidence(plan.run_id, o, at=NOW + 1.0) for o in plan.cohort}
    assert len(held) == len(plan.cohort)
    del rollout, store

    restarted = ObjectiveStore(tmp_path / "objectives.db")
    after = _rollout(restarted)
    outcome = after.activate_broadly(plan.run_id, now=NOW + 5.0)
    assert outcome.allowed is False and outcome.reason == "canary_incomplete"
    assert outcome.activated == () and _rest_untouched(restarted, base, plan)


def test_a_restart_that_lost_the_run_denies(tmp_path):
    """Прогона нет в долговечном состоянии — значит активации нет."""
    _, base, _, _, plan = _opened(tmp_path)
    elsewhere, _ = _fleet(tmp_path / "fresh", "objectives.db")
    outcome = _rollout(elsewhere).activate_broadly(plan.run_id, now=NOW + 5.0)
    assert outcome.allowed is False and outcome.reason == "canary_run_unknown"
    assert all(elsewhere.get(o).spec_digest == base[o].digest for o in FLEET)


def test_a_restart_without_the_evidence_key_denies(tmp_path):
    """Новый процесс без ключа улик не «доверяет им на слово»."""
    store, base, _, rollout, plan = _healthy_run(tmp_path)
    del rollout, store
    restarted = ObjectiveStore(tmp_path / "objectives.db")
    outcome = _rollout(restarted, key=OTHER_KEY).activate_broadly(plan.run_id, now=NOW + 5.0)
    assert outcome.allowed is False and outcome.reason == "canary_evidence_unattested"
    assert _rest_untouched(restarted, base, plan)


def test_a_tampered_run_row_denies(tmp_path):
    """Строку прогона правили в базе — это не план, а отказ."""
    store, base, _, rollout, plan = _healthy_run(tmp_path)
    import sqlite3
    con = sqlite3.connect(store.path)
    con.execute("UPDATE v5_canary_runs SET cohort_digest=? WHERE run_id=?",
                ("d" * 64, plan.run_id))
    con.commit()
    con.close()
    outcome = _rollout(ObjectiveStore(tmp_path / "objectives.db")).activate_broadly(
        plan.run_id, now=NOW + 5.0)
    assert outcome.allowed is False and outcome.reason == "canary_run_tampered"
    assert _rest_untouched(store, base, plan)


# ------------------------------------------------------- ПОЛОЖИТЕЛЬНЫЙ

def test_the_real_sequence_canary_restart_activation_failure_rollback_restart(tmp_path):
    """Канарейка -> долговечная улика -> ПЕРЕЗАПУСК -> активация -> внесённое
    падение -> отказ -> НАСТОЯЩИЙ откат -> ПЕРЕЗАПУСК -> откат на месте.

    «Перезапуск» здесь настоящий: новая ручка хранилища и новый объект границы
    читают долговечное состояние, ничего не унаследовав из памяти.
    """
    db = tmp_path / "objectives.db"
    store, base = _fleet(tmp_path)
    candidates = {o: _candidate(s) for o, s in base.items()}
    first = _rollout(store)
    plan = first.open(candidates, now=NOW)
    for objective_id in plan.cohort:
        first.report_healthy(plan.run_id, objective_id, at=NOW + 1.0)
    del first, store

    # --- ПЕРЕЗАПУСК: другой процесс того же сервиса, только долговечное состояние.
    store_b = ObjectiveStore(db)
    second = _rollout(store_b)
    allowed, reason, verdict, grant = second.authorize(plan.run_id, now=NOW + 10.0)
    assert allowed and reason == "canary_passed"
    assert verdict.state == PASSED and verdict.attested and verdict.unattested == ()
    outcome = second.activate_broadly(plan.run_id, now=NOW + 10.0)
    assert outcome.allowed and outcome.activated == plan.rest
    assert _digests(store_b) == {o: c.digest for o, c in candidates.items()}
    assert store_b.canary_run(plan.run_id)["state"] == ACTIVATED

    # --- Внесённое падение: следующая ревизия ломает члена когорты.
    next_candidates = {o: _candidate(c, priority=3) for o, c in candidates.items()}
    second_plan = second.open(next_candidates, now=NOW + 20.0)
    broken = second_plan.cohort[0]
    second.report_unhealthy(second_plan.run_id, broken, at=NOW + 21.0, detail="regression")
    for objective_id in second_plan.cohort[1:]:
        second.report_healthy(second_plan.run_id, objective_id, at=NOW + 21.0)
    denied = second.activate_broadly(second_plan.run_id, now=NOW + 22.0)
    assert denied.allowed is False and denied.reason == "canary_failed"
    assert denied.activated == ()
    # Остальной парк остался на ПРЕДЫДУЩЕЙ (раскатанной) ревизии.
    assert all(store_b.get(o).spec_digest == candidates[o].digest for o in second_plan.rest)

    # --- НАСТОЯЩИЙ откат: когорта возвращается на раскатанную ревизию.
    restored = second.rollback(second_plan.run_id, now=NOW + 23.0)
    assert set(restored) == set(second_plan.cohort)
    assert _bodies(store_b) == {o: _body(c) for o, c in candidates.items()}
    assert store_b.canary_run(second_plan.run_id)["state"] == ABANDONED
    del second, store_b

    # --- ПЕРЕЗАПУСК: откат пережил перезапуск, и прогон больше не активируется.
    store_c = ObjectiveStore(db)
    third = _rollout(store_c)
    assert _bodies(store_c) == {o: _body(c) for o, c in candidates.items()}
    for objective_id in second_plan.cohort:
        body = store_c.get_spec(objective_id).to_dict()
        # 1 -> 2 (канарейка) -> 3 (раскат) -> 4 (откат вперёд с прежним телом).
        assert body["priority"] == 9 and body["revision"] == 4
    assert store_c.canary_run(second_plan.run_id)["state"] == ABANDONED
    after = third.activate_broadly(second_plan.run_id, now=NOW + 30.0)
    assert after.allowed is False and after.reason == REASON_NOT_OPEN
    assert _bodies(store_c) == {o: _body(c) for o, c in candidates.items()}
    # И журнал помнит и раскат, и откат — это факт, а не память процесса.
    events = [row["event"] for row in store_c.journal(broken)]
    assert events.count("revised") == 3 and "canary_report" in events


def test_the_revision_digest_binds_every_member_of_the_fleet(tmp_path):
    """Цифра раската — по кандидату КАЖДОЙ цели: подмена одного меняет её."""
    store, base = _fleet(tmp_path)
    candidates = {o: _candidate(s) for o, s in base.items()}
    swapped = dict(candidates)
    swapped[FLEET[0]] = _candidate(base[FLEET[0]], priority=7)
    assert revision_digest(candidates) != revision_digest(swapped)
    with pytest.raises(ActivationError):
        revision_digest({FLEET[0]: json.dumps({"not": "a spec"})})
