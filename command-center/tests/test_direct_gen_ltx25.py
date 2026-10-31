"""Direct window: LTX-2.5 through sd-cli, the same run Motion Studio uses (tools/tiktok_outfit_clip.py gen).

FAKE runners and a fake file tree: no GPU, no real generation, neutral prompts. Proven here: the catalogue entry is
computed from the real file layout (each missing file named), the job builds Motion Studio's argv for T2V and I2V,
the measured memory ceiling (576x1024) is enforced, STOP kills the engine, one-shot bars are not sampling steps.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from bcc.direct_gen import catalog, sdcli, video_progress

from .conftest import wait_for
from .test_direct_gen import make_models
from .test_direct_gen_photo import PNG, FakeSd, create, finish, install

NEUTRAL = "a paper boat floating on a calm lake, sunrise"
LTX = sdcli.BY_ID["ltx-2.5"]


async def _by_id(env) -> dict:
    return {m["id"]: m for m in (await env.client.get("/api/direct-gen/models")).json()["models"]}


# ------------------------------------------------------------------------------------------------- catalogue
async def test_ltx25_is_listed_available_with_the_proven_limits(env, tmp_path):
    install(env, tmp_path, FakeSd)
    m = (await _by_id(env))["ltx-2.5"]
    assert m["available"] is True and m["reason"] == ""
    assert (m["kind"], m["family"], m["runtime"], m["modes"]) == ("video", "ltx", "sdcpp", ["T2V", "I2V"])
    assert (m["fps"], m["max_seconds"], m["min_side"], m["max_side"], m["side_multiple"]) == (24, 5, 256, 1024, 32)
    assert m["max_pixels"] == 576 * 1024 and m["default_resolution"] == "512x288"
    assert (m["default_steps"], m["max_steps"]) == (8, 8)
    assert m["accepts_image"] is True and m["requires_image"] is False and m["max_refs"] == 1
    assert {w["role"] for w in m["weights"]} == {"diffusion", "vae", "audio_vae", "llm"}
    assert m["license"] == "UNKNOWN" and "не проверена" in m["notes"]


def test_ltx25_files_are_the_motion_studio_files():
    """The catalogue points at exactly the files tools/tiktok_outfit_clip.py runs (distilled Q6_K by default)."""
    assert LTX.files == {"diffusion": "ltx-2.5/distilled/ltx-2.5-22b-distilled-transformer-Q6_K.gguf",
                         "vae": "ltx-2.5/vae/ltx-2.5-video-vae-conv-bf16.safetensors",
                         "audio_vae": "ltx-2.5/vae/ltx-2.5-audio-vae-bf16.safetensors",
                         "llm": "ltx-2.5/text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors"}
    assert sdcli.binary_for(LTX, Path("X/vulkan/sd-cli.exe")) == Path("X/vulkan/sd-cli.exe")   # default build


@pytest.mark.parametrize("role", ["diffusion", "vae", "audio_vae", "llm"])
def test_each_missing_ltx25_file_is_named_in_the_reason(tmp_path, role):
    media, binary = _media(tmp_path)
    media.joinpath(*LTX.files[role].split("/")).unlink()
    info = sdcli.describe(LTX, media, binary)
    assert info["available"] is False
    assert f"{role} ({LTX.files[role]})" in info["reason"] and "weights missing on disk" in info["reason"]


def test_empty_weight_file_or_missing_engine_is_not_ready(tmp_path):
    media, binary = _media(tmp_path)
    media.joinpath(*LTX.files["llm"].split("/")).write_bytes(b"")          # an interrupted download
    assert "llm (" in sdcli.describe(LTX, media, binary)["reason"]
    media, binary = _media(tmp_path / "b")
    binary.unlink()
    info = sdcli.describe(LTX, media, binary)
    assert info["available"] is False and "sd-cli" in info["reason"]
    assert sdcli.describe(LTX, *_media(tmp_path / "c"))["available"] is True          # negative control


def test_old_comfyui_ltx_video_spec_is_retired(tmp_path):
    assert "ltx-video" not in catalog.BY_ID
    assert "ltx-video" not in {m["id"] for m in catalog.list_models(*make_models(tmp_path))}


def _media(root):
    root.mkdir(parents=True, exist_ok=True)
    media = root / "media"
    for rel in LTX.files.values():
        p = media.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"w" * 16)
    binary = root / "sd-cli.exe"
    binary.write_bytes(b"MZ")
    return media, binary


async def test_job_refused_with_reason_when_weights_missing(env, tmp_path):
    install(env, tmp_path, FakeSd, only=("z-image-turbo",))
    r = await create(env, model="ltx-2.5", resolution="512x288", duration=2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "model_unavailable"
    assert "ltx-2.5/distilled/ltx-2.5-22b-distilled-transformer-Q6_K.gguf" in r.json()["error"]["message"]
    assert not FakeSd.instances                                            # nothing was started


# ------------------------------------------------------------------------------------------------- job / argv
def _flag(argv, name):
    return argv[argv.index(name) + 1]


async def test_t2v_job_builds_motion_studio_argv(env, tmp_path):
    svc = install(env, tmp_path, FakeSd)
    r = await create(env, model="ltx-2.5", prompt=NEUTRAL, resolution="512x288", duration=4, steps=8, seed=11)
    assert r.status_code == 202, r.text
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed" and job["kind"] == "video", job
    assert job["params"]["frames"] == 97 and job["params"]["fps"] == 24              # 8k+1 frames at 24 fps
    argv = FakeSd.instances[-1].argv
    media = svc.media_dir
    assert argv[0] == str(tmp_path / "sd-cli.exe") and _flag(argv, "-M") == "vid_gen"
    assert _flag(argv, "--diffusion-model") == str(media / "ltx-2.5" / "distilled" / "ltx-2.5-22b-distilled-transformer-Q6_K.gguf")
    assert _flag(argv, "--vae").endswith("ltx-2.5-video-vae-conv-bf16.safetensors")
    assert _flag(argv, "--audio-vae").endswith("ltx-2.5-audio-vae-bf16.safetensors")
    assert _flag(argv, "--llm").endswith("gemma4-12b-with-proj-ltx-2.5-bf16.safetensors")
    assert _flag(argv, "-p") == NEUTRAL                                               # verbatim, nothing added
    assert (_flag(argv, "--cfg-scale"), _flag(argv, "--sampling-method"), _flag(argv, "--steps")) == ("1.0", "euler", "8")
    assert (_flag(argv, "-W"), _flag(argv, "-H"), _flag(argv, "--video-frames"), _flag(argv, "--fps"), _flag(argv, "-s")) == \
        ("512", "288", "97", "24", "11")
    for f in ("--offload-to-cpu", "--vae-tiling", "--diffusion-fa", "--mmap"):
        assert f in argv
    assert "-i" not in argv and "-n" not in argv and "--t5xxl" not in argv            # T2V, no negative written
    assert _flag(argv, "-o").endswith("video.webm") and job["result"]["mime"] == "video/webm"
    prov = job["provenance"]
    assert prov["runtime"] == "sdcpp" and prov["model_id"] == "ltx-2.5" and NEUTRAL not in " ".join(prov["command"])


async def test_i2v_job_passes_the_start_picture_and_the_negative(env, tmp_path):
    install(env, tmp_path, FakeSd)
    r = await create(env, model="ltx-2.5", prompt=NEUTRAL, negative="text, watermark", resolution="576x1024",
                     duration=5, image_b64=base64.b64encode(PNG).decode())
    assert r.status_code == 202, r.text
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed", job
    argv = FakeSd.instances[-1].argv
    assert _flag(argv, "-i").endswith("input.png") and _flag(argv, "-n") == "text, watermark"
    assert (_flag(argv, "-W"), _flag(argv, "-H"), _flag(argv, "--video-frames")) == ("576", "1024", "121")
    assert argv.index("-i") < argv.index("-o") == len(argv) - 2


@pytest.mark.parametrize("over,code", [
    ({"resolution": "1024x1024"}, "invalid_resolution"),     # above the measured 576x1024 memory ceiling
    ({"resolution": "1280x720"}, "invalid_resolution"),      # side above 1024
    ({"resolution": "500x288"}, "invalid_resolution"),       # not a multiple of 32
    ({"resolution": "512x288", "duration": 6}, "invalid_duration"),
    ({"resolution": "512x288", "steps": 9}, "invalid_steps"),
])
async def test_values_outside_the_proven_run_are_refused(env, tmp_path, over, code):
    install(env, tmp_path, FakeSd)
    r = await create(env, model="ltx-2.5", **over)
    assert r.status_code == 422 and r.json()["error"]["code"] == code, r.text
    assert not FakeSd.instances


@pytest.mark.parametrize("res", ["576x1024", "1024x576", "256x256"])
def test_pixel_ceiling_admits_the_measured_size(res):              # negative control of the refusal above
    w, h = catalog.parse_resolution(LTX, res)
    assert w * h <= sdcli.LTX25_MAX_PIXELS


def test_pixel_ceiling_does_not_touch_other_models():
    assert catalog.parse_resolution(sdcli.BY_ID["wan2.2-ti2v-5b"], "1280x1280") == (1280, 1280)


# ------------------------------------------------------------------------------------------------- STOP / progress
async def test_stop_kills_the_ltx_engine_and_leaves_no_result(env, tmp_path):
    install(env, tmp_path, lambda: FakeSd(steps=2, hold=True))
    job_id = (await create(env, model="ltx-2.5", resolution="512x288", duration=2)).json()["job_id"]

    async def running():
        j = (await env.client.get(f"/api/direct-gen/jobs/{job_id}", headers={"X-Participant": "owner"})).json()
        return j if j["stage"] == "generating" else None
    await wait_for(running)
    r = await env.client.post(f"/api/direct-gen/jobs/{job_id}/cancel", headers={"X-Participant": "owner"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert FakeSd.instances[-1].killed is True
    assert not (env.svc.direct_gen.store.job_dir("owner", job_id) / "result").exists()


class BarsSd(FakeSd):
    """sd-cli as LTX-2.5 I2V prints it: a 1/1 start-picture encode bar, the 8 sampling steps, a 1/1 decode bar."""

    seen: list[dict] = []

    async def run(self, argv, *, cwd, on_progress=None):
        self.argv = list(argv)
        job = next(iter(BarsSd.svc._tasks), None)
        for v, m in [(1, 1)] + [(i, 8) for i in range(1, 9)] + [(1, 1)]:
            await on_progress(v, m)
            if job:
                BarsSd.seen.append(dict(BarsSd.svc.store.load("owner", job)["progress"]))
        Path(argv[argv.index("-o") + 1]).write_bytes(b"webm")
        return sdcli.RunResult(0, [], 1, 0.1)


async def test_one_shot_bars_are_not_sampling_progress(env, tmp_path):
    BarsSd.svc = install(env, tmp_path, BarsSd)
    BarsSd.seen = []
    r = await create(env, model="ltx-2.5", resolution="512x288", duration=1, image_b64=base64.b64encode(PNG).decode())
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed", job
    assert all(p.get("max") in (None, 8) for p in BarsSd.seen), BarsSd.seen          # never "1/1 = 100 %"
    assert job["progress"] == {"kind": "steps", "value": 8, "max": 8, "percent": 100.0}


LTX_I2V_LOG = [
    "[INFO   ] diffusion_engine.cpp:709  - loading diffusion model from 'ltx.gguf'",
    "[INFO   ] video.cpp:737  - IMG2VID",
    "  |==================================================| 1/1 - 1.22s/it\x1b[K",
    "[INFO   ] video.cpp:816  - encode_first_stage completed, taking 1237 ms",
    "[INFO   ] video.cpp:1602 - generate_video 576x1024x121",
    *[f"  |======>        | {i}/8 - 114.47s/it\x1b[K" for i in range(1, 9)],
    "[INFO   ] video.cpp:1699 - sampling completed, taking 941.22s",
    "[INFO   ] ltx_audio_vae.hpp:1052 - ltx audio vae decode completed, taking 3.08s",
    "  |==================================================| 1/1 - 220.66s/it\x1b[K",
    "[INFO   ] video.cpp:1240 - decode_first_stage completed, taking 221.97s",
    "[INFO   ] main.cpp:554  - save result video to 'video.webm'",
]


def test_live_stage_tracker_reads_the_ltx_i2v_output():
    """Line shapes from the real LTX-2.5 runs of 25.10 (prompts/paths removed): the start-picture encode bar used to
    be counted as a sampling pass (8 steps showed as 9, 15 % before sampling began)."""
    t = video_progress.VideoProgress(steps=8)
    snaps = []
    for line in LTX_I2V_LOG:
        t.feed_line(line)
        snaps.append(t.snapshot())
    at = {line: s for line, s in zip(LTX_I2V_LOG, snaps)}
    first_bar = at[LTX_I2V_LOG[2]]
    assert first_bar["stage"] == "encode" and first_bar["steps"] == 8 and first_bar["pct"] < 10
    assert at[LTX_I2V_LOG[5]]["stage"] == "sample" and at[LTX_I2V_LOG[5]]["step"] == 1
    last_step = at[LTX_I2V_LOG[12]]
    assert last_step["step"] == 8 and last_step["steps"] == 8
    assert snaps[-1]["stage"] == "save" and snaps[-1]["steps"] == 8


def test_wan_bars_are_unchanged_by_the_one_shot_rule():            # negative control: a real 1-step bar still counts
    t = video_progress.VideoProgress(steps=1)
    t.feed_line("  |=====| 1/1 - 3.00s/it")
    assert t.snapshot()["stage"] == "sample" and t.snapshot()["step"] == 1
