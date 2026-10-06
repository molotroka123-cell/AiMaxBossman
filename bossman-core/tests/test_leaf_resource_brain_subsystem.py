"""authored_by_lane (opsplug): bossman.resource_brain.subsystem - the probe loop lifecycle."""
import asyncio

from bossman import events
from bossman.resource_brain.brain import ResourceBrain
from bossman.resource_brain.models import ResourceSnapshot, WorkloadRequest
from bossman.resource_brain.subsystem import ResourceBrainSubsystem


class _Probe:
    name = "stub"

    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def snapshot(self, model_resident=()):
        self.calls += 1
        return ResourceSnapshot(1000, 800, 10_000, 9_000, probe="stub", model_resident=tuple(model_resident))


class _BrokenProbe(_Probe):
    def snapshot(self, model_resident=()):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("probe exploded")
        return super().snapshot(model_resident)


def test_contract_flags_a_failed_probe_must_not_stop_the_core():
    sub = ResourceBrainSubsystem(ResourceBrain(), _Probe())
    assert sub.name == "resource_brain" and sub.critical is False


async def test_validate_puts_a_snapshot_into_the_brain():
    brain = ResourceBrain()
    await ResourceBrainSubsystem(brain, _Probe()).validate()
    assert brain.current_snapshot is not None and brain.current_snapshot.probe == "stub"


async def test_loop_publishes_snapshots_sweeps_and_survives_a_probe_error():
    brain = ResourceBrain(max_ram_pressure=0.95, disk_reserve=0, default_lease_ttl=0.05)
    probe = _BrokenProbe()
    sub = ResourceBrainSubsystem(brain, probe, interval=0.02)
    brain.set_snapshot(ResourceSnapshot(1000, 900, 10_000, 9_000))
    brain.acquire(WorkloadRequest(estimated_ram=10))               # short ttl: the loop must sweep it
    queue = events.subscribe()
    try:
        await sub.start()
        await sub.start()                                          # idempotent: still one task
        for _ in range(100):
            if probe.calls >= 3 and not brain.leases():
                break
            await asyncio.sleep(0.02)
        assert probe.calls >= 3, "loop died on the first probe error"
        assert brain.current_snapshot.probe == "stub" and brain.leases() == []
        seen = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        assert any("resource.snapshot" in str(x) for x in seen)
    finally:
        events.unsubscribe(queue)
        await sub.stop()
    await sub.stop()                                                # second stop is harmless
    n = probe.calls
    await asyncio.sleep(0.1)
    assert probe.calls == n                                         # really stopped
