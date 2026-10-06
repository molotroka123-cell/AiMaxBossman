"""Short-lived cache for slow "is that service alive" probes (stale-while-revalidate, single flight).

Several read-only health endpoints wait on a sidecar, a socket or a subprocess and take 1-7 s each when the
service is down; the UI calls them on every page open. The answer is stable for seconds, so:

* fresh (younger than ``ttl``)            -> returned at once;
* stale but younger than ``stale_ttl``    -> returned at once and refreshed in the background;
* older or missing                         -> computed once, however many callers wait (single flight).

Failures are never cached (the exception reaches the caller). ``BCC_PROBE_CACHE=off`` switches the cache off
(tests use it so every call measures the mocked state). The cache lives on the service object, so two apps
never share answers.
"""
from __future__ import annotations

import asyncio
import copy
import os
import time
from typing import Any, Awaitable, Callable

from .single_flight import await_shared

DEFAULT_TTL_S = 15.0
DEFAULT_STALE_TTL_S = 90.0


def enabled() -> bool:
    return os.environ.get("BCC_PROBE_CACHE", "").strip().lower() not in {"off", "0", "false", "no"}


class ProbeCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[float, Any]] = {}
        self._inflight: dict[str, asyncio.Future] = {}

    def _launch(self, key: str, factory: Callable[[], Awaitable[Any]]) -> asyncio.Future:
        task = self._inflight.get(key)
        if task is not None and not task.done():
            return task
        task = asyncio.ensure_future(factory())
        self._inflight[key] = task

        def _done(finished: asyncio.Future, key: str = key) -> None:
            if self._inflight.get(key) is finished:
                self._inflight.pop(key, None)
            if finished.cancelled():
                return
            if finished.exception() is None:            # retrieve failures: nobody may be waiting any more
                self._entries[key] = (self._clock(), finished.result())

        task.add_done_callback(_done)
        return task

    async def get(self, key: str, factory: Callable[[], Awaitable[Any]], *, ttl: float = DEFAULT_TTL_S,
                  stale_ttl: float = DEFAULT_STALE_TTL_S) -> Any:
        if not enabled():
            return await factory()
        entry = self._entries.get(key)
        if entry is not None:
            age = self._clock() - entry[0]
            if age <= ttl:
                return copy.deepcopy(entry[1])
            if age <= stale_ttl:
                self._launch(key, factory)              # refresh in the background, answer from memory now
                return copy.deepcopy(entry[1])
        return copy.deepcopy(await await_shared(self._launch(key, factory)))

    def clear(self) -> None:
        self._entries.clear()


def probes(svc: Any) -> ProbeCache:
    cache = getattr(svc, "_probe_cache", None)
    if cache is None:
        cache = ProbeCache()
        svc._probe_cache = cache
    return cache
