"""Resource-lifecycle correction retains strict clock and regression controls."""
import sqlite3

import pytest

from tools.human_speed_gate import (FreshConnectionFloor, cas_latency_contract,
                                   latency_contract, FAIL, PASS)
from tools.objective_cas_profile import measure_cas, fixture_spec
from bossman_shared.objective_store import ObjectiveStore


def test_floor_closes_every_handle_and_commits_real_wal_full_writes(tmp_path, monkeypatch):
    original = sqlite3.connect
    captured = []
    statements = []

    def connect(*args, **kwargs):
        con = original(*args, **kwargs)
        con.set_trace_callback(statements.append)
        captured.append(con)
        return con

    monkeypatch.setattr(sqlite3, "connect", connect)
    floor = FreshConnectionFloor(tmp_path)
    for _ in range(20):
        assert floor.tick() > 0
    floor.close()
    assert len(captured) == 21 and len(floor.samples) == len(floor.cpu_samples) == 20
    for con in captured:  # strong refs prevent GC from hiding a leak
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            con.execute("SELECT 1")
    with original(floor._path) as check:
        assert check.execute("SELECT v FROM floor WHERE k=1").fetchone()[0] == 20
        assert check.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    check.close()
    assert statements.count("PRAGMA synchronous=FULL") == 20
    with pytest.raises(RuntimeError, match="closed"):
        floor.tick()


def test_slow_wall_floor_cannot_hide_real_cpu_regression():
    # Actual old Windows CI wall medians; the CPU inputs are explicit unit data.
    wall, floor = [17.6017] * 100, [2.612] * 100
    assert latency_contract(wall, limit_ms=10, floor_samples_ms=floor)["status"] == PASS
    result = cas_latency_contract(wall, cpu_samples_ms=[4.4] * 100,
        floor_samples_ms=floor, floor_cpu_samples_ms=[.2] * 100)
    assert result["status"] == FAIL and result["reason"] == "thread_cpu_contract_failed"
    assert result["wall"]["status"] == PASS and result["thread_cpu"]["status"] == FAIL
    assert result["wall"]["max_ms"] == 17.6017
    assert result["thread_cpu"]["max_ms"] == 4.4
    for clock in (result["wall"], result["thread_cpu"]):
        assert clock["floor_multiple"] == 8 and clock["limit_ms"] == 10
        assert clock["max_isolated_stalls"] == 1 and clock["outliers_removed"] == 0


def test_fast_cpu_cannot_excuse_a_failed_wall_contract():
    result = cas_latency_contract([20.] * 100, cpu_samples_ms=[.2] * 100,
        floor_samples_ms=[.3] * 100, floor_cpu_samples_ms=[.1] * 100)
    assert result["status"] == FAIL and result["reason"] == "wall_contract_failed"
    assert result["thread_cpu"]["status"] == PASS


def test_different_clock_sample_counts_cannot_be_combined():
    result = cas_latency_contract([.2] * 100, cpu_samples_ms=[.2] * 200,
        floor_samples_ms=[.1] * 100, floor_cpu_samples_ms=[.1] * 200)
    assert result["status"] == FAIL and result["reason"] == "clock_sample_count_mismatch"


@pytest.mark.parametrize("floor_name", ["floor_samples_ms", "floor_cpu_samples_ms"])
@pytest.mark.parametrize("count", [99, 101])
def test_each_floor_must_pair_with_the_same_100_operations(floor_name, count):
    floors = {"floor_samples_ms": [.1] * 100, "floor_cpu_samples_ms": [.1] * 100}
    floors[floor_name] = [.1] * count
    result = cas_latency_contract([.2] * 100, cpu_samples_ms=[.2] * 100, **floors)
    assert result["status"] == FAIL and result["reason"] == "clock_sample_count_mismatch"


def test_all_four_healthy_paired_populations_pass():
    result = cas_latency_contract([.2] * 100, cpu_samples_ms=[.2] * 100,
        floor_samples_ms=[.1] * 100, floor_cpu_samples_ms=[.1] * 100)
    assert result["status"] == PASS
    for clock in (result["wall"], result["thread_cpu"]):
        assert clock["status"] == PASS and clock["n"] == clock["n_floor"] == 100


def test_collector_balances_order_and_preserves_every_sample(tmp_path):
    store = ObjectiveStore(tmp_path / "cas.db")
    state, measured = measure_cas(store, store.create(fixture_spec()), tmp_path)
    assert state.version == 101 and state.observations_used == 100
    assert measured["orders"] == ["floor-CAS", "CAS-floor"] * 50
    for key in ("samples_ms", "cpu_samples_ms", "floor_samples_ms", "floor_cpu_samples_ms"):
        assert len(measured[key]) == 100
    assert store.get(state.objective_id) == state


def test_collector_rejects_a_thin_corpus(tmp_path):
    with pytest.raises(ValueError, match="100"):
        measure_cas(None, None, tmp_path, n=99)
