"""authored_by_lane (opsplug): bossman.resource_brain.brain - admission, leases and model ranking."""
import pytest

from bossman import errors
from bossman.resource_brain.brain import ResourceBrain
from bossman.resource_brain.ledger import LeaseLedger
from bossman.resource_brain.models import ResourceSnapshot, WorkloadRequest


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _snap(ram_total=1000, ram_avail=1000, disk_free=100_000, **kw):
    return ResourceSnapshot(ram_total, ram_avail, 200_000, disk_free, **kw)


def test_admit_checks_disk_reserve_first_then_ram_pressure():
    b = ResourceBrain(max_ram_pressure=0.8, disk_reserve=1000)
    d = b.admit(_snap(disk_free=1500), WorkloadRequest(estimated_ram=1, estimated_disk=600))
    assert (d.allowed, d.reason) == (False, "disk_reserve")
    d = b.admit(_snap(ram_avail=500), WorkloadRequest(estimated_ram=400, estimated_disk=0, model="m"))
    assert (d.allowed, d.reason) == (False, "ram_pressure")
    ok = b.admit(_snap(ram_avail=500), WorkloadRequest(estimated_ram=100, model="m"))
    assert ok.allowed and ok.suggested_model == "m" and ok.pressure == pytest.approx(0.6)


def test_acquire_without_any_snapshot_refuses_instead_of_assuming_free():
    with pytest.raises(errors.ResourceExhausted, match="no resource snapshot"):
        ResourceBrain().acquire(WorkloadRequest(estimated_ram=1))


def test_acquire_reserves_so_a_second_request_against_the_same_snapshot_sees_it():
    b = ResourceBrain(max_ram_pressure=0.9, disk_reserve=0)
    snap = _snap(ram_avail=400)
    first = b.acquire(WorkloadRequest(kind="llm", estimated_ram=250), snap)
    assert b.held()[0] == 250 and [l.id for l in b.leases()] == [first.id]
    with pytest.raises(errors.ResourceExhausted):
        b.acquire(WorkloadRequest(kind="llm", estimated_ram=250), snap)   # would not fit next to the first
    assert b.release(first.id) is True
    assert b.release(first.id) is False                                    # idempotent
    b.acquire(WorkloadRequest(kind="llm", estimated_ram=250), snap)        # fits again after release


def test_sweep_reclaims_expired_leases_and_reports_them():
    clock = _Clock()
    b = ResourceBrain(max_ram_pressure=0.95, disk_reserve=0, default_lease_ttl=10.0, ledger=LeaseLedger(clock=clock))
    b.set_snapshot(_snap())
    lease = b.acquire(WorkloadRequest(estimated_ram=100))
    clock.t = 9.0
    assert b.sweep() == [] and b.held()[0] == 100
    clock.t = 11.0
    assert [l.id for l in b.sweep()] == [lease.id] and b.held() == (0, 0)


def test_pressure_level_follows_the_live_snapshot():
    b = ResourceBrain()
    assert b.pressure_level().value == "nominal"                           # nothing measured yet
    b.set_snapshot(_snap(ram_avail=50))
    assert b.pressure_level().value == "critical"


def test_rank_models_prefers_fit_then_health_then_resident_then_small_then_fast():
    b = ResourceBrain()
    b.residency.mark_resident("warm")
    snap = _snap(ram_avail=500, model_resident=("warm",))
    ranked = b.rank_models(snap, [
        {"id": "too_big", "ram_estimate": 900, "health": "healthy"},
        {"id": "sick", "ram_estimate": 100, "health": "down"},
        {"id": "cold_small", "ram_estimate": 100, "health": "healthy", "latency_ms": 50},
        {"id": "warm", "ram_estimate": 300, "health": "healthy", "latency_ms": 90},
        {"id": "cold_fast", "ram_estimate": 100, "health": "healthy", "latency_ms": 10},
    ])
    assert [m["id"] for m in ranked] == ["warm", "cold_fast", "cold_small", "sick", "too_big"]
