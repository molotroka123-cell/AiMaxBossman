"""authored_by_lane (opsplug): bossman.resource_brain.models - the unified-memory data model."""
import pytest

from bossman.resource_brain.models import (
    ModelResidency,
    PressureLevel,
    ResourceLease,
    ResourceSnapshot,
    WorkloadRequest,
)


@pytest.mark.parametrize("p,level", [
    (0.0, PressureLevel.NOMINAL), (0.5999, PressureLevel.NOMINAL),
    (0.60, PressureLevel.ELEVATED), (0.7999, PressureLevel.ELEVATED),
    (0.80, PressureLevel.HIGH), (0.9199, PressureLevel.HIGH),
    (0.92, PressureLevel.CRITICAL), (1.0, PressureLevel.CRITICAL),
])
def test_pressure_levels_include_their_lower_boundary(p, level):
    assert PressureLevel.from_pressure(p) is level


def test_vram_is_a_claim_against_the_one_pool_never_added_to_it():
    s = ResourceSnapshot(1000, 900, 10_000, 5_000, gpu_memory_used=400, gpu_memory_total=9_999)
    assert s.pool_total == 1000                       # gpu_memory_total is reference only
    assert s.unified_available == 600                 # min(900, 1000 - 400): not 900 + anything
    assert s.ram_pressure == pytest.approx(0.4)
    cpu_only = ResourceSnapshot(1000, 900, 10_000, 5_000)
    assert cpu_only.unified_available == 900 and cpu_only.pressure_level is PressureLevel.NOMINAL


def test_degenerate_snapshots_do_not_divide_by_zero_or_go_negative():
    assert ResourceSnapshot(0, 0, 0, 0).ram_pressure == 0.0
    over = ResourceSnapshot(1000, 100, 1, 1, gpu_memory_used=2000)
    assert over.unified_available == 0 and over.pressure_level is PressureLevel.CRITICAL


def test_event_payload_has_numbers_only_and_is_consistent():
    ev = ResourceSnapshot(1000, 500, 10_000, 8_000, model_resident=("a", "b"), probe="stub", ts=1.5).to_event()
    assert ev["ram_pressure"] == 0.5 and ev["pressure_level"] == "nominal" and ev["model_resident"] == ["a", "b"]
    assert all(isinstance(v, (int, float, bool, str, list, type(None))) for v in ev.values())


def test_lease_age_remaining_and_expiry():
    lease = ResourceLease(id="l1", kind="llm", ram=10, disk=1, ttl=30.0, created_at=100.0)
    assert lease.age(110.0) == 10.0 and lease.remaining(110.0) == 20.0
    assert not lease.expired(129.9) and lease.expired(130.0)
    assert lease.age(50.0) == 0.0                      # clock going backwards never gives a negative age
    forever = ResourceLease(id="l2", kind="llm", ram=1, disk=0, ttl=0, created_at=0.0)
    assert not forever.expired(10 ** 9)                # ttl 0 = no expiry
    assert lease.to_public() == {"id": "l1", "kind": "llm", "ram": 10, "disk": 1, "ttl": 30.0, "cid": {}}


def test_model_residency_cost_is_zero_only_when_resident():
    r = ModelResidency(cold_start_cost={"big": 42.0})
    assert r.cost("big") == 42.0 and r.cost("unknown") == 0.0
    r.mark_resident("big")
    assert r.is_resident("big") and r.cost("big") == 0.0 and r.as_tuple() == ("big",)
    r.evict("big")
    r.evict("never-there")                              # evicting an absent model is harmless
    assert not r.is_resident("big")


def test_workload_request_defaults_are_conservative():
    w = WorkloadRequest()
    assert (w.kind, w.estimated_ram, w.estimated_disk, w.priority, w.model) == ("other", 0, 0, 50, None)
