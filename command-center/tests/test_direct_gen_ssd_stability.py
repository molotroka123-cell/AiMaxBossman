"""Direct Gen on the new SSD (owner 23.10): metadata writes survive Windows, the GPU queue stays serial, owner STOP
reaches queued + running jobs, models come from BOSSMAN_MEDIA_MODELS (D:) and never fall back to the old C: tree.

Fake runners and a fake file tree only: no GPU, no real generation, neutral prompts. The live proof is
docs/audits/2026-10-23-ssd-generation.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from bcc.direct_gen import sdcli, store as job_store
from bcc.direct_gen.service import DirectGenService
from bcc.direct_gen.store import JobStore, atomic_write_text

from .conftest import wait_for
from .test_direct_gen import FakeComfy, make_models
from .test_direct_gen_photo import FakeSd, NEUTRAL, create, finish, install, make_media

H = {"X-Participant": "owner"}


async def _ready(flag):
    return flag


# ----------------------------------------------------------------------------- job metadata writes (WinError 5/32)

def test_concurrent_writers_and_a_reader_never_fail_on_one_job(tmp_path):
    """Six writers + one scanner on ONE job.json: the old shared job.tmp failed 1751 of 1800 saves on Windows."""
    s = JobStore(tmp_path)
    base = {"job_id": "a" * 32, "participant": "owner", "status": "queued", "timeline": []}
    errors, stop = [], threading.Event()

    def writer(n):
        for i in range(150):
            try:
                s.save(dict(base, n=n, i=i))
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

    def reader():
        while not stop.is_set():
            try:
                s.list("owner")
                s.load("owner", base["job_id"])
            except KeyError:
                pass
            except Exception as exc:  # noqa: BLE001
                errors.append("reader " + repr(exc))
    r = threading.Thread(target=reader)
    ws = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    r.start()
    [t.start() for t in ws]
    [t.join() for t in ws]
    stop.set()
    r.join()
    assert errors == []
    final = s.load("owner", base["job_id"])
    assert final["job_id"] == base["job_id"] and final["i"] == 149
    jobdir = s.job_dir("owner", base["job_id"])
    assert sorted(p.name for p in jobdir.iterdir()) == ["job.json"], "no temp file may be left behind"


def test_a_transient_replace_failure_is_retried_then_succeeds(tmp_path, monkeypatch):
    calls = []
    real = job_store.os.replace

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            err = PermissionError(13, "access denied")
            err.winerror = 5
            raise err
        return real(src, dst)
    monkeypatch.setattr(job_store.os, "replace", flaky)
    monkeypatch.setattr(job_store.time, "sleep", lambda s: None)
    target = tmp_path / "job.json"
    atomic_write_text(target, '{"ok": true}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True} and len(calls) == 3
    assert [p.name for p in tmp_path.iterdir()] == ["job.json"]


def test_a_persistent_failure_is_raised_not_swallowed_and_cleans_its_temp(tmp_path, monkeypatch):
    def always(src, dst):
        raise PermissionError(13, "denied")
    monkeypatch.setattr(job_store.os, "replace", always)
    monkeypatch.setattr(job_store.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "job.json", "{}")
    assert list(tmp_path.iterdir()) == []


def test_a_non_transient_error_is_not_retried(tmp_path, monkeypatch):
    calls = []

    def disk_full(src, dst):
        calls.append(1)
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(job_store.os, "replace", disk_full)
    with pytest.raises(OSError):
        atomic_write_text(tmp_path / "job.json", "{}")
    assert len(calls) == 1


# ----------------------------------------------------------------------------- serial GPU queue, parallel intake

class GatedSd(FakeSd):
    """Engine that runs until the test opens its gate; records overlap of heavy runs."""
    active = 0
    max_active = 0

    def __init__(self):
        super().__init__(steps=1)
        self.go = asyncio.Event()
        self.entered = False

    async def run(self, argv, *, cwd, on_progress=None):
        self.entered = True
        GatedSd.active += 1
        GatedSd.max_active = max(GatedSd.max_active, GatedSd.active)
        try:
            await self.go.wait()
            return await super().run(argv, cwd=cwd, on_progress=on_progress)
        finally:
            GatedSd.active -= 1


def _gated(env, tmp_path):
    GatedSd.active, GatedSd.max_active = 0, 0
    made = []

    def factory():
        g = GatedSd()
        made.append(g)
        return g
    svc = install(env, tmp_path, factory)
    return svc, made


async def test_two_simultaneous_enqueues_get_distinct_ids_and_run_one_after_the_other(env, tmp_path):
    svc, made = _gated(env, tmp_path)
    ra, rb = await asyncio.gather(create(env, prompt="job A: " + NEUTRAL), create(env, prompt="job B: " + NEUTRAL))
    a, b = ra.json(), rb.json()
    assert ra.status_code == 202 and rb.status_code == 202 and a["job_id"] != b["job_id"]

    async def first_running():
        return (len(made) >= 1 and GatedSd.active == 1) or None
    await wait_for(first_running)
    states = {j["job_id"]: (await env.client.get(f"/api/direct-gen/jobs/{j['job_id']}", headers=H)).json()["status"]
              for j in (a, b)}
    waiting = [jid for jid, st in states.items() if st == "queued"]
    started = [jid for jid, st in states.items() if st != "queued"]
    assert len(waiting) == 1 and len(started) == 1, states
    assert GatedSd.active == 1, "the second job must wait for the GPU"
    first, second = {"job_id": started[0]}, {"job_id": waiting[0]}
    runner_one = [m for m in made if not m.go.is_set()][0]
    runner_one.go.set()
    await finish(env, first["job_id"])

    async def second_started():
        return (GatedSd.active == 1 and any(not m.go.is_set() for m in made)) or None
    await wait_for(second_started)
    [m for m in made if not m.go.is_set()][0].go.set()
    j2 = await finish(env, second["job_id"])
    assert j2["status"] == "completed" and GatedSd.max_active == 1


# ----------------------------------------------------------------------------- owner STOP reaches Direct Gen

async def test_owner_stop_all_cancels_the_running_and_the_queued_job(env, tmp_path):
    svc, made = _gated(env, tmp_path)
    a = (await create(env, prompt="running: " + NEUTRAL)).json()["job_id"]

    async def running():
        return (len(made) == 1 and GatedSd.active == 1) or None
    await wait_for(running)
    b = (await create(env, prompt="queued: " + NEUTRAL)).json()["job_id"]
    assert (await env.client.get(f"/api/direct-gen/jobs/{b}", headers=H)).json()["status"] == "queued"
    assert sorted(svc.active_job_ids()) == sorted([a, b])
    body = (await env.client.post("/api/control-plane/stop-all")).json()
    assert sorted(body["stopped"]["direct_gen"]) == sorted([a, b]) and body["remaining"]["direct_gen"] == []
    for jid in (a, b):
        job = (await env.client.get(f"/api/direct-gen/jobs/{jid}", headers=H)).json()
        assert job["status"] == "cancelled", (jid, job["status"])
        assert not (svc.store.job_dir("owner", jid) / "result").exists()
    ran = [m for m in made if m.entered]
    assert len(ran) == 1 and ran[0].killed is True, "only the running job reached the engine, and it was killed"
    assert svc.active_job_ids() == []


async def test_stop_all_with_nothing_running_is_a_clean_noop(env, tmp_path):
    install(env, tmp_path, FakeSd)
    body = (await env.client.post("/api/control-plane/stop-all")).json()
    assert body["stopped"]["direct_gen"] == [] and body["remaining"]["direct_gen"] == []


async def test_a_stale_unfinished_row_without_a_live_task_does_not_hold_stop_red(env, tmp_path):
    svc = install(env, tmp_path, FakeSd)
    stale = {"job_id": "b" * 32, "participant": "owner", "status": "generating", "stage": "generating", "timeline": []}
    svc.store.save(stale)
    assert svc.active_job_ids() == []
    body = (await env.client.post("/api/control-plane/stop-all")).json()
    assert body["remaining"]["direct_gen"] == []


# ----------------------------------------------------------------------------- models come from D:, never from C:

def test_media_root_comes_from_BOSSMAN_MEDIA_MODELS(tmp_path, monkeypatch):
    d_media = tmp_path / "D" / "Bossman" / "models" / "media"
    d_media.mkdir(parents=True)
    monkeypatch.setenv("BOSSMAN_MEDIA_MODELS", str(d_media))
    assert sdcli.default_media_root() == d_media


def test_models_are_found_on_D_and_the_command_names_D_paths(tmp_path):
    d_media, binary = make_media(tmp_path / "D")
    spec = sdcli.BY_ID["flux2-klein-4b"]
    assert sdcli.describe(spec, d_media, binary, {})["available"] is True
    argv = sdcli.build_argv(spec, binary, d_media, {"width": 512, "height": 512, "steps": 1, "seed": 1}, NEUTRAL, "",
                            tmp_path / "out.png", None)
    weights = [a for a in argv if a.lower().endswith((".gguf", ".safetensors"))]
    assert weights and all(str(d_media) in w for w in weights)


def test_there_is_no_fallback_to_the_old_c_tree(tmp_path, monkeypatch):
    """The env var points at an EMPTY D tree while the old C tree still holds every weight: nothing may be served."""
    fake_home = tmp_path / "home"
    make_media(fake_home / "Bossman" / "models")
    binary = fake_home / "sd-cli.exe"
    binary.write_bytes(b"MZ")
    d_empty = tmp_path / "D" / "media"
    d_empty.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("BOSSMAN_MEDIA_MODELS", str(d_empty))
    root = sdcli.default_media_root()
    assert root == d_empty and str(fake_home) not in str(root)
    spec = sdcli.BY_ID["flux2-klein-4b"]
    assert sdcli.describe(spec, root, binary, {})["available"] is False
    assert not any(m["available"] and m["kind"] == "photo" for m in sdcli.list_models(root, binary, {}))


# ----------------------------------------------------------------------------- restart

async def test_after_a_restart_health_and_status_work_and_history_survives(env, tmp_path):
    install(env, tmp_path, FakeSd)
    done = await finish(env, (await create(env)).json()["job_id"])
    assert done["status"] == "completed"
    media, binary = make_media(tmp_path)
    models, flows = make_models(tmp_path)
    reborn = DirectGenService(env.svc, client=FakeComfy(), models_dir=models, workflows_dir=flows, poll_seconds=0.01,
                              verify=lambda p: {"verified": True, "duration_s": 1.0, "width": 480, "height": 272},
                              media_dir=media, sd_bin=binary, sd_runner=FakeSd,
                              free_memory=lambda: 64 * 1024 ** 3, foreign_engines=lambda: [], gpu_grace_seconds=0.0)
    env.svc.direct_gen = reborn
    assert (await env.client.get("/health/live")).status_code == 200
    status = await env.client.get("/api/direct-gen/status")
    assert status.status_code == 200 and status.json()["queue"] == {"running": None, "waiting": 0}
    again = (await env.client.get(f"/api/direct-gen/jobs/{done['job_id']}", headers=H)).json()
    assert again["status"] == "completed" and again["result"]["verified"] is True
