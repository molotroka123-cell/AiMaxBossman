"""The persistent runtime.

A clinic camera service is not a sequence of one-off jobs: it runs for weeks.
What is proven here is the long-lived loop itself — that it starts, that it
stops, that a lost camera or a lost network does not end it, that the retry
delay is bounded, and that it never spins.
"""

from __future__ import annotations

import asyncio

import pytest

from ai_webcam_vision.config import Settings
from ai_webcam_vision.errors import CaptureError
from ai_webcam_vision.runtime.service import VisionService
from ai_webcam_vision.runtime.supervisor import RuntimeState, RuntimeSupervisor
from ai_webcam_vision.transport.base import SourceDescriptor, SourceKind
from ai_webcam_vision.transport.mock import FaultScript, SyntheticFrameSource, SyntheticScene


def recording_sleeper(delays: list[float]):
    async def sleeper(delay: float) -> None:
        delays.append(delay)
        await asyncio.sleep(0)

    return sleeper


async def build(settings: Settings, source, delays: list[float]) -> tuple[VisionService, RuntimeSupervisor]:
    service = VisionService(settings, source=source, sleep=recording_sleeper([]))
    service.baseline.save(await SyntheticFrameSource(scene=SyntheticScene()).grab())
    supervisor = RuntimeSupervisor(service, sleep=recording_sleeper(delays))
    return service, supervisor


# ---------------------------------------------------------------- lifecycle
async def test_runtime_loop_runs_and_stops_cleanly(settings):
    before = {t for t in asyncio.all_tasks() if not t.done()}
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    delays: list[float] = []
    service, supervisor = await build(settings, source, delays)
    try:
        await supervisor.start()
        assert await supervisor.wait_for_cycles(3, timeout=5.0)
        assert supervisor.state is RuntimeState.RUNNING
        await supervisor.stop()
        assert supervisor.state is RuntimeState.STOPPED
        assert supervisor.cycles >= 3
        assert service.counters.observations_stored >= 3
    finally:
        await supervisor.stop()
        await service.aclose()
    await asyncio.sleep(0)
    leaked = {t for t in asyncio.all_tasks() if not t.done()} - before
    assert not leaked, [t.get_name() for t in leaked]


async def test_stop_is_idempotent(settings):
    source = SyntheticFrameSource()
    delays: list[float] = []
    service, supervisor = await build(settings, source, delays)
    try:
        await supervisor.start()
        await supervisor.wait_for_cycles(1, timeout=5.0)
        await supervisor.stop()
        await supervisor.stop()
        assert supervisor.state is RuntimeState.STOPPED
    finally:
        await service.aclose()


async def test_starting_twice_does_not_create_a_second_loop(settings):
    source = SyntheticFrameSource()
    delays: list[float] = []
    service, supervisor = await build(settings, source, delays)
    try:
        await supervisor.start()
        await supervisor.start()
        await supervisor.wait_for_cycles(2, timeout=5.0)
        assert supervisor.loops_started == 1
    finally:
        await supervisor.stop()
        await service.aclose()


# ----------------------------------------------------------------- recovery
async def test_runtime_recovers_after_the_camera_disappears(settings):
    """Camera gone for a while, then back. The loop must survive and resume."""
    script = FaultScript(steps=["ok"] + ["fail"] * 12 + ["ok", "ok"], default="ok")
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True), script=script)
    delays: list[float] = []
    service, supervisor = await build(settings, source, delays)
    try:
        await supervisor.start()
        assert await supervisor.wait_for_state(RuntimeState.RECOVERING, timeout=5.0)
        assert supervisor.consecutive_failures >= 1
        assert await supervisor.wait_for_state(RuntimeState.RUNNING, timeout=10.0)
        assert supervisor.consecutive_failures == 0
        assert supervisor.recoveries >= 1
        assert service.counters.observations_stored >= 1
    finally:
        await supervisor.stop()
        await service.aclose()


async def test_reconnect_backoff_is_bounded_and_capped(settings):
    """Bounded backoff: growing, then flat at the cap, and never above it."""
    tuned = settings.with_overrides(
        retry=type(settings.retry)(max_attempts=1, base_delay=0.5, factor=2.0, max_delay=4.0)
    )
    source = SyntheticFrameSource(script=FaultScript(steps=[], default="fail"))
    delays: list[float] = []
    service, supervisor = await build(tuned, source, delays)
    try:
        await supervisor.start()
        assert await supervisor.wait_for_cycles(8, timeout=10.0)
        await supervisor.stop()
    finally:
        await service.aclose()

    failure_delays = supervisor.recorded_delays
    assert failure_delays, "the loop must wait between reconnect attempts"
    assert max(failure_delays) <= 4.0, failure_delays
    assert failure_delays[0] < failure_delays[-1] or failure_delays[-1] == 4.0
    assert failure_delays[-1] == pytest.approx(4.0)


async def test_runtime_never_busy_loops(settings):
    """Every cycle waits, on the success path and on every failure path."""
    source = SyntheticFrameSource(script=FaultScript(steps=["ok", "fail", "ok", "fail"], default="fail"))
    delays: list[float] = []
    service, supervisor = await build(settings, source, delays)
    try:
        await supervisor.start()
        assert await supervisor.wait_for_cycles(6, timeout=10.0)
        await supervisor.stop()
    finally:
        await service.aclose()

    assert len(delays) >= supervisor.cycles - 1
    assert min(delays) >= supervisor.min_cycle_seconds > 0.0, delays


async def test_missing_baseline_does_not_spin_the_loop(settings):
    """A detector that is not ready must back off, not hammer the camera."""
    source = SyntheticFrameSource()
    service = VisionService(settings, source=source)
    delays: list[float] = []
    supervisor = RuntimeSupervisor(service, sleep=recording_sleeper(delays))
    try:
        await supervisor.start()
        assert await supervisor.wait_for_cycles(3, timeout=5.0)
        assert supervisor.state is RuntimeState.RECOVERING
        assert min(delays) >= supervisor.min_cycle_seconds
        assert service.health()["components"]["detector"]["state"] == "unavailable"
    finally:
        await supervisor.stop()
        await service.aclose()


async def test_unexpected_error_does_not_kill_the_loop(settings):
    class Exploding(SyntheticFrameSource):
        async def grab(self):
            raise RuntimeError("something nobody planned for")

    source = Exploding()
    delays: list[float] = []
    service, supervisor = await build(settings, source, delays)
    try:
        await supervisor.start()
        assert await supervisor.wait_for_cycles(3, timeout=5.0)
        assert supervisor.unexpected_errors >= 1
        assert supervisor.state is RuntimeState.RECOVERING
        assert supervisor.running is True
    finally:
        await supervisor.stop()
        await service.aclose()


# ------------------------------------------------------- real network loss
class SwitchingSource:
    """Real ffmpeg against a refused RTSP port, then against a real fixture.

    This is network loss as ffmpeg actually reports it, not an injected
    exception: connection refused, then a working input.
    """

    def __init__(self, broken, working) -> None:
        self._broken = broken
        self._working = working
        self.use_working = False
        self.descriptor: SourceDescriptor = broken.descriptor

    async def probe(self):
        return await (self._working if self.use_working else self._broken).probe()

    async def grab(self):
        target = self._working if self.use_working else self._broken
        self.descriptor = target.descriptor
        return await target.grab()

    async def grab_snapshot_jpeg(self, max_width: int, blur_sigma: float) -> bytes:
        raise CaptureError("not used in this test")

    async def aclose(self) -> None:
        await self._broken.aclose()
        await self._working.aclose()


async def test_runtime_recovers_after_real_network_loss(tmp_path, ffmpeg_path, video_fixture):
    """REAL transport: refused RTSP endpoint, then a decodable input."""
    from conftest import closed_port

    from ai_webcam_vision.transport import build_source

    common = {
        "AWV_STATE_DIR": str(tmp_path / "state"),
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_CONNECT_TIMEOUT_SECONDS": "2",
        "AWV_CAPTURE_TIMEOUT_SECONDS": "5",
        "AWV_RETRY_MAX_ATTEMPTS": "1",
        "AWV_RETRY_BASE_DELAY_SECONDS": "0",
        "AWV_ACTIVE_INTERVAL_SECONDS": "0.01",
        "AWV_IDLE_INTERVAL_SECONDS": "0.01",
        "AWV_MAX_SAMPLE_RATE_HZ": "1000",
    }
    broken = build_source(Settings.from_env({
        **common,
        "AWV_CAMERA_MODE": "rtsp",
        "AWV_CAMERA_HOST": "127.0.0.1",
        "AWV_CAMERA_PORT": str(closed_port()),
        "AWV_CAMERA_USERNAME": "u",
    }))
    settings = Settings.from_env({
        **common,
        "AWV_CAMERA_MODE": "file",
        "AWV_CAMERA_FIXTURE": str(video_fixture),
    })
    working = build_source(settings)

    source = SwitchingSource(broken, working)
    service = VisionService(settings, source=source)
    service.baseline.save(await working.grab())
    delays: list[float] = []
    supervisor = RuntimeSupervisor(service, sleep=recording_sleeper(delays))
    try:
        await supervisor.start()
        assert await supervisor.wait_for_state(RuntimeState.RECOVERING, timeout=30.0)
        assert service.health()["components"]["camera"]["state"] == "offline"

        source.use_working = True
        assert await supervisor.wait_for_state(RuntimeState.RUNNING, timeout=30.0)
        assert service.counters.observations_stored >= 1
        assert service.health()["components"]["camera"]["state"] == "ok"
    finally:
        await supervisor.stop()
        await service.aclose()
