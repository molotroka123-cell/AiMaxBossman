"""Independent release-breaker controls for lease/resource/queue lifetimes."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from bossman_v3.fleet import FleetControlPlane, FleetStore, LeaseManager
from bossman_v3.fleet.models import Heartbeat, NodeState, PlacementRequirement
from bossman_v3.fleet.queue import WorkQueue
from bossman_v3.fleet.scheduler import FleetScheduler
from tests.test_v3_fleet_core import _contract


def node(**kwargs):
    return NodeState("n", ram_gb=16, gpu_memory_gb=8, capabilities={"terminal.run"}, **kwargs)


def test_stale_heartbeat_refuses_placement_without_separate_watchdog_call(tmp_path):
    plane = FleetControlPlane(tmp_path / "fleet.db", heartbeat_timeout_s=10)
    plane.registry.register(node(last_heartbeat_ts=100), now=100)
    assert not plane.place(_contract("stale"), now=111).ok
    plane.registry.heartbeat(Heartbeat("n", 112), now=112)
    assert plane.place(_contract("fresh"), now=112).ok


def test_expired_reacquisition_does_not_accumulate_memory_reservations(tmp_path):
    plane = FleetControlPlane(tmp_path / "fleet.db")
    plane.registry.register(node(), now=100)
    req = PlacementRequirement(min_ram_gb=1)
    for index in range(8):
        plane.leases.acquire(node_id="n", work_id=str(index), resource_class="cpu", exclusive=False,
                             requirement=req, now=100 + index * 2, ttl_seconds=1)
    with plane.store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM fleet_leases").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fleet_memory_reservations").fetchone()[0] == 1


@pytest.mark.parametrize("changes", [{"work_id": "forged"}, {"fence": 999},
                                      {"node_id": "forged"}, {"exclusive": False},
                                      {"resource_class": "forged"}])
def test_release_requires_complete_persisted_lease_identity(tmp_path, changes):
    leases = LeaseManager(FleetStore(tmp_path / "fleet.db"))
    current = leases.acquire(node_id="n", work_id="w", now=100, ttl_seconds=10)
    assert not leases.release(replace(current, **changes))
    assert leases.valid(current, now=101)[0]
    assert leases.release(current)


@pytest.mark.parametrize("field,invalid", [
    (field, invalid)
    for field in ("load", "ram_used_gb", "gpu_memory_used_gb", "active_work")
    for invalid in (-1, float("nan"), float("inf"), None, "nonsense", True)
    if not (field == "active_work" and invalid is None)  # API: None means not reported.
])
def test_invalid_heartbeat_cannot_admit_work(field, invalid, tmp_path):
    plane = FleetControlPlane(tmp_path / "fleet.db")
    plane.registry.register(node(), now=100)
    plane.registry.heartbeat(Heartbeat("n", 101, **{field: invalid}), now=101)
    assert plane.scheduler.reject_reasons(plane.registry.node("n"), PlacementRequirement(min_ram_gb=1))
    plane.registry.heartbeat(Heartbeat("n", 102, active_work=0), now=102)
    assert not plane.scheduler.reject_reasons(plane.registry.node("n"), PlacementRequirement(min_ram_gb=1))


@pytest.mark.parametrize("field", ["ram_gb", "ram_used_gb", "gpu_memory_gb", "gpu_memory_used_gb", "load"])
@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf"), None, "nonsense", True])
def test_raw_invalid_measurement_is_rejected_without_crashing_scheduler(field, invalid):
    observed = node()
    setattr(observed, field, invalid)
    assert FleetScheduler().reject_reasons(observed, PlacementRequirement())


def test_old_claim_cannot_complete_work_after_dead_letter_requeue_and_restart(tmp_path):
    path = tmp_path / "fleet.db"
    q = WorkQueue(FleetStore(path), FleetScheduler(), authorize_requeue=lambda by, work: by == "owner")
    q.enqueue("w", "m", priority=1, requirement=PlacementRequirement())
    stale = q.claim(node(), now=100)
    q.on_failure("w", "m", claim=stale, reason="PolicyDeniedError: denied", now=101)
    assert q.requeue_dead_letter("w", by="owner")
    q = WorkQueue(FleetStore(path), FleetScheduler())
    fresh = q.claim(node(), now=102)
    assert fresh.claim_fence > stale.claim_fence
    assert not q.complete(stale)
    assert q.store.queue()[0]["attempts"] == 2
    assert q.complete(fresh)


def test_completed_work_id_reuse_does_not_reuse_authority(tmp_path):
    q = WorkQueue(FleetStore(tmp_path / "fleet.db"), FleetScheduler())
    q.enqueue("w", "m", priority=1, requirement=PlacementRequirement())
    stale = q.claim(node(), now=100)
    assert q.complete(stale)
    q.enqueue("w", "different-mission", priority=1, requirement=PlacementRequirement())
    fresh = q.claim(node(), now=101)
    assert fresh.claim_fence > stale.claim_fence
    assert not q.complete(stale)
    assert q.complete(fresh)


def test_racing_new_claims_remain_unique_after_requeue(tmp_path):
    path = tmp_path / "fleet.db"
    q = WorkQueue(FleetStore(path), FleetScheduler())
    q.enqueue("w", "m", priority=1, requirement=PlacementRequirement())
    barrier = threading.Barrier(4)
    def claim(_):
        local = WorkQueue(FleetStore(path), FleetScheduler())
        barrier.wait()
        return local.claim(node(), now=100)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, range(4)))
    assert len([claim for claim in claims if claim]) == 1


def test_waiting_for_database_lock_cannot_revive_an_expired_lease(tmp_path, monkeypatch):
    import time
    from bossman_v3.fleet.leases import StaleLease
    store = FleetStore(tmp_path / "fleet.db")
    leases = LeaseManager(store)
    original = leases.acquire(node_id="n", work_id="w", now=time.time(), ttl_seconds=60)
    entered = threading.Event()
    outcome = {}
    connect = store.connect
    def signaled_connect():
        if threading.current_thread().name == "blocked-renewal":
            entered.set()
        return connect()
    monkeypatch.setattr(store, "connect", signaled_connect)
    blocker = connect()
    blocker.execute("BEGIN IMMEDIATE")
    def renew():
        try:
            outcome["renewed"] = leases.renew(original, now=time.time(), ttl_seconds=10)
        except StaleLease as exc:
            outcome["refused"] = str(exc)
    worker = threading.Thread(target=renew, name="blocked-renewal")
    worker.start()
    try:
        assert entered.wait(5)
        # Another writer shortens the same lease while renewal waits. Its new
        # expiry is after the waiting caller's timestamp, but before lock release.
        blocker.execute("UPDATE fleet_leases SET expires_ts=? WHERE lease_id=?",
                        (time.time() + 0.05, original.lease_id))
        time.sleep(0.1)
    finally:
        blocker.execute("COMMIT")
        blocker.close()
        worker.join(5)
    assert not worker.is_alive()
    assert "refused" in outcome and "renewed" not in outcome
    assert not leases.valid(original, now=time.time())[0]


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf"), None, "nonsense", True, 1e30])
def test_invalid_persisted_heartbeat_cannot_bypass_placement_freshness(tmp_path, invalid):
    plane = FleetControlPlane(tmp_path / "fleet.db")
    observed = node(last_heartbeat_ts=100)
    plane.registry.register(observed, now=100)
    observed.last_heartbeat_ts = invalid
    plane.store.save_node(observed)
    assert not plane.place(_contract("invalid-heartbeat"), now=101).ok
    assert plane.store.leases() == []
    plane.registry.heartbeat(Heartbeat("n", 102), now=102)
    assert plane.place(_contract("fresh-heartbeat"), now=102).ok
