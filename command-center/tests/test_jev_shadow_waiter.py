"""Jev shadow: a timed-out job must not unregister the NEXT step's route waiter.

`pick_model` runs once per model step, so one task id gets several shadow jobs.
Job 1 (route never arrived) timed out and its `finally` popped
`state.waiters[task_id]` — which by then was job 2's waiter. Job 2's
`router.route_selected` then landed in `state.routes` as a stale entry, job 2
recorded the agent model as its baseline instead of the routed one, and the
stale route was handed to step 3. Mock-only: fake bus, stubbed baseline/egress.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bcc.features import jev


class _Bus:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()

    def subscribe(self):
        return self.q

    def unsubscribe(self, _q):
        pass

    async def emit(self, kind, **kw):
        pass


def _state():
    st = jev._State.__new__(jev._State)       # no config/provider: shadow call is stubbed
    st.routes, st.waiters, st.jobs, st.dropped = {}, {}, set(), 0
    st.sem = asyncio.Semaphore(2)
    st.recorder = SimpleNamespace(write=lambda rec: None)
    return st


@pytest.fixture
def seen(monkeypatch):
    out: list[tuple] = []

    async def fake_baseline(_svc, model_id, _agent, source):
        out.append((model_id, source))
        return SimpleNamespace()

    async def no_egress(*_a):
        return False                            # job ends right after the baseline

    monkeypatch.setattr(jev, "_baseline", fake_baseline)
    monkeypatch.setattr(jev, "_egress_allowed", no_egress)
    return out


async def test_timed_out_job_keeps_next_steps_waiter(monkeypatch, seen):
    svc = SimpleNamespace(bus=_Bus())
    st = _state()
    consumer = asyncio.create_task(jev._consume(svc, st))
    try:
        loop = asyncio.get_running_loop()
        w1 = loop.create_future()
        st.waiters[7] = w1
        monkeypatch.setattr(jev, "ROUTE_WAIT_S", 0)            # step 1: route never comes
        job1 = asyncio.create_task(jev._shadow_job(svc, st, {"id": 7}, {"model_id": 1}, w1))
        w2 = loop.create_future()
        st.waiters[7] = w2                                     # step 2's hook registered
        await job1
        assert st.waiters.get(7) is w2, "step 1 cleanup removed step 2's waiter"

        monkeypatch.setattr(jev, "ROUTE_WAIT_S", 5.0)
        job2 = asyncio.create_task(jev._shadow_job(svc, st, {"id": 7}, {"model_id": 1}, w2))
        await svc.bus.q.put({"kind": "router.route_selected", "task_id": 7, "model_id": 42})
        await asyncio.wait_for(job2, 1.0)
        assert seen == [(1, "agent_model"), (42, "router")]
        assert 7 not in st.routes, "routed model leaked as a stale entry for the next step"
        assert 7 not in st.waiters
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


async def test_timed_out_job_still_cleans_its_own_waiter(monkeypatch, seen):
    """Control: with no newer step, a timed-out job still unregisters itself."""
    svc = SimpleNamespace(bus=_Bus())
    st = _state()
    w1 = asyncio.get_running_loop().create_future()
    st.waiters[9] = w1
    st.routes[9] = 5                      # late route after timeout is discarded as before
    monkeypatch.setattr(jev, "ROUTE_WAIT_S", 0)
    await jev._shadow_job(svc, st, {"id": 9}, {"model_id": 3}, w1)
    assert 9 not in st.waiters and 9 not in st.routes
    assert seen == [(3, "agent_model")]
