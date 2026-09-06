"""V5 N4: очерёдность, ограниченное старение, квоты, отсутствие голодания.

Два независимых утверждения проверяются здесь.

ПЕРВОЕ, и оно про настоящий дефект: допуск, захвативший ключ конфликта, никогда
его не отпускал. Единственный вызов `conflicts.release` стоял на пути ОТКАЗА, а
успешный путь возвращался, держа ключ, — правильно на время миссии и навсегда
после неё. Никакая очерёдность этого не лечит: `admit` отказывает
состарившемуся проигравшему независимо от ранга.

ВТОРОЕ: сам порядок. Гарантия отсутствия голодания даётся квотой в окне и
крайним сроком ожидания, а не старением — старение ограничено намеренно.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from bossman_shared.objective_fairness import (COOLDOWN_ACTIVE, NOT_READY, QUOTA_EXHAUSTED,
                                               Candidate, FairnessError, FairnessPolicy,
                                               candidate_from_store, rank, select)
from bossman_shared.objective_admission import (RELEASE_DONE, RELEASE_UNKNOWN_KEYS,
                                                RELEASE_UNRESOLVED)
from bossman_shared.objective_store import ObjectiveStoreError
from test_v5_admission import (CAPABILITY, EFFECT, NOW, OWNER, AdmissionKernel, CostEstimate,
                               FakeConflicts, FakePolicy, FakeTreasury, ObjectiveSpec,
                               ObjectiveStore, build_proposal, observation, spec_dict)


# ------------------------------------------------------ конец допуска

def _admittable(tmp_path, objective_id, now):
    store = ObjectiveStore(tmp_path / f"{objective_id}.sqlite3")
    spec = ObjectiveSpec.from_dict({**spec_dict(), "objective_id": objective_id})
    state = store.create(spec)
    state = store.enroll_sources(objective_id, ("src:build",), owner_id=OWNER,
                                 expected_version=state.version)
    store.transition(objective_id, "ACTIVE", now=now, owner_id=OWNER,
                     expected_version=state.version)
    proposal = build_proposal(
        store, objective_id, [observation(spec.digest, observed_at=now - 10.0,
                                          observation_id=f"obs-{objective_id}")],
        now=now, trigger="scheduled", requested_capabilities=(CAPABILITY,),
        expected_effects=(EFFECT,), cost_estimate=CostEstimate(0.5, 1000, 30.0))
    return store, proposal


def _kernel(conflicts):
    return AdmissionKernel(FakePolicy({"perm:repo.write"}, {CAPABILITY}), FakeTreasury(), conflicts)


def test_settling_an_admission_returns_the_conflict_key(tmp_path):
    """Хостильный: obj-a отработала и завершилась. Ключ обязан вернуться, иначе
    obj-b не пройдёт НИКОГДА — что и было измерено до исправления."""
    conflicts = FakeConflicts()
    kernel = _kernel(conflicts)

    store_a, proposal_a = _admittable(tmp_path, "obj-a", NOW)
    decision = kernel.admit(store_a, proposal_a, now=NOW)
    assert decision.admitted
    assert conflicts.held == {"repo:main": "obj-a"}      # миссия идёт — держим

    kernel.settle(store_a, decision.reservation_id, "COMMITTED")
    assert conflicts.held == {}                          # миссия кончилась — отпустили
    assert conflicts.releases == [(("repo:main",), "obj-a")]

    store_b, proposal_b = _admittable(tmp_path, "obj-b", NOW + 5000.0)
    second = kernel.admit(store_b, proposal_b, now=NOW + 5000.0)
    assert second.admitted, second.reason


def test_a_released_admission_also_returns_the_key(tmp_path):
    """Не только успех: отменённая миссия тоже обязана вернуть область."""
    conflicts = FakeConflicts()
    kernel = _kernel(conflicts)
    store, proposal = _admittable(tmp_path, "obj-a", NOW)
    decision = kernel.admit(store, proposal, now=NOW)
    kernel.settle(store, decision.reservation_id, "RELEASED")
    assert conflicts.held == {}


def test_the_keys_released_are_the_ones_that_were_taken(tmp_path):
    """Ревизия могла поменять conflict_keys уже ПОСЛЕ допуска. Отпускать надо
    захваченное, иначе старый ключ остаётся висеть, а чужой снимается."""
    conflicts = FakeConflicts()
    kernel = _kernel(conflicts)
    store, proposal = _admittable(tmp_path, "obj-a", NOW)
    decision = kernel.admit(store, proposal, now=NOW)
    reservation = store.reservation(decision.reservation_id)
    assert reservation["payload"]["conflict_keys"] == ["repo:main"]
    conflicts.held["repo:other"] = "obj-somebody-else"
    kernel.settle(store, decision.reservation_id, "COMMITTED")
    assert conflicts.held == {"repo:other": "obj-somebody-else"}


def test_admission_history_is_readable_and_counts_only_real_admissions(tmp_path):
    """Расписание обязано считать по ОБСЛУЖИВАНИЮ. В v5_reservations лежат и
    отказы (строка заводится до опроса портов), поэтому фильтр по phase='READY'
    — не деталь, а условие честности."""
    conflicts = FakeConflicts()
    kernel = _kernel(conflicts)
    store, proposal = _admittable(tmp_path, "obj-a", NOW)
    assert store.last_admission_at("obj-a") is None
    assert store.admissions_since("obj-a", NOW - 10_000.0) == 0

    decision = kernel.admit(store, proposal, now=NOW)
    kernel.settle(store, decision.reservation_id, "COMMITTED")
    assert store.last_admission_at("obj-a") == NOW
    assert store.admissions_since("obj-a", NOW - 10_000.0) == 1
    assert store.admissions_since("obj-a", NOW + 1.0) == 0        # окно исключает

    # Отказ (ключ занят кем-то другим) записывает строку, но не допуск.
    conflicts.held["repo:main"] = "obj-someone"
    store_b, proposal_b = _admittable(tmp_path, "obj-b", NOW + 1.0)
    refused = kernel.admit(store_b, proposal_b, now=NOW + 1.0)
    assert not refused.admitted and refused.reason == "conflict_held"
    assert store_b.last_admission_at("obj-b") is None
    assert store_b.admissions_since("obj-b", NOW - 10_000.0) == 0


def test_candidate_is_built_from_the_durable_history(tmp_path):
    conflicts = FakeConflicts()
    kernel = _kernel(conflicts)
    store, proposal = _admittable(tmp_path, "obj-a", NOW)
    decision = kernel.admit(store, proposal, now=NOW)
    kernel.settle(store, decision.reservation_id, "COMMITTED")
    candidate = candidate_from_store(store, "obj-a", priority=5, eligible_since=NOW - 100.0,
                                     now=NOW + 60.0)
    assert candidate.last_admitted_at == NOW and candidate.admissions_in_window == 1


# ------------------------------------------------------ порядок

POLICY = FairnessPolicy(window_seconds=3600.0, quota_per_window=3, starvation_deadline_s=900.0,
                        aging_per_second=1.0 / 60.0, max_aging_bonus=5)


def test_a_continuous_high_priority_stream_cannot_starve_lower_priority_work():
    """ХОСТИЛЬНЫЙ, ради которого всё написано.

    `hot` имеет приоритет 100 и повод действовать в КАЖДОМ круге; `cold` имеет
    приоритет 1 и ждёт. Симулируется час по кругу в минуту. `cold` обязана
    получить очередь, и не «когда-нибудь», а до крайнего срока.
    """
    quota, admissions, first_win = POLICY.quota_per_window, {"hot": 0, "cold": 0}, {}
    cold_ready_since = 0.0
    last_admitted = {"hot": None, "cold": None}
    for tick in range(60):
        now = tick * 60.0
        window_start = now - POLICY.window_seconds
        winner = select([
            Candidate("hot", priority=100, eligible_since=now,          # повод каждый круг
                      last_admitted_at=last_admitted["hot"],
                      admissions_in_window=admissions["hot"]),
            Candidate("cold", priority=1, eligible_since=cold_ready_since,
                      last_admitted_at=last_admitted["cold"],
                      admissions_in_window=admissions["cold"]),
        ], now=now, policy=POLICY)
        if winner is None:
            continue
        admissions[winner.objective_id] += 1
        last_admitted[winner.objective_id] = now
        first_win.setdefault(winner.objective_id, now)
        if winner.objective_id == "cold":
            cold_ready_since = now
        assert admissions[winner.objective_id] <= quota, "квота не удержала поток"

    assert admissions["cold"] > 0, "низкоприоритетная цель не получила очередь ни разу"
    assert first_win["cold"] <= POLICY.starvation_deadline_s + 60.0, first_win
    assert admissions["hot"] <= quota and admissions["cold"] <= quota


def test_quota_is_what_stops_the_stream_not_aging():
    """Честность про механизм: пока квота не выбрана, приоритет 100 побеждает —
    ограниченное старение и не должно перекрывать разрыв в 99 единиц."""
    hot = Candidate("hot", priority=100, eligible_since=0.0)
    cold = Candidate("cold", priority=1, eligible_since=0.0)
    assert select([hot, cold], now=60.0, policy=POLICY).objective_id == "hot"
    spent = Candidate("hot", priority=100, eligible_since=0.0,
                      admissions_in_window=POLICY.quota_per_window)
    assert select([spent, cold], now=60.0, policy=POLICY).objective_id == "cold"
    assert rank([spent, cold], now=60.0, policy=POLICY)[-1].reason == QUOTA_EXHAUSTED


def test_the_starvation_deadline_outranks_priority():
    """Вторая гарантия: прождавшая дольше срока идёт раньше всех неголодающих."""
    waiting = Candidate("cold", priority=1, eligible_since=0.0)
    fresh = Candidate("hot", priority=100, eligible_since=POLICY.starvation_deadline_s)
    order = rank([fresh, waiting], now=POLICY.starvation_deadline_s, policy=POLICY)
    assert order[0].objective_id == "cold" and order[0].starved
    assert not order[1].starved


def test_aging_is_bounded_and_monotone():
    """«Bounded» — это измеримо: надбавка не превышает объявленного потолка
    никогда, и до потолка растёт не убывая."""
    previous = -1.0
    for minutes in range(0, 600):
        row = rank([Candidate("c", priority=0, eligible_since=0.0)],
                   now=minutes * 60.0, policy=POLICY)[0]
        assert 0 <= row.aging_bonus <= POLICY.max_aging_bonus
        assert row.aging_bonus >= previous
        previous = row.aging_bonus
    assert previous == pytest.approx(POLICY.max_aging_bonus)


def test_the_order_settles_instead_of_oscillating():
    """Анти-осцилляция как измеримое свойство, а не как надежда.

    У каждой цели ровно три границы, и каждая проходится ОДИН раз и без
    возврата: она становится подходящей, её старение упирается в потолок, у неё
    наступает крайний срок голодания. Между границами сравнение пары монотонно,
    поэтому переворотов не больше, чем границ, — то есть шесть на пару в худшем
    случае (здесь измерено три). Ключевое не число, а то, что границы кончаются:
    к концу таймлайна порядок ЗАМИРАЕТ и больше не меняется никогда.
    Неограниченное число переворотов — это осцилляция; конечное — расписание.

    Первая версия проверки поймала настоящий дефект: ступенчатая надбавка
    старения переступала у разных целей в разных фазах, и пары менялись местами
    каждые полминуты — двенадцать переворотов на паре.
    """
    pool = [Candidate(f"obj-{i}", priority=i % 4, eligible_since=float(i) * 30.0)
            for i in range(8)]
    ticks = [t * 15.0 for t in range(400)]
    flips, previous, late_flips = {}, {}, 0
    settled_after = ticks[len(ticks) * 2 // 3]
    for now in ticks:
        order = [r.objective_id for r in rank(pool, now=now, policy=POLICY)]
        position = {name: i for i, name in enumerate(order)}
        for a in position:
            for b in position:
                if a >= b:
                    continue
                ahead = position[a] < position[b]
                if (a, b) in previous and previous[(a, b)] != ahead:
                    flips[(a, b)] = flips.get((a, b), 0) + 1
                    late_flips += now > settled_after
                previous[(a, b)] = ahead
    assert not [pair for pair, n in flips.items() if n > 6], flips
    # Настоящее утверждение: к хвосту таймлайна все границы пройдены и порядок
    # больше не меняется ни у одной пары.
    assert late_flips == 0, "порядок продолжал меняться после того, как все факты застыли"


def test_a_candidate_that_is_not_eligible_yet_does_not_compete():
    """Цель, чей срок ещё не наступил, не «ждёт ноль секунд» — её нет в круге."""
    future = Candidate("later", priority=99, eligible_since=500.0)
    rows = {r.objective_id: r for r in rank(
        [future, Candidate("now", priority=1, eligible_since=0.0)], now=100.0, policy=POLICY)}
    assert rows["later"].reason == NOT_READY and not rows["later"].eligible
    assert select([future], now=100.0, policy=POLICY) is None


def test_the_longest_waiter_leads_the_starved_group():
    """Подъём по голоданию не откатывается: вошедший в голодание раньше остаётся
    впереди и тогда, когда голодать начинает более приоритетный сосед."""
    deadline = POLICY.starvation_deadline_s
    early = Candidate("early", priority=0, eligible_since=0.0)
    later = Candidate("later", priority=100, eligible_since=300.0)
    at_first = rank([early, later], now=deadline, policy=POLICY)
    assert at_first[0].objective_id == "early" and at_first[0].starved
    both = rank([early, later], now=deadline + 300.0, policy=POLICY)
    assert [r.objective_id for r in both] == ["early", "later"]
    assert both[0].starved and both[1].starved


def test_ranking_is_deterministic_and_total():
    pool = [Candidate("b", priority=5, eligible_since=0.0),
            Candidate("a", priority=5, eligible_since=0.0),
            Candidate("c", priority=5, eligible_since=0.0)]
    first = [r.objective_id for r in rank(pool, now=120.0, policy=POLICY)]
    assert first == ["a", "b", "c"]                                   # ничья -> по id
    assert first == [r.objective_id for r in rank(list(reversed(pool)), now=120.0, policy=POLICY)]


def test_an_unavailable_candidate_is_reported_not_dropped():
    """Кандидат не исчезает молча: владелец обязан видеть причину."""
    rows = {r.objective_id: r for r in rank([
        Candidate("cooling", priority=9, eligible_since=0.0, cooldown_until=500.0),
        Candidate("busy", priority=9, eligible_since=0.0, ready=False),
        Candidate("spent", priority=9, eligible_since=0.0, admissions_in_window=99),
        Candidate("ok", priority=1, eligible_since=0.0),
    ], now=100.0, policy=POLICY)}
    assert len(rows) == 4
    assert rows["cooling"].reason == COOLDOWN_ACTIVE and not rows["cooling"].eligible
    assert rows["busy"].reason == NOT_READY
    assert rows["spent"].reason == QUOTA_EXHAUSTED
    assert rows["ok"].eligible
    assert select(list(rows.values() and [
        Candidate("cooling", priority=9, eligible_since=0.0, cooldown_until=500.0),
        Candidate("ok", priority=1, eligible_since=0.0)]), now=100.0,
        policy=POLICY).objective_id == "ok"


def test_no_eligible_candidate_selects_nothing():
    assert select([Candidate("x", priority=1, eligible_since=0.0, ready=False)],
                  now=10.0, policy=POLICY) is None
    assert select([], now=10.0, policy=POLICY) is None


@pytest.mark.parametrize("kwargs", [
    {"window_seconds": -1.0}, {"quota_per_window": -1}, {"max_aging_bonus": -1},
    {"aging_per_second": float("nan")}, {"starvation_deadline_s": float("inf")},
    {"max_aging_bonus": float("nan")}, {"quota_per_window": 1.5},
])
def test_an_unusable_policy_is_refused_not_silently_clamped(kwargs):
    with pytest.raises(FairnessError):
        FairnessPolicy(**kwargs)


def test_a_duplicate_candidate_is_refused():
    """Одна цель дважды в одном круге означала бы двойную квоту."""
    with pytest.raises(FairnessError):
        rank([Candidate("x", priority=1, eligible_since=0.0),
              Candidate("x", priority=9, eligible_since=0.0)], now=10.0, policy=POLICY)


def test_a_nonfinite_clock_is_refused():
    with pytest.raises(FairnessError):
        rank([Candidate("x", priority=1, eligible_since=0.0)], now=float("nan"), policy=POLICY)


def test_serving_an_objective_resets_its_wait():
    """Только что обслуженная цель не считается ждущей с начала времён —
    иначе она немедленно объявила бы себя голодающей и забрала очередь снова."""
    served = Candidate("served", priority=1, eligible_since=0.0, last_admitted_at=1000.0)
    row = rank([served], now=1060.0, policy=POLICY)[0]
    assert row.waited_s == pytest.approx(60.0) and not row.starved


# ------------------------------------------- враждебные случаи закрытия допуска

def _admitted(tmp_path, name="obj-a", now=NOW, conflicts=None):
    conflicts = conflicts if conflicts is not None else FakeConflicts()
    kernel = _kernel(conflicts)
    store, proposal = _admittable(tmp_path, name, now)
    decision = kernel.admit(store, proposal, now=now)
    assert decision.admitted, decision.reason
    return store, kernel, conflicts, decision


def test_the_owner_comes_from_the_record_never_from_the_caller(tmp_path):
    """Отпустить чужую аренду по аргументу вызывающего нельзя: у `settle` такого
    аргумента больше нет вовсе, а владелец читается из брони."""
    import inspect
    from bossman_shared.objective_admission import AdmissionKernel as Kernel
    assert "objective_id" not in inspect.signature(Kernel.settle).parameters

    store, kernel, conflicts, decision = _admitted(tmp_path)
    conflicts.held["repo:other"] = "obj-somebody-else"
    result = kernel.settle(store, decision.reservation_id, "COMMITTED")
    assert result.objective_id == "obj-a" and result.released
    assert conflicts.held == {"repo:other": "obj-somebody-else"}


def test_an_unknown_reservation_is_refused_before_anything_is_released(tmp_path):
    store, kernel, conflicts, _ = _admitted(tmp_path)
    with pytest.raises(ObjectiveStoreError, match="unknown reservation"):
        kernel.settle(store, "no-such-reservation", "COMMITTED")
    assert conflicts.releases == []              # ничего не тронуто


def test_a_second_settle_is_refused_and_does_not_release_twice(tmp_path):
    """Две одновременные попытки: закрытие атомарно, вторая получает отказ и не
    вызывает порт повторно."""
    store, kernel, conflicts, decision = _admitted(tmp_path)
    assert kernel.settle(store, decision.reservation_id, "COMMITTED").released
    assert len(conflicts.releases) == 1
    with pytest.raises(ObjectiveStoreError, match="already settled"):
        kernel.settle(store, decision.reservation_id, "RELEASED")
    assert len(conflicts.releases) == 1


def test_an_unresolved_release_is_visible_and_repeatable(tmp_path):
    """Падение порта ровно между закрытием брони и отпусканием ключей.

    Общей транзакции у БД и внешнего реестра нет, и вид её здесь не делается:
    исход записан в бронь, поэтому незавершённый release ВИДЕН и повторяем.
    """
    class Flaky(FakeConflicts):
        def __init__(self):
            super().__init__()
            self.fail = True

        def release(self, conflict_keys, objective_id):
            if self.fail:
                raise TimeoutError("conflict registry did not answer")
            return super().release(conflict_keys, objective_id)

    conflicts = Flaky()
    store, kernel, _, decision = _admitted(tmp_path, conflicts=conflicts)
    result = kernel.settle(store, decision.reservation_id, "COMMITTED")
    assert not result.released and result.needs_cleanup
    assert result.release_status == RELEASE_UNRESOLVED
    assert "TimeoutError" in result.detail
    assert conflicts.held == {"repo:main": "obj-a"}          # ключ всё ещё держится

    pending = store.pending_releases("obj-a")
    assert [p["reservation_id"] for p in pending] == [decision.reservation_id]

    conflicts.fail = False
    done = kernel.resolve_pending_releases(store, "obj-a")
    assert [d.release_status for d in done] == [RELEASE_DONE]
    assert conflicts.held == {}
    assert store.pending_releases("obj-a") == []             # список обязан пустеть


def test_repeatable_cleanup_never_steals_a_lease_taken_since(tmp_path):
    """Уборка после падения приходит позже, чем ресурс успела законно взять
    другая цель. Старый вызов не имеет права снять её аренду."""
    class Flaky(FakeConflicts):
        fail = True

        def release(self, conflict_keys, objective_id):
            if type(self).fail:
                raise TimeoutError("registry down")
            return super().release(conflict_keys, objective_id)

    Flaky.fail = True
    conflicts = Flaky()
    store, kernel, _, decision = _admitted(tmp_path, conflicts=conflicts)
    assert kernel.settle(store, decision.reservation_id, "COMMITTED").needs_cleanup

    # Пока уборка не прошла, ключ перехватывает другая цель.
    conflicts.held["repo:main"] = "obj-b"
    Flaky.fail = False
    kernel.resolve_pending_releases(store, "obj-a")
    assert conflicts.held == {"repo:main": "obj-b"}          # чужая аренда цела
    assert store.pending_releases("obj-a") == []             # но уборка завершена


def test_cleanup_is_idempotent(tmp_path):
    store, kernel, conflicts, decision = _admitted(tmp_path)
    assert kernel.settle(store, decision.reservation_id, "COMMITTED").released
    assert kernel.resolve_pending_releases(store, "obj-a") == ()
    assert kernel.resolve_pending_releases(store, "obj-a") == ()
    assert len(conflicts.releases) == 1


def test_a_legacy_reservation_without_recorded_keys_says_so(tmp_path):
    """Бронь старого билда не хранит захваченные ключи. Отпускать по ней нечего,
    и это не «успех»: молчание здесь скрывало бы удержанный ключ."""
    store, kernel, conflicts, decision = _admitted(tmp_path)
    with store._connect() as con:
        con.execute("UPDATE v5_reservations SET payload=? WHERE reservation_id=?",
                    ('{"phase": "READY"}', decision.reservation_id))
    result = kernel.settle(store, decision.reservation_id, "COMMITTED")
    assert result.release_status == RELEASE_UNKNOWN_KEYS
    assert result.needs_cleanup and not result.released
    assert "predates" in result.detail
    assert conflicts.releases == []


def test_the_keys_released_are_the_recorded_ones_not_the_current_spec(tmp_path):
    """Ревизия могла поменять conflict_keys уже ПОСЛЕ допуска: отпускается то,
    что было захвачено, иначе старый ключ висит, а чужой снимается."""
    store, kernel, conflicts, decision = _admitted(tmp_path)
    with store._connect() as con:
        con.execute("UPDATE v5_objectives SET spec_json=replace(spec_json,"
                    "'repo:main','repo:renamed') WHERE objective_id='obj-a'")
    result = kernel.settle(store, decision.reservation_id, "COMMITTED")
    assert result.conflict_keys == ("repo:main",)
    assert conflicts.releases == [(("repo:main",), "obj-a")]
