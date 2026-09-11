"""Measured latency gates. A component PASS never activates V5/N0.

UI evidence is supplied by the independent visible-user run, not synthesized by
these microbenchmarks. This validator checks provenance fields, not the honesty
of an arbitrary producer; retain the original trace and trusted run identity.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


def finite_nonnegative(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


class StorageFloor:
    """Во что этому хосту обходится САМАЯ ДЕШЁВАЯ долговечная запись.

    Пустая IMMEDIATE-транзакция в sqlite на том же диске: ниже этого не может
    быть НИ ОДНА операция, которая обязана пережить падение. Это не оценка
    «скорости машины вообще» — это пол ровно того класса работы, который меряет
    гейт, снятый здесь и сейчас, а не откалиброванный когда-то на чужом железе.

    Замеряется ЧЕРЕДУЯСЬ с измеряемой операцией (`tick()` внутри того же цикла).
    Иначе срыв планировщика попадает в одно распределение и не попадает в другое,
    и нормировка перестаёт что-либо значить именно тогда, когда она нужна.
    """

    def __init__(self, directory: Any) -> None:
        import sqlite3
        self.samples: list[float] = []
        self._path = str(Path(directory) / "storage-floor.db")
        self._con = sqlite3.connect(self._path, timeout=30, isolation_level="IMMEDIATE")
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("CREATE TABLE floor(k INTEGER PRIMARY KEY, v INTEGER NOT NULL)")
        self._con.commit()
        self._n = 0

    def tick(self) -> float:
        import time
        started = time.perf_counter_ns()
        self._con.execute("INSERT INTO floor(k,v) VALUES(?,?)", (self._n, self._n))
        self._con.commit()
        elapsed = (time.perf_counter_ns() - started) / 1e6
        self._n += 1
        self.samples.append(elapsed)
        return elapsed

    def close(self) -> None:
        self._con.close()

    def at(self, percentile: int = 100) -> float:
        """Пол по ТОЙ ЖЕ статистике, что и измерение, которое он нормирует."""
        if not self.samples:
            raise ValueError("storage floor was never sampled")
        ordered = sorted(self.samples)
        return ordered[math.ceil(len(ordered) * percentile / 100) - 1]


class FreshConnectionFloor:
    """Minimum WAL/FULL write with the CAS store's required handle lifetime.

    A retained connection omits open/schema/WAL-close costs and is not a valid
    floor for operations required to close immediately. This fixture contains
    one integer row, no objective schema, CAS, serialization or application
    logic. Wall and current-thread CPU samples remain separate.
    """

    def __init__(self, directory: Any) -> None:
        import sqlite3
        from contextlib import closing
        self._path = str(Path(directory) / "fresh-connection-floor.db")
        self.samples: list[float] = []
        self.cpu_samples: list[float] = []
        self._closed = False
        with closing(sqlite3.connect(self._path, isolation_level="IMMEDIATE")) as con:
            con.execute("PRAGMA journal_mode=WAL")
            with con:
                con.execute("CREATE TABLE floor(k INTEGER PRIMARY KEY, v INTEGER NOT NULL)")
                con.execute("INSERT INTO floor VALUES(1,0)")

    def tick(self) -> float:
        import sqlite3
        import time
        from contextlib import closing
        if self._closed:
            raise RuntimeError("storage floor is closed")
        start = time.perf_counter_ns()
        cpu_start = time.thread_time_ns()
        with closing(sqlite3.connect(self._path, timeout=30, isolation_level="IMMEDIATE")) as con:
            con.execute("PRAGMA synchronous=FULL")
            with con:
                con.execute("UPDATE floor SET v=v+1 WHERE k=1")
        cpu_elapsed = (time.thread_time_ns() - cpu_start) / 1e6
        elapsed = (time.perf_counter_ns() - start) / 1e6
        self.samples.append(elapsed)
        self.cpu_samples.append(cpu_elapsed)
        return elapsed

    def close(self) -> None:
        # Every handle has already closed before tick returns.
        self._closed = True


def _nearest_rank(ordered: list[float], percentile: int) -> float:
    """Перцентиль по ближайшему рангу, без интерполяции.

    Каждое опубликованное число обязано быть НАСТОЯЩИМ замером, который
    действительно случился, а не средним между двумя соседними: иначе отчёт
    показывает величину, которой на этом хосте никто не наблюдал.
    """
    return ordered[math.ceil(len(ordered) * percentile / 100) - 1]


def latency_summary(samples_ms: list[float], *, limit_ms: float,
                    minimum: int = 100, percentile: int = 95) -> dict[str, Any]:
    """Nearest-rank percentile, retaining outliers and every timed attempt.

    Только абсолютный порог. Нормировка по полу хоста живёт в
    `latency_contract` и ТОЛЬКО там: две реализации одного и того же правила
    неизбежно разъезжаются, и тогда невозможно сказать, какая из них вынесла
    вердикт.
    """
    if (type(samples_ms) is not list or not finite_nonnegative(limit_ms)
            or limit_ms == 0 or type(minimum) is not int or minimum < 1
            or type(percentile) is not int or not 1 <= percentile <= 100):
        raise ValueError("invalid latency-gate configuration")
    if any(not finite_nonnegative(x) for x in samples_ms):
        return {"status": FAIL, "basis": None, "reason": "invalid_sample",
                "n": len(samples_ms)}
    if len(samples_ms) < minimum:
        return {"status": INSUFFICIENT, "basis": None, "reason": "sample_count",
                "n": len(samples_ms)}
    ordered = sorted(samples_ms)
    value = _nearest_rank(ordered, percentile)
    result = {"status": FAIL, "basis": None, "n": len(ordered), "percentile": percentile,
              "value_ms": value, "p50_ms": _nearest_rank(ordered, 50), "max_ms": ordered[-1],
              "limit_ms": limit_ms, "comparison": "strictly_less_than", "outliers_removed": 0}
    if value < limit_ms:
        return {**result, "status": PASS, "basis": "absolute"}
    return result


def latency_contract(samples_ms: list[float], *, limit_ms: float,
                     floor_samples_ms: list[float] | None,
                     minimum: int = 100, max_isolated_stalls: int = 1,
                     floor_multiple: float = 8.0) -> dict[str, Any]:
    """Приёмка по времени, которая отделяет СРЫВ ПЛАНИРОВЩИКА от РЕГРЕССИИ БД.

    Обе половины требования держатся одновременно, ни одна не заменяет другую.

    1. Приёмка на целевом хосте СОХРАНЕНА. `p100 < limit_ms` остаётся первым и
       главным основанием (`basis="absolute_p100"`), порог не поднят, вердикт
       не превращён в предупреждение, сырой максимум всегда лежит в `max_ms`
       и `value_ms` под своим настоящим именем.
    2. Отличие шума от дефекта делается ИЗМЕРЕНИЕМ, а не на вкус. Настоящая
       регрессия сдвигает РАСПРЕДЕЛЕНИЕ: растут и медиана, и тело. Одиночный
       срыв планировщика двигает ровно один худший замер и не трогает ни
       медиану, ни тело, ни чередующийся пол хоста.

    Три и только три основания для PASS, и каждое записывается в `basis`,
    чтобы зелёный результат можно было перепроверить по числам, а не по слову:

    * `absolute_p100` — весь максимум уложился в порог. Это то, что владелец
      и требует от своей машины.
    * `host_floor` — хост медленный ЦЕЛИКОМ: и тело, и максимум держатся в
      пределах кратности от СВОЕГО ЖЕ пола, снятого чередуясь в том же цикле.
      Это прежний контракт (506b2f2), сохранённый, плюс новое условие на тело.
    * `isolated_stall` — за порог вышло не больше `max_isolated_stalls`
      замеров из `minimum` (1 из 100 = 1%), а ВСЁ остальное распределение
      строго внутри порога. Это ровно и только та дыра, которую чередующийся
      пол закрыть не может: один срыв на быстром хосте попадает в CAS и НЕ
      попадает ни в один тик пола.

    И ни одно из трёх не действует, если операция непропорциональна полу
    СВОЕГО ЖЕ хоста: `p50 < floor_p50 * floor_multiple` — жёсткое условие для
    любого PASS. Это и есть детектор регрессии, работающий независимо от
    скорости железа: при равномерном замедлении хоста растут ОБА числа и
    отношение стоит на месте, при регрессии кода растёт только числитель.

    Измерено на этой машине (31 прогон по 100 замеров; полные числа и их
    происхождение — в `tests/test_v5_human_speed.py`):
    отношение p50/floor_p50 держится в 3.67–5.15 при том, что абсолютная
    задержка менялась в 17 раз (p50 CAS 1.05 мс в покое против 20.1 мс под
    12 счётными процессами и 4 параллельными fsync-потоками на 4 ядрах).
    Настоящий более медленный путь хранения (journal_mode=DELETE +
    synchronous=FULL, без единой вставленной задержки) даёт 8.34–8.63 и
    отвергается, оставаясь при этом ПОД абсолютным порогом (p100 5.2–9.8 мс).

    `floor_samples_ms` обязателен и должен быть снят ЧЕРЕДУЯСЬ, 1:1 по длине.
    Оправдание задержки — это утверждение о хосте, поэтому оно принимается
    только вместе с измерением самого хоста, сделанным в том же цикле.
    """
    if (type(samples_ms) is not list or not finite_nonnegative(limit_ms)
            or limit_ms == 0 or type(minimum) is not int or minimum < 1
            or type(max_isolated_stalls) is not int or max_isolated_stalls < 0
            or not finite_nonnegative(floor_multiple) or floor_multiple <= 0):
        raise ValueError("invalid latency-gate configuration")
    # Послабление нельзя расширить, не собрав больше замеров: доля вышедших за
    # порог замеров ограничена одним процентом ПО КОНСТРУКЦИИ, а не подписью.
    if max_isolated_stalls * 100 > minimum:
        raise ValueError("isolated-stall allowance may not exceed 1% of the sample floor")

    head: dict[str, Any] = {"status": FAIL, "basis": None, "reason": None,
                            "n": len(samples_ms), "limit_ms": limit_ms,
                            "comparison": "strictly_less_than", "outliers_removed": 0,
                            "max_isolated_stalls": max_isolated_stalls,
                            "floor_multiple": floor_multiple}
    if any(not finite_nonnegative(x) for x in samples_ms):
        return {**head, "reason": "invalid_sample"}
    if len(samples_ms) < minimum:
        return {**head, "status": INSUFFICIENT, "reason": "sample_count"}
    if type(floor_samples_ms) is not list:
        return {**head, "status": INSUFFICIENT, "reason": "host_floor_not_measured"}
    if any(not finite_nonnegative(x) for x in floor_samples_ms):
        return {**head, "reason": "invalid_floor_sample"}
    if len(floor_samples_ms) != len(samples_ms):
        return {**head, "reason": "floor_not_interleaved",
                "n_floor": len(floor_samples_ms)}

    ordered, floor_ordered = sorted(samples_ms), sorted(floor_samples_ms)
    n = len(ordered)
    # Тело распределения — самый медленный замер из тех, что НЕ попали в
    # разрешённое число срывов. Всё, что не срыв, обязано быть внутри порога.
    body_rank = n - max_isolated_stalls
    stalls = sorted((x for x in samples_ms if x >= limit_ms), reverse=True)
    stats: dict[str, Any] = {
        "percentile": 100, "value_ms": ordered[-1], "max_ms": ordered[-1],
        "p50_ms": _nearest_rank(ordered, 50), "p95_ms": _nearest_rank(ordered, 95),
        "body_ms": ordered[body_rank - 1], "body_rank": body_rank,
        "over_limit": len(stalls), "stalls_ms": stalls[:10],
        # Сколько раз за порог вышел САМ ПОЛ — то есть самая дешёвая
        # долговечная запись этого хоста, которой заведомо не за что быть
        # медленной. Ни на один вердикт это число не влияет и ни в одну ветку
        # ниже не входит; оно отвечает на вопрос, который иначе приходится
        # решать спором: «хост срывался, или это код?». Пол над порогом —
        # измеренный ответ «хост», записанный рядом с вердиктом, а не
        # восстановленный потом из чужих логов.
        "floor_over_limit": sum(1 for x in floor_samples_ms if x >= limit_ms),
        "n_floor": len(floor_ordered),
        "floor_p50_ms": _nearest_rank(floor_ordered, 50),
        "floor_body_ms": floor_ordered[body_rank - 1], "floor_max_ms": floor_ordered[-1]}
    # Нулевая медиана означает, что мерили не то или не тем: долговечная запись
    # не занимает ноль. Такой набор не PASS и не FAIL, а отсутствие evidence.
    if stats["p50_ms"] == 0 or stats["floor_p50_ms"] == 0:
        return {**head, **stats, "status": INSUFFICIENT, "reason": "degenerate_measurement"}

    stats["p50_ratio"] = stats["p50_ms"] / stats["floor_p50_ms"]
    stats["allowed_p50_ms"] = stats["floor_p50_ms"] * floor_multiple
    stats["allowed_body_ms"] = stats["floor_body_ms"] * floor_multiple
    stats["allowed_max_ms"] = stats["floor_max_ms"] * floor_multiple
    body = {**head, **stats}

    if stats["p50_ms"] >= stats["allowed_p50_ms"]:
        return {**body, "reason": "operation_disproportionate_to_its_own_host_floor",
                "note": ("the median operation costs this multiple of the same host's own "
                         "minimum durable write, measured interleaved in the same loop; "
                         "a slow host moves both numbers and leaves this ratio alone")}
    if stats["max_ms"] < limit_ms:
        return {**body, "status": PASS, "basis": "absolute_p100"}
    if (stats["body_ms"] < stats["allowed_body_ms"]
            and stats["max_ms"] < stats["allowed_max_ms"]):
        return {**body, "status": PASS, "basis": "host_floor",
                "note": ("absolute limit exceeded on a host whose own minimum durable write "
                         "is this slow; the operation stayed within the allowed multiple of it")}
    if stats["over_limit"] <= max_isolated_stalls and stats["body_ms"] < limit_ms:
        return {**body, "status": PASS, "basis": "isolated_stall",
                "note": ("the absolute limit was exceeded by at most the allowed number of "
                         "samples while the whole remaining distribution stayed strictly "
                         "inside it; the raw over-limit values are retained in stalls_ms "
                         "and max_ms and are not relabelled")}
    # Сюда попадают только распределения: если за порог вышло не больше
    # разрешённого, то тело по построению внутри порога и путь выше уже отдал
    # PASS. Поэтому причина здесь ровно одна и она не про единичный замер.
    return {**body, "reason": "excess_spread_across_the_distribution"}


def cas_latency_contract(samples_ms: list[float], *, cpu_samples_ms: list[float],
                         floor_samples_ms: list[float],
                         floor_cpu_samples_ms: list[float]) -> dict[str, Any]:
    """CAS must pass both unchanged wall and thread-CPU latency contracts.

    The CPU comparison prevents an expensive storage host from buying an
    allowance for a real compute regression. No clock is substituted for wall
    time: both full verdicts and their original samples are published.
    """
    wall = latency_contract(samples_ms, limit_ms=10.0, floor_samples_ms=floor_samples_ms)
    cpu = latency_contract(cpu_samples_ms, limit_ms=10.0, floor_samples_ms=floor_cpu_samples_ms)
    # All four measured populations must pair 1:1. Leave an unmeasured floor
    # to the original insufficient-evidence verdict below.
    if (len(samples_ms) != len(cpu_samples_ms)
            or (type(floor_samples_ms) is list and len(floor_samples_ms) != len(samples_ms))
            or (type(floor_cpu_samples_ms) is list
                and len(floor_cpu_samples_ms) != len(samples_ms))):
        return {"status": FAIL, "reason": "clock_sample_count_mismatch",
                "wall": wall, "thread_cpu": cpu,
                "floor_lifecycle": "open_wal_full_write_commit_close"}
    if wall["status"] != PASS:
        status, reason = wall["status"], "wall_contract_failed"
    elif cpu["status"] != PASS:
        status, reason = cpu["status"], "thread_cpu_contract_failed"
    else:
        status, reason = PASS, None
    return {"status": status, "reason": reason, "wall": wall, "thread_cpu": cpu,
            "floor_lifecycle": "open_wal_full_write_commit_close"}


def validate_ui_trace(trace: Any, *, expected_sha: str) -> dict[str, Any]:
    """Five sessions x 100 genuine input/visible-ack pairs, one browser clock.

    CLI/API/controller timings are rejected as UI input evidence. Missing ACKs
    fail instead of disappearing from the denominator. The owner run must keep
    original screenshot/trace artifacts; JSON alone is not hardware attestation.
    """
    def bad(reason: str, status: str = FAIL) -> dict[str, Any]:
        return {"status": status, "reason": reason, "n0_activation_authorized": False}

    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("full expected code SHA required")
    if trace is None:
        return bad("visible_input_trace_not_supplied", INSUFFICIENT)
    if (not isinstance(trace, dict) or type(trace.get("schema_version")) is not int
            or trace["schema_version"] != 1):
        return bad("trace_schema")
    if trace.get("code_sha") != expected_sha or trace.get("dirty") is not False:
        return bad("stale_or_dirty_source")
    if trace.get("tier") != "USER_UI_LIVE":
        return bad("wrong_evidence_tier")
    sessions = trace.get("sessions")
    if not isinstance(sessions, list) or len(sessions) < 5:
        return bad("need_five_sessions", INSUFFICIENT)
    ids: set[str] = set()
    summaries = []
    for session in sessions:
        if not isinstance(session, dict):
            return bad("session_schema")
        sid = session.get("session_id")
        if not isinstance(sid, str) or not sid or sid in ids:
            return bad("session_identity")
        ids.add(sid)
        if (not finite_nonnegative(session.get("time_origin_ms"))
                or not isinstance(session.get("trace_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", session["trace_sha256"])):
            return bad("clock_or_trace_provenance")
        events = session.get("events")
        if not isinstance(events, list):
            return bad("events_schema")
        seen: set[str] = set()
        samples: list[float] = []
        last_start = -1.0
        for event in events:
            if not isinstance(event, dict):
                return bad("event_schema")
            eid = event.get("event_id")
            if not isinstance(eid, str) or not eid or eid in seen:
                return bad("event_identity")
            seen.add(eid)
            if (event.get("kind") not in ("click", "key")
                    or event.get("trusted") is not True
                    or event.get("ack_visible") is not True):
                return bad("untrusted_or_unacknowledged_input")
            start, end = event.get("input_ms"), event.get("ack_ms")
            if (not finite_nonnegative(start) or not finite_nonnegative(end)
                    or end < start or start < last_start):
                return bad("invalid_clock_order")
            last_start = start
            samples.append(end - start)
        summaries.append(latency_summary(samples, limit_ms=100.0))
    status = (FAIL if any(s["status"] == FAIL for s in summaries) else
              INSUFFICIENT if any(s["status"] == INSUFFICIENT for s in summaries) else PASS)
    return {"status": status, "sessions": summaries,
            "scope": "visible_click_key_acknowledgement_only",
            "human_comparison": "NOT_RUN", "n0_activation_authorized": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-sha", required=True)
    parser.add_argument("--ui-trace", type=Path)
    args = parser.parse_args()
    try:
        trace = json.loads(args.ui_trace.read_text(encoding="utf-8")) if args.ui_trace else None
        result = validate_ui_trace(trace, expected_sha=args.expect_sha)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": FAIL, "reason": type(exc).__name__}))
        return 1
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["status"] == PASS else 2 if result["status"] == INSUFFICIENT else 1


if __name__ == "__main__":
    raise SystemExit(main())
