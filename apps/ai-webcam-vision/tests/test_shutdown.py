"""Clean shutdown: nothing keeps running after the service is closed."""

from __future__ import annotations

import asyncio

import pytest

from ai_webcam_vision.runtime.jobs import JobStatus
from ai_webcam_vision.runtime.service import VisionService
from ai_webcam_vision.transport.mock import FaultScript, SyntheticFrameSource, SyntheticScene


async def test_close_is_idempotent_and_closes_the_source(settings):
    source = SyntheticFrameSource()
    service = VisionService(settings, source=source)
    await service.start()
    await service.aclose()
    await service.aclose()
    assert source.closed is True
    assert service.stopping is True


async def test_running_job_is_cancelled_on_shutdown(settings):
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source)
    baseline = await source.grab()
    service.baseline.save(baseline)

    job = service.jobs.create("observe", {"duration_seconds": 30})
    await asyncio.sleep(0.1)
    assert job.status is JobStatus.RUNNING

    await service.aclose()
    assert job.status is JobStatus.CANCELLED
    assert job.finished_at is not None


async def test_no_stray_tasks_remain_after_shutdown(settings):
    before = {t for t in asyncio.all_tasks() if not t.done()}
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source)
    service.baseline.save(await source.grab())
    service.jobs.create("observe", {"duration_seconds": 30})
    service.jobs.create("sample")
    await asyncio.sleep(0.1)
    await service.aclose()
    await asyncio.sleep(0.05)

    after = {t for t in asyncio.all_tasks() if not t.done()}
    leaked = after - before
    assert not leaked, f"tasks survived shutdown: {[t.get_name() for t in leaked]}"


async def test_cancel_endpointless_job_via_manager(settings):
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source)
    service.baseline.save(await source.grab())
    try:
        job = service.jobs.create("observe", {"duration_seconds": 30})
        await asyncio.sleep(0.05)
        assert await service.jobs.cancel(job.id) is True
        assert job.status is JobStatus.CANCELLED
        assert await service.jobs.cancel(job.id) is False
    finally:
        await service.aclose()


async def test_failed_job_records_a_scrubbed_error(settings):
    async def sleeper(_delay: float) -> None:
        return None

    source = SyntheticFrameSource(script=FaultScript(steps=[], default="fail"))
    service = VisionService(settings, source=source, sleep=sleeper)
    try:
        job = service.jobs.create("sample")
        for _ in range(200):
            if job.status in {JobStatus.FAILED, JobStatus.SUCCEEDED}:
                break
            await asyncio.sleep(0.01)
        assert job.status is JobStatus.FAILED
        assert job.error_code == "capture_failed"
        assert "injected transport failure" in job.error
    finally:
        await service.aclose()


async def test_ffmpeg_child_does_not_survive_service_shutdown(settings, ffmpeg_path, tmp_path):
    """A long-running capture is killed when the job is cancelled."""
    psutil = pytest.importorskip("psutil")

    from ai_webcam_vision.config import Settings
    from ai_webcam_vision.transport.ffmpeg import FfmpegRunner

    runner = FfmpegRunner(ffmpeg_path)

    async def endless():
        await runner.run(
            ["-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "testsrc=size=320x180:rate=25", "-f", "null", "-"],
            timeout=60,
        )

    task = asyncio.create_task(endless())
    await asyncio.sleep(0.6)
    children = psutil.Process().children(recursive=True)
    assert children, "ffmpeg did not start; the assertion below would be vacuous"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)

    alive = [c for c in psutil.Process().children(recursive=True)
             if c.is_running() and c.status() != psutil.STATUS_ZOMBIE]
    assert not alive, f"ffmpeg survived cancellation: {alive}"
