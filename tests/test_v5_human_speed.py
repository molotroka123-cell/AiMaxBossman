"""V5 human-speed gates on LOCAL hardware: quick, no model, no network, no sleep.

Master-prompt targets (PR #7 roadmap):
- ObjectiveStore CAS op < 10 ms
- observation cycle deterministic + < 100 ms, no sleep() placeholders
- crash recovery (restart) to known-good state < 2 s

If any gate fails on this host, that IS the audit finding (do not loosen the
bound to go green — report it). All fixtures are local tmp SQLite/files.
"""
from __future__ import annotations

import statistics
import time
from pathlib import Path

import pytest

from bossman_shared.objective_observer import (
    DirectoryStateObserver,
    EnrolledSource,
    FileStateObserver,
    collect,
)
from bossman_shared.objective_recovery import recover
from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import ObjectiveStore

OWNER = "owner"
SCOPE = "project"
SOURCE = "report-file"

CAS_BUDGET_MS = 10.0
OBSERVE_BUDGET_MS = 100.0
RECOVER_BUDGET_S = 2.0
REPEATS = 60


def _raw_spec(objective_id="keep-report"):
    return {
        "schema_version": 1, "owner_id": OWNER, "scope_id": SCOPE,
        "objective_id": objective_id, "revision": 1, "previous_digest": None,
        "sources": [{"source_ref": SOURCE, "source_revision": "v1", "max_age_seconds": 30}],
        "predicates": [{"predicate_id": "present", "source_ref": SOURCE, "field": "exists",
                        "value_type": "boolean", "operator": "eq", "expected": True}],
        "expires_at": 10_000, "priority": 1, "allowed_triggers": ["source_change"],
        "permission_refs": ["owner-grant"], "conflict_keys": ["report"],
        "cooldown_seconds": 5,
        "limits": {"max_observations": 1000, "max_missions": 100, "max_wall_seconds": 3600,
                   "max_cost_usd": 0},
        "stop_conditions": ["owner-revocation"],
    }


def _enrollment(spec):
    return EnrolledSource(source_ref=SOURCE, source_revision="v1",
                          owner_id=OWNER, scope_id=SCOPE,
                          objective_digest=spec.digest)


def _p95_ms(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000.0


def test_objective_store_cas_ops_stay_under_10ms(tmp_path):
    """CAS-guarded writes (create/transition/enroll) must feel instant."""
    store = ObjectiveStore(tmp_path / "objectives.sqlite3")
    specs = [ObjectiveSpec.from_dict(_raw_spec(f"obj-{i:03d}")) for i in range(REPEATS)]
    lat: list[float] = []
    state = None
    for spec in specs:
        t0 = time.perf_counter()
        state = store.create(spec)
        lat.append(time.perf_counter() - t0)
    # real CAS path with expected_version on one objective
    state = store.enroll_sources(state.objective_id, (SOURCE,), owner_id=OWNER,
                                 expected_version=state.version)
    t0 = time.perf_counter()
    state = store.transition(state.objective_id, "ACTIVE", now=100.0,
                             owner_id=OWNER, expected_version=state.version)
    lat.append(time.perf_counter() - t0)
    p95 = _p95_ms(lat)
    assert p95 < CAS_BUDGET_MS, f"CAS p95 {p95:.2f} ms over {CAS_BUDGET_MS} ms ({len(lat)} ops)"


def test_observation_cycle_is_deterministic_and_under_100ms(tmp_path):
    """Same world twice → identical observation ids; whole cycle < 100 ms."""
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    spec = ObjectiveSpec.from_dict(_raw_spec())
    store = ObjectiveStore(tmp_path / "objectives.sqlite3")
    state = store.create(spec)
    state = store.enroll_sources(state.objective_id, (SOURCE,), owner_id=OWNER,
                                 expected_version=state.version)
    observers = [FileStateObserver(_enrollment(spec), target),
                 DirectoryStateObserver(_enrollment(spec), tmp_path,
                                        max_entries=50)]
    t0 = time.perf_counter()
    first = collect(observers, state=state, max_observations=10, now=100.0)
    second = collect(observers, state=state, max_observations=10, now=100.0)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert [o.observation_id for o in first.observations] == \
        [o.observation_id for o in second.observations]
    assert elapsed_ms < OBSERVE_BUDGET_MS, f"observe cycle {elapsed_ms:.1f} ms"


def test_observer_hot_path_contains_no_sleep_placeholders():
    """Observation must never wait on wall-clock sleep to 'settle'."""
    src = (Path(__file__).resolve().parents[1] / "bossman_shared" /
           "objective_observer.py").read_text(encoding="utf-8")
    assert "sleep(" not in src, "observer hot path must not sleep"


def test_crash_recovery_reaches_known_good_state_under_2s(tmp_path):
    """Killed process → reopen store → recover() parks the unknown effect."""
    db = tmp_path / "objectives.sqlite3"
    store = ObjectiveStore(db)
    spec = ObjectiveSpec.from_dict(_raw_spec())
    state = store.create(spec)
    state = store.transition(state.objective_id, "ACTIVE", now=100.0,
                             owner_id=OWNER, expected_version=state.version)
    store.reserve_once(reservation_id="r-1", objective_id=state.objective_id,
                       proposal_id="p-1", created_at=101.0,
                       payload={"effect_class": "IRREVERSIBLE", "wall_seconds": 0.5})
    del store  # процесс умер

    t0 = time.perf_counter()
    reopened = ObjectiveStore(db)  # рестарт: тот же durable-файл
    report = recover(reopened, state.objective_id, now=200.0,
                     is_effect_applied=lambda reservation: "UNKNOWN")
    elapsed = time.perf_counter() - t0
    assert elapsed < RECOVER_BUDGET_S, f"recovery {elapsed:.2f} s over budget"
    assert len(report.outcomes) == 1
    # необратимый эффект с неизвестным исходом — PARKED, решает владелец,
    # повторного исполнения нет
    assert report.outcomes[0].disposition == "PARKED"
    assert report.outcomes[0].requires_owner is True
    assert reopened.open_reservations(state.objective_id)[0]["reservation_id"] == "r-1"
