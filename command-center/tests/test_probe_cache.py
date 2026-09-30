"""bcc.probe_cache: fresh hits, stale-while-revalidate, single flight, no cached failures, off switch."""
from __future__ import annotations

import asyncio

import pytest

from bcc.probe_cache import ProbeCache


@pytest.fixture(autouse=True)
def _cache_on(monkeypatch):
    monkeypatch.delenv("BCC_PROBE_CACHE", raising=False)


class Counter:
    def __init__(self, delay=0.0, fail=False):
        self.calls, self.delay, self.fail = 0, delay, fail

    async def __call__(self):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("probe failed")
        return {"n": self.calls}


def test_a_fresh_answer_is_served_without_running_the_probe_again():
    async def scenario():
        now = [0.0]
        cache, probe = ProbeCache(clock=lambda: now[0]), Counter()
        assert await cache.get("k", probe, ttl=10) == {"n": 1}
        now[0] = 5.0
        assert await cache.get("k", probe, ttl=10) == {"n": 1}
        assert probe.calls == 1
    asyncio.run(scenario())


def test_a_stale_answer_is_returned_at_once_and_refreshed_in_the_background():
    async def scenario():
        now = [0.0]
        cache, probe = ProbeCache(clock=lambda: now[0]), Counter(delay=0.05)
        await cache.get("k", probe, ttl=10, stale_ttl=60)
        now[0] = 30.0
        started = asyncio.get_running_loop().time()
        assert await cache.get("k", probe, ttl=10, stale_ttl=60) == {"n": 1}      # stale value, no waiting
        assert asyncio.get_running_loop().time() - started < 0.04
        await asyncio.sleep(0.15)
        assert probe.calls == 2 and await cache.get("k", probe, ttl=10, stale_ttl=60) == {"n": 2}
    asyncio.run(scenario())


def test_concurrent_callers_share_one_probe():
    async def scenario():
        cache, probe = ProbeCache(), Counter(delay=0.05)
        results = await asyncio.gather(*(cache.get("k", probe) for _ in range(8)))
        assert probe.calls == 1 and all(r == {"n": 1} for r in results)
    asyncio.run(scenario())


def test_failures_are_not_cached():
    async def scenario():
        cache, probe = ProbeCache(), Counter(fail=True)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cache.get("k", probe)
        assert probe.calls == 2
        probe.fail = False
        assert await cache.get("k", probe) == {"n": 3}
    asyncio.run(scenario())


def test_callers_get_copies_so_one_cannot_change_the_answer_of_another():
    async def scenario():
        cache, probe = ProbeCache(), Counter()
        first = await cache.get("k", probe)
        first["n"] = 99
        assert await cache.get("k", probe) == {"n": 1}
    asyncio.run(scenario())


def test_the_off_switch_runs_the_probe_every_time(monkeypatch):
    monkeypatch.setenv("BCC_PROBE_CACHE", "off")

    async def scenario():
        cache, probe = ProbeCache(), Counter()
        await cache.get("k", probe)
        await cache.get("k", probe)
        assert probe.calls == 2
    asyncio.run(scenario())
