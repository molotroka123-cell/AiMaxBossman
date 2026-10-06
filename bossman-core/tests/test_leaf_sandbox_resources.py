"""authored_by_lane (opsplug): bossman.sandbox.resources - sandbox leases on the one Resource Brain."""
import pytest

from bossman import errors
from bossman.resource_brain import ResourceBrain, ResourceSnapshot
from bossman.sandbox.models import ResourceRequest, SandboxSession, SandboxSpec
from bossman.sandbox.resources import ResourceLeaseAdapter


def _session(sid="sbx_a", ram=100, disk=10):
    return SandboxSession(id=sid, spec=SandboxSpec(task="t", resources=ResourceRequest(ram_bytes=ram, disk_bytes=disk)))


def _brain(ram_total=1000, ram_avail=1000):
    b = ResourceBrain(max_ram_pressure=0.9, disk_reserve=0)
    b.set_snapshot(ResourceSnapshot(ram_total, ram_avail, 100_000, 100_000))
    return b


def test_reserve_is_idempotent_per_sandbox_and_release_is_safe_twice():
    brain = _brain()
    ad = ResourceLeaseAdapter(brain)
    s = _session()
    lease = ad.reserve(s)
    assert ad.reserve(s) == lease and s.lease_id == lease and len(brain.leases()) == 1
    assert brain.held()[0] == 100
    assert ad.release(s) is True
    assert ad.release(s) is False                       # double release is harmless
    assert brain.held() == (0, 0) and ad.active() == {}


def test_reserve_refuses_when_the_pool_is_exhausted_and_holds_nothing_extra():
    brain = _brain(ram_total=1000, ram_avail=250)
    ad = ResourceLeaseAdapter(brain)
    ad.reserve(_session("sbx_1", ram=100))
    with pytest.raises(errors.ResourceExhausted):
        ad.reserve(_session("sbx_2", ram=100))          # the first lease already holds the capacity
    assert list(ad.active()) == ["sbx_1"]


def test_release_without_a_lease_returns_false_instead_of_raising():
    assert ResourceLeaseAdapter(_brain()).release(_session("sbx_none")) is False


def test_no_snapshot_means_refusal_not_blind_admission():
    ad = ResourceLeaseAdapter(ResourceBrain())
    with pytest.raises(errors.ResourceExhausted):
        ad.reserve(_session())
