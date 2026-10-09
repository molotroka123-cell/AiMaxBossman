"""Direct window: photo + video models through sd-cli, with FAKE runners and a fake file tree.

No GPU, no real generation. Prompts are neutral. What is proven here:
registry from files on disk, the chosen model's own command, verbatim prompt,
real step progress, STOP killing the process tree, honest refusals.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import psutil

from bcc.direct_gen import sdcli
from bcc.direct_gen.service import DirectGenService

from .conftest import wait_for
from .test_direct_gen import FakeComfy, TERMINAL, make_models

NEUTRAL = "a lighthouse at dusk, film photo"
PNG = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (512).to_bytes(4, "big") + (512).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"


def make_media(root: Path, *, only: tuple[str, ...] | None = None) -> tuple[Path, Path]:
    media = root / "media"
    media.mkdir(parents=True, exist_ok=True)
    for spec in sdcli.SPECS:
        if only is not None and spec.id not in only:
            continue
        for rel in spec.files.values():
            path = media.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"w" * 16)
    binary = root / "sd-cli.exe"
    binary.write_bytes(b"MZ")
    return media, binary


class FakeSd:
    """Scripted sd-cli: records argv, reports steps, writes the file named after ``-o``."""

    instances: list["FakeSd"] = []

    def __init__(self, *, steps: int = 3, hold: bool = False, returncode: int = 0, log=(), write=True):
        self.steps, self.hold, self.returncode, self.log, self.write = steps, hold, returncode, list(log), write
        self.argv: list[str] = []
        self.killed = False
        self.proc = None
        FakeSd.instances.append(self)

    async def run(self, argv, *, cwd, on_progress=None):
        self.argv = list(argv)
        for i in range(1, self.steps + 1):
            await on_progress(i, self.steps)
            await asyncio.sleep(0.01)
        while self.hold and not self.killed:
            await asyncio.sleep(0.01)
        if self.returncode == 0 and self.write:
            out = Path(argv[argv.index("-o") + 1])
            out.write_bytes(PNG if out.suffix == ".png" else b"webm")
        return sdcli.RunResult(self.returncode, self.log, 123 * 1048576, 0.5)

    def kill(self):
        self.killed = True


def install(env, tmp_path, runner, *, free=64 * 1024 ** 3, foreign=(), only=None, foreign_fn=None, grace=0.0):
    media, binary = make_media(tmp_path, only=only)
    models, flows = make_models(tmp_path)
    FakeSd.instances = []
    svc = DirectGenService(env.svc, client=FakeComfy(), models_dir=models, workflows_dir=flows, poll_seconds=0.01,
                           verify=lambda p: {"verified": True, "duration_s": 1.0, "width": 480, "height": 272},
                           media_dir=media, sd_bin=binary, sd_runner=runner,
                           free_memory=lambda: free, foreign_engines=foreign_fn or (lambda: list(foreign)), gpu_grace_seconds=grace)
    env.svc.direct_gen = svc
    return svc


async def create(env, **over):
    body = {"model": "z-image-turbo", "prompt": NEUTRAL, "resolution": "512x512", "seed": 5, "mode": "DIRECT", **over}
    return await env.client.post("/api/direct-gen/jobs", json=body, headers={"X-Participant": "owner"})


async def finish(env, job_id, timeout=5):
    async def check():
        j = (await env.client.get(f"/api/direct-gen/jobs/{job_id}", headers={"X-Participant": "owner"})).json()
        return j if j["status"] in TERMINAL else None
    return await wait_for(check, timeout=timeout)


async def test_registry_from_files_groups_photo_and_video(env, tmp_path):
    install(env, tmp_path, FakeSd)
    by = {m["id"]: m for m in (await env.client.get("/api/direct-gen/models")).json()["models"]}
    for pid in ("z-image-turbo", "flux1-schnell", "flux2-klein-4b", "sdxl-base", "qwen-image-2.1",
                "qwen-image-2.1-uc", "qwen-image-viggle-turbo", "qwen-image-edit-2509"):
        assert by[pid]["kind"] == "photo" and by[pid]["available"] is True and by[pid]["runtime"] == "sdcpp", pid
    assert by["wan2.2-ti2v-5b"]["kind"] == "video" and by["wan2.2-ti2v-5b"]["available"] is True
    assert set(by["wan2.2-ti2v-5b"]["modes"]) == {"T2V", "I2V"}
    assert by["qwen-image-edit-2509"]["modes"] == ["I2I"] and by["qwen-image-edit-2509"]["requires_image"] is True
    assert "vace" in by["wan21-vace-14b"]["reason"].lower() or "VACE" in by["wan21-vace-14b"]["reason"]
    assert by["wan21-vace-14b"]["available"] is False
    uc = by["qwen-image-2.1-uc"]
    assert uc["source"] == "UNKNOWN" and uc["license"] == "UNKNOWN" and uc["revision"] == "UNKNOWN"
    # ComfyUI-side video models are listed too, with their own honest reasons
    assert by["wan2.2-s2v-14b"]["kind"] == "video" and by["wan2.2-s2v-14b"]["runtime"] == "comfyui"
    assert by["wan2.2-animate-14b"]["available"] is False


async def test_missing_weights_and_engine_give_reasons(env, tmp_path):
    install(env, tmp_path, FakeSd, only=("z-image-turbo",))
    by = {m["id"]: m for m in (await env.client.get("/api/direct-gen/models")).json()["models"]}
    assert by["z-image-turbo"]["available"] is True
    assert by["flux1-schnell"]["available"] is False and "weights missing" in by["flux1-schnell"]["reason"]
    r = await create(env, model="flux1-schnell")
    assert r.status_code == 409 and r.json()["error"]["code"] == "model_unavailable"
    (tmp_path / "sd-cli.exe").unlink()
    by = {m["id"]: m for m in (await env.client.get("/api/direct-gen/models")).json()["models"]}
    assert by["z-image-turbo"]["available"] is False and "sd-cli" in by["z-image-turbo"]["reason"]


async def test_selected_model_gets_its_own_command_and_verbatim_prompt(env, tmp_path):
    install(env, tmp_path, FakeSd)
    prompt = "  a lighthouse   at dusk,\nfilm photo — EXACT  "
    for model, marker in (("flux1-schnell", "flux1-schnell-Q8_0.gguf"), ("z-image-turbo", "z_image_turbo-Q8_0.gguf"),
                          ("sdxl-base", "sd_xl_base_1.0.safetensors")):
        r = await create(env, model=model, prompt=prompt, negative="blurry", steps=6)
        assert r.status_code == 202, r.text
        job = await finish(env, r.json()["job_id"])
        assert job["status"] == "completed" and job["model"] == model
        argv = FakeSd.instances[-1].argv
        joined = " ".join(argv)
        assert marker in joined
        assert argv[argv.index("-p") + 1] == prompt                      # nothing rewritten or trimmed
        assert argv[argv.index("-n") + 1] == "blurry" and argv[argv.index("--steps") + 1] == "6"
        assert argv[argv.index("-s") + 1] == "5"
        others = [s for s in sdcli.SPECS if s.id != model and s.manifest_key != model]
        for spec in others:
            unique = Path(spec.files.get("diffusion") or spec.files.get("model", "")).name
            if unique and unique not in marker and unique != Path(sdcli.BY_ID[model].files["vae"]).name:
                assert unique not in joined, f"{model} command mentions {spec.id}"
        assert job["provenance"]["command"][job["provenance"]["command"].index("-p") + 1] == "<prompt>"


async def test_uc_variant_uses_its_own_diffusion_weights_not_stock_qwen(env, tmp_path):
    install(env, tmp_path, FakeSd)
    job = await finish(env, (await create(env, model="qwen-image-2.1-uc")).json()["job_id"])
    assert job["status"] == "completed"
    joined = " ".join(FakeSd.instances[-1].argv)
    assert "qwen-image-2.1-UC-Q4_K_M.gguf" in joined and "qwen_image_2.1-Q8_0.gguf" not in joined
    job = await finish(env, (await create(env, model="qwen-image-viggle-turbo", steps=6)).json()["job_id"])
    argv = FakeSd.instances[-1].argv
    assert "viggle-turbo" in " ".join(argv) and argv[argv.index("--cfg-scale") + 1] == "1.0"


async def test_photo_lifecycle_progress_and_file(env, tmp_path):
    install(env, tmp_path, lambda: FakeSd(steps=4))
    r = await create(env)
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed" and job["kind"] == "photo"
    assert job["progress"] == {"kind": "steps", "value": 4, "max": 4, "percent": 100.0}
    assert job["result"]["mime"] == "image/png" and job["result"]["width"] == 512
    assert job["provenance"]["peak_rss_mb"] == 123
    stages = [t["stage"] for t in job["timeline"]]
    assert stages == ["queued", "loading", "generating", "postprocessing", "completed"]
    f = await env.client.get(f"/api/direct-gen/jobs/{job['job_id']}/file", headers={"X-Participant": "owner"})
    assert f.status_code == 200 and f.content == PNG and f.headers["content-type"] == "image/png"


async def test_video_job_uses_ti2v_flags(env, tmp_path):
    install(env, tmp_path, FakeSd)
    r = await create(env, model="wan2.2-ti2v-5b", resolution="480x272", duration=1, steps=10)
    assert r.status_code == 202, r.text
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed" and job["kind"] == "video" and job["params"]["frames"] == 17
    argv = FakeSd.instances[-1].argv
    assert argv[argv.index("-M") + 1] == "vid_gen" and argv[argv.index("--video-frames") + 1] == "17"
    assert argv[argv.index("-W") + 1] == "480" and argv[argv.index("-H") + 1] == "272"
    assert "umt5-xxl-encoder-Q8_0.gguf" in " ".join(argv) and "-i" not in argv
    assert job["result"]["mime"] == "video/webm"


async def test_edit_model_needs_reference_image_and_passes_it(env, tmp_path):
    import base64
    install(env, tmp_path, FakeSd)
    r = await create(env, model="qwen-image-edit-2509")
    assert r.status_code == 422 and r.json()["error"]["code"] == "image_required"
    r = await create(env, model="qwen-image-edit-2509", image_b64=base64.b64encode(b"not an image").decode())
    assert r.json()["error"]["code"] == "image_invalid"
    r = await create(env, model="z-image-turbo", image_b64=base64.b64encode(PNG).decode())
    assert r.json()["error"]["code"] == "image_unsupported"
    r = await create(env, model="qwen-image-edit-2509", image_b64=base64.b64encode(PNG).decode())
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed"
    argv = FakeSd.instances[-1].argv
    assert argv[argv.index("-r") + 1].endswith(".png") and "--llm_vision" in argv


async def test_not_enough_memory_is_a_clear_refusal_not_a_crash(env, tmp_path):
    install(env, tmp_path, FakeSd, free=1024)
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["status"] == "failed" and job["error"]["code"] == "insufficient_memory"
    assert "nothing was started" in job["error"]["message"]
    assert FakeSd.instances[-1].argv == []                                # the engine never ran


async def test_foreign_engine_blocks_start(env, tmp_path):
    install(env, tmp_path, FakeSd, foreign=(4242,))
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["status"] == "failed" and job["error"]["code"] == "gpu_busy" and "4242" in job["error"]["message"]


async def test_a_just_finished_engine_does_not_fail_the_next_job_with_gpu_busy(env, tmp_path):
    """Seen 09.10 on the owner PC: the previous sd-cli had already finished but was still listed for a moment; the next job died with gpu_busy."""
    polls = {"n": 0}

    def foreign():
        polls["n"] += 1
        return [4242] if polls["n"] <= 3 else []

    install(env, tmp_path, FakeSd, foreign_fn=foreign, grace=5.0)
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["status"] == "completed", job.get("error")
    assert polls["n"] >= 4


async def test_a_persistent_foreign_engine_still_blocks_after_the_grace_period(env, tmp_path):
    install(env, tmp_path, FakeSd, foreign=(4242,), grace=0.3)
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["status"] == "failed" and job["error"]["code"] == "gpu_busy"


async def test_engine_failure_is_reported_with_its_own_text(env, tmp_path):
    install(env, tmp_path, lambda: FakeSd(returncode=3, log=["something broke"]))
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["error"]["code"] == "engine_error" and "something broke" in job["error"]["message"]
    install(env, tmp_path, lambda: FakeSd(returncode=1, log=["ggml: out of memory"]))
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["error"]["code"] == "out_of_memory"
    install(env, tmp_path, lambda: FakeSd(write=False))
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["error"]["code"] == "empty_output"


async def test_stop_kills_the_engine_and_leaves_no_result(env, tmp_path):
    install(env, tmp_path, lambda: FakeSd(steps=2, hold=True))
    job_id = (await create(env)).json()["job_id"]

    async def running():
        j = (await env.client.get(f"/api/direct-gen/jobs/{job_id}", headers={"X-Participant": "owner"})).json()
        return j if j["stage"] == "generating" else None
    await wait_for(running)
    r = await env.client.post(f"/api/direct-gen/jobs/{job_id}/cancel", headers={"X-Participant": "owner"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert FakeSd.instances[-1].killed is True
    assert not (env.svc.direct_gen.store.job_dir("owner", job_id) / "result").exists()


async def test_retry_repeats_seed_steps_and_prompt(env, tmp_path):
    install(env, tmp_path, FakeSd)
    first = await finish(env, (await create(env, steps=7, seed=99)).json()["job_id"])
    r = await env.client.post(f"/api/direct-gen/jobs/{first['job_id']}/retry", headers={"X-Participant": "owner"})
    again = await finish(env, r.json()["job_id"])
    assert again["params"]["seed"] == 99 and again["params"]["steps"] == 7
    assert again["raw_prompt"] == NEUTRAL and again["retry_of"] == first["job_id"]


async def test_one_heavy_job_at_a_time(env, tmp_path):
    install(env, tmp_path, lambda: FakeSd(steps=1, hold=True))
    a = (await create(env)).json()["job_id"]
    b = (await create(env)).json()
    assert b["status"] == "queued" and b["queue_position"] >= 1
    await env.client.post(f"/api/direct-gen/jobs/{b['job_id']}/cancel", headers={"X-Participant": "owner"})
    await env.client.post(f"/api/direct-gen/jobs/{a}/cancel", headers={"X-Participant": "owner"})
    await finish(env, a)


async def test_invalid_inputs(env, tmp_path):
    install(env, tmp_path, FakeSd)
    assert (await create(env, resolution="500x500")).json()["error"]["code"] == "invalid_resolution"
    assert (await create(env, steps=500)).status_code == 422
    assert (await create(env, steps=99)).json()["error"]["code"] == "invalid_steps"
    assert (await create(env, model="wan2.2-ti2v-5b", resolution="480x272", duration=60)).json()["error"]["code"] == "invalid_duration"
    assert (await create(env, model="wan21-vace-14b")).json()["error"]["code"] == "model_unavailable"


# ---- the real process runner, driven by a harmless python child (no sd-cli, no GPU) ----

CHILD = textwrap.dedent("""
    import subprocess, sys, time
    kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    open(sys.argv[1], "w").write(str(kid.pid))
    for i in range(1, 4):
        sys.stdout.write("  |=====>      | %d/3 - 0.50s/it\\r" % i); sys.stdout.flush()
        time.sleep(0.2)
    sys.stdout.write("log line\\n"); sys.stdout.flush()
    time.sleep(120)
""")


async def test_runner_parses_real_progress_and_stop_kills_whole_tree(tmp_path):
    pidfile = tmp_path / "kid.pid"
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    seen: list[tuple[int, int]] = []

    async def on_progress(v, m):
        seen.append((v, m))

    runner = sdcli.SdCliRunner()
    task = asyncio.create_task(runner.run([sys.executable, str(script), str(pidfile)], cwd=tmp_path, on_progress=on_progress))
    await wait_for(lambda: _done(len(seen) >= 3), timeout=20)
    assert seen[:3] == [(1, 3), (2, 3), (3, 3)]
    kid = int(pidfile.read_text())
    parent = runner.proc.pid
    assert psutil.pid_exists(kid) and psutil.pid_exists(parent)
    runner.kill()
    result = await asyncio.wait_for(task, 20)
    assert result.returncode != 0
    await asyncio.sleep(0.5)
    assert not psutil.pid_exists(parent) and not psutil.pid_exists(kid)


async def _done(value):
    return value


def test_build_argv_is_pure_and_json_safe(tmp_path):
    media, binary = make_media(tmp_path)
    spec = sdcli.BY_ID["flux1-schnell"]
    argv = sdcli.build_argv(spec, binary, media, {"width": 512, "height": 512, "steps": 4, "seed": 1}, NEUTRAL, "", tmp_path / "o.png", None)
    json.dumps(argv)
    assert "-n" not in argv and argv[argv.index("-p") + 1] == NEUTRAL
    assert subprocess.list2cmdline(argv)


def test_lora_dir_is_passed_to_sd_cli_only_when_the_prompt_names_a_lora(tmp_path, monkeypatch):
    """The owner's own LoRA (trained 09.10) reaches sd-cli: --lora-model-dir, and only for a prompt that carries <lora:...>."""
    media, binary = make_media(tmp_path)
    loras = tmp_path / "loras"
    loras.mkdir()
    spec = sdcli.BY_ID["epicrealism-xl"]
    params = {"width": 512, "height": 512, "steps": 25, "seed": 1}
    tagged = "pchela, portrait <lora:pchela-lora:0.8>"
    monkeypatch.delenv("BOSSMAN_DIRECT_GEN_LORA_DIR", raising=False)
    assert "--lora-model-dir" not in sdcli.build_argv(spec, binary, media, params, tagged, "", tmp_path / "o.png", None)
    monkeypatch.setenv("BOSSMAN_DIRECT_GEN_LORA_DIR", str(loras))
    on = sdcli.build_argv(spec, binary, media, params, tagged, "", tmp_path / "o.png", None)
    assert on[on.index("--lora-model-dir") + 1] == str(loras)
    assert on[on.index("-p") + 1] == tagged, "the prompt is still passed verbatim"
    assert "--lora-model-dir" not in sdcli.build_argv(spec, binary, media, params, NEUTRAL, "", tmp_path / "o.png", None)
    monkeypatch.setenv("BOSSMAN_DIRECT_GEN_LORA_DIR", str(tmp_path / "missing"))
    assert "--lora-model-dir" not in sdcli.build_argv(spec, binary, media, params, tagged, "", tmp_path / "o.png", None)


def test_shipped_s2v_template_is_valid_and_fully_parameterised():
    from bcc.direct_gen import catalog
    from bcc.direct_gen.client import validate_template
    from bcc.direct_gen.service import KNOWN, fill_template, placeholders
    tpl = json.loads(catalog.template_path(Path("nowhere"), "wan2.2-s2v-14b").read_text(encoding="utf-8"))
    validate_template(tpl)
    assert placeholders(tpl) == {"prompt", "negative", "seed", "width", "height", "frames", "fps", "image", "audio"}
    assert placeholders(tpl) <= KNOWN
    filled = fill_template(tpl, {"prompt": NEUTRAL, "negative": "", "seed": 1, "width": 832, "height": 480,
                                 "frames": 77, "fps": 16, "image": "a.png", "audio": "a.wav"})
    assert filled["8"]["inputs"]["text"] == NEUTRAL and not placeholders(filled)
    assert catalog.BY_ID["wan2.2-s2v-14b"].requires_audio


async def test_s2v_becomes_available_when_its_weights_exist(env, tmp_path):
    svc = install(env, tmp_path, FakeSd)
    files = ("diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors", "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
             "vae/wan_2.1_vae.safetensors", "audio_encoders/wav2vec2_large_english_fp16.safetensors",
             "loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors")
    for rel in files:
        path = svc.models_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 8)
    by = {m["id"]: m for m in svc.models()}
    assert by["wan2.2-s2v-14b"]["available"] is True and by["wan2.2-s2v-14b"]["modes"] == ["S2V"]
