"""V5 IV5-CAN-002: улика члена канарейки обязана РАЗРЕШАТЬСЯ и быть привязанной.

До правки `CanaryOutcome.evidence_ref` был произвольной строкой: её никто не
резолвил, не проверял и ни к чему не привязывал. Поэтому широкую активацию
открывала ЛЮБАЯ строка — выдуманная, чужая, от прошлого прогона, — а пустая
строка по умолчанию была ровно такой же строкой. Дефект стоит дорого именно
потому, что канарейка — последний барьер перед выпуском ревизии на весь парк:
пройдя его подделкой, сломанная ревизия получает все цели сразу.

Проверка «строка непустая» этого не чинит, и здесь её нет: `"x"` непустая, а
`"cev1.<чужой payload>.<чужой tag>"` непустая и правдоподобная. Улика проверяется
ключом и привязками: цель, цифра ревизии, состав когорты, ЭТОТ прогон, процесс,
окно времени. Ссылка, которую резолвер не разрешил, ОТКАЗЫВАЕТСЯ — она не
«отсутствует, и потому не мешает».

Враждебные случаи владельца A-F, по одному тесту на каждый, плюс главный:
одна негодная улика среди годных ЗАКРЫВАЕТ выпуск.
"""
from __future__ import annotations

import pytest

from bossman_shared.objective_canary import (FAILED, PASSED, STALE, UNRESOLVED, WRONG_COHORT,
                                             WRONG_OBJECTIVE, WRONG_PROCESS, WRONG_REVISION,
                                             WRONG_RUN, CanaryError, CanaryEvidenceLedger,
                                             CanaryOutcome, CanaryPolicy, authorize_broad_activation,
                                             evaluate_canary, may_activate_broadly, plan_canary)

POPULATION = tuple(f"obj-{i:02d}" for i in range(20))
DIGEST = "a" * 64
NOW = 1_700_000_000.0
KEY = b"canary-evidence-key-32-bytes-!!!"
PROCESS = "bossman.releaser@host-7/pid-4242"


def _ledger() -> CanaryEvidenceLedger:
    return CanaryEvidenceLedger(KEY)


def _plan(*, revision_digest: str = DIGEST, now: float = NOW,
          process_identity: str = PROCESS, run_nonce: str = "run-a"):
    return plan_canary(POPULATION, revision_digest=revision_digest, now=now,
                       process_identity=process_identity, run_nonce=run_nonce)


def _honest_outcomes(plan, ledger, *, issued_at: float = NOW + 1.0):
    """Настоящие, выпущенные ключом улики для КАЖДОГО члена когорты."""
    return [CanaryOutcome(o, True, ledger.issue(plan, o, issued_at=issued_at))
            for o in plan.cohort]


def _authorize(plan, outcomes, ledger, *, now: float = NOW + 2.0):
    return authorize_broad_activation(plan, outcomes, resolve=ledger.resolve, now=now)


# ------------------------------------------------------- F: положительный контроль

def test_case_f_real_attested_evidence_opens_broad_activation():
    """F. Контроль, без которого остальные тесты ничего не доказывают: правка,
    запрещающая ВСЁ, прошла бы A-E и была бы бесполезна."""
    plan, ledger = _plan(), _ledger()
    allowed, reason, verdict = _authorize(plan, _honest_outcomes(plan, ledger), ledger)
    assert (allowed, reason) == (True, "canary_passed")
    assert verdict.state == PASSED and verdict.unattested == () and verdict.attested


# ------------------------------------------------------- A-E: враждебные случаи

def test_case_a_fabricated_evidence_ref_is_refused():
    """A. Выдуманная ссылка. Именно она открывала выпуск до правки."""
    plan, ledger = _plan(), _ledger()
    outcomes = [CanaryOutcome(o, True, f"totally-made-up-{o}") for o in plan.cohort]
    allowed, reason, verdict = _authorize(plan, outcomes, ledger)
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    assert verdict.state == FAILED
    assert {why for _, why in verdict.unattested} == {UNRESOLVED}


@pytest.mark.parametrize("forged", [
    "",                                    # пустая строка по умолчанию — тоже строка
    "   ",                                 # и «непустая» строка ничего не доказывает
    "cev1",                                # правдоподобный префикс без тела
    "cev1.payload.deadbeef",               # правильная форма, неверная подпись
    "ev-verified-1",                       # похоже на улику из objective_store
])
def test_case_a_plausible_but_unsigned_refs_are_refused(forged):
    """A (шире). Форма ссылки ничего не значит: значение имеет только ключ.
    Тест назван так намеренно — он бы ПРОШЁЛ при проверке «строка непустая»
    для трёх последних значений, поэтому такая проверка и не годится."""
    plan, ledger = _plan(), _ledger()
    outcomes = _honest_outcomes(plan, ledger)
    outcomes[0] = CanaryOutcome(plan.cohort[0], True, forged)
    allowed, reason, _ = _authorize(plan, outcomes, ledger)
    assert (allowed, reason) == (False, "canary_evidence_unattested")


def test_case_a_evidence_signed_with_another_key_is_refused():
    """A (сильнее). Улика правильной формы и с полной привязкой, но выпущенная
    ЧУЖИМ ключом: подделать ссылку без ключа нельзя."""
    plan = _plan()
    stranger = CanaryEvidenceLedger(b"a-different-key-32-bytes-long!!!")
    outcomes = [CanaryOutcome(o, True, stranger.issue(plan, o, issued_at=NOW + 1.0))
                for o in plan.cohort]
    allowed, reason, verdict = _authorize(plan, outcomes, _ledger())
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    assert {why for _, why in verdict.unattested} == {UNRESOLVED}


def test_case_b_valid_evidence_belonging_to_another_objective_is_refused():
    """B. Улика настоящая, выпущена нашим ключом, для ЭТОГО прогона — но про
    другую цель. Без привязки к objective_id одна здоровая цель отчитывалась бы
    за всю когорту."""
    plan, ledger = _plan(), _ledger()
    victim, donor = plan.cohort[0], plan.cohort[1]
    outcomes = _honest_outcomes(plan, ledger)
    outcomes[0] = CanaryOutcome(victim, True, ledger.issue(plan, donor, issued_at=NOW + 1.0))
    allowed, reason, verdict = _authorize(plan, outcomes, ledger)
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    assert verdict.unattested == ((victim, WRONG_OBJECTIVE),)


def test_case_c_valid_evidence_for_another_revision_is_refused():
    """C. Улика прошлой, здоровой ревизии. Без привязки к цифре ревизии зелёная
    история открывала бы выпуск сломанному коду."""
    ledger = _ledger()
    plan = _plan()
    # Когорта у другой ревизии другая; нужен ОБЩИЙ член, иначе улику некуда
    # предъявить и случай C непроверяем. Ищем такую ревизию, а не угадываем.
    other, victim = None, None
    for char in "bcdef0123456789":
        candidate = _plan(revision_digest=char * 64)
        shared = sorted(set(plan.cohort) & set(candidate.cohort))
        if shared:
            other, victim = candidate, shared[0]
            break
    assert other is not None, "не нашлось ревизии с общим членом когорты"
    assert other.revision_digest != plan.revision_digest
    outcomes = _honest_outcomes(plan, ledger)
    outcomes = [o for o in outcomes if o.objective_id != victim]
    outcomes.append(CanaryOutcome(victim, True, ledger.issue(other, victim, issued_at=NOW + 1.0)))
    allowed, reason, verdict = _authorize(plan, outcomes, ledger)
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    assert verdict.unattested == ((victim, WRONG_REVISION),)


def test_case_d_stale_evidence_from_a_previous_canary_run_is_refused():
    """D. ТА ЖЕ ревизия, ТОТ ЖЕ парк, ТА ЖЕ когорта — но прошлый прогон.
    Привязка к ревизии этого не ловит: ловит только привязка к прогону."""
    ledger = _ledger()
    previous = _plan(now=NOW - 86_400.0, run_nonce="run-a")
    current = _plan(now=NOW, run_nonce="run-a")
    assert previous.cohort == current.cohort and previous.run_id != current.run_id
    outcomes = [CanaryOutcome(o, True, ledger.issue(previous, o, issued_at=NOW - 86_000.0))
                for o in current.cohort]
    allowed, reason, verdict = _authorize(current, outcomes, ledger)
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    assert {why for _, why in verdict.unattested} == {WRONG_RUN}


def test_case_d_a_second_run_in_the_same_instant_still_gets_its_own_identity():
    """D (край). Два прогона в одно и то же `now` не должны делить улики —
    иначе «прошлый прогон» отличается от текущего только часами."""
    ledger = _ledger()
    first = _plan(now=NOW, run_nonce="run-a")
    second = _plan(now=NOW, run_nonce="run-b")
    assert first.run_id != second.run_id
    outcomes = [CanaryOutcome(o, True, ledger.issue(first, o, issued_at=NOW + 1.0))
                for o in second.cohort]
    assert _authorize(second, outcomes, ledger)[:2] == (False, "canary_evidence_unattested")


def test_case_d_evidence_issued_before_the_run_started_is_stale():
    """D (по времени). Отдельная привязка: улика, выпущенная ДО начала прогона,
    не может быть о нём. Проверяется независимо от run_id."""
    plan, ledger = _plan(), _ledger()
    outcomes = _honest_outcomes(plan, ledger, issued_at=NOW - 1.0)
    allowed, reason, verdict = _authorize(plan, outcomes, ledger)
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    assert {why for _, why in verdict.unattested} == {STALE}


def test_case_d_evidence_issued_in_the_future_is_stale():
    """D (по времени, вторая сторона). Улика «из будущего» — тоже не улика:
    окно закрыто с обеих сторон, иначе часы предъявителя решают за нас."""
    plan, ledger = _plan(), _ledger()
    outcomes = _honest_outcomes(plan, ledger, issued_at=NOW + 10_000.0)
    allowed, reason, verdict = _authorize(plan, outcomes, ledger, now=NOW + 2.0)
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    assert {why for _, why in verdict.unattested} == {STALE}


def test_case_e_evidence_from_a_member_outside_the_cohort_is_refused():
    """E. Улика выпущена под ДРУГИМ составом когорты. Тот же парк и та же
    ревизия, но когорта иная — значит и наблюдение иное."""
    ledger = _ledger()
    plan = _plan()
    narrow = plan_canary(POPULATION, revision_digest=DIGEST, now=NOW,
                         policy=CanaryPolicy(fraction=0.9, max_cohort=12),
                         process_identity=PROCESS, run_nonce="run-a")
    assert narrow.cohort_digest != plan.cohort_digest
    victim = sorted(set(plan.cohort) & set(narrow.cohort))[0]
    outcomes = [o for o in _honest_outcomes(plan, ledger) if o.objective_id != victim]
    outcomes.append(CanaryOutcome(victim, True, ledger.issue(narrow, victim, issued_at=NOW + 1.0)))
    allowed, reason, verdict = _authorize(plan, outcomes, ledger)
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    assert verdict.unattested == ((victim, WRONG_COHORT),)


def test_case_e_a_report_from_outside_the_cohort_never_reaches_evidence():
    """E (раньше по пути). Отчёт нечлена когорты отвергается до всякой улики —
    и его нельзя ни выпустить, ни предъявить."""
    plan, ledger = _plan(), _ledger()
    outsider = next(o for o in POPULATION if o not in plan.cohort)
    with pytest.raises(CanaryError, match="not in the cohort"):
        ledger.issue(plan, outsider, issued_at=NOW + 1.0)
    with pytest.raises(CanaryError, match="not in the cohort"):
        _authorize(plan, _honest_outcomes(plan, ledger) + [CanaryOutcome(outsider, True)], ledger)


def test_evidence_from_another_process_is_refused():
    """Привязка к процессу/прогону: улика, выпущенная другим релизёром, не
    авторизует выпуск, который ведёт этот."""
    ledger = _ledger()
    plan = _plan(process_identity="bossman.releaser@host-7/pid-4242")
    stranger_run = _plan(process_identity="bossman.releaser@host-9/pid-1")
    assert plan.cohort == stranger_run.cohort
    outcomes = [CanaryOutcome(o, True, ledger.issue(stranger_run, o, issued_at=NOW + 1.0))
                for o in plan.cohort]
    allowed, reason, verdict = _authorize(plan, outcomes, ledger)
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    # run_id включает процесс, поэтому первым срабатывает он; проверим и саму
    # привязку к процессу в изоляции — иначе WRONG_PROCESS был бы мёртвой веткой.
    assert {why for _, why in verdict.unattested} <= {WRONG_RUN, WRONG_PROCESS}


def test_the_process_binding_is_checked_on_its_own():
    """Изолированная проверка WRONG_PROCESS: прогоны совпадают во всём, кроме
    процесса. Без этого теста ветка привязки к процессу недостижима и мертва."""
    from bossman_shared.objective_canary import CanaryAttestation, _binding_failure
    plan = _plan(process_identity="releaser-A")
    attestation = CanaryAttestation("ref", plan.cohort[0], plan.revision_digest,
                                    plan.cohort_digest, plan.run_id, NOW + 1.0, "releaser-B")
    assert _binding_failure(attestation, plan, plan.cohort[0], NOW + 2.0) == WRONG_PROCESS


# --------------------------------------------- одна негодная улика среди годных

def test_one_invalid_member_among_valid_ones_denies_broad_activation():
    """Главное обещание владельца. Большинство здесь ничего не решает: девять
    настоящих улик не перевешивают одну поддельную."""
    plan, ledger = _plan(), _ledger()
    outcomes = _honest_outcomes(plan, ledger)
    victim = plan.cohort[-1]
    outcomes[-1] = CanaryOutcome(victim, True, "fabricated")
    allowed, reason, verdict = _authorize(plan, outcomes, ledger)
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    assert verdict.unattested == ((victim, UNRESOLVED),)
    # И это не «почти прошло»: вердикт терминальный, ждать нечего.
    assert verdict.state == FAILED and verdict.silent == ()


@pytest.mark.parametrize("index", range(4))
def test_any_single_member_can_deny_the_whole_cohort(index):
    """Свойство, а не один случай: негодная улика ЛЮБОГО члена закрывает выпуск.
    Тест на длинной оси ловит порядковую зависимость, которой один пример не видит."""
    plan, ledger = _plan(), _ledger()
    outcomes = _honest_outcomes(plan, ledger)
    victim = plan.cohort[index]
    outcomes[index] = CanaryOutcome(victim, True, "fabricated")
    assert _authorize(plan, outcomes, ledger)[:2] == (False, "canary_evidence_unattested")


# ------------------------------------------------------- fail-closed по краям

def test_a_verdict_reached_without_a_resolver_cannot_open_broad_activation():
    """«Улики не проверяли» — не «улики в порядке». Вердикт без резолвера
    остаётся вердиктом, но парк не открывает: иначе достаточно вызвать
    evaluate_canary без резолвера, чтобы обойти всю привязку."""
    plan, ledger = _plan(), _ledger()
    verdict = evaluate_canary(plan, _honest_outcomes(plan, ledger))
    assert verdict.state == PASSED and not verdict.attested
    assert may_activate_broadly(verdict) == (False, "canary_unattested")


def test_the_activation_door_cannot_be_opened_without_a_resolver():
    with pytest.raises(CanaryError, match="requires an evidence resolver"):
        authorize_broad_activation(_plan(), [], resolve=None, now=NOW)


def test_evidence_cannot_be_judged_without_a_clock():
    """Свежесть без часов не судится; молча принять улику любого возраста —
    то же самое, что не проверять её."""
    plan, ledger = _plan(), _ledger()
    with pytest.raises(CanaryError, match="now is required"):
        evaluate_canary(plan, _honest_outcomes(plan, ledger), resolve=ledger.resolve)


def test_a_resolver_that_raises_is_a_refusal_not_a_pass():
    """Резолвер упал — улики нет. Исключение не имеет права стать «улика ок»."""
    plan, ledger = _plan(), _ledger()

    def exploding(_ref):
        raise RuntimeError("ledger unreachable")

    allowed, reason, verdict = authorize_broad_activation(
        plan, _honest_outcomes(plan, ledger), resolve=exploding, now=NOW + 2.0)
    assert (allowed, reason) == (False, "canary_evidence_unattested")
    assert {why for _, why in verdict.unattested} == {UNRESOLVED}


def test_a_resolver_returning_something_that_is_not_an_attestation_is_refused():
    """Резолвер вернул строку/словарь/True вместо улики — это отказ, а не улика."""
    plan, ledger = _plan(), _ledger()
    for junk in ("ok", {"objective_id": plan.cohort[0]}, True, object()):
        allowed, reason, _ = authorize_broad_activation(
            plan, _honest_outcomes(plan, ledger), resolve=lambda _r, j=junk: j, now=NOW + 2.0)
        assert (allowed, reason) == (False, "canary_evidence_unattested"), junk


def test_a_failure_report_needs_no_evidence_and_still_closes_the_release():
    """Улика нужна, чтобы ОТКРЫТЬ выпуск, а не чтобы его закрыть. Иначе
    достаточно испортить свою улику, чтобы падение перестало быть падением."""
    plan, ledger = _plan(), _ledger()
    outcomes = _honest_outcomes(plan, ledger)
    outcomes[0] = CanaryOutcome(plan.cohort[0], False, "no-evidence-at-all")
    allowed, reason, verdict = _authorize(plan, outcomes, ledger)
    assert verdict.state == FAILED and verdict.reason == "canary_member_unhealthy"
    assert (allowed, reason) == (False, "canary_failed")


def test_silence_still_outranks_attested_health():
    """Привязка улик не отменяет прежнего правила: молчание — не успех."""
    plan, ledger = _plan(), _ledger()
    outcomes = _honest_outcomes(plan, ledger)[:-1]
    allowed, reason, verdict = _authorize(plan, outcomes, ledger)
    assert verdict.state == "PENDING" and (allowed, reason) == (False, "canary_incomplete")


def test_an_evidence_key_too_weak_to_bind_is_refused():
    with pytest.raises(CanaryError, match="evidence key"):
        CanaryEvidenceLedger(b"short")
    with pytest.raises(CanaryError, match="evidence key"):
        CanaryEvidenceLedger("a-string-not-bytes-but-long-enough")


def test_a_tampered_reference_does_not_survive_its_own_signature():
    """Отрицательный контроль к самой ссылке: сдвинь один символ полезной
    нагрузки — и подпись перестаёт совпадать."""
    plan, ledger = _plan(), _ledger()
    ref = ledger.issue(plan, plan.cohort[0], issued_at=NOW + 1.0)
    assert ledger.resolve(ref) is not None
    scheme, payload, tag = ref.split(".")
    flipped = "A" if payload[5] != "A" else "B"
    assert ledger.resolve(f"{scheme}.{payload[:5]}{flipped}{payload[6:]}.{tag}") is None
    assert ledger.resolve(f"{scheme}.{payload}.{tag[:-1]}{'0' if tag[-1] != '0' else '1'}") is None
