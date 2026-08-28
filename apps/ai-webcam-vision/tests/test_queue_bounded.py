"""The frame buffer must not grow. This is the memory-safety proof."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai_webcam_vision.pipeline.frames import BoundedFrameQueue
from ai_webcam_vision.transport.base import Frame, SourceKind


def make_frame(seq: int, width: int = 160, height: int = 90) -> Frame:
    return Frame(
        seq=seq,
        ts=datetime.now(timezone.utc),
        width=width,
        height=height,
        data=bytes(width * height),
        source_kind=SourceKind.SYNTHETIC,
    )


def test_queue_rejects_zero_capacity():
    with pytest.raises(ValueError):
        BoundedFrameQueue(0)


def test_memory_does_not_grow_with_a_fast_producer():
    capacity = 8
    queue = BoundedFrameQueue(capacity)
    frame_bytes = make_frame(0).nbytes

    for seq in range(10_000):
        queue.put(make_frame(seq))
        assert len(queue) <= capacity
        assert queue.bytes_retained <= capacity * frame_bytes

    stats = queue.stats()
    assert stats.size == capacity
    assert stats.accepted == 10_000
    assert stats.dropped_oldest == 10_000 - capacity
    assert stats.high_water_mark == capacity
    assert stats.bytes_retained == capacity * frame_bytes


def test_oldest_frames_are_the_ones_dropped():
    queue = BoundedFrameQueue(3)
    for seq in range(6):
        queue.put(make_frame(seq))
    remaining = [queue.get().seq for _ in range(3)]
    assert remaining == [3, 4, 5]
    assert queue.get() is None


def test_byte_budget_bounds_large_frames():
    frame_bytes = make_frame(0).nbytes
    queue = BoundedFrameQueue(1000, max_bytes=frame_bytes * 4)
    for seq in range(500):
        queue.put(make_frame(seq))
    assert queue.bytes_retained <= frame_bytes * 4
    assert len(queue) <= 4


def test_latest_discards_stale_frames():
    queue = BoundedFrameQueue(8)
    for seq in range(5):
        queue.put(make_frame(seq))
    newest = queue.latest()
    assert newest.seq == 4
    assert len(queue) == 0
    assert queue.bytes_retained == 0
    assert queue.latest() is None


async def test_observe_loop_keeps_the_queue_bounded(settings):
    """A producer far faster than the consumer must not accumulate frames."""
    from ai_webcam_vision.runtime.service import VisionService
    from ai_webcam_vision.transport.mock import SyntheticFrameSource, SyntheticScene

    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    tight = settings.with_overrides(frame_queue_max=4)
    service = VisionService(tight, source=source)
    try:
        baseline_frame = await source.grab()
        service.baseline.save(baseline_frame)

        # Producer never sleeps; consumer does real work per frame.
        async def no_sleep(_delay: float) -> None:
            return None

        service._sleep = no_sleep
        result = await service.observe(duration=0.4, max_samples=15)

        stats = service.queue.stats()
        assert stats.high_water_mark <= 4
        assert stats.size <= 4
        assert result.samples >= 1
        assert stats.accepted >= result.samples
    finally:
        await service.aclose()
