"""Live reply text for the Jeff window (SSE) and Telegram (edit-in-place drafts).

The runtime knows one thing: a per-turn *sink*, an async callable that receives
visible reply text as it arrives and ``None`` when what was shown so far must be
discarded (an attempt failed, was refused or is being retried). The sink is carried
in a ``ContextVar`` so the model call stays a plain ``handle()`` call. The text a
sink receives is a preview: the authoritative reply is always the value ``handle``
returns, which is what gets saved and delivered exactly once.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from contextvars import ContextVar
from typing import Awaitable, Callable

Sink = Callable[["str | None"], Awaitable[None]]

reply_sink: ContextVar[Sink | None] = ContextVar("jeff_reply_sink", default=None)


class TurnStream:
    """Per-turn bookkeeping between a model adapter's ``on_delta`` and the sink."""

    def __init__(self, sink: Sink | None, clock=time.perf_counter):
        self.sink = sink
        self.clock = clock
        self.started = clock()
        self.first_at: float | None = None
        self.shown = False

    async def on_delta(self, text: str | None) -> None:
        if text is None:
            await self.reset()
            return
        if not text:
            return
        if self.first_at is None:
            self.first_at = self.clock()
        self.shown = True
        if self.sink is not None:
            try:
                await self.sink(text)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a broken preview must never break the reply
                self.sink = None

    async def reset(self) -> None:
        """What was shown belongs to a failed/refused attempt: tell the sink to drop it."""
        if not self.shown:
            return
        self.shown = False
        if self.sink is not None:
            with contextlib.suppress(Exception):
                await self.sink(None)

    @property
    def ttft_ms(self) -> float | None:
        return None if self.first_at is None else (self.first_at - self.started) * 1000.0


class EditPacer:
    """Rate limit for progressive edits: at most one edit per ``interval`` seconds and only
    when enough new text arrived; the final edit is never paced away."""

    def __init__(self, interval: float = 1.5, min_chars: int = 24, clock=time.monotonic):
        self.interval, self.min_chars, self.clock = interval, min_chars, clock
        self.last_at = -1e9
        self.last_len = 0

    def due(self, length: int) -> bool:
        return (length - self.last_len >= self.min_chars
                and self.clock() - self.last_at >= self.interval)

    def mark(self, length: int) -> None:
        self.last_at, self.last_len = self.clock(), length
