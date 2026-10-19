"""Warm queue (owner 19.10: «не выгружать модель между одинаковыми задачами в очереди») + measured timings.

No GPU, no real process: a scripted sd-server (FakeWorld) and a scripted sd-cli (FakeSd). Proven here:
  * auto mode: a same-model queue run loads the weights ONCE (server started once, later jobs warm), a single job and a
    model change keep / go back to the per-job sd-cli path, the server is released as soon as the run is over;
  * any resident error falls back to sd-cli and the job still completes; STOP of a warm job works; idle releases;
  * default mode without a live proof = old path; with a proof only the proven model goes warm;
  * every finished job is measured (job["timing"] + append-only timings.jsonl in the data dir) and served owner-only.
NOT proven here: real speed or picture parity of sd-server on the GPU (tools/direct_gen_bench/warm_queue_bench.py).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from bcc.direct_gen import resident, timings

from .conftest import wait_for
from .test_direct_gen_photo import FakeSd, create, finish, install
from .test_direct_gen_resident import FakeWorld, make_engine

OWNER = {"X-Participant": "owner"}
FIELDS = {"ts", "job_id", "model", "kind", "status", "width", "height", "steps", "frames", "engine", "load", "load_s",
          "generate_s", "post_s", "total_s", "queue_wait_s"}


def auto_service(env, tmp_path, monkeypatch, world: FakeWorld, *, flag="auto", idle_s=60.0):
    monkeypatch.setenv(resident.ENV_FLAG, flag)
    svc = install(env, tmp_path, None)
    svc._resident = make_engine(world, tmp_path, idle_s=idle_s)
    svc._sd_runner = FakeSd                          # the sd-cli path (single jobs, fallbacks)
    return svc


async def queue(env, svc, models: list[str], **over) -> list[str]:
    """Create a «лесенка»: all jobs are queued while the GPU gate is held, then released at once."""
    ids = []
    async with svc._gate():
        for n, m in enumerate(models):
            r = await create(env, model=m, seed=100 + n, steps=over.get("steps", 6))
            assert r.status_code == 202, r.text
            ids.append(r.json()["job_id"])
        await asyncio.sleep(0.02)                    # every task is now waiting at the gate
        assert len(svc._waiting) == len(models)
    return ids


async def done(env, ids):
    return [await finish(env, i, timeout=10) for i in ids]


# ----------------------------------------------------------------------------------------------- warm queue run


async def test_same_model_queue_loads_once_and_releases_when_the_run_ends(env, tmp_path, monkeypatch):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    jobs = await done(env, await queue(env, svc, ["z-image-turbo"] * 3))
    assert [j["status"] for j in jobs] == ["completed"] * 3
    assert len(world.procs) == 1 and svc._resident.starts == 1                  # ONE load for three jobs
    assert [j["provenance"]["engine_warm"] for j in jobs] == [False, True, True]
    assert all(j["provenance"]["engine"] == "sd-server (resident)" for j in jobs)
    assert [j["timing"]["load"] for j in jobs] == ["cold", "warm", "warm"]
    assert all(j["timing"]["engine"] == "sd-server" for j in jobs)
    assert [q["seed"] for q in world.requests] == [100, 101, 102]               # per-job params snapshot kept
    assert not FakeSd.instances                                                 # sd-cli never ran
    assert world.killed_pids == [5000] and not svc._resident.alive()            # queue empty -> memory freed at once
    assert svc._resident._idle is None


async def test_single_job_keeps_the_proven_sd_cli_path(env, tmp_path, monkeypatch):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    (job,) = await done(env, await queue(env, svc, ["z-image-turbo"]))
    assert job["status"] == "completed" and not world.procs and len(FakeSd.instances) == 1
    assert job["provenance"]["engine"] == "sd-cli" and "single job" in job["provenance"]["engine_decision"]
    assert job["timing"]["engine"] == "sd-cli" and job["timing"]["load"] == "cold"


async def test_model_change_forces_a_cold_load(env, tmp_path, monkeypatch):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    jobs = await done(env, await queue(env, svc, ["z-image-turbo", "z-image-turbo", "flux1-schnell", "flux1-schnell"]))
    assert [j["status"] for j in jobs] == ["completed"] * 4
    assert svc._resident.starts == 2                                            # z once, flux once
    assert [j["provenance"]["engine_warm"] for j in jobs] == [False, True, False, True]
    assert any("z_image_turbo" in a for a in world.argvs[0]) and any("flux1-schnell" in a for a in world.argvs[1])
    assert world.killed_pids == [5000, 5001]                                    # z released before flux loaded
    # interleaved models: no two consecutive jobs share weights -> every job is a cold per-job sd-cli run
    world2 = FakeWorld()
    svc._resident = make_engine(world2, tmp_path)
    jobs = await done(env, await queue(env, svc, ["z-image-turbo", "flux1-schnell", "z-image-turbo"]))
    assert [j["status"] for j in jobs] == ["completed"] * 3 and not world2.procs
    assert all(j["timing"]["engine"] == "sd-cli" and j["timing"]["load"] == "cold" for j in jobs)


async def test_server_start_failure_falls_back_and_every_job_completes(env, tmp_path, monkeypatch):
    world = FakeWorld(exit_on_start=True)
    svc = auto_service(env, tmp_path, monkeypatch, world)
    jobs = await done(env, await queue(env, svc, ["z-image-turbo"] * 2))
    assert [j["status"] for j in jobs] == ["completed", "completed"]
    assert jobs[0]["provenance"]["engine"] == "sd-cli"
    assert jobs[0]["provenance"]["engine_fallback"].startswith("sd-server unavailable")
    assert len(FakeSd.instances) == 2 and not svc._resident.alive()


async def test_server_generation_failure_falls_back_and_the_job_completes(env, tmp_path, monkeypatch):
    world = FakeWorld(gen_status="failed")
    svc = auto_service(env, tmp_path, monkeypatch, world)
    jobs = await done(env, await queue(env, svc, ["z-image-turbo"] * 2))
    assert [j["status"] for j in jobs] == ["completed", "completed"]
    assert "rerun on sd-cli" in jobs[0]["provenance"]["engine_fallback"]
    assert jobs[0]["result"]["sha256"] and jobs[0]["timing"]["engine"] == "sd-cli"
    assert not svc._resident.alive()


class HangSecond(FakeWorld):
    """The first server job completes, the second one hangs until STOP."""

    def http(self, method, url, body=None, timeout=30.0):
        if "/sdcpp/v1/jobs/" in url and url.endswith("job_2"):
            return 200, {"id": "job_2", "status": "generating"}
        return super().http(method, url, body, timeout)


async def test_stop_during_a_warm_run_cancels_only_that_job(env, tmp_path, monkeypatch):
    world = HangSecond()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    ids = await queue(env, svc, ["z-image-turbo"] * 3)
    first = await finish(env, ids[0], timeout=10)
    assert first["status"] == "completed"
    await wait_for(lambda: asyncio.sleep(0, result=len(world.requests) >= 2 or None), timeout=5)
    c = await env.client.post(f"/api/direct-gen/jobs/{ids[1]}/cancel", headers=OWNER)
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert 5000 in world.killed_pids                                            # the warm server was killed by STOP
    third = await finish(env, ids[2], timeout=10)
    assert third["status"] == "completed"                                       # the queue goes on
    stopped = await finish(env, ids[1])
    assert stopped["status"] == "cancelled" and stopped["timing"]["status"] == "cancelled"
    folder = svc.store.job_dir("owner", ids[1])
    assert not (folder / "result").exists()                                     # no picture from a stopped job


async def test_delete_after_a_warm_run_and_facelock_input_are_kept(env, tmp_path, monkeypatch):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    jobs = await done(env, await queue(env, svc, ["z-image-turbo"] * 2))
    d = await env.client.delete(f"/api/direct-gen/jobs/{jobs[0]['job_id']}", headers=OWNER)
    assert d.status_code == 200 and d.json()["deleted"] is True
    rows = svc.timing_log.read()
    assert [r["job_id"] for r in rows] == [j["job_id"] for j in jobs]           # measurements survive the delete


async def test_idle_timeout_releases_a_kept_server(env, tmp_path, monkeypatch):
    """Safety net: the server was kept for a next job that never came (here: forced) -> released after idle_s."""
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world, idle_s=0.05)
    monkeypatch.setattr(svc, "_resident_wanted", lambda exclude=None: True)
    jobs = await done(env, await queue(env, svc, ["z-image-turbo"] * 2))
    assert [j["status"] for j in jobs] == ["completed"] * 2
    await wait_for(lambda: asyncio.sleep(0, result=(not svc._resident.alive()) or None), timeout=3)
    assert world.killed_pids == [5000]


async def test_other_engines_never_run_next_to_a_resident_server(env, tmp_path, monkeypatch):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world, flag="1")             # always-on: server stays after a job
    (job,) = await done(env, await queue(env, svc, ["z-image-turbo"]))
    assert job["status"] == "completed" and svc._resident.alive()
    video = await finish(env, (await create(env, model="wan2.2-ti2v-5b", duration=1, resolution="480x272")).json()["job_id"])
    assert video["status"] == "completed" and not svc._resident.alive()          # released before the video engine


async def test_default_mode_uses_the_server_only_for_models_with_a_live_proof(env, tmp_path, monkeypatch):
    monkeypatch.delenv(resident.ENV_FLAG, raising=False)
    svc = install(env, tmp_path, None)
    world = FakeWorld()
    svc._resident = make_engine(world, tmp_path)
    svc._sd_runner = FakeSd
    server = svc._resident.server_bin
    server.write_bytes(b"MZ-server")
    assert svc._mode_for("z-image-turbo") == resident.MODE_OFF                  # no proof file: old path
    st = server.stat()
    proof = {"schema": resident.PROOF_SCHEMA, "server_size": st.st_size, "server_mtime": int(st.st_mtime),
             "models": {"z-image-turbo": {"verdict": "PASS"}, "flux1-schnell": {"verdict": "FAIL"}}}
    svc.data_dir.mkdir(parents=True, exist_ok=True)
    (svc.data_dir / resident.PROOF_FILE).write_text(json.dumps(proof), encoding="utf-8")
    assert svc._mode_for("z-image-turbo") == resident.MODE_AUTO and svc._mode_for("flux1-schnell") == resident.MODE_OFF
    jobs = await done(env, await queue(env, svc, ["z-image-turbo", "z-image-turbo", "flux1-schnell", "flux1-schnell"]))
    assert [j["provenance"].get("engine_warm") for j in jobs[:2]] == [False, True]
    assert "engine" not in jobs[2]["provenance"] and "engine" not in jobs[3]["provenance"]   # unproven: old path
    status = (await env.client.get("/api/direct-gen/status")).json()["resident"]
    assert status["mode"] == resident.MODE_PROVEN and status["proven_models"] == ["z-image-turbo"]
    server.write_bytes(b"MZ-another-build")                                     # a new sd-server binary: proof void
    os.utime(server, (st.st_mtime + 10, st.st_mtime + 10))
    assert svc._mode_for("z-image-turbo") == resident.MODE_OFF


def test_engine_starts_at_high_priority(tmp_path):
    seen = []
    world = FakeWorld()
    eng = resident.ResidentEngine(tmp_path / "sd-server.exe", spawn=world.spawn, http=world.http, kill=world.kill,
                                  bind=lambda pid: None, port_picker=lambda: 18080, poll_s=0.005, ready_timeout_s=2.0,
                                  cwd=tmp_path, prioritize=seen.append)

    async def go():
        plan = resident.Plan(startup=("--diffusion-model", "m.gguf"), prompt="p", out=str(tmp_path / "o.png"))
        await eng.ensure(plan, "m")
        eng.kill_now()
    asyncio.run(go())
    assert seen == [5000]


# ----------------------------------------------------------------------------------------------- measurements


async def test_every_finished_job_is_measured_with_all_fields(env, tmp_path):
    svc = install(env, tmp_path, FakeSd)
    job = await finish(env, (await create(env, steps=6)).json()["job_id"])
    t = job["timing"]
    assert job["status"] == "completed" and t["engine"] == "sd-cli" and t["load"] == "cold"
    assert (t["width"], t["height"], t["steps"]) == (512, 512, 6) and t["split"] == "timeline"
    for k in ("load_s", "generate_s", "post_s", "total_s", "queue_wait_s"):
        assert isinstance(t[k], float) and t[k] >= 0, k
    log = Path(env.svc.settings.data_dir) / "direct-gen" / timings.FILE                # inside the data dir (BCC_DATA_DIR)
    (row,) = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert FIELDS <= row.keys() and row["job_id"] == job["job_id"]
    assert "prompt" not in json.dumps(row) and "owner" not in json.dumps(row)    # no prompt, no participant
    failed = await finish(env, (await create(env, model="flux1-schnell")).json()["job_id"])
    assert failed["timing"]["status"] in ("completed", "failed")
    assert len(svc.timing_log.read()) == 2


async def test_timings_endpoint_is_owner_only_and_summarises(env, tmp_path):
    install(env, tmp_path, FakeSd)
    for s in (1, 2, 3):
        await finish(env, (await create(env, seed=s, steps=6)).json()["job_id"])
    r = await env.client.get("/api/direct-gen/timings", headers=OWNER)
    assert r.status_code == 200
    body = r.json()
    assert len(body["records"]) == 3 and body["min_samples"] == 3
    s = body["summary"]["z-image-turbo"]
    assert s["n"] == 3 and s["measured"] is True and s["cold"]["n"] == 3 and s["warm"]["n"] == 0
    pit = await env.client.get("/api/direct-gen/timings", headers={"X-Participant": "a" * 64})
    assert pit.status_code == 404


def test_measure_split_and_engine_labels():
    base = {"job_id": "j", "model": "m", "kind": "photo", "status": "completed",
            "created_at": "2026-10-19T10:00:00+00:00", "started_at": "2026-10-19T10:00:30+00:00",
            "finished_at": "2026-10-19T10:02:00+00:00", "params": {"width": 512, "height": 512, "steps": 10}}
    tl = [{"stage": "queued", "at": "2026-10-19T10:00:00+00:00"}, {"stage": "loading", "at": "2026-10-19T10:00:30+00:00"},
          {"stage": "generating", "at": "2026-10-19T10:01:10+00:00"},
          {"stage": "postprocessing", "at": "2026-10-19T10:01:50+00:00"}, {"stage": "completed", "at": "2026-10-19T10:02:00+00:00"}]
    row = timings.measure({**base, "timeline": tl, "provenance": {"runtime": "sdcpp"}})
    assert (row["load_s"], row["generate_s"], row["post_s"], row["total_s"], row["queue_wait_s"]) == (40.0, 40.0, 10.0, 90.0, 30.0)
    assert (row["engine"], row["load"]) == ("sd-cli", "cold")
    warm = timings.measure({**base, "timeline": [t for t in tl if t["stage"] != "generating"],
                            "provenance": {"runtime": "sdcpp", "engine": "sd-server (resident)", "engine_warm": True,
                                           "engine_seconds": 75.0, "engine_ready_s": 1.0}})
    assert warm["split"] == "engine_clock" and (warm["engine"], warm["load"]) == ("sd-server", "warm")
    assert warm["load_s"] == 6.0 and warm["generate_s"] == 74.0                   # 80 s loading->post, 75 s engine
    unknown = timings.measure({**base, "timeline": tl[:2], "provenance": {"runtime": "comfyui"}})
    assert unknown["load_s"] is None and unknown["engine"] == "comfyui"


def test_summary_medians_cold_and_warm_apart():
    def r(load, kind="cold", gen=20.0):
        return {"model": "m", "kind": "photo", "status": "completed", "load": kind, "load_s": load, "generate_s": gen,
                "steps": 10, "width": 512, "height": 512, "post_s": 1.0, "total_s": load + gen + 1}
    s = timings.summary([r(40), r(44), r(400), r(2, "warm"), r(3, "warm"), {**r(1), "status": "failed"}])["m"]
    assert s["n"] == 5 and s["measured"] and s["cold"] == {"n": 3, "load_s": 44.0} and s["warm"] == {"n": 2, "load_s": 2.5}
    assert s["s_per_step_512"] == 2.0
    assert timings.summary([r(40), r(41)])["m"]["measured"] is False            # < 3 runs: not «по замеру»


def test_pult_caption_carries_the_measured_time():
    from bcc.direct_gen import pult
    job = {"job_id": "abcdef0123", "model": "z-image-turbo", "finished_at": "2026-10-19T10:02:00+00:00",
           "params": {"width": 512, "height": 512, "steps": 8},
           "timing": {"total_s": 246.4, "load_s": 40.2, "load": "cold"}}
    parts = pult.caption_for(job).split(" · ")
    assert parts[4] == "#abcdef01" and parts[5] == "за 4 мин 6 с (загрузка 40 с, холодная)"
    warm = pult.timing_text({"timing": {"total_s": 30, "load_s": 1.0, "load": "warm"}})
    assert warm == "за 30 с (загрузка 1 с, модель уже была загружена)"
    assert pult.timing_text({}) == "" and len(pult.caption_for({**job, "timing": None}).split(" · ")) == 5


def test_pult_eta_text_uses_medians_only_with_enough_runs():
    from bcc.telegram_companion.gen_progress import measured_eta
    summary = {"z-image-turbo": {"n": 5, "measured": True, "cold": {"load_s": 40.0}, "s_per_step_512": 2.0,
                                 "post_s": 1.0, "generate_s": 16.0, "total_s": 57.0},
               "flux1-schnell": {"n": 2, "measured": False, "cold": {"load_s": 25.0}, "s_per_step_512": 2.6}}
    assert measured_eta(summary, "sdcpp:z-image-turbo", 8, 512, 512) == "≈ 55 с (по замеру 5 генераций)"
    assert measured_eta(summary, "sdcpp:z-image-turbo", 30, 1024, 1024) == "≈ 5 мин (по замеру 5 генераций)"
    assert measured_eta(summary, "sdcpp:z-image-turbo") == "≈ 55 с (по замеру 5 генераций)"   # steps unknown: medians
    assert measured_eta(summary, "sdcpp:flux1-schnell", 4, 512, 512) is None             # 2 runs: keep the rough text
    assert measured_eta(None, "x") is None and measured_eta({}, "sdcpp:other") is None


def test_torn_log_line_is_skipped(tmp_path):
    log = timings.TimingLog(tmp_path)
    log.append({"job_id": "a", "status": "completed"})
    with log.path.open("a", encoding="utf-8") as fh:
        fh.write('{"job_id": "b", "sta')
    assert [r["job_id"] for r in log.read()] == ["a"]
