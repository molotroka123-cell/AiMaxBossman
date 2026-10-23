"""Keep-warm photo edit models (owner 23.10: «Не разгружай модель, пусть подряд генерирует всё»).

Measured on the owner's PC: a lone qwen-image-2.1-uc-edit job ran on the cold per-job sd-cli (engine_decision
"single job: no queued job of the same model", load_s 1599) while warm resident jobs loaded in 78 s. Proven here with a
scripted sd-server (FakeWorld) and a scripted sd-cli (FakeSd), no GPU / process / network:
  * a lone edit job runs on the resident sd-server and the weights STAY loaded for the keep-warm window;
  * the next edit inside the window is warm (no new load), the window restarts with every job;
  * the server is released when the window ends, on low memory (after the job or while kept), on the owner STOP flag,
    for another model, before a video engine, and on cancel;
  * non-edit photo models and video models keep their old behaviour; keep-warm can be switched off.
NOT proven here: the real load time on the GPU (needs an owner-PC run).
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from bcc.direct_gen import resident, sdcli

from .conftest import wait_for
from .test_direct_gen_photo import PNG, FakeSd, create, finish
from .test_direct_gen_resident import FakeWorld, make_engine
from .test_direct_gen_warm_queue import HangSecond, auto_service

OWNER = {"X-Participant": "owner"}
EDIT_MODELS = ["qwen-image-2.1-uc-edit", "qwen-image-edit-2509"]
EDIT = "qwen-image-2.1-uc-edit"


async def edit(env, model=EDIT, seed=5):
    r = await create(env, model=model, prompt="make @1 smile", image_b64=base64.b64encode(PNG).decode(), seed=seed)
    assert r.status_code == 202, r.text
    return await finish(env, r.json()["job_id"], timeout=10)


async def gone(engine) -> None:
    await wait_for(lambda: asyncio.sleep(0, result=(not engine.alive()) or None), timeout=3)


# ----------------------------------------------------------------------------------------------- the owner's case


@pytest.mark.parametrize("model", EDIT_MODELS)
async def test_lone_edit_job_uses_the_resident_engine_and_stays_loaded(env, tmp_path, monkeypatch, model):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    first = await edit(env, model)
    p = first["provenance"]
    assert first["status"] == "completed", first.get("error")
    assert p["engine"] == "sd-server (resident)" and p["engine_warm"] is False         # one cold load, on the server
    assert "single job: no queued job" not in p["engine_decision"]
    assert p["engine_decision"].startswith("keep-warm edit model: resident engine even for a single job")
    assert "kept loaded 30 min after the job" in p["engine_decision"]
    assert not FakeSd.instances                                                          # the cold sd-cli never ran
    eng = svc._resident
    assert eng.alive() and eng.kept() and not world.killed_pids                          # STILL loaded after the job
    status = (await env.client.get("/api/direct-gen/status", headers=OWNER)).json()["resident"]
    assert status["loaded"] is True and 1700 < status["keep_warm_left_s"] <= 1800
    assert status["keep_warm_s"] == resident.KEEP_WARM_S == 1800.0 and model in status["keep_warm_models"]

    second = await edit(env, model, seed=6)
    q = second["provenance"]
    assert second["status"] == "completed"
    assert q["engine_warm"] is True and q["engine_start_seconds"] == 0.0                 # the load was skipped
    assert second["timing"]["load"] == "warm" and second["timing"]["engine"] == "sd-server"
    assert q["engine_decision"].startswith("keep-warm edit model: weights still loaded from the previous job")
    assert len(world.procs) == 1 and eng.starts == 1 and eng.kept()                      # one load for both edits
    assert [r["seed"] for r in world.requests] == [5, 6]
    eng.kill_now()


async def test_window_restarts_with_every_job_and_expiry_releases(env, tmp_path, monkeypatch):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    assert (await edit(env))["status"] == "completed"
    eng = svc._resident
    first_deadline = eng.keep_until
    await asyncio.sleep(0.05)
    assert (await edit(env, seed=6))["provenance"]["engine_warm"] is True               # inside the window: warm
    assert eng.keep_until > first_deadline and eng.kept()                               # the window was restarted
    svc.keep_warm_s = 0.05                                                              # a short window from now on
    assert (await edit(env, seed=7))["provenance"]["engine_warm"] is True
    await gone(eng)                                                                     # the window ends -> released
    assert world.killed_pids == [5000] and eng.keep_until == 0.0 and eng._idle is None
    last = await edit(env, seed=8)
    assert last["provenance"]["engine_warm"] is False and eng.starts == 2               # after the window: cold again
    eng.kill_now()


# ----------------------------------------------------------------------------------------------- safety releases


async def test_low_memory_after_the_job_releases_and_says_so(env, tmp_path, monkeypatch):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    svc.keep_warm_min_free = 10 ** 15                                                   # more than any PC has free
    job = await edit(env)
    assert job["status"] == "completed" and job["provenance"]["engine"] == "sd-server (resident)"
    assert "released after the job: low memory" in job["provenance"]["engine_decision"]
    assert not svc._resident.alive() and world.killed_pids == [5000]
    assert "low memory" in svc._resident.status()["keep_warm_released"]


async def test_low_memory_or_owner_stop_while_kept_releases(env, tmp_path, monkeypatch):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    svc.keep_warm_check_s = 0.02
    free = {"b": 64 * sdcli.GIB}
    svc._free_memory = lambda: free["b"]
    assert (await edit(env))["status"] == "completed"
    eng = svc._resident
    await asyncio.sleep(0.1)
    assert eng.alive() and eng.kept()                                                   # plenty of memory: kept
    free["b"] = 4 * sdcli.GIB                                                           # another program took memory
    await gone(eng)
    assert world.killed_pids == [5000] and "low memory" in eng.keep_released

    free["b"] = 64 * sdcli.GIB
    assert (await edit(env, seed=6))["status"] == "completed" and eng.kept()
    stop = Path(svc.svc.settings.data_dir) / "computer" / "STOP"
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("stop", encoding="utf-8")
    try:
        await gone(eng)
        assert "owner STOP" in eng.keep_released and world.killed_pids == [5000, 5001]
    finally:
        stop.unlink()


async def test_another_model_or_a_video_releases_the_kept_edit_model_first(env, tmp_path, monkeypatch):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    assert (await edit(env))["status"] == "completed" and svc._resident.kept()
    other = await finish(env, (await create(env, model="z-image-turbo")).json()["job_id"])
    assert other["status"] == "completed" and other["provenance"]["engine"] == "sd-cli"   # non-edit model: old path
    assert "single job: no queued job of the same model" == other["provenance"]["engine_decision"]
    assert world.killed_pids == [5000] and not svc._resident.alive()                   # released BEFORE sd-cli loaded
    assert (await edit(env, seed=6))["status"] == "completed" and svc._resident.kept()
    video = await finish(env, (await create(env, model="wan2.2-ti2v-5b", duration=1, resolution="480x272")).json()["job_id"])
    assert video["status"] == "completed" and not svc._resident.alive()                 # released before the video engine
    assert world.killed_pids == [5000, 5001]


async def test_cancel_of_a_warm_edit_kills_the_kept_server(env, tmp_path, monkeypatch):
    world = HangSecond()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    assert (await edit(env))["status"] == "completed" and svc._resident.kept()
    r = await create(env, model=EDIT, prompt="make @1 smile", image_b64=base64.b64encode(PNG).decode(), seed=6)
    await wait_for(lambda: asyncio.sleep(0, result=len(world.requests) >= 2 or None), timeout=5)
    c = await env.client.post(f"/api/direct-gen/jobs/{r.json()['job_id']}/cancel", headers=OWNER)
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert world.killed_pids == [5000] and not svc._resident.alive() and svc._resident.keep_until == 0.0


# ----------------------------------------------------------------------------------------------- unchanged elsewhere


async def test_video_and_non_edit_models_are_unchanged(env, tmp_path, monkeypatch):
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    assert not {s.id for s in sdcli.SPECS if s.kind == "video"} & resident.KEEP_WARM_MODELS
    assert svc._keep_warm_for(sdcli.BY_ID["wan2.2-ti2v-5b"]) == 0.0
    assert svc._keep_warm_for(sdcli.BY_ID["z-image-turbo"]) == 0.0
    video = await finish(env, (await create(env, model="wan2.2-ti2v-5b", duration=1, resolution="480x272")).json()["job_id"])
    assert video["status"] == "completed" and not world.procs                            # never on the resident server
    assert "engine" not in video["provenance"] and "engine_decision" not in video["provenance"]
    single = await finish(env, (await create(env, model="z-image-turbo")).json()["job_id"])
    assert single["provenance"]["engine"] == "sd-cli" and not world.procs and not svc._resident.alive()


async def test_keep_warm_off_switch_restores_the_single_job_path(env, tmp_path, monkeypatch):
    monkeypatch.setenv(resident.ENV_KEEP_WARM, "0")
    world = FakeWorld()
    svc = auto_service(env, tmp_path, monkeypatch, world)
    assert svc.keep_warm_s == 0.0
    job = await edit(env)
    assert job["provenance"]["engine"] == "sd-cli" and "single job" in job["provenance"]["engine_decision"]
    assert not world.procs and len(FakeSd.instances) == 1
    assert resident.keep_warm_seconds({}) == 1800.0 and resident.keep_warm_seconds({resident.ENV_KEEP_WARM: "x"}) == 1800.0
    assert resident.keep_warm_seconds({resident.ENV_KEEP_WARM: "600"}) == 600.0


async def test_explicit_off_never_starts_the_server_for_edits(env, tmp_path, monkeypatch):
    monkeypatch.setenv(resident.ENV_FLAG, "0")
    from .test_direct_gen_photo import install
    svc = install(env, tmp_path, None)
    assert svc._resident is None
    svc._sd_runner = FakeSd
    job = await edit(env)
    assert job["status"] == "completed" and "engine" not in job["provenance"]           # old per-job path, byte for byte


async def test_engine_keep_warm_primitives(tmp_path):
    world = FakeWorld()
    eng = make_engine(world, tmp_path)
    assert eng.keep_warm(60) is False and not eng.kept()                                # nothing loaded: nothing kept
    await eng.ensure(resident.Plan(startup=("--diffusion-model", "m.gguf"), prompt="p", out=str(tmp_path / "o.png")), "m")
    assert eng.keep_warm(0) is False and not eng.kept()                                 # 0 = off
    assert eng.keep_warm(60) is True and eng.kept() and eng._idle is not None
    eng.kill_now()                                                                      # STOP
    assert not eng.kept() and eng.keep_until == 0.0 and eng._idle is None
