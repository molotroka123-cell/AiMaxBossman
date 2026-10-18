"""Resident sd-server engine for the Direct window: no GPU, no real process, no network.

Proven here: argv -> server parity, spec -> request mapping, lifecycle decisions (same model keeps the server,
another model restarts it, idle unloads it, STOP kills it, anything inexpressible or broken falls back to sd-cli),
and that with the flag OFF nothing changes. NOT proven here: real speed, real quality (see the GPU measurements).
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from bcc.direct_gen import resident, sdcli
from bcc.direct_gen.service import DirectGenService

from .conftest import wait_for
from .test_direct_gen import FakeComfy, make_models
from .test_direct_gen_photo import FakeSd, NEUTRAL, PNG, create, finish, install, make_media

async def _noop(v, m):
    pass


PARAMS = {"width": 384, "height": 384, "steps": 6, "seed": 7, "frames": 1, "fps": 0}


def argv_for(model: str, root: Path, *, prompt=NEUTRAL, negative="", image: Path | None = None, out=None) -> list[str]:
    spec = sdcli.BY_ID[model]
    return sdcli.build_argv(spec, root / "sd-cli.exe", root / "media", PARAMS, prompt, negative,
                            out or root / "image.png", image)


# ----------------------------------------------------------------------------- fakes


class FakeServerProc:
    def __init__(self, pid=4242, log=()):
        self.pid, self.returncode, self.killed = pid, None, False
        self.stdout = asyncio.StreamReader()
        for line in log:
            self.stdout.feed_data(line.encode() + b"\n")

    def feed(self, text: str) -> None:
        self.stdout.feed_data(text.encode())

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.005)
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.stdout.feed_eof()


class FakeWorld:
    """Spawn + HTTP of a scripted sd-server."""

    def __init__(self, *, ready=True, exit_on_start=False, gen_status="completed", steps=3):
        self.procs: list[FakeServerProc] = []
        self.argvs: list[list[str]] = []
        self.requests: list[dict] = []
        self.ready, self.exit_on_start, self.gen_status, self.steps = ready, exit_on_start, gen_status, steps
        self.pending: dict[str, int] = {}
        self.killed_pids: list[int] = []

    async def spawn(self, argv, cwd):
        p = FakeServerProc(pid=5000 + len(self.procs), log=["loading tensors ..."])
        self.procs.append(p)
        self.argvs.append(list(argv))
        if self.exit_on_start:
            p.returncode = 1
            p.stdout.feed_eof()
        return p

    def kill(self, proc):
        if proc is not None and proc.returncode is None:
            self.killed_pids.append(proc.pid)
            proc.kill()

    def http(self, method, url, body=None, timeout=30.0):
        if url.endswith("/sdcpp/v1/capabilities"):
            return (200, {}) if self.ready else (0, None)
        if url.endswith("/sdcpp/v1/img_gen"):
            self.requests.append(body)
            jid = f"job_{len(self.requests)}"
            self.pending[jid] = 0
            return 202, {"id": jid, "status": "queued"}
        if "/sdcpp/v1/jobs/" in url:
            jid = url.rsplit("/", 1)[1]
            self.pending[jid] += 1
            if self.gen_status == "hang":
                return 200, {"id": jid, "status": "generating"}
            if self.pending[jid] < 2:
                self.procs[-1].feed(f"  |==>   | 1/{self.steps} - 1.00s/it\r")
                return 200, {"id": jid, "status": "generating"}
            if self.gen_status == "failed":
                return 200, {"id": jid, "status": "failed", "error": {"code": "generation_failed", "message": "boom"}}
            return 200, {"id": jid, "status": "completed",
                         "result": {"output_format": "png", "images": [{"index": 0, "b64_json": base64.b64encode(PNG).decode()}]}}
        raise AssertionError(url)


def make_engine(world: FakeWorld, tmp_path: Path, idle_s=60.0) -> resident.ResidentEngine:
    return resident.ResidentEngine(tmp_path / "sd-server.exe", idle_s=idle_s, spawn=world.spawn, http=world.http,
                                   kill=world.kill, bind=lambda pid: None, port_picker=lambda: 18080, poll_s=0.005,
                                   ready_timeout_s=2.0, cwd=tmp_path)


def make_runner(engine, tag, tmp_path, cli=None):
    written: dict[str, bytes] = {}
    runner = resident.ResidentRunner(engine, tag=tag, cli_factory=cli or (lambda: FakeSd()),
                                     read_bytes=lambda p: Path(p).read_bytes(), write_bytes=lambda p, b: written.__setitem__(p, b))
    runner.written = written
    return runner


# ----------------------------------------------------------------------------- flag parity / request mapping


PHOTO_T2I = [s.id for s in sdcli.SPECS if s.kind == "photo" and not s.requires_image]


@pytest.mark.parametrize("model", PHOTO_T2I)
def test_every_photo_spec_maps_one_to_one(model, tmp_path):
    media, _ = make_media(tmp_path, only=(model,))
    argv = argv_for(model, tmp_path, negative="blurry")
    plan = resident.plan_argv(argv)
    assert plan.fallback == "", plan.fallback
    rebuilt = [*plan.startup, "-p", plan.prompt, "-n", plan.negative, "-W", str(plan.width), "-H", str(plan.height),
               "--steps", str(plan.steps), "-s", str(plan.seed), "-o", plan.out]
    # parity: exactly the tokens build_argv produced (order of flag groups aside), nothing added, nothing dropped
    assert sorted(rebuilt) == sorted(argv[1:])
    assert (plan.width, plan.height, plan.steps, plan.seed) == (384, 384, 6, 7)
    assert not set(plan.startup) & {"-p", "-W", "-H", "--steps", "-s", "-o"}   # per-job values never pin the engine


def test_request_carries_job_values_verbatim_and_nothing_model_specific(tmp_path):
    make_media(tmp_path)
    prompt = "  a lighthouse   at dusk,\nfilm photo — EXACT  "
    plan = resident.plan_argv(argv_for("qwen-image-2.1-uc", tmp_path, prompt=prompt, negative="neg text"))
    body = resident.to_request(plan)
    assert body["prompt"] == prompt and body["negative_prompt"] == "neg text"
    assert (body["width"], body["height"], body["seed"], body["sample_params"]) == (384, 384, 7, {"sample_steps": 6})
    assert "ref_images" not in body and body["batch_count"] == 1
    assert "cfg_scale" not in json.dumps(body) and "txt_cfg" not in json.dumps(body)   # cfg stays the startup default
    assert "--diffusion-model" in plan.startup and "--cfg-scale" in plan.startup and "--offload-to-cpu" in plan.startup


def test_reference_images_are_base64_in_order(tmp_path):
    make_media(tmp_path)
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"AAA")
    b.write_bytes(b"BBB")
    argv = argv_for("qwen-image-edit-2509", tmp_path, image=a) + ["-r", str(b)]      # multi -r (consolidated branch)
    plan = resident.plan_argv(argv)
    assert plan.fallback == "" and plan.refs == (str(a), str(b))
    body = resident.to_request(plan)
    assert [base64.b64decode(x) for x in body["ref_images"]] == [b"AAA", b"BBB"]
    assert "-r" not in plan.startup and "--llm_vision" in plan.startup


def test_aliases_and_negative_seed_are_not_mistaken_for_flags(tmp_path):
    plan = resident.plan_argv(["x", "--diffusion-model", "m.gguf", "--prompt", "-dash start", "--seed", "-1",
                               "--width", "64", "--height", "64", "--steps", "2", "--output", "o.png"])
    assert plan.fallback == "" and plan.prompt == "-dash start" and plan.seed == -1


@pytest.mark.parametrize("extra,why", [
    (["-M", "vid_gen"], "-M"), (["-i", "init.png"], "-i"), (["--lora-model-dir", "L"], "lora"),
    (["--video-frames", "9"], "video"), (["--mask", "m.png"], "mask"), (["--some-new-flag", "1"], "some-new-flag"),
])
def test_inexpressible_features_fall_back_to_sd_cli(tmp_path, extra, why):
    make_media(tmp_path)
    plan = resident.plan_argv(argv_for("z-image-turbo", tmp_path) + extra)
    assert plan.fallback and why.lower() in plan.fallback.lower()


def test_lora_tag_in_prompt_and_video_specs_fall_back(tmp_path):
    make_media(tmp_path)
    assert "LoRA" in resident.plan_argv(argv_for("z-image-turbo", tmp_path, prompt="x <lora:foo:1>")).fallback
    assert resident.plan_argv(argv_for("wan2.2-ti2v-5b", tmp_path)).fallback          # -M vid_gen
    assert resident.plan_argv(["x", "-p", "p"]).fallback                              # no -o
    assert resident.plan_argv(["x", "-p"]).fallback                                   # dangling flag


def test_env_switches_default_off(monkeypatch):
    monkeypatch.delenv(resident.ENV_FLAG, raising=False)
    assert resident.enabled() is False
    for v in ("", "0", "no", "off", "false"):
        assert resident.enabled({resident.ENV_FLAG: v}) is False
    for v in ("1", "true", "ON", " yes "):
        assert resident.enabled({resident.ENV_FLAG: v}) is True
    assert resident.idle_seconds({}) == 120.0 and resident.idle_seconds({resident.ENV_IDLE: "30"}) == 30.0
    assert resident.idle_seconds({resident.ENV_IDLE: "junk"}) == 120.0


def test_sd_bin_env_accepts_folder_or_exe_and_default_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.delenv("BOSSMAN_DIRECT_GEN_SD_BIN", raising=False)
    monkeypatch.delenv("BOSSMAN_SDCPP_BIN", raising=False)
    assert sdcli.default_binary().parts[-2:] == ("vulkan", "sd-cli.exe")
    monkeypatch.setenv("BOSSMAN_DIRECT_GEN_SD_BIN", str(tmp_path / "vulkan-new"))
    assert sdcli.default_binary() == tmp_path / "vulkan-new" / "sd-cli.exe"
    assert resident.server_binary_for(sdcli.default_binary()) == tmp_path / "vulkan-new" / "sd-server.exe"
    monkeypatch.setenv("BOSSMAN_DIRECT_GEN_SD_BIN", str(tmp_path / "x" / "sd-cli.exe"))
    assert sdcli.default_binary() == tmp_path / "x" / "sd-cli.exe"


# ----------------------------------------------------------------------------- lifecycle


async def test_same_model_keeps_the_server_and_second_job_is_warm(tmp_path):
    make_media(tmp_path)
    world = FakeWorld()
    engine = make_engine(world, tmp_path)
    progress = []

    async def on_progress(v, m):
        progress.append((v, m))

    out1, out2 = tmp_path / "1.png", tmp_path / "2.png"
    r1 = make_runner(engine, "qwen-image-2.1-uc", tmp_path)
    res1 = await r1.run(argv_for("qwen-image-2.1-uc", tmp_path, out=out1), cwd=tmp_path, on_progress=on_progress)
    r2 = make_runner(engine, "qwen-image-2.1-uc", tmp_path)
    res2 = await r2.run(argv_for("qwen-image-2.1-uc", tmp_path, prompt="second", out=out2), cwd=tmp_path)
    assert res1.returncode == 0 and res2.returncode == 0
    assert len(world.procs) == 1 and engine.starts == 1                    # ONE server for both jobs
    assert (r1.warm, r2.warm) == (False, True) and r2.startup_s == 0.0
    assert r1.engine_name == r2.engine_name == "sd-server (resident)" and r1.fallback_reason == ""
    assert r1.written[str(out1)] == PNG and r2.written[str(out2)] == PNG
    assert [q["prompt"] for q in world.requests] == [NEUTRAL, "second"]
    assert progress and progress[0][1] == 3                                  # real step progress from the engine log
    assert "--listen-ip" in world.argvs[0] and "127.0.0.1" in world.argvs[0] and "18080" in world.argvs[0]
    assert not world.killed_pids
    engine.kill_now()


async def test_different_weights_restart_the_server(tmp_path):
    make_media(tmp_path)
    world = FakeWorld()
    engine = make_engine(world, tmp_path)
    await make_runner(engine, "qwen-image-2.1-uc", tmp_path).run(argv_for("qwen-image-2.1-uc", tmp_path), cwd=tmp_path)
    r = make_runner(engine, "z-image-turbo", tmp_path)
    assert await engine.prepare("z-image-turbo") is False                      # other model: released BEFORE the memory check
    assert world.killed_pids == [5000]
    await r.run(argv_for("z-image-turbo", tmp_path), cwd=tmp_path)
    assert len(world.procs) == 2 and r.warm is False
    assert any("z_image_turbo" in a for a in world.argvs[1]) and not any("z_image_turbo" in a for a in world.argvs[0])
    engine.kill_now()


async def test_same_flags_different_cfg_is_a_different_engine(tmp_path):
    make_media(tmp_path)
    world = FakeWorld()
    engine = make_engine(world, tmp_path)
    for model in ("qwen-image-2.1-uc", "qwen-image-2.1-uc", "qwen-image-viggle-turbo"):
        await make_runner(engine, model, tmp_path).run(argv_for(model, tmp_path), cwd=tmp_path)
    assert engine.starts == 2                                                   # uc, uc (warm), viggle (new weights)
    engine.kill_now()


async def test_idle_timeout_unloads_and_next_job_cold_starts(tmp_path):
    make_media(tmp_path)
    world = FakeWorld()
    engine = make_engine(world, tmp_path, idle_s=0.05)
    await make_runner(engine, "z-image-turbo", tmp_path).run(argv_for("z-image-turbo", tmp_path), cwd=tmp_path)
    assert engine.alive()
    await asyncio.sleep(0.01)
    assert engine.alive()                                                       # still within the idle window
    await wait_for(lambda: asyncio.sleep(0, result=(not engine.alive()) or None), timeout=2)
    assert world.killed_pids == [5000] and not engine.alive()
    r = make_runner(engine, "z-image-turbo", tmp_path)
    await r.run(argv_for("z-image-turbo", tmp_path), cwd=tmp_path)
    assert r.warm is False and engine.starts == 2
    engine.kill_now()


async def test_next_job_inside_idle_window_cancels_the_unload(tmp_path):
    make_media(tmp_path)
    world = FakeWorld()
    engine = make_engine(world, tmp_path, idle_s=0.15)
    await make_runner(engine, "z-image-turbo", tmp_path).run(argv_for("z-image-turbo", tmp_path), cwd=tmp_path)
    await asyncio.sleep(0.1)
    assert await engine.prepare("z-image-turbo") is True                       # warm: memory check will be skipped
    await asyncio.sleep(0.2)
    assert engine.alive() and not world.killed_pids
    engine.kill_now()


async def test_stop_kills_the_server_mid_generation(tmp_path):
    make_media(tmp_path)
    world = FakeWorld(gen_status="hang")
    engine = make_engine(world, tmp_path)
    runner = make_runner(engine, "z-image-turbo", tmp_path)
    task = asyncio.create_task(runner.run(argv_for("z-image-turbo", tmp_path), cwd=tmp_path))
    await wait_for(lambda: asyncio.sleep(0, result=bool(world.requests) or None), timeout=2)
    runner.kill()                                                               # what the service does on STOP
    assert world.killed_pids == [5000] and runner.proc is world.procs[0]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not engine.alive() and not runner.written                           # no result file was produced
    assert engine._idle is None                                                 # nothing armed to fire later


async def test_cancel_before_kill_still_kills_the_server(tmp_path):
    make_media(tmp_path)
    world = FakeWorld(gen_status="hang")
    engine = make_engine(world, tmp_path)
    runner = make_runner(engine, "z-image-turbo", tmp_path)
    task = asyncio.create_task(runner.run(argv_for("z-image-turbo", tmp_path), cwd=tmp_path))
    await wait_for(lambda: asyncio.sleep(0, result=bool(world.requests) or None), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert world.killed_pids == [5000]


async def test_unexpressible_job_falls_back_and_releases_resident_memory(tmp_path):
    make_media(tmp_path)
    world = FakeWorld()
    engine = make_engine(world, tmp_path)
    await make_runner(engine, "z-image-turbo", tmp_path).run(argv_for("z-image-turbo", tmp_path), cwd=tmp_path)
    cli = FakeSd()
    r = make_runner(engine, "z-image-turbo", tmp_path, cli=lambda: cli)
    res = await r.run(argv_for("z-image-turbo", tmp_path, prompt="x <lora:a:1>", out=tmp_path / "f.png"), cwd=tmp_path, on_progress=_noop)
    assert res.returncode == 0 and cli.argv and r.engine_name == "sd-cli" and "LoRA" in r.fallback_reason
    assert world.killed_pids == [5000] and not engine.alive()                   # the resident weights are gone before sd-cli loads its own
    assert not world.requests[1:]                                               # nothing was sent to the server for the fallback job


async def test_server_start_failure_falls_back_to_sd_cli(tmp_path):
    make_media(tmp_path)
    for world in (FakeWorld(exit_on_start=True), FakeWorld(ready=False)):
        engine = make_engine(world, tmp_path)
        engine.ready_timeout_s = 0.1
        cli = FakeSd()
        r = make_runner(engine, "z-image-turbo", tmp_path, cli=lambda: cli)
        res = await r.run(argv_for("z-image-turbo", tmp_path, out=tmp_path / "g.png"), cwd=tmp_path, on_progress=_noop)
        assert res.returncode == 0 and cli.argv and r.engine_name == "sd-cli"
        assert r.fallback_reason.startswith("sd-server unavailable")
        assert not engine.alive()


async def test_failed_job_is_an_error_not_a_picture_and_server_is_dropped(tmp_path):
    make_media(tmp_path)
    world = FakeWorld(gen_status="failed")
    engine = make_engine(world, tmp_path)
    r = make_runner(engine, "z-image-turbo", tmp_path)
    res = await r.run(argv_for("z-image-turbo", tmp_path), cwd=tmp_path)
    assert res.returncode == 1 and "boom" in " ".join(res.log) and not r.written
    assert not engine.alive()


async def test_server_crash_mid_job_is_reported(tmp_path):
    make_media(tmp_path)
    world = FakeWorld(gen_status="hang")
    engine = make_engine(world, tmp_path)
    runner = make_runner(engine, "z-image-turbo", tmp_path)
    task = asyncio.create_task(runner.run(argv_for("z-image-turbo", tmp_path), cwd=tmp_path))
    await wait_for(lambda: asyncio.sleep(0, result=bool(world.requests) or None), timeout=2)
    world.procs[0].returncode = 3
    res = await asyncio.wait_for(task, 2)
    assert res.returncode == 1 and "exited" in " ".join(res.log)


# ----------------------------------------------------------------------------- through the service


async def test_flag_off_is_the_old_path_byte_for_byte(env, tmp_path, monkeypatch):
    monkeypatch.delenv(resident.ENV_FLAG, raising=False)
    svc = install(env, tmp_path, None)                                         # no injected runner -> real default wiring
    assert svc._resident is None and svc._sd_runner is sdcli.SdCliRunner
    svc._sd_runner = FakeSd
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["status"] == "completed" and "engine" not in job["provenance"]
    assert FakeSd.instances and FakeSd.instances[0].argv[0].endswith("sd-cli.exe")


async def test_injected_runner_wins_over_the_flag(env, tmp_path, monkeypatch):
    monkeypatch.setenv(resident.ENV_FLAG, "1")
    svc = install(env, tmp_path, FakeSd)
    assert svc._resident is None


async def test_flag_on_service_runs_through_one_resident_server_and_journals_it(env, tmp_path, monkeypatch):
    monkeypatch.setenv(resident.ENV_FLAG, "1")
    svc = install(env, tmp_path, None)
    assert svc._resident is not None
    world = FakeWorld()
    svc._resident = make_engine(world, tmp_path)
    svc._resident.cwd = tmp_path
    jobs = []
    for seed in (1, 2):
        r = await create(env, model="qwen-image-2.1-uc", seed=seed, steps=6)
        assert r.status_code == 202, r.text
        jobs.append(await finish(env, r.json()["job_id"]))
    first, second = jobs
    assert first["status"] == second["status"] == "completed"
    assert first["provenance"]["engine"] == "sd-server (resident)" and first["provenance"]["engine_warm"] is False
    assert second["provenance"]["engine_warm"] is True and second["provenance"]["engine_start_seconds"] == 0.0
    assert len(world.procs) == 1 and [q["seed"] for q in world.requests] == [1, 2]
    assert (await env.client.get("/api/direct-gen/status", headers={"X-Participant": "owner"})).status_code in (200, 404)
    # an I2I/other model afterwards: the old server is released first, then a new one starts
    r = await create(env, model="z-image-turbo", seed=3)
    third = await finish(env, r.json()["job_id"])
    assert third["status"] == "completed" and len(world.procs) == 2 and world.killed_pids == [5000]
    svc._resident.kill_now()


async def test_memory_check_is_skipped_only_for_the_resident_model(env, tmp_path, monkeypatch):
    monkeypatch.setenv(resident.ENV_FLAG, "1")
    low = 1 * 1024 ** 3
    svc = install(env, tmp_path, None, free=low)                                # far less free memory than the model needs
    world = FakeWorld()
    svc._resident = make_engine(world, tmp_path)
    cold = await finish(env, (await create(env, model="z-image-turbo")).json()["job_id"])
    assert cold["status"] == "failed" and cold["error"]["code"] == "insufficient_memory" and not world.procs   # cold: refused
    # make the model resident (as if a previous job had loaded it), memory now "low" because it is occupying it
    plan = resident.plan_argv(argv_for("z-image-turbo", tmp_path))
    await svc._resident.ensure(plan, "z-image-turbo")
    warm = await finish(env, (await create(env, model="z-image-turbo")).json()["job_id"])
    assert warm["status"] == "completed" and warm["provenance"]["engine_warm"] is True
    other = await finish(env, (await create(env, model="flux1-schnell")).json()["job_id"])
    assert other["status"] == "failed" and other["error"]["code"] == "insufficient_memory"   # released, then checked: still refused
    assert not svc._resident.alive()
    svc._resident.kill_now()


async def test_stop_through_the_service_kills_the_resident_server(env, tmp_path, monkeypatch):
    monkeypatch.setenv(resident.ENV_FLAG, "1")
    svc = install(env, tmp_path, None)
    world = FakeWorld(gen_status="hang")
    svc._resident = make_engine(world, tmp_path)
    r = await create(env, model="z-image-turbo")
    job_id = r.json()["job_id"]
    await wait_for(lambda: asyncio.sleep(0, result=bool(world.requests) or None), timeout=3)
    c = await env.client.post(f"/api/direct-gen/jobs/{job_id}/cancel", headers={"X-Participant": "owner"})
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert world.killed_pids == [5000] and not svc._resident.alive()
    assert not list((tmp_path).rglob("image.png"))
