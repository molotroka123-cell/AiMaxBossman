"""GET /api/direct-gen/jobs and /jobs/{id} read job files OFF the event loop (live 24.10).

py-spy on the hung backend: MainThread inside JobStore.list -> read_text_retry while the disk was busy; every other
request (the pult, /api/health) waited behind it. The store is now called from a worker thread; view() stays on the
loop. A synthetic store records which thread read it; a slow read must not stop a concurrent request.
"""
from __future__ import annotations

import asyncio
import threading
import time

from bcc.direct_gen.service import DirectGenService


def _job(i: int) -> dict:
    return {"job_id": f"{i:032x}", "participant": "owner", "status": "completed",
            "created_at": f"2026-10-24T00:00:{i:02d}", "model": "z-image-turbo"}


class SlowStore:
    def __init__(self, delay: float):
        self.delay, self.threads = delay, []

    def list(self, participant):
        self.threads.append(threading.get_ident())
        time.sleep(self.delay)
        return [_job(2), _job(1)]

    def load(self, participant, job_id):
        self.threads.append(threading.get_ident())
        time.sleep(self.delay)
        if job_id != _job(1)["job_id"]:
            raise KeyError(job_id)
        return _job(1)


async def test_job_list_and_get_read_files_off_the_event_loop(env, monkeypatch):
    store = SlowStore(0.6)
    svc: DirectGenService = env.svc.direct_gen
    monkeypatch.setattr(svc, "store", store)
    loop_thread = threading.get_ident()

    async def other():
        await asyncio.sleep(0.1)                    # let the slow list start first
        t0 = time.perf_counter()
        r = await env.client.get("/api/direct-gen/models")
        return r.status_code, time.perf_counter() - t0

    listed, (code, waited) = await asyncio.gather(env.client.get("/api/direct-gen/jobs"), other())
    assert listed.status_code == 200 and [j["job_id"] for j in listed.json()["jobs"]] == [_job(2)["job_id"], _job(1)["job_id"]]
    assert code == 200 and waited < 0.45, f"a concurrent request waited {waited:.2f}s behind the job-file read"

    one = await env.client.get(f"/api/direct-gen/jobs/{_job(1)['job_id']}")
    assert one.status_code == 200 and one.json()["job_id"] == _job(1)["job_id"]
    missing = await env.client.get(f"/api/direct-gen/jobs/{'f' * 32}")
    assert missing.status_code == 404 and "unknown_job" in missing.text
    assert store.threads and loop_thread not in store.threads, "job files were read on the event-loop thread"
