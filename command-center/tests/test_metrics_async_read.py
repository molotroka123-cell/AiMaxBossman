"""Telemetry must not stall the owner loop; no live GPU/model is claimed here.

The blocking reader is controlled by events, not a widened latency budget.
Database and bus doubles record ordering; the sampler and HTTP handler are real.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from bcc import metrics


def snapshot():
    return {
        "ts": metrics.utcnow(), "cpu_pct": 7.0,
        "ram_used_mb": 1024.0, "ram_total_mb": 8192.0,
        "disk_used_gb": None, "disk_total_gb": None,
        "gpu": [{"name": "test fixture", "procs": [{"pid": 123}]}],
    }


class DBRecorder:
    """Deliberately not SQLite acceptance: observe sampler-side effects only."""
    def __init__(self):
        self.executions = []
        self.commits = 0

    def session(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        self.executions.append(statement)
        return SimpleNamespace(first=lambda: None, fetchall=lambda: [])

    async def commit(self):
        self.commits += 1


class BusRecorder:
    def __init__(self):
        self.events = []

    async def emit(self, name, **data):
        self.events.append((name, data))


class ControlledRead:
    def __init__(self, *, result=None, error=None):
        self.loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.release = threading.Event()
        self.calls = 0
        self.threads = []
        self.expired = False
        self.result = snapshot() if result is None else result
        self.error = error

    def __call__(self):
        self.calls += 1
        self.threads.append(threading.get_ident())
        self.loop.call_soon_threadsafe(self.started.set)
        # Safety escape for the old synchronous implementation; NOT a perf gate.
        if not self.release.wait(2):
            self.expired = True
        if self.error:
            raise self.error
        return self.result


async def settle():
    for _ in range(6):
        await asyncio.sleep(0)


def test_periodic_sample_does_not_block_the_owner_loop(monkeypatch):
    async def run():
        db, bus = DBRecorder(), BusRecorder()
        sampler = metrics.MetricsSampler(db, bus)
        probe = ControlledRead()
        monkeypatch.setattr(sampler, "read", probe)
        task = asyncio.create_task(sampler.sample())
        try:
            await asyncio.wait_for(probe.started.wait(), 5)
            # A real observer runs while the system read is STILL waiting.
            remained_in_flight = not task.done()
            no_early_write = not db.executions and not bus.events
        finally:
            probe.release.set()
            await task
        assert remained_in_flight and not probe.expired, "system read stalled the loop"
        assert no_early_write, "a partial reading was published"
        assert len(probe.threads) == 1
        assert probe.threads[0] != threading.get_ident()
        assert db.commits == 1 and len(db.executions) == 2
        assert len(bus.events) == 1 and bus.events[0][0] == "system.metrics"
    asyncio.run(run())


def test_system_api_waits_asynchronously_for_the_same_sampler(monkeypatch):
    from bcc import api

    async def run():
        db = DBRecorder()
        sampler = metrics.MetricsSampler(db, BusRecorder())
        probe = ControlledRead()
        monkeypatch.setattr(sampler, "read", probe)
        async def healthy(svc):
            return {"status": "fixture"}
        monkeypatch.setattr(api, "_health", healthy)
        svc = SimpleNamespace(metrics=sampler, db=db, started_at="fixture",
                              startup=SimpleNamespace(to_dict=lambda: {}))
        endpoint = next(r.endpoint for r in api._api_router().routes if r.path == "/api/system")
        task = asyncio.create_task(endpoint(svc))
        try:
            await asyncio.wait_for(probe.started.wait(), 5)
            in_flight = not task.done()
        finally:
            probe.release.set()
            body = await task
        assert in_flight and not probe.expired, "GET /system blocked unrelated work"
        assert body["metrics"]["ram_total_mb"] == 8192
        assert body["queue"] == {} and body["history"] == []
    asyncio.run(run())


def test_no_sample_resource_fallback_does_not_block(monkeypatch):
    from bcc.features import resources

    async def run():
        db = DBRecorder()
        sampler = metrics.MetricsSampler(db, BusRecorder())
        probe = ControlledRead()
        monkeypatch.setattr(sampler, "read", probe)
        svc = SimpleNamespace(db=db, metrics=sampler)
        task = asyncio.create_task(resources._snapshot(svc, {"reserve_floor_mb": 512}))
        try:
            await asyncio.wait_for(probe.started.wait(), 5)
            in_flight = not task.done()
        finally:
            probe.release.set()
            result = await task
        assert in_flight and not probe.expired
        assert result.measured and result.total_memory_mb == 8192
    asyncio.run(run())


def test_concurrent_readers_share_only_inflight_work(monkeypatch):
    async def run():
        sampler = metrics.MetricsSampler(None, None)
        probe = ControlledRead()
        monkeypatch.setattr(sampler, "read", probe)
        tasks = [asyncio.create_task(sampler.read_async()) for _ in range(32)]
        try:
            await asyncio.wait_for(probe.started.wait(), 5)
            await settle()
            assert probe.calls == 1
        finally:
            probe.release.set()
            results = await asyncio.gather(*tasks)
        assert len(results) == 32
        await sampler.read_async()
        assert probe.calls == 2, "completed results became an unauthorized new TTL cache"
    asyncio.run(run())


def test_cancelling_one_waiter_keeps_other_waiters_alive(monkeypatch):
    async def run():
        sampler = metrics.MetricsSampler(None, None)
        probe = ControlledRead()
        monkeypatch.setattr(sampler, "read", probe)
        first = asyncio.create_task(sampler.read_async())
        second = asyncio.create_task(sampler.read_async())
        try:
            await asyncio.wait_for(probe.started.wait(), 5)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not second.done()
        finally:
            probe.release.set()
            body = await second
        assert probe.calls == 1 and body["ram_total_mb"] == 8192
    asyncio.run(run())


def test_cancelled_sampling_does_not_write_after_the_thread_finishes(monkeypatch):
    async def run():
        db, bus = DBRecorder(), BusRecorder()
        sampler = metrics.MetricsSampler(db, bus)
        probe = ControlledRead()
        monkeypatch.setattr(sampler, "read", probe)
        task = asyncio.create_task(sampler.sample())
        try:
            await asyncio.wait_for(probe.started.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # A new waiter joins the bounded existing read, not another process.
            joined = asyncio.create_task(sampler.read_async())
            await settle()
            assert probe.calls == 1
        finally:
            probe.release.set()
        await joined
        assert not db.executions and not bus.events and sampler.last_sample is None
    asyncio.run(run())


def test_reader_exceptions_propagate_and_next_request_can_recover(monkeypatch):
    async def run():
        sampler = metrics.MetricsSampler(None, None)
        probe = ControlledRead(error=OSError("fixture missing system data"))
        monkeypatch.setattr(sampler, "read", probe)
        tasks = [asyncio.create_task(sampler.read_async()) for _ in range(4)]
        try:
            await asyncio.wait_for(probe.started.wait(), 5)
            await settle()
        finally:
            probe.release.set()
            failures = await asyncio.gather(*tasks, return_exceptions=True)
        assert probe.calls == 1 and all(isinstance(x, OSError) for x in failures)
        probe.error = None
        assert (await sampler.read_async())["ram_used_mb"] == 1024
        assert probe.calls == 2
    asyncio.run(run())


def test_cancelled_all_waiters_do_not_leave_unhandled_exceptions(monkeypatch):
    async def run():
        sampler = metrics.MetricsSampler(None, None)
        loop = asyncio.get_running_loop()
        errors = []
        before = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: errors.append(context))
        probe = ControlledRead(error=OSError("fixture error after cancellation"))
        monkeypatch.setattr(sampler, "read", probe)
        task = asyncio.create_task(sampler.read_async())
        try:
            await asyncio.wait_for(probe.started.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            pending = sampler._pending_read
            finished = asyncio.Event()
            pending.add_done_callback(lambda _: finished.set())
            probe.release.set()
            await asyncio.wait_for(finished.wait(), 5)
            await settle()
            assert sampler._pending_read is None
            # Do not retrieve pending.exception() in the test: the production
            # callback, not the test, must consume an orphaned reader failure.
            del pending
            import gc
            gc.collect()
            await settle()
            assert not errors
        finally:
            probe.release.set()
            loop.set_exception_handler(before)
    asyncio.run(run())


def test_each_waiter_has_an_independent_nested_result(monkeypatch):
    async def run():
        sampler = metrics.MetricsSampler(None, None)
        probe = ControlledRead()
        monkeypatch.setattr(sampler, "read", probe)
        tasks = [asyncio.create_task(sampler.read_async()) for _ in range(2)]
        try:
            await asyncio.wait_for(probe.started.wait(), 5)
        finally:
            probe.release.set()
            a, b = await asyncio.gather(*tasks)
        a["gpu"][0]["procs"][0]["pid"] = -1
        assert b["gpu"][0]["procs"][0]["pid"] == 123
        assert probe.result["gpu"][0]["procs"][0]["pid"] == 123
    asyncio.run(run())


def test_cpu_percent_keeps_the_event_loop_threads_baseline(monkeypatch):
    async def run():
        owner_thread = threading.get_ident()
        calls = []
        def cpu_percent(interval=None):
            thread = threading.get_ident()
            calls.append(thread)
            return 61.0 if thread == owner_thread else 0.0
        monkeypatch.setattr(metrics.psutil, "cpu_percent", cpu_percent)
        monkeypatch.setattr(metrics, "gpu_info", lambda: None)
        sampler = metrics.MetricsSampler(None, None)
        body = await sampler.read_async()
        assert body["cpu_pct"] == 61.0, "worker-thread first-call zero leaked into CPU report"
        assert calls[0] == owner_thread
        assert any(x != owner_thread for x in calls)
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["empty", "error"])
def test_missing_measurement_still_denies_admission(monkeypatch, kind):
    from bcc.features import resources
    from bcc.v2.resource_brain import plan_memory

    async def run():
        sampler = metrics.MetricsSampler(None, None)
        def unavailable():
            if kind == "error":
                raise OSError("fixture unavailable")
            return {}
        monkeypatch.setattr(sampler, "read", unavailable)
        svc = SimpleNamespace(db=DBRecorder(), metrics=sampler)
        result = await resources._snapshot(svc, {"total_override_mb": 128000})
        assert not result.measured
        assert not plan_memory(result, 1, policy="balanced").allowed
    asyncio.run(run())


def test_existing_sample_avoids_unnecessary_live_read(monkeypatch):
    from bcc.features import resources

    async def run():
        class Existing(DBRecorder):
            async def execute(self, statement):
                return SimpleNamespace(first=lambda: SimpleNamespace(_mapping=snapshot()),
                                       fetchall=lambda: [])
        sampler = metrics.MetricsSampler(None, None)
        def forbidden():
            raise AssertionError("no live read when a persisted sample exists")
        monkeypatch.setattr(sampler, "read", forbidden)
        result = await resources._snapshot(SimpleNamespace(db=Existing(), metrics=sampler), {})
        assert result.measured and result.total_memory_mb == 8192
    asyncio.run(run())


def test_gpu_cache_and_device_timeouts_are_not_relaxed():
    assert metrics.GPU_CACHE_TTL == 5.0
    assert metrics.NVIDIA_TIMEOUT == 5.0
    assert metrics.SAMPLE_SECONDS == 10.0


def test_graceful_stop_drains_current_sample_without_another_read(monkeypatch):
    async def run():
        db, bus = DBRecorder(), BusRecorder()
        sampler = metrics.MetricsSampler(db, bus, interval=0.001)
        sampler.stop_event = asyncio.Event()
        probe = ControlledRead()
        monkeypatch.setattr(sampler, "read", probe)
        task = asyncio.create_task(sampler.loop())
        try:
            await asyncio.wait_for(probe.started.wait(), 5)
            sampler.stop_event.set()
        finally:
            probe.release.set()
            await asyncio.wait_for(task, 5)
        assert probe.calls == 1 and db.commits == 1
        assert len(bus.events) == 1 and not probe.expired
    asyncio.run(run())


def test_failed_sample_does_not_publish_partial_or_old_data(monkeypatch):
    async def run():
        db, bus = DBRecorder(), BusRecorder()
        sampler = metrics.MetricsSampler(db, bus)
        def broken_read():
            raise OSError("fixture: memory measurement unavailable")
        monkeypatch.setattr(sampler, "read", broken_read)
        with pytest.raises(OSError):
            await sampler.sample()
        assert not db.executions and not bus.events and sampler.last_sample is None
    asyncio.run(run())
