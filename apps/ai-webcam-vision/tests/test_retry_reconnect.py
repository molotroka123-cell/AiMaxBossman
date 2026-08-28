"""Reconnect behaviour and fault injection."""

from __future__ import annotations

import asyncio

import pytest

from ai_webcam_vision.config import RetryConfig
from ai_webcam_vision.errors import CaptureError, DependencyMissing
from ai_webcam_vision.transport.mock import FaultScript, SyntheticFrameSource, SyntheticScene
from ai_webcam_vision.transport.retry import RetryStats, backoff_delays, with_retry


def test_backoff_is_exponential_and_capped():
    config = RetryConfig(max_attempts=6, base_delay=0.5, factor=2.0, max_delay=4.0)
    assert backoff_delays(config) == [0.5, 1.0, 2.0, 4.0, 4.0]


async def test_retry_recovers_after_injected_failures():
    slept: list[float] = []

    async def sleeper(delay: float) -> None:
        slept.append(delay)

    source = SyntheticFrameSource(script=FaultScript(steps=["fail", "fail", "ok"]))
    stats = RetryStats()
    frame = await with_retry(
        source.grab,
        RetryConfig(max_attempts=5, base_delay=0.1, factor=3.0, max_delay=10.0),
        sleep=sleeper,
        stats=stats,
    )
    assert frame.seq == 1, "only the successful grab produces a frame"
    assert stats.attempts == 3
    assert slept == pytest.approx([0.1, 0.3])


async def test_retry_budget_is_bounded():
    attempts = 0

    async def always_failing():
        nonlocal attempts
        attempts += 1
        raise CaptureError("still down")

    async def sleeper(_delay: float) -> None:
        return None

    with pytest.raises(CaptureError):
        await with_retry(always_failing, RetryConfig(max_attempts=4, base_delay=0), sleep=sleeper)
    assert attempts == 4, "the retry budget must be respected exactly"


async def test_missing_dependency_is_not_retried():
    attempts = 0

    async def failing():
        nonlocal attempts
        attempts += 1
        raise DependencyMissing("ffmpeg is not installed")

    with pytest.raises(DependencyMissing):
        await with_retry(failing, RetryConfig(max_attempts=5, base_delay=0))
    assert attempts == 1, "retrying will not install a missing binary"


async def test_cancellation_beats_reconnection():
    """Shutdown must win over an in-progress backoff sleep."""

    async def failing():
        raise CaptureError("down")

    task = asyncio.create_task(
        with_retry(failing, RetryConfig(max_attempts=100, base_delay=5.0, factor=1.0, max_delay=5.0))
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_service_counts_reconnects_and_recovers(settings):
    from ai_webcam_vision.runtime.service import VisionService

    async def sleeper(_delay: float) -> None:
        return None

    source = SyntheticFrameSource(
        scene=SyntheticScene(),
        script=FaultScript(steps=["ok", "fail", "ok"]),
    )
    service = VisionService(settings, source=source, sleep=sleeper)
    try:
        first = await service._grab_with_retry()
        assert first.seq == 1
        assert service.source_health.state == "ok"

        second = await service._grab_with_retry()
        assert second.seq == 2
        assert service.counters.reconnects == 1
        assert service.counters.capture_failures == 1
        assert service.source_health.consecutive_failures == 0
        assert service.source_health.state == "ok"
    finally:
        await service.aclose()


async def test_service_marks_source_unavailable_when_budget_is_exhausted(settings):
    from ai_webcam_vision.runtime.service import VisionService

    async def sleeper(_delay: float) -> None:
        return None

    source = SyntheticFrameSource(script=FaultScript(steps=[], default="fail"))
    service = VisionService(settings, source=source, sleep=sleeper)
    try:
        with pytest.raises(CaptureError):
            await service._grab_with_retry()
        assert service.source_health.state == "unavailable"
        assert service.counters.capture_failures >= settings.retry.max_attempts
        health = service.health()
        assert health["status"] in {"degraded", "unavailable"}
        assert any("source unavailable" in blocker for blocker in health["blockers"])
    finally:
        await service.aclose()
