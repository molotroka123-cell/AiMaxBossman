"""Real local component timings, NOT UI/OS/human or N0 certification.

CAS <10 ms is measured per operation (max, not an average hiding slow writes).
Recovery <2 s covers a crashed ObjectiveStore process, not the whole desktop.
JSON samples can be retained outside the repo through BOSSMAN_SPEED_RESULTS.
Timing assertions are always active; no skip, retry-to-green or threshold switch.
Current CAS measurements match the mandatory open/commit/close lifecycle and
require both wall and thread-CPU contracts. The historical measurements below
describe the old retained-connection comparator; see the 20260909 triage report
for its reproduced false positive and Windows CPU-regression false negative.

ПОЧЕМУ КОНТРАКТ CAS-ГЕЙТА ВЫГЛЯДИТ ИМЕННО ТАК (`latency_contract`).

Гейт брал percentile=100 против абсолютных 10 мс и падал ДВАЖДЫ на CI, оба
раза одной и той же подписью: ОДИН замер из ста мимо порога при здоровом
распределении и БЫСТРОМ поле хоста.

  329d58a  p100 = 25.16834 мс, порог 10, отказ и по относительному основанию
           => floor_p100 <= 25.16834/8 = 3.146 мс
  e25920f7 p100 = 10.012683 мс (мимо на 12.7 микросекунды!), тот же отказ
           => floor_p100 <= 10.012683/8 = 1.2516 мс
  e25920f7 на ТОМ ЖЕ коде: прогон 34083139457 зелёный и на py3.11, и на
           py3.12; прогон 34083136908 красный только этим гейтом.
           Один код, три раннера, два вердикта — сигнал не про код.
  историческое: 27.53 мс на ветке-ОСНОВЕ, 82.19 мс и 309.97 мс на раннерах,
           23-26 мс на сборочной машине под параллельными наборами, при том
           что операция становилась БЫСТРЕЕ (p50 1.68 против 2.79 мс).

Что здесь измерено (эта машина, 4 ядра, `.venv` py3.11, по 100 замеров):

  40 тихих прогонов подряд, прежний абсолютный контракт: 4 отказа из 40.
    То есть гейт краснел на 10% прогонов на ПРОСТАИВАЮЩЕЙ машине.
  Из этих четырёх: один — ровно подпись CI (один замер 21.989 мс, тело
    1.719 мс, пол хоста максимум 0.462 мс — пол срыва не видел);
    два — запинался весь хост (у пола поднялся ВЕСЬ хвост, 2.15-3.62 мс
    при медиане 0.215 мс); один — распределение развалилось (5 замеров
    мимо, тело 12.839 мс). Новый контракт: 1 отказ из 40, и это последний.
  Отношение p50/floor_p50 (31 прогон, покой и экстремальная нагрузка):
    3.673 - 5.153, при том что абсолютная задержка менялась в 17 раз
    (p50 1.05 мс в покое против 20.1 мс под 12 счётными процессами и
    4 параллельными fsync-потоками). Отношение к полу СВОЕГО ЖЕ хоста
    не зависит от скорости железа — на этом и держится разделение.
  Настоящий более медленный путь хранения, без единой вставленной задержки:
    journal_mode=DELETE + synchronous=FULL даёт отношение 8.34-8.63 и
    отвергается, ОСТАВАЯСЬ ПОД абсолютным порогом (p100 5.2-9.8 мс);
    16 дополнительных fsync на операцию — 9.92-11.09, тоже отказ.

Чего этот контракт НЕ обещает. Периодическая цена в коде, случающаяся ровно
реже одного раза на сто операций, попадёт в разрешённый один срыв и пройдёт;
поймать её можно только большей выборкой, а не другой статистикой. И
измеренное здесь отношение 3.67-5.15 — это ЭТА машина: на раннере GitHub
здоровое отношение не установлено, и если оно там окажется выше 8 без правок
кода, честный ответ — опубликовать замеры, а не поднять константу.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from bossman_shared.objective_observer import EnrolledSource, FileStateObserver
from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import CompareAndSwapError, ObjectiveStore
from tools.human_speed_gate import (FAIL, PASS, INSUFFICIENT,
                                    latency_contract, latency_summary,
                                    validate_ui_trace)
from tools.objective_cas_profile import measure_cas


def spec():
    return ObjectiveSpec.from_dict({
        "schema_version": 1, "owner_id": "speed-owner", "scope_id": "local-fixture",
        "objective_id": "speed-check", "revision": 1, "previous_digest": None,
        "sources": [{"source_ref": "file", "source_revision": "v1", "max_age_seconds": 30}],
        "predicates": [{"predicate_id": "exists", "source_ref": "file", "field": "exists",
                        "value_type": "boolean", "operator": "eq", "expected": True}],
        "expires_at": 10000, "priority": 1, "allowed_triggers": ["source_change"],
        "permission_refs": ["local-read"], "conflict_keys": ["speed-fixture"],
        "cooldown_seconds": 0,
        "limits": {"max_observations": 1000, "max_missions": 1,
                   "max_wall_seconds": 60, "max_cost_usd": 0},
        "stop_conditions": ["owner-stop"]})


def record(name, samples, result, record_property, floor_samples=None,
           cpu_samples=None, floor_cpu_samples=None, orders=None):
    # Сырые замеры сохраняются ОБА: без чередующегося пола зелёный вердикт по
    # относительному основанию нечем перепроверить, а он на нём и держится.
    data = {"name": name, "samples_ms": samples, "floor_samples_ms": floor_samples,
            "cpu_samples_ms": cpu_samples, "floor_cpu_samples_ms": floor_cpu_samples,
            "measurement_order": orders,
            "result": result,
            "tier": "LOCAL_COMPONENT", "python": sys.version.split()[0],
            "platform": sys.platform, "n0_activation_authorized": False}
    record_property(name, json.dumps(data, allow_nan=False))
    # Вердикт печатается ВСЕГДА и целиком. В CI виден только текст ассерта, а
    # pytest укорачивает его многоточием — то есть до лога не доходили ровно
    # те числа, по которым и отличают срыв планировщика от регрессии. Сырые
    # замеры остаются в record_property, здесь только сам вердикт.
    print("LATENCY_CONTRACT " + json.dumps({"name": name, **result}, allow_nan=False))
    folder = os.getenv("BOSSMAN_SPEED_RESULTS")
    if folder:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        # A distinct artifact directory per run is required. Never overwrite old evidence.
        with (path / (name + ".json")).open("x", encoding="utf-8") as f:
            json.dump(data, f, indent=2, allow_nan=False)


def test_objective_cas_under_10ms_and_stale_write_is_denied(tmp_path, record_property):
    store = ObjectiveStore(tmp_path / "cas.db")
    state = store.create(spec())
    # Matched lifecycle, balanced floor/CAS ordering, both raw clocks, all 100
    # first-write/commit/close samples. The collector retains every version check.
    state, measured = measure_cas(store, state, tmp_path)
    restored = ObjectiveStore(tmp_path / "cas.db").get(state.objective_id)
    assert restored == state and restored.observations_used == 100
    with pytest.raises(CompareAndSwapError):
        store.record_observation(state.objective_id, observed_at=101, count=1,
                                 expected_version=state.version - 1)
    assert store.get(state.objective_id) == restored
    assert state.lifecycle == "DRAFT" and state.condition == "UNKNOWN"
    result = measured["result"]
    record_cas("objective_cas", measured, record_property)
    # Each original 8x / 10ms / one-isolated-stall contract must pass. No clock
    # replaces another and no median/max/outlier is relabelled or discarded.
    assert result["status"] == PASS, result
    for clock in (result["wall"], result["thread_cpu"]):
        assert clock["basis"] in ("absolute_p100", "host_floor", "isolated_stall"), result


def test_observation_cycle_deterministic_without_sleep(tmp_path, monkeypatch, record_property):
    target = tmp_path / "observed.txt"
    target.write_text("same local fixture", encoding="utf-8")
    contract = spec()
    observer = FileStateObserver(EnrolledSource("file", "v1", "speed-owner",
                                                "local-fixture", contract.digest), target)
    def forbidden_sleep(*args, **kwargs):
        raise AssertionError("observation must not depend on sleep placeholders")
    monkeypatch.setattr(time, "sleep", forbidden_sleep)
    samples, identities = [], set()
    for _ in range(100):
        start = time.perf_counter_ns()
        observed = observer.observe(now=100.0)
        samples.append((time.perf_counter_ns() - start) / 1e6)
        identities.add(observed.observation_id)
        assert observed.values["exists"] is True
    assert len(identities) == 1
    target.write_text("changed local fixture", encoding="utf-8")
    assert observer.observe(now=100.0).observation_id not in identities
    # This is observation cost, deliberately not the UI input-ack gate.
    record("observation_cycle", samples, {"status": PASS, "deterministic": True,
           "input_ack_measured": False, "max_ms": max(samples)}, record_property)


def test_crashed_store_recovers_known_committed_state_under_2s(tmp_path, record_property):
    path = tmp_path / "crash.db"
    store = ObjectiveStore(path)
    state = store.create(spec())
    state = store.record_observation(state.objective_id, observed_at=100.0, count=1,
                                     expected_version=state.version)
    expected = json.loads(json.dumps(asdict(state)))
    crash = '''import os,sys
from bossman_shared.objective_store import ObjectiveStore
s=ObjectiveStore(sys.argv[1])
c=s._connect()
c.execute("BEGIN IMMEDIATE")
c.execute("UPDATE v5_objectives SET condition='SATISFIED',observations_used=999")
os._exit(86)
'''
    reopen = '''import json,sys
from dataclasses import asdict
from bossman_shared.objective_store import ObjectiveStore
print(json.dumps(asdict(ObjectiveStore(sys.argv[1]).get('speed-check'))))
'''
    samples = []
    for _ in range(5):
        failed = subprocess.run([sys.executable, "-c", crash, str(path)],
                                capture_output=True, text=True, timeout=10)
        assert failed.returncode == 86, failed.stderr
        start = time.perf_counter_ns()
        recovered = subprocess.run([sys.executable, "-c", reopen, str(path)],
                                   capture_output=True, text=True, timeout=10)
        elapsed = (time.perf_counter_ns() - start) / 1e6
        assert recovered.returncode == 0, recovered.stderr
        assert json.loads(recovered.stdout) == expected
        samples.append(elapsed)
    result = latency_summary(samples, limit_ms=2000, minimum=5, percentile=100)
    result["scope"] = "process_restart_and_store_rollback_only"
    record("store_crash_recovery", samples, result, record_property)
    assert result["status"] == PASS, result


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, 10**400])
def test_invalid_timings_cannot_pass(bad):
    assert latency_summary([bad] * 100, limit_ms=10)["status"] == FAIL


def test_timing_gate_keeps_slow_attempts_and_strict_threshold():
    assert latency_summary([10.0] * 100, limit_ms=10)["status"] == FAIL
    result = latency_summary([0.01] * 99 + [20.0], limit_ms=10, percentile=100)
    assert result["status"] == FAIL and result["max_ms"] == 20.0
    assert result["outliers_removed"] == 0
    assert latency_summary([], limit_ms=10)["status"] == INSUFFICIENT


def ui_fixture():
    # Evaluator fixture ONLY. These synthetic numbers are never benchmark evidence.
    return {"schema_version": 1, "code_sha": "a" * 40, "dirty": False,
            "tier": "USER_UI_LIVE", "sessions": [
        {"session_id": str(s), "time_origin_ms": 1000.0, "trace_sha256": "b" * 64,
         "events": [{"event_id": str(i), "kind": "click", "trusted": True,
                     "ack_visible": True, "input_ms": i * 200.0, "ack_ms": i * 200.0 + 20}
                    for i in range(100)]} for s in range(5)]}


def test_input_gate_requires_real_external_evidence_not_component_results():
    assert validate_ui_trace(None, expected_sha="a" * 40)["status"] == INSUFFICIENT
    data = ui_fixture()
    assert validate_ui_trace(data, expected_sha="a" * 40)["status"] == PASS
    data["tier"] = "LOCAL_COMPONENT"
    assert validate_ui_trace(data, expected_sha="a" * 40)["status"] == FAIL


@pytest.mark.parametrize("attack", ["sha", "dirty", "sessions", "samples", "missing_ack",
                                     "untrusted", "clock", "duplicate", "slow"])
def test_input_gate_rejects_stale_incomplete_or_invalid_traces(attack):
    data = ui_fixture()
    event = data["sessions"][0]["events"][0]
    if attack == "sha": data["code_sha"] = "c" * 40
    elif attack == "dirty": data["dirty"] = True
    elif attack == "sessions": data["sessions"].pop()
    elif attack == "samples": data["sessions"][0]["events"].pop()
    elif attack == "missing_ack": event.pop("ack_ms")
    elif attack == "untrusted": event["trusted"] = False
    elif attack == "clock": event["ack_ms"] = -1
    elif attack == "duplicate": data["sessions"][0]["events"][1]["event_id"] = "0"
    elif attack == "slow":
        for row in data["sessions"][0]["events"]: row["ack_ms"] = row["input_ms"] + 101
    assert validate_ui_trace(data, expected_sha="a" * 40)["status"] != PASS


def test_malformed_type_fields_are_refused_not_coerced():
    data = ui_fixture()
    data["schema_version"] = True
    assert validate_ui_trace(data, expected_sha="a" * 40)["status"] == FAIL
    data = ui_fixture()
    data["sessions"][0]["events"][0]["kind"] = []
    assert validate_ui_trace(data, expected_sha="a" * 40)["status"] == FAIL


def spin_ms(milliseconds):
    """Настоящая работа внутри измеряемого окна, а не уступка планировщику.

    `sleep` отдал бы процессор и мерил бы готовность планировщика вернуть его
    обратно. Здесь нужно подорожание САМОЙ операции, поэтому цикл крутится на
    процессоре и попадает в замер целиком, как настоящая лишняя работа.
    """
    # Count real CPU work; scheduler pauses cannot consume the injected burden.
    deadline = time.thread_time_ns() + int(milliseconds * 1e6)
    while time.thread_time_ns() < deadline:
        pass


class BurdenedStore(ObjectiveStore):
    """Настоящий более дорогой путь записи: обёртка вокруг самой CAS-операции.

    `only_at=None` — дорожает КАЖДАЯ запись (регрессия кода: сдвигается всё
    распределение). `only_at=i` — дорожает ровно одна (срыв планировщика:
    двигается один худший замер и больше ничего).
    """

    def __init__(self, path, *, burden_ms, only_at=None):
        super().__init__(path)
        self._burden_ms = burden_ms
        self._only_at = only_at
        self._calls = 0

    def record_observation(self, *args, **kwargs):
        burdened = self._only_at is None or self._only_at == self._calls
        self._calls += 1
        if burdened:
            spin_ms(self._burden_ms)
        return super().record_observation(*args, **kwargs)


def measured_cas_run(tmp_path, store, n=100):
    """Тот же цикл, что и в гейте: n замеров CAS с ЧЕРЕДУЮЩИМСЯ полом хоста."""
    state = store.create(spec())
    state, measured = measure_cas(store, state, tmp_path, n=n)
    assert store.get(state.objective_id).observations_used == n
    return measured


def record_cas(name, measured, record_property):
    record(name, measured["samples_ms"], measured["result"], record_property,
           floor_samples=measured["floor_samples_ms"],
           cpu_samples=measured["cpu_samples_ms"],
           floor_cpu_samples=measured["floor_cpu_samples_ms"], orders=measured["orders"])


def test_a_real_4ms_cpu_regression_is_rejected_on_slow_storage(tmp_path, record_property):
    """A disk stall cannot buy permission for extra CPU work.

    The former wall-p50<10 assertion was disproved by Windows CI: wall p50
    17.60ms / floor 2.612ms let this real regression PASS. The unchanged wall
    contract is now required together with the unchanged CPU contract. The
    pure gate suite still proves rejection below the absolute wall threshold.
    """
    store = BurdenedStore(tmp_path / "cas.db", burden_ms=4.0)
    measured = measured_cas_run(tmp_path, store)
    result = measured["result"]
    record_cas("objective_cas_regression_4ms_cpu", measured, record_property)
    assert result["status"] == FAIL, result
    cpu = result["thread_cpu"]
    assert cpu["status"] == FAIL, result
    assert cpu["reason"] == "operation_disproportionate_to_its_own_host_floor", result
    assert cpu["p50_ratio"] > cpu["floor_multiple"], result
    assert cpu["p50_ms"] >= 4.0, "the injected work must be real CPU, not scheduler delay"


def test_a_gross_regression_fails_on_every_basis(tmp_path, record_property):
    """Регрессия, вышедшая за порог, закрывает все три двери сразу.

    12 мс на каждую запись: тело за порогом, вышедших замеров сто из ста,
    отношение к полу около 45. Ни absolute_p100, ни host_floor, ни
    isolated_stall — послабление для одиночного срыва здесь не спасает.
    """
    store = BurdenedStore(tmp_path / "cas.db", burden_ms=12.0)
    measured = measured_cas_run(tmp_path, store)
    result = measured["result"]
    record_cas("objective_cas_regression_gross", measured, record_property)
    assert result["status"] == FAIL, result
    for clock in (result["wall"], result["thread_cpu"]):
        assert clock["body_ms"] >= clock["limit_ms"], result
        assert clock["over_limit"] > clock["max_isolated_stalls"], result
    cpu = result["thread_cpu"]
    assert cpu["status"] == FAIL and cpu["basis"] is None, result
    assert cpu["p50_ratio"] > cpu["floor_multiple"], result


def test_one_injected_stall_on_a_healthy_run_is_not_called_a_db_defect(
        tmp_path, record_property):
    """Вторая сторона той же правки: 25 мс в ОДНОМ замере из ста — не дефект.

    Ровно подпись обоих красных прогонов CI, воспроизведённая живьём на
    настоящем хранилище. Прежний абсолютный контракт здесь краснеет; новый
    смотрит на тело и на чередующийся пол и говорит `isolated_stall`, сохраняя
    сырые 25+ мс в `max_ms` и `stalls_ms`.

    Ветка `else` — не поблажка, а проверка обратного: если хост подкинул свои
    срывы сверх внедрённого, премиса «в остальном здоровое распределение» не
    выполнена, и контракт ОБЯЗАН отказаться называть это шумом (единственный
    допустимый зелёный тогда — подтверждённый самим полом `host_floor`).
    """
    store = BurdenedStore(tmp_path / "cas.db", burden_ms=25.0, only_at=59)
    measured = measured_cas_run(tmp_path, store)
    combined = measured["result"]
    result = combined["wall"]
    samples = measured["samples_ms"]
    record_cas("objective_cas_single_stall", measured, record_property)
    # Прежний контракт (абсолютный p100) на этих же сырых замерах — отказ.
    assert latency_summary(samples, limit_ms=10.0, percentile=100)["status"] == FAIL
    # Сырое значение сохранено, а не переименовано в PASS-величину.
    assert result["max_ms"] >= 25.0 and result["stalls_ms"][0] == result["max_ms"]
    assert result["over_limit"] >= 1
    if result["over_limit"] == 1:
        assert result["status"] == PASS, result
        assert result["body_ms"] < result["limit_ms"], result
        # Основание называется по тому, ЧТО именно подтвердило измерение.
        # Если пол хоста сам не покрывает этот максимум, оправдание может быть
        # только одно — одиночный срыв. Если покрывает (а на шумной машине пол
        # тоже запинается: измерено floor_max до 3.6 мс при медиане 0.2 мс),
        # то это тем более не дефект БД, и основание — `host_floor`.
        if result["max_ms"] >= result["allowed_max_ms"]:
            assert result["basis"] == "isolated_stall", result
        else:
            assert result["basis"] == "host_floor", result
    else:
        assert result["status"] == FAIL or result["basis"] == "host_floor", result
    cpu = combined["thread_cpu"]
    assert cpu["max_ms"] >= 25.0 and cpu["over_limit"] >= 1, cpu
    assert combined["status"] == (PASS if result["status"] == cpu["status"] == PASS else FAIL)


def test_the_gate_cannot_be_satisfied_without_an_interleaved_host_floor(tmp_path):
    """Зелёный вердикт обязан быть перепроверяемым, значит пол обязателен.

    Замеры без пола — это утверждение без второй половины: сказать, чьё это
    замедление, по ним нельзя. Это не PASS и не FAIL, а отсутствие evidence.
    """
    store = ObjectiveStore(tmp_path / "cas.db")
    measured = measured_cas_run(tmp_path, store, n=100)
    samples, floor = measured["samples_ms"], measured["floor_samples_ms"]
    assert latency_contract(samples, limit_ms=10.0,
                            floor_samples_ms=None)["status"] == INSUFFICIENT
    assert latency_contract(samples, limit_ms=10.0,
                            floor_samples_ms=floor[:50])["status"] == FAIL
    # И наоборот: с чередующимся полом основание всегда названо — ровно тогда,
    # когда вердикт зелёный, и никогда, когда нет.
    green = latency_contract(samples, limit_ms=10.0, floor_samples_ms=floor)
    assert (green["basis"] is None) == (green["status"] != PASS), green
