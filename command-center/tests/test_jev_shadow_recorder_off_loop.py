"""Jev shadow: the egress-denied record is written off the event loop.

``ShadowRecorder.write`` appends to a JSONL file under a ``threading.Lock``. The Jev
call path already runs it in a worker thread (``asyncio.to_thread(provider.shadow)``),
which holds that lock for its whole append. The egress-denied path called ``write``
directly inside the async job, so whenever another writer was mid-append (slow disk,
an antivirus scan of the file on Windows) the WHOLE event loop — every task step, every
HTTP request — waited on that lock. Mock-only: stubbed baseline/egress, the real
recorder, its real lock and a real file.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from bcc.features import jev
from bcc.jev.decision import Baseline, ShadowRecorder

HOLD_S = 0.8


class SimpleBus:
    async def emit(self, *_a, **_k):          # denied path never emits
        raise AssertionError("egress-denied job must not emit a shadow decision")


def _state(recorder):
    st = jev._State.__new__(jev._State)       # no config/provider: denied path only
    st.routes, st.waiters, st.jobs, st.dropped = {}, {}, set(), 0
    st.sem = asyncio.Semaphore(2)
    st.recorder = recorder
    return st


@pytest.fixture(autouse=True)
def denied(monkeypatch):
    async def fake_baseline(_svc, model_id, _agent, source):
        return Baseline(source=source, model_alias=f"m{model_id}")

    async def no_egress(*_a):
        return False

    monkeypatch.setattr(jev, "_baseline", fake_baseline)
    monkeypatch.setattr(jev, "_egress_allowed", no_egress)


def _routed(model_id):
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(model_id)
    return fut


async def test_denied_record_waits_for_the_lock_without_freezing_the_loop(tmp_path):
    path = tmp_path / "jev" / "shadow-decisions.jsonl"
    rec = ShadowRecorder(path)
    st = _state(rec)
    held = threading.Event()

    def other_writer():                       # e.g. provider.shadow's write in to_thread
        with rec._lock:
            held.set()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"task_id": "other"}) + "\n")
                fh.flush()
                time.sleep(HOLD_S)            # slow append

    writer = threading.Thread(target=other_writer)
    writer.start()
    assert held.wait(5)
    try:
        job = asyncio.create_task(jev._shadow_job(SimpleBus(), st, {"id": 3, "kind": "coding", "prompt": "x"},
                                                  {"model_id": 1}, _routed(42)))
        gaps, last = [], time.perf_counter()
        while not job.done():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now
        await job
    finally:
        writer.join(5)
    assert max(gaps) < HOLD_S / 2, f"event loop froze for {max(gaps):.3f}s waiting on the recorder lock"
    # Locking/ordering unchanged: the record waited for the in-progress append and
    # is complete (memory + file) by the time the job returns.
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [r["task_id"] for r in lines] == ["other", 3]
    assert lines[1]["fallback_reason"] == "egress_not_allowed" and lines[1]["authoritative"] is False
    assert lines[1]["baseline"]["model_alias"] == "m42" and lines[1]["baseline"]["source"] == "router"
    assert [r["task_id"] for r in rec.recent] == [3]


async def test_concurrent_denied_jobs_each_write_one_whole_line(tmp_path):
    """Control: many jobs at once — every record lands once, no torn or lost lines."""
    path = tmp_path / "jev" / "shadow-decisions.jsonl"
    rec = ShadowRecorder(path)
    st = _state(rec)
    ids = list(range(40))
    await asyncio.gather(*(jev._shadow_job(SimpleBus(), st, {"id": i, "prompt": f"p{i}"}, {"model_id": i},
                                           _routed(i)) for i in ids))
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["task_id"] for r in lines) == ids
    assert sorted(r["task_id"] for r in rec.recent) == ids
    assert all(r["fallback_reason"] == "egress_not_allowed" for r in lines)
