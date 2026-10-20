"""Video job progress from the engine's own output (owner 20.10: the pult/phone said «загрузка модели» all run).

Fixtures are REAL lines of this PC: ``wan2gp_t2v_1.3b_30steps.log`` = 18 lines of the Wan2GP 1.3B smoke run of
18.10 (``apps-local/wan2gp-smoke-out/run.log``; the prompt line and lines with local paths left out, the harness's
``[  t]`` time stamps kept), ``wan2gp_download_bar.txt`` = 3 redraws of Wan2GP's ffmpeg download bar (wgp-err.log).
No GPU, no engine: the service test uses a scripted runner that replays those bytes.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bcc.direct_gen import sdcli, video_progress as vp
from bcc.telegram_companion.gen_progress import plate_job, progress_line

from .conftest import wait_for
from .test_direct_gen_photo import PNG, FakeSd, create, finish, install

FIX = Path(__file__).parent / "fixtures" / "video_progress"
WAN = (FIX / "wan2gp_t2v_1.3b_30steps.log").read_bytes()
DOWNLOAD = (FIX / "wan2gp_download_bar.txt").read_bytes()


class Clock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def feed_lines(p: vp.VideoProgress, data: bytes) -> list[dict]:
    out = []
    for line in data.split(b"\r\n"):
        if line:
            p.feed(line + b"\r\n")
            out.append(p.snapshot())
    return out


# ------------------------------------------------------------------------------------------- parser: real lines
def test_real_wan2gp_run_walks_load_encode_sample_decode_with_real_steps():
    snaps = feed_lines(vp.VideoProgress(steps=30, clock=Clock()), WAN)
    stages = [s["stage"] for s in snaps]
    assert stages[:3] == ["load", "load", "load"]                  # [0/3] Loading - ... Text Encoder: still loading
    assert stages[3:6] == ["encode"] * 3                           # Encoding Prompt + its tqdm bar
    assert stages[6:15] == ["sample"] * 9 and stages[15] == "sample"
    assert stages[16:] == ["decode", "decode"]                     # [30/30] VAE Decoding, then the final 30/30 redraw
    assert [s["step"] for s in snaps[6:16]] == [0, 0, 1, 1, 2, 4, 4, 11, 12, 29]
    assert snaps[16]["step"] == 30 and snaps[-1]["step"] == 30 and snaps[-1]["total"] == 30
    pcts = [s["pct"] for s in snaps]
    assert pcts == sorted(pcts) and max(pcts) < 97.0               # monotonic; 100 % only after the file check
    assert all(s["known"] for s in snaps)


def test_tqdm_remaining_is_the_preferred_eta_and_is_marked():
    clock = Clock()
    p = vp.VideoProgress(steps=30, clock=clock)
    p.feed(b"  3%|\xe2\x96\x8e         | 1/30 [00:58<28:26, 58.84s/it]\r")
    s = p.snapshot()
    assert (s["eta_s"], s["eta_source"], s["eta_scope"]) == (1706.0, "tqdm", "sampling")
    clock.t = 100.0                                               # no new bar for 100 s: the remainder shrinks
    assert p.snapshot()["eta_s"] == 1606.0
    assert s["s_per_it"] == 58.84


def test_engine_estimate_when_only_wan2gp_status_lines_arrive():
    p = vp.VideoProgress(steps=30, clock=Clock())
    p.feed(b"[  383.4]   [4/30] Denoising | 3m 21s / 24m 38s\r\n")
    s = p.snapshot()
    assert s["stage"] == "sample" and s["step"] == 4
    assert (s["eta_s"], s["eta_source"], s["eta_scope"]) == (1277.0, "engine_estimate", "sampling")


def test_carriage_return_chunks_of_any_size_give_the_same_result():
    whole = vp.VideoProgress(steps=30, clock=Clock())
    feed_lines(whole, WAN)
    redraws = WAN.replace(b"\r\n", b"\r")                          # a live tqdm stream: \r only, no newline
    for size in (1, 7, 64, 4096):
        p = vp.VideoProgress(steps=30, clock=Clock())
        for i in range(0, len(redraws), size):
            p.feed(redraws[i:i + size])
        a, b = p.snapshot(), whole.snapshot()
        assert (a["stage"], a["step"], a["total"], a["pct"]) == (b["stage"], b["step"], b["total"], b["pct"]), size


def test_partial_bar_is_not_counted_until_complete_and_the_newest_bar_counts_at_once():
    p = vp.VideoProgress(steps=30, clock=Clock())
    p.feed("\r 37%|███▋      | 11/30 [06:53<09:".encode())
    assert p.snapshot()["step"] is None or p.snapshot()["step"] == 0          # cut bar: nothing invented
    p.feed(b"56, 31.42s/it]")                                     # the bar is complete but not yet terminated
    s = p.snapshot()
    assert s["step"] == 11 and s["eta_s"] == 596.0 and s["eta_source"] == "tqdm"
    p.feed(b"\r")
    assert p.snapshot()["step"] == 11                              # re-applying the same bar changes nothing


@pytest.mark.parametrize("line, step, total, s_per_it, remaining", [
    ("  37%|███▋      | 11/30 [01:02<01:47, 5.6s/it]", 11, 30, 5.6, 107.0),
    ("100%|██████████| 30/30 [00:30<00:00,  1.00it/s]", 30, 30, 1.0, 0.0),
    ("  0%|          | 0/30 [00:00<?, ?it/s]", 0, 30, None, None),
    ("Denoising:  50%|█████     | 25/50 [1:02:03<1:02:03, 148.92s/it]", 25, 50, 148.92, 3723.0),
    ("sampling: 10%|#         | 2/20 [00:04<00:36,  2.00s/it, loss=0.12]", 2, 20, 2.0, 36.0),
    ("100%|##########| 4/4 [00:02<00:00,  4.00it/s]", 4, 4, 0.25, 0.0),
])
def test_tqdm_variants(line, step, total, s_per_it, remaining):
    ev = vp.parse_line(line)
    assert ev is not None and ev.stage == "sample" and ev.kind == "tqdm"
    assert (ev.step, ev.total, ev.remaining_s) == (step, total, remaining)
    assert ev.s_per_it == pytest.approx(s_per_it) if s_per_it is not None else ev.s_per_it is None


def test_other_engine_lines():
    assert vp.parse_line("Step 11/30") == vp.Event("sample", "step", 11, 30)
    assert vp.parse_line("  |==================>       | 3/8 - 7.62s/it\x1b[K").kind == "sdcli"   # sd-cli bar
    assert vp.parse_line("[INFO ] stable-diffusion.cpp:1 - generate_video 640x512x33").stage == "encode"
    assert vp.parse_line("sample(high noise) 640x512x33").stage == "sample"
    assert vp.parse_line("decoding 9 latents").stage == "decode"
    assert vp.parse_line("Wan VAE temporal tile 3/8").frac == pytest.approx(2 / 8)
    assert vp.parse_line("save result image to 'x.webm'").stage == "save"
    assert vp.parse_line("[  182.0]   Model Loaded").stage == "load"
    assert vp.parse_line("Encoding Text Prompt 2/2:  62%|██████▎   | 15/24 [00:00<00:00, 43.24it/s]").stage == "encode"


@pytest.mark.parametrize("garbage", [
    "", "   ", "\x1b[1m\x1b[95m************ Memory Management for the GPU Poor ************\x1b[0m",
    "[Task 1/1] decoding a saved video, step 3/4, 50%|##| 1/2 [00:01<00:01, 1.0s/it]",   # an echoed prompt
    "Traceback (most recent call last):", "37%|", "11/30", "|  | 31/30 [00:01<00:00, 1.0s/it]",
    "  5%|▌ | 3/0 [00:01<?, ?it/s]", "[12/10] Denoising | 1s", "Step 9/0", "[1.5] [RAM] MMGP RAM Allocator",
    "  |=====>   | 46/386 - 417.07MB/s",                        # sd-cli tensor loading: a byte rate
])
def test_garbage_is_never_progress(garbage):
    assert vp.parse_line(garbage) is None


def test_download_bars_and_binary_noise_do_not_move_anything():
    p = vp.VideoProgress(steps=30, clock=Clock())
    assert p.feed(DOWNLOAD) is False                              # real Wan2GP ffmpeg download: MB/s, not steps
    assert p.feed(bytes(range(256)) * 4 + b"\xff\xfe\r\n\x00\x00") is False
    s = p.snapshot()
    assert (s["known"], s["stage"], s["step"], s["pct"], s["eta_s"]) == (False, "load", 0, 0.0, None)


def test_parse_duration():
    assert vp.parse_duration("1m 04s") == 64.0 and vp.parse_duration("4.8s") == 4.8
    assert vp.parse_duration("01:43") == 103.0 and vp.parse_duration("1:02:03") == 3723.0
    assert vp.parse_duration("2h 1m 3s") == 7263.0
    for bad in ("?", "", None, "soon", "1x"):
        assert vp.parse_duration(bad) is None


# ------------------------------------------------------------------------------------- stages, passes, windows
def test_two_expert_sub_passes_of_sd_cli_add_up_and_eta_counts_the_pass_not_started():
    p = vp.VideoProgress(steps=4, clock=Clock())
    p.feed(b"sample(high noise) 640x512x33\n\r  |=====>  | 1/2 - 3300.00s/it\r  |==========| 2/2 - 3310.00s/it")
    s = p.snapshot()
    assert s["stage"] == "sample" and s["step"] == 2 and s["total"] == 4
    assert s["eta_source"] == "s_per_it" and s["eta_s"] == pytest.approx(2 * 3310.0)
    p.feed(b"\nsampling(high noise) completed\n\r  |=====>  | 1/2 - 3320.00s/it\n")
    assert p.snapshot()["step"] == 3
    p.feed(b"\r  |==========| 2/2 - 3320.00s/it\nsampling completed\n")
    s = p.snapshot()
    assert s["stage"] == "decode" and s["step"] == 4


def test_vae_decode_steps_of_sd_cli_are_decode_progress_not_new_sampling():
    clock = Clock()
    p = vp.VideoProgress(steps=8, clock=clock)
    p.feed(b"\r  |====| 8/8 - 7.00s/it\ndecoding 9 latents\n")
    clock.t = 10.0
    p.feed(b"\r  |==>  | 2/4 - 5.00s/it\n")
    s = p.snapshot()
    assert s["stage"] == "decode" and s["step"] == 8
    assert (s["eta_s"], s["eta_source"]) == (10.0, "decode_tiles")           # half done in 10 s -> 10 s left


def test_a_new_window_after_decode_is_counted_and_pct_never_drops():
    p = vp.VideoProgress(steps=30, clock=Clock())
    p.feed(b"  100%|##| 30/30 [00:30<00:00, 1.00s/it]\r\n[30/30] VAE Decoding | 30s / 30s\r\n")
    before = p.snapshot()["pct"]
    p.feed(b"  3%|#  | 1/30 [00:01<00:29, 1.00s/it]\r\n")            # Wan2GP sliding window: sampling again
    s = p.snapshot()
    assert s["stage"] == "sample" and s["step"] == 31 and s["pct"] >= before


def test_finished_is_verify_with_cap_and_zero_eta():
    p = vp.VideoProgress(steps=30, clock=Clock())
    feed_lines(p, WAN)
    p.mark_finished()
    s = p.snapshot()
    assert s["stage"] == "verify" and s["pct"] == 97.0 and s["eta_s"] == 0.0 and s["eta_source"] == "done"


# ----------------------------------------------------------------------------------------------- ETA fallback
ROWS = [
    {"status": "completed", "model": "wan2.2-ti2v-5b", "width": 480, "height": 272, "frames": 17, "steps": 10,
     "load": "cold", "load_s": 600.0, "generate_s": 300.0, "post_s": 2.0},
    {"status": "completed", "model": "wan2.2-ti2v-5b", "width": 480, "height": 272, "frames": 17, "steps": 20,
     "load": "cold", "load_s": 700.0, "generate_s": 800.0, "post_s": 4.0},
    {"status": "completed", "model": "wan2.2-ti2v-5b", "width": 480, "height": 272, "frames": 17, "steps": 10,
     "load": "warm", "load_s": 1.0, "generate_s": 300.0, "post_s": 3.0},
    {"status": "failed", "model": "wan2.2-ti2v-5b", "width": 480, "height": 272, "frames": 17, "steps": 10,
     "load_s": 5.0, "generate_s": 1.0, "post_s": 0.0},
    {"status": "completed", "model": "wan2.2-ti2v-5b", "width": 832, "height": 480, "frames": 49, "steps": 30,
     "load_s": 9.0, "generate_s": 9000.0, "post_s": 1.0},
    {"status": "completed", "model": "other", "width": 480, "height": 272, "frames": 17, "steps": 10,
     "load_s": 1.0, "generate_s": 1.0, "post_s": 1.0},
    {"status": "completed", "model": "wan2.2-ti2v-5b", "width": 480, "height": 272, "frames": 17, "steps": 0,
     "load_s": 1.0, "generate_s": 1.0, "post_s": 1.0},                  # steps 0: no per-step value, no division
]


def test_timing_basis_uses_only_completed_runs_of_the_same_model_size_and_frames():
    b = vp.timing_basis(ROWS, "wan2.2-ti2v-5b", 480, 272, 17, 10)
    assert b["n"] == 3 and b["per_step_s"] == 30.0                # 30, 40, 30 s/step -> median 30
    assert b["load_s"] == 650.0                                   # cold loads only (warm 1 s left out)
    assert b["post_s"] == 3.0 and b["total_s"] == 650.0 + 300.0 + 3.0
    assert vp.timing_basis(ROWS, "wan2.2-ti2v-5b", 640, 640, 17, 10) is None
    assert vp.timing_basis(ROWS, "wan2.2-ti2v-5b", 480, 272, 17, 0) is None
    assert vp.timing_basis(ROWS, "wan2.2-ti2v-5b", 480, 272, 17, None) is None
    assert vp.timing_basis([], "x", 1, 1, 1, 4) is None and vp.timing_basis(None, "x", 1, 1, 1, 4) is None


def test_eta_falls_back_to_timing_medians_while_loading_and_before_a_rate_is_known():
    clock = Clock()
    basis = vp.timing_basis(ROWS, "wan2.2-ti2v-5b", 480, 272, 17, 10)
    p = vp.VideoProgress(steps=10, basis=basis, clock=clock)
    clock.t = 100.0
    s = p.snapshot()                                              # nothing printed yet: medians only
    assert (s["stage"], s["eta_s"], s["eta_source"], s["eta_scope"], s["basis_n"]) == \
        ("load", 853.0, "timings_median", "total", 3)
    p.feed(b"Step 4/10\n")                                        # a step, no rate
    s = p.snapshot()
    assert (s["eta_s"], s["eta_source"]) == (6 * 30.0 + 3.0, "timings_median")
    p.feed(b"\r  |===>   | 5/10 - 20.00s/it\n")                   # a measured rate wins over the medians
    s = p.snapshot()
    assert (s["eta_s"], s["eta_source"], s["eta_scope"]) == (100.0, "s_per_it", "sampling")
    late = vp.VideoProgress(steps=10, basis=basis, clock=Clock(0.0))  # far slower than usual: unknown, never negative
    late.clock = Clock(5000.0)
    assert late.snapshot()["eta_s"] is None and late.snapshot()["eta_source"] is None


def test_no_basis_no_rate_means_unknown_eta_and_zero_rates_never_divide():
    p = vp.VideoProgress(steps=None, clock=Clock())
    p.feed(b"Step 3/20\n")
    s = p.snapshot()
    assert (s["step"], s["total"], s["eta_s"], s["eta_source"]) == (3, 20, None, None)
    p.feed(b"  15%|#  | 3/20 [00:03<?, 0.00it/s]\n")               # 0 it/s: no rate, no ZeroDivisionError
    assert p.snapshot()["s_per_it"] is None and p.snapshot()["eta_s"] is None
    q = vp.VideoProgress(steps=0, basis={"per_step_s": 0.0}, clock=Clock())
    assert q.snapshot()["eta_s"] is None                         # empty basis is ignored


def test_comfyui_step_counts_feed_the_same_structure():
    p = vp.VideoProgress(steps=None, clock=Clock())
    p.observe_step(2, 4)
    p.observe_step(5, 4)                                          # impossible count: ignored
    s = p.snapshot()
    assert (s["stage"], s["step"], s["total"], s["known"]) == ("sample", 2, 4, True)


# ------------------------------------------------------------------------------------- the pult plate wording
def test_pult_plate_reads_a_direct_gen_job_like_a_studio_job():
    p = vp.VideoProgress(steps=30, clock=Clock())
    p.feed(" 37%|███▋      | 11/30 [06:53<09:56, 31.42s/it]\r\n".encode())
    job = {"job_id": "x", "status": "generating", "stage": "generating",
           "progress": {"kind": "steps", "value": 11, "max": 30, "percent": 36.7}, "progress_detail": p.snapshot()}
    line = progress_line(plate_job(job))
    assert "шаг 11/30" in line and "осталось ~10 мин на шаги, потом сборка кадров" in line and "34%" in line
    loading = {"status": "loading", "progress": {"kind": "none"}, "progress_detail": vp.VideoProgress().snapshot()}
    assert progress_line(plate_job(loading)) == "⏳ прогресс неизвестен"
    p2 = vp.VideoProgress(steps=10, basis=vp.timing_basis(ROWS, "wan2.2-ti2v-5b", 480, 272, 17, 10), clock=Clock())
    p2.feed(b"[0/1] Encoding Prompt\n")
    line2 = progress_line(plate_job({"status": "loading", "progress": {"kind": "none"}, "progress_detail": p2.snapshot()}))
    assert "разбор промпта" in line2 and "(по замерам)" in line2
    studio = {"status": "running", "progress": 0.42, "progress_detail": {"known": True, "phase": "sample", "step": 9,
                                                                         "steps": 20, "eta_s": 360}}
    assert plate_job(studio) is studio and progress_line(studio).endswith("шаг 9/20 · осталось ~6 мин")


# ------------------------------------------------------------------------------------- the job status payload
class WanReplay(FakeSd):
    """A runner that prints the real Wan2GP lines as \\r-redraws, holds before exit, then writes the output."""

    release: asyncio.Event | None = None

    async def run(self, argv, *, cwd, on_progress=None, on_output=None):
        self.argv = list(argv)
        stream = WAN.replace(b"\r\n", b"\r")
        for i in range(0, len(stream), 97):
            await on_output(stream[i:i + 97])
            await asyncio.sleep(0)
        while not WanReplay.release.is_set() and not self.killed:
            await asyncio.sleep(0.01)
        out = Path(argv[argv.index("-o") + 1])
        out.write_bytes(PNG if out.suffix == ".png" else b"webm")
        return sdcli.RunResult(0, [], 1048576, 0.5)


async def test_video_job_status_carries_live_stage_step_and_eta(env, tmp_path):
    WanReplay.release = asyncio.Event()
    install(env, tmp_path, lambda: WanReplay())
    r = await create(env, model="wan2.2-ti2v-5b", resolution="480x272", duration=1, steps=30)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    async def decoding():
        j = (await env.client.get(f"/api/direct-gen/jobs/{job_id}", headers={"X-Participant": "owner"})).json()
        return j if (j.get("progress_detail") or {}).get("stage") == "decode" else None

    j = await wait_for(decoding, timeout=10)
    d = j["progress_detail"]
    assert j["status"] == "generating"                              # sampling began -> not «loading» any more
    assert (d["step"], d["total"], d["known"]) == (30, 30, True)
    assert 80.0 < d["pct"] < 97.0 and d["source"] == "engine output"
    assert {"stage", "step", "total", "pct", "eta_s", "eta_source", "eta_scope"} <= d.keys()
    WanReplay.release.set()
    done = await finish(env, job_id)
    assert done["status"] == "completed"
    assert done["progress_detail"]["stage"] == "verify" and done["progress_detail"]["pct"] == 97.0
    stages = [t["stage"] for t in done["timeline"]]
    assert stages == ["queued", "loading", "generating", "postprocessing", "completed"]


async def test_photo_jobs_and_runners_without_on_output_are_unchanged(env, tmp_path):
    install(env, tmp_path, lambda: FakeSd(steps=4))                 # FakeSd.run has no on_output parameter
    r = await create(env, model="wan2.2-ti2v-5b", resolution="480x272", duration=1, steps=4)
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed" and "progress_detail" not in job
    assert job["progress"] == {"kind": "steps", "value": 4, "max": 4, "percent": 100.0}
