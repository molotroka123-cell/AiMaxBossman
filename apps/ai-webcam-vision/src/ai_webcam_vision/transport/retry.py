"""Bounded reconnect with exponential backoff."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from ..config import RetryConfig
from ..errors import CaptureError, DependencyMissing, VisionError

T = TypeVar("T")

Sleeper = Callable[[float], Awaitable[None]]

#: Errors worth retrying. A missing binary is not one of them: retrying will
#: not install ffmpeg, and pretending otherwise wastes the whole budget.
RETRYABLE = (CaptureError,)


@dataclass
class RetryStats:
    attempts: int = 0
    delays: list[float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.delays is None:
            self.delays = []


def backoff_delays(config: RetryConfig) -> list[float]:
    """The delay sequence this configuration will use, for reporting."""
    delays = []
    delay = config.base_delay
    for _ in range(max(0, config.max_attempts - 1)):
        delays.append(min(delay, config.max_delay))
        delay *= config.factor
    return delays


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    config: RetryConfig,
    *,
    sleep: Sleeper | None = None,
    on_retry: Callable[[int, float, VisionError], None] | None = None,
    stats: RetryStats | None = None,
) -> T:
    """Run ``operation`` with bounded retries and exponential backoff.

    Raises the last error once the attempt budget is exhausted. Cancellation is
    never swallowed, so shutdown always wins over reconnection.
    """
    sleeper = sleep or asyncio.sleep
    delay = config.base_delay
    last: VisionError | None = None

    for attempt in range(1, config.max_attempts + 1):
        if stats is not None:
            stats.attempts = attempt
        try:
            return await operation()
        except DependencyMissing:
            raise
        except RETRYABLE as exc:
            last = exc
            if attempt >= config.max_attempts:
                break
            wait = min(delay, config.max_delay)
            if stats is not None:
                stats.delays.append(wait)
            if on_retry is not None:
                on_retry(attempt, wait, exc)
            await sleeper(wait)
            delay *= config.factor

    assert last is not None
    raise last
