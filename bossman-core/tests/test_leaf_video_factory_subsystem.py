"""authored_by_lane (opsplug): bossman.video_factory.subsystem - degrades without ffmpeg, never orphans leases."""
import asyncio

import pytest

from bossman import errors
from bossman.resource_brain import ResourceBrain, ResourceSnapshot
from bossman.video_factory import VideoFactoryService, subsystem
from bossman.video_factory.model import JobState


def _service(tmp_path, **kw):
    brain = ResourceBrain(disk_reserve=0, max_ram_pressure=0.999)
    brain.set_snapshot(ResourceSnapshot(10 ** 12, 10 ** 12, 10 ** 12, 10 ** 12))
    return VideoFactoryService(tmp_path / "jobs", brain=brain, workers=2, **kw)


def test_contract_not_critical_so_the_core_boots_without_video():
    sub = subsystem.VideoFactorySubsystem(VideoFactoryService())
    assert sub.name == "video_factory" and sub.critical is False and sub.degraded_reason is None


async def test_validate_without_ffmpeg_marks_degraded_and_raises_a_typed_error(tmp_path, monkeypatch):
    monkeypatch.setattr(subsystem, "ffmpeg_available", lambda: False)     # environment probe, not the unit
    sub = subsystem.VideoFactorySubsystem(_service(tmp_path))
    with pytest.raises(errors.VideoProviderFailed, match="ffmpeg"):
        await sub.validate()
    assert sub.degraded_reason == "ffmpeg binary not available"


async def test_validate_with_ffmpeg_creates_the_job_root(tmp_path, monkeypatch):
    monkeypatch.setattr(subsystem, "ffmpeg_available", lambda: True)
    svc = _service(tmp_path)
    await subsystem.VideoFactorySubsystem(svc).validate()
    assert (tmp_path / "jobs").is_dir()


async def test_start_reconciles_interrupted_jobs_runs_workers_and_stop_releases_everything(tmp_path):
    svc = _service(tmp_path)
    job = svc.factory.create("crashed", ["p1"])
    job.state = JobState.RUNNING                            # a crash left it RUNNING with a running scene
    job.scenes[0].status = "running"
    svc.factory.save(job)
    sub = subsystem.VideoFactorySubsystem(svc)
    await sub.start()
    try:
        reloaded = svc.factory.load(job.id)
        assert reloaded.state == JobState.INTERRUPTED and reloaded.scenes[0].status == "planned"
        assert len(svc._workers) == 2 and all(not t.done() for t in svc._workers)
        await sub.start()                                   # idempotent: no extra workers
        assert len(svc._workers) == 2
    finally:
        await sub.stop()
    assert svc._workers == [] and svc._running is False
    assert svc._brain.leases() == []                       # no orphaned capacity reservation
    await sub.stop()
