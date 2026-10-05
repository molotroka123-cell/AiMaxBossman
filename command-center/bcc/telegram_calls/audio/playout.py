"""Real-time paced playout queue with barge-in / STOP flush.

Speech synthesis produces audio faster than real time. If it were handed to the call engine at once,
an interruption could not remove it. So the queue holds the audio and releases exactly one 20 ms
frame per 20 ms of wall time. Every chunk carries a *generation*; ``flush`` bumps the generation,
drops everything queued and asks the transport to drop what it buffers, so a late chunk from a
cancelled synthesis is rejected at ``enqueue`` instead of being heard after the interruption.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Awaitable, Callable

from ..types import PLAYOUT_FRAME_MS
from .pcm import FrameSlicer

SendFn = Callable[[bytes], Awaitable[None]]
ClearFn = Callable[[], Awaitable[None]]


class Playout:
    def __init__(self, send: SendFn, clear: ClearFn, *, sample_rate: int, frame_ms: int = PLAYOUT_FRAME_MS,
                 pace: float = 1.0, clock: Callable[[], float] = time.monotonic):
        self._send, self._clear = send, clear
        self.sample_rate, self.frame_ms, self.pace, self._clock = sample_rate, frame_ms, pace, clock
        self.frame_bytes = sample_rate * frame_ms // 1000 * 2
        self.generation = 0
        self._queue: deque[tuple[int, bytes, int | None]] = deque()
        self._slicers: dict[int, FrameSlicer] = {}
        self._pending: dict[int, int] = {}          # gen -> frames queued or in flight
        self._ended: set[int] = set()               # gens whose producer has finished
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._task: asyncio.Task | None = None
        self._closed = False
        # observers (all optional, all must be cheap and non-raising)
        self.on_first_frame: Callable[[int, float], None] | None = None
        self.on_marker_started: Callable[[int, int], None] | None = None   # (gen, marker)
        self.on_frame_sent: Callable[[bytes, float], None] | None = None   # (frame, monotonic time)
        self.on_drained: Callable[[int], None] | None = None
        self._first_sent: set[int] = set()
        self._markers: dict[int, int] = {}          # frame-slicer offset bookkeeping per gen
        self.frames_sent = 0
        self.frames_dropped = 0
        self.underruns = 0

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="calls-playout")

    async def close(self) -> None:
        self._closed = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # ------------------------------------------------------------ producer side
    def enqueue(self, pcm: bytes, gen: int, marker: int | None = None) -> bool:
        """Queue PCM16 at ``sample_rate``. Returns False (and drops) if ``gen`` is stale."""
        if self._closed or gen != self.generation or not pcm:
            return False
        slicer = self._slicers.setdefault(gen, FrameSlicer(self.frame_bytes))
        first = True
        for frame in slicer.push(pcm):
            self._queue.append((gen, frame, marker if first else None))
            first = False
            self._pending[gen] = self._pending.get(gen, 0) + 1
        if marker is not None and first:
            # audio shorter than one frame: the marker rides on the (padded) tail when the gen ends
            self._markers[gen] = marker
        self._idle.clear()
        self._wake.set()
        return True

    def end(self, gen: int) -> None:
        """Producer finished this generation: pad and queue the tail; mark for drain detection."""
        if gen != self.generation:
            return
        slicer = self._slicers.pop(gen, None)
        if slicer is not None and slicer.pending:
            tail = slicer.flush(pad=True)
            self._queue.append((gen, tail, self._markers.pop(gen, None)))
            self._pending[gen] = self._pending.get(gen, 0) + 1
        self._ended.add(gen)
        self._check_drained(gen)
        self._wake.set()

    def invalidate(self) -> int:
        """Synchronous half of a flush: from this instant nothing of the old generation can be sent.

        Barge-in and STOP call this first (no ``await`` in between), so the audible stop happens within
        one frame time; ``flush`` adds the (async) request to the transport to drop what it buffers.
        """
        self.generation += 1
        self.frames_dropped += len(self._queue)
        self._queue.clear()
        self._slicers.clear()
        self._markers.clear()
        self._pending = {g: n for g, n in self._pending.items() if g == self.generation}
        self._ended = {g for g in self._ended if g == self.generation}
        self._idle.set()
        return self.generation

    async def flush(self, reason: str = "flush") -> int:
        """Barge-in / STOP: invalidate everything not yet on the wire. Returns the new generation."""
        gen = self.invalidate()
        try:
            await self._clear()
        except Exception:  # noqa: BLE001 - a transport that cannot clear must not break the flush
            pass
        return gen

    # ------------------------------------------------------------ state
    @property
    def busy(self) -> bool:
        return not self._idle.is_set()

    @property
    def queued_ms(self) -> int:
        return len(self._queue) * self.frame_ms

    async def wait_drained(self, gen: int, timeout: float | None = None) -> bool:
        """Wait until every frame of ``gen`` has been sent (or the generation was flushed)."""
        async def waiter():
            while gen == self.generation and not (gen in self._ended and self._pending.get(gen, 0) == 0):
                await asyncio.sleep(0.005)
        try:
            await asyncio.wait_for(waiter(), timeout)
        except asyncio.TimeoutError:
            return False
        return gen == self.generation

    def _check_drained(self, gen: int) -> None:
        if gen in self._ended and self._pending.get(gen, 0) == 0:
            self._first_sent.discard(gen)
            if self.on_drained:
                self.on_drained(gen)
        if not self._queue:
            self._idle.set()

    # ------------------------------------------------------------ pacing loop
    async def _run(self) -> None:
        step = self.frame_ms / 1000.0
        next_at: float | None = None
        while not self._closed:
            if not self._queue:
                if next_at is not None:
                    self.underruns += 1
                next_at = None
                self._idle.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            gen, frame, marker = self._queue.popleft()
            now = self._clock()
            if next_at is None or next_at < now - 0.1:
                next_at = now                       # (re)start the schedule; never burst to catch up
            if self.pace > 0 and next_at > now:
                await asyncio.sleep((next_at - now) / self.pace)
            if gen != self.generation:              # flushed while we slept
                continue
            try:
                await self._send(frame)
            except Exception:  # noqa: BLE001 - transport failure is reported by the transport's own events
                self._pending[gen] = max(0, self._pending.get(gen, 1) - 1)
                continue
            sent_at = self._clock()
            self.frames_sent += 1
            if gen not in self._first_sent:
                self._first_sent.add(gen)
                if self.on_first_frame:
                    self.on_first_frame(gen, sent_at)
            if marker is not None and self.on_marker_started:
                self.on_marker_started(gen, marker)
            if self.on_frame_sent:
                self.on_frame_sent(frame, sent_at)
            self._pending[gen] = max(0, self._pending.get(gen, 1) - 1)
            self._check_drained(gen)
            next_at += step
            await asyncio.sleep(0)
