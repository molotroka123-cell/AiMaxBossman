"""Local video-generator bench harness (sd.cpp Vulkan candidates on Radeon 8060S / gfx1151, Windows).

Fixed prompt + fixed input (input_04.mp4, 5 s), rungs 480p -> 720p, tasks t2v / i2v / outpaint (9:16 by VACE).
Metrics per run (JSON next to the frames): seconds per frame, wall split (encode / sampling / decode from the
sd-cli log), peak process-tree RSS, peak system used RAM, peak GPU shared+dedicated (typeperf), Laplacian
sharpness, flicker (static ROI and full frame), validity (frame count, not black, not flat), outpaint PSNR of
the original region and seam brightness. Thresholds are read from bench_thresholds.json (frozen BEFORE any
run, see docs/owner/LOCAL_GEN_BENCH_PLAN_20261011.md) and never edited here.

Safety (owner 11.10): one GPU process at a time, every run has a watchdog + log, the first `device lost`
aborts the whole matrix (no blind retry), the PC must be idle of other generators.

Usage:
  python tools/local_gen/bench_local_gen.py preflight
  python tools/local_gen/bench_local_gen.py run --candidate wan22-ti2v-5b-q8 --task t2v --rung 480p
  python tools/local_gen/bench_local_gen.py matrix --rung 480p            # all candidates x tasks, short first
  python tools/local_gen/bench_local_gen.py report                          # results.md from all run.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import psutil
from PIL import Image

HERE = Path(__file__).resolve().parent
MEDIA = Path(r"C:\Users\asd\Bossman\models\media")
PROD_SD_CLI = Path(r"C:\Users\asd\Bossman\media-runtime\sdcpp\vulkan\sd-cli.exe")           # commit 74988b2
NEW_SD_CLI = MEDIA / "sdcpp-vulkan-master-956-1b0ba10" / "sd-cli.exe"                          # master-956
OUT_ROOT = Path(r"C:\Users\asd\Videos\Bossman-Genjutsu\local-gen-bench-20261011")
SRC = Path(r"C:\Users\asd\Videos\Bossman-Genjutsu\motion-v1-review-20261011\01_sources\owner_inputs_4\input_04.mp4")
SRC_CROP = dict(cx=268, cw=740, ch=720, start=1.0)   # same panel as tools/outpaint_test (16:9 with blurred bars)
T5 = MEDIA / "wan22-ti2v-5b" / "umt5-xxl-encoder-Q8_0.gguf"
VAE21 = MEDIA / "wan21-vace-1.3b" / "wan_2.1_vae.safetensors"
NEG = ("oversaturated, overexposed, static, blurry details, subtitles, worst quality, low quality, jpeg artifacts, ugly, "
       "deformed, extra fingers, bad hands, bad face, cluttered background, three legs, many people in the background")
PROMPTS = {
    "t2v": ("A wide landscape of a snow-capped mountain valley at sunrise, a turquoise river winding through dark pine "
            "forest, light mist drifting over the water, golden light on the peaks, static camera, cinematic, highly detailed."),
    "i2v": ("A young person in a grey t-shirt sits in a cosy room at night and talks to the camera, subtle natural head "
            "movement, soft warm indoor light, static camera, photorealistic."),
    "outpaint": ("a vertical phone video of a young person in a grey t-shirt sitting in a cosy room at night, the same room "
                 "continues above and below, warm indoor light, static camera, photorealistic"),
}
SEED = 20261011
LIGHTNING = ("<lora:wan22_lightning_t2v_v1.1_high_noise:1><lora:wan22_lightning_t2v_v1.1_low_noise:1>")

# frames/fps per model family; sizes multiples of 32 (5B needs 32, 14B 16) -> use 32 everywhere.
RUNGS = {
    "480p": {"t2v": (832, 480), "i2v": (832, 480), "outpaint": (448, 768)},
    "720p": {"t2v": (1280, 704), "i2v": (1280, 704), "outpaint": (704, 1280)},
}
CANDIDATES = {
    "wan21-vace-1.3b-baseline": dict(
        cli=PROD_SD_CLI, tasks=("t2v", "i2v", "outpaint"), frames=81, fps=16, steps=20, cfg=6.0, order=1,
        args=["--diffusion-model", str(MEDIA / "wan21-vace-1.3b/wan2.1_vace_1.3B_fp16.safetensors"), "--vae", str(VAE21)]),
    "wan22-ti2v-5b-q8": dict(
        cli=NEW_SD_CLI, tasks=("t2v", "i2v"), frames=121, fps=24, steps=20, cfg=5.0, order=0,
        args=["--diffusion-model", str(MEDIA / "wan22-ti2v-5b/Wan2.2-TI2V-5B-Q8_0.gguf"),
              "--vae", str(MEDIA / "wan22-ti2v-5b/wan2.2_vae.safetensors")]),
    "wan22-vace-fun-a14b-q8": dict(
        cli=NEW_SD_CLI, tasks=("t2v", "i2v", "outpaint"), frames=81, fps=16, steps=4, cfg=1.0, order=2, lora=LIGHTNING,
        args=["--diffusion-model", str(MEDIA / "wan22-vace-fun-a14b/LowNoise/Wan2.2-VACE-Fun-A14B-low-noise-Q8_0.gguf"),
              "--high-noise-diffusion-model", str(MEDIA / "wan22-vace-fun-a14b/HighNoise/Wan2.2-VACE-Fun-A14B-high-noise-Q8_0.gguf"),
              "--high-noise-cfg-scale", "1.0", "--vae", str(MEDIA / "wan22-vace-fun-a14b/wan_2.1_vae.safetensors"),
              "--lora-model-dir", str(MEDIA / "wan22-vace-fun-a14b/loras")]),
    "wan21-vace-14b-q4": dict(   # already on disk; control run 2.1-14B vs 2.2-Fun; no 2.1 lightning LoRA for VACE -> 20 steps
        cli=NEW_SD_CLI, tasks=("outpaint",), frames=81, fps=16, steps=20, cfg=6.0, order=3,
        args=["--diffusion-model", str(MEDIA / "wan21-vace-14b/Wan2.1_14B_VACE-Q4_K_M.gguf"), "--vae", str(VAE21)]),
}
TH = json.loads((HERE / "bench_thresholds.json").read_text())
DEVICE_LOST = re.compile(r"device lost|ErrorDeviceLost|TDR|VK_ERROR_DEVICE_LOST", re.I)


# ---------------------------------------------------------------- helpers
def gpu_busy():
    """names of other processes that are likely using the GPU (sd-cli / facefusion / comfy / wan)."""
    bad = []
    me = os.getpid()
    for p in psutil.process_iter(["name", "cmdline", "pid"]):
        n = (p.info["name"] or "").lower()
        cl = " ".join(p.info["cmdline"] or []).lower()
        if p.info["pid"] == me:
            continue
        gen_script = any(s in cl for s in ("comfyui\\main.py", "comfyui/main.py", "facefusion.py", "wgp.py", "genjutsu\\film.py",
                                            "genjutsu/film.py", "outpaint_test.py", "bench_local_gen.py run", "bench_local_gen.py matrix"))
        if n.startswith("sd-cli") or n.startswith("sd-server") or n.startswith("facefusion") or (gen_script and n.startswith("python")):
            bad.append(f"{n} pid={p.info['pid']}")
    return bad


def preflight(verbose=True):
    issues = []
    busy = gpu_busy()
    if busy:
        issues.append("GPU processes running: " + ", ".join(busy))
    vm = psutil.virtual_memory()
    if vm.available / 1e9 < 40:
        issues.append(f"free RAM {vm.available/1e9:.1f} GB < 40 GB")
    for p in (SRC, T5, VAE21):
        if not p.exists():
            issues.append(f"missing {p}")
    if verbose:
        print("preflight:", "OK" if not issues else "BLOCKED", *issues, sep="\n  ")
    return issues


def typeperf_gpu():
    """peak GPU shared+dedicated in GB from Windows perf counters (one sample)."""
    try:
        out = subprocess.run(["typeperf", r"\GPU Adapter Memory(*)\Shared Usage", r"\GPU Adapter Memory(*)\Dedicated Usage",
                              "-sc", "1"], capture_output=True, text=True, timeout=15).stdout
        vals = [float(x.strip('"')) for x in out.strip().splitlines()[-1].split(",")[1:] if x.strip('"').replace(".", "").isdigit()]
        return sum(vals) / 1e9
    except Exception:
        return float("nan")


class Sampler(threading.Thread):
    def __init__(self, proc, every=5.0):
        super().__init__(daemon=True)
        self.proc, self.every, self.stop = proc, every, False
        self.peak_rss = self.peak_sys = self.peak_gpu = 0.0
        self.swap0 = psutil.swap_memory().used
        self.swap_peak = self.swap0

    def run(self):
        while not self.stop:
            try:
                ps = psutil.Process(self.proc.pid)
                rss = ps.memory_info().rss + sum(c.memory_info().rss for c in ps.children(recursive=True))
                self.peak_rss = max(self.peak_rss, rss)
            except psutil.Error:
                pass
            self.peak_sys = max(self.peak_sys, psutil.virtual_memory().used)
            self.swap_peak = max(self.swap_peak, psutil.swap_memory().used)
            g = typeperf_gpu()
            if g == g:
                self.peak_gpu = max(self.peak_gpu, g)
            time.sleep(self.every)


def ffmpeg_frames(dst, n, fps, w, h, crop=True):
    dst.mkdir(parents=True, exist_ok=True)
    c = SRC_CROP
    vf = (f"crop={c['cw']}:{c['ch']}:{c['cx']}:0," if crop else "") + f"fps={fps},scale={w}:{h}:flags=lanczos"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(c["start"]), "-i", str(SRC), "-vf", vf, "-frames:v", str(n),
                    str(dst / "%04d.png")], check=True)


def make_outpaint_ctrl(work, n, fps, gw, gh):
    """control frames: source panel centred on a grey-127 9:16 canvas (grey = generate for VACE)."""
    c = SRC_CROP
    hg = round(c["ch"] * gw / c["cw"]) // 2 * 2
    yg = (gh - hg) // 2
    src = work / "src16"
    ffmpeg_frames(src, n, fps, gw, hg)
    ctrl = work / "ctrl"
    ctrl.mkdir(exist_ok=True)
    for p in sorted(src.glob("*.png")):
        canvas = Image.new("RGB", (gw, gh), (127, 127, 127))
        canvas.paste(Image.open(p).convert("RGB"), (0, yg))
        canvas.save(ctrl / p.name)
    return dict(hg=hg, yg=yg)


# ---------------------------------------------------------------- metrics
def gray(p):
    return np.asarray(Image.open(p).convert("L"), np.float32)


def laplacian_var(g):
    k = g[1:-1, 1:-1] * -4 + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(k.var())


def psnr(x, y):
    mse = np.mean((x - y) ** 2)
    return 99.0 if mse == 0 else float(10 * np.log10(255 ** 2 / mse))


def analyse(frames_dir, n_expected, task, geom=None, ctrl_dir=None):
    frames = sorted(frames_dir.glob("*.png"))
    m = dict(frames_found=len(frames), frames_expected=n_expected)
    if not frames:
        m.update(valid=False, reason="no frames")
        return m
    v = TH["validity"]
    gs = [gray(p) for p in frames]
    ok = [(v["frame_mean_min"] <= g.mean() <= v["frame_mean_max"]) and g.std() >= v["frame_std_min"] for g in gs]
    m["valid_frame_share"] = float(np.mean(ok))
    m["size"] = list(reversed(gs[0].shape))
    idx = sorted({0, len(gs) // 4, len(gs) // 2, 3 * len(gs) // 4, len(gs) - 1})
    m["laplacian_var_mean5"] = float(np.mean([laplacian_var(gs[i]) for i in idx]))
    roi = TH["static_roi_fraction"].get(task)
    d_full, d_roi = [], []
    h, w = gs[0].shape
    for a, b in zip(gs, gs[1:]):
        d_full.append(np.abs(a - b).mean())
        if roi:
            x0, y0, x1, y1 = int(roi[0] * w), int(roi[1] * h), int(roi[2] * w), int(roi[3] * h)
            d_roi.append(np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean())
    m["flicker_full_mean_abs_diff"] = float(np.mean(d_full)) if d_full else None
    m["flicker_static_roi_mean_abs_diff"] = float(np.mean(d_roi)) if d_roi else None
    if task == "outpaint" and geom and ctrl_dir:
        hg, yg = geom["hg"], geom["yg"]
        ctrls = sorted(ctrl_dir.glob("*.png"))
        ps, seam, dg, do = [], [], [], []
        prev = None
        band = 16
        for g, cp in zip(gs, ctrls):
            c = gray(cp)
            inner = slice(yg + 8, yg + hg - 8)
            ps.append(psnr(g[inner], c[inner]))
            seam.append(abs(c[yg:yg + band].mean() - g[yg - band:yg].mean()))
            seam.append(abs(c[yg + hg - band:yg + hg].mean() - g[yg + hg:yg + hg + band].mean()))
            gz = np.ones(h, bool)
            gz[max(0, yg - 8):yg + hg + 8] = False
            cur = (g[gz], g[inner])
            if prev is not None:
                dg.append(np.abs(cur[0] - prev[0]).mean())
                do.append(np.abs(cur[1] - prev[1]).mean())
            prev = cur
        m.update(psnr_original_region_min_db=float(min(ps)), psnr_original_region_mean_db=float(np.mean(ps)),
                 seam_brightness_diff_mean=float(np.mean(seam)),
                 outpaint_flicker_gen_over_orig=float(np.mean(dg) / max(np.mean(do), 1e-6)) if dg else None)
    m["valid"] = (len(frames) == n_expected) and m["valid_frame_share"] >= v["valid_frame_share_min"]
    return m


# ---------------------------------------------------------------- run
def run_one(cand, task, rung, steps=None, frames=None, cap=None, dry=False):
    c = CANDIDATES[cand]
    if task not in c["tasks"]:
        return None
    w, h = RUNGS[rung][task]
    n = frames or c["frames"]
    steps = steps or c["steps"]
    work = OUT_ROOT / rung / cand / task
    work.mkdir(parents=True, exist_ok=True)
    gen = work / "gen"
    gen.mkdir(exist_ok=True)
    for old in gen.glob("*.png"):
        old.unlink()
    prompt = PROMPTS[task] + (c.get("lora", "") if c.get("lora") else "")
    cmd = [str(c["cli"]), "-M", "vid_gen", *c["args"], "--t5xxl", str(T5), "-p", prompt, "-n", NEG,
           "-W", str(w), "-H", str(h), "--video-frames", str(n), "--fps", str(c["fps"]), "--steps", str(steps),
           "--cfg-scale", str(c["cfg"]), "--sampling-method", "euler", "-s", str(SEED), "--diffusion-fa",
           "--vae-tiling", "--temporal-tiling", "-o", str(gen / "%04d.png"), "-v"]
    geom = None
    if task == "i2v":
        ffmpeg_frames(work / "init", 1, c["fps"], w, h)
        cmd += ["-i", str(work / "init" / "0001.png")]
    elif task == "outpaint":
        geom = make_outpaint_ctrl(work, n, c["fps"], w, h)
        cmd += ["--control-video", str(work / "ctrl"), "--vace-strength", "1.0"]
    (work / "cmd.json").write_text(json.dumps(cmd, indent=1))
    rec = dict(candidate=cand, task=task, rung=rung, width=w, height=h, frames=n, steps=steps, fps=c["fps"], seed=SEED,
               sd_cli=str(c["cli"]), metric_version=TH["metric_version"], started=time.strftime("%Y-%m-%dT%H:%M:%S"),
               other_load=dict(cpu_percent=psutil.cpu_percent(interval=1.0),
                               procs=[f"{p.info['name']} {' '.join(p.info['cmdline'] or [])[:80]}" for p in psutil.process_iter(["name", "cmdline"])
                                      if (p.info["name"] or "").lower() in ("ffmpeg.exe",) or "hq_v2.py" in " ".join(p.info["cmdline"] or [])]))
    if dry:
        print(json.dumps(rec, indent=1)); print(" ".join(cmd)); return rec
    if gpu_busy():
        rec.update(status="SKIPPED_GPU_BUSY", gpu_busy=gpu_busy()); (work / "run.json").write_text(json.dumps(rec, indent=1)); return rec
    cap = cap or TH["rungs"][rung]["wall_cap_s"]
    log = work / "sd-cli.log"
    t0 = time.monotonic()
    with log.open("w", encoding="utf-8", errors="replace") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
        smp = Sampler(proc); smp.start()
        try:
            code = proc.wait(timeout=cap)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            for ch in psutil.Process(proc.pid).children(recursive=True):
                ch.kill()
            proc.kill(); code = -9
        smp.stop = True
    wall = time.monotonic() - t0
    text = log.read_text(encoding="utf-8", errors="replace")
    split = {}
    for key, pat in (("encode_s", r"encode_first_stage completed, taking ([\d.]+)\s*(ms|s)"),
                     ("sampling_s", r"sampling completed, taking ([\d.]+)\s*(ms|s)"),
                     ("decode_s", r"decode_first_stage completed, taking ([\d.]+)\s*(ms|s)")):
        mm = re.search(pat, text)
        if mm:
            split[key] = float(mm.group(1)) / (1000 if mm.group(2) == "ms" else 1)
    device_lost = bool(DEVICE_LOST.search(text))
    metrics = analyse(gen, n, task, geom, work / "ctrl" if task == "outpaint" else None)
    rec.update(exit_code=code, timed_out=timed_out, device_lost=device_lost, wall_s=round(wall, 1),
               sec_per_frame=round(wall / n, 2), split=split, peak_rss_gb=round(smp.peak_rss / 1e9, 2),
               peak_system_used_gb=round(smp.peak_sys / 1e9, 2), peak_gpu_shared_plus_dedicated_gb=round(smp.peak_gpu, 2),
               swap_growth_gb=round((smp.swap_peak - smp.swap0) / 1e9, 2), geom=geom, metrics=metrics)
    rec["status"] = "DEVICE_LOST" if device_lost else "TIMEOUT" if timed_out else "OK" if code == 0 and metrics.get("valid") else "FAIL"
    (work / "run.json").write_text(json.dumps(rec, indent=1))
    print(json.dumps({k: rec[k] for k in ("candidate", "task", "rung", "status", "wall_s", "sec_per_frame")}))
    return rec


def gates(rec, baseline=None):
    """PASS/FAIL per gate from bench_thresholds.json; baseline = run.json of wan21-vace-1.3b-baseline same task/rung."""
    r = TH["rungs"][rec["rung"]]
    m = rec.get("metrics", {})
    g = {}
    g["G1_completion"] = rec["status"] == "OK" and rec["wall_s"] <= r["wall_cap_s"]
    g["G1b_speed"] = rec["sec_per_frame"] <= r["sec_per_frame_pass"]
    g["G2_validity"] = bool(m.get("valid"))
    lap = m.get("laplacian_var_mean5") or 0
    g["G3_sharpness"] = lap >= TH["sharpness"]["laplacian_var_abs_min"] and (
        baseline is None or lap >= TH["sharpness"]["ratio_to_baseline_min"] * (baseline["metrics"].get("laplacian_var_mean5") or 0))
    if rec["task"] == "outpaint":
        g["G4_flicker"] = (m.get("outpaint_flicker_gen_over_orig") or 9e9) <= TH["flicker"]["outpaint_gen_over_orig_ratio_max"]
        o = TH["outpaint"]
        g["G5_outpaint"] = (m.get("psnr_original_region_min_db", 0) >= o["psnr_original_region_min_db"] and
                            m.get("psnr_original_region_mean_db", 0) >= o["psnr_original_region_mean_db"] and
                            m.get("seam_brightness_diff_mean", 9e9) <= o["seam_brightness_diff_mean_max"])
    else:
        f = m.get("flicker_static_roi_mean_abs_diff")
        g["G4_flicker"] = f is not None and f <= TH["flicker"]["static_roi_mean_abs_diff_max"] and (
            baseline is None or f <= TH["flicker"]["ratio_to_baseline_max"] * (baseline["metrics"].get("flicker_static_roi_mean_abs_diff") or 9e9))
    g["G6_memory"] = (rec["peak_system_used_gb"] + rec["peak_gpu_shared_plus_dedicated_gb"] <= TH["memory"]["peak_system_plus_gpu_shared_gb_max"]
                      and rec["swap_growth_gb"] <= TH["memory"]["swap_growth_gb_max"])
    g["ALL"] = all(g.values())
    return g


def report():
    rows = []
    recs = {}
    for p in OUT_ROOT.rglob("run.json"):
        rec = json.loads(p.read_text())
        recs[(rec["rung"], rec["candidate"], rec["task"])] = rec
    for (rung, cand, task), rec in sorted(recs.items()):
        base = recs.get((rung, "wan21-vace-1.3b-baseline", task)) if cand != "wan21-vace-1.3b-baseline" else None
        g = gates(rec, base) if rec["status"] not in ("SKIPPED_GPU_BUSY",) else {}
        m = rec.get("metrics", {})
        rows.append("| %s | %s | %s | %s | %.1f | %.1f | %s | %s | %s | %s | %s |" % (
            rung, cand, task, rec["status"], rec.get("wall_s", 0), rec.get("sec_per_frame", 0),
            m.get("laplacian_var_mean5") and round(m["laplacian_var_mean5"], 1),
            m.get("flicker_static_roi_mean_abs_diff") and round(m["flicker_static_roi_mean_abs_diff"], 2),
            m.get("psnr_original_region_min_db") and round(m["psnr_original_region_min_db"], 1),
            f"{rec.get('peak_system_used_gb',0)}+{rec.get('peak_gpu_shared_plus_dedicated_gb',0)}",
            ",".join(k for k, v in g.items() if not v) or ("PASS" if g else "-")))
    md = ("# local-gen bench results (metric_version %s)\n\n| rung | candidate | task | status | wall s | s/frame | laplacian | "
          "flicker ROI | psnr orig min | peak sys+gpu GB | failed gates |\n|---|---|---|---|---|---|---|---|---|---|---|\n" % TH["metric_version"]
          + "\n".join(rows) + "\n")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "results.md").write_text(md)
    print(md)


def matrix(rung, cands, tasks, steps=None, frames=None):
    order = sorted(cands, key=lambda k: CANDIDATES[k]["order"])
    for cand in order:
        for task in tasks:
            if task not in CANDIDATES[cand]["tasks"]:
                continue
            if not CANDIDATES[cand]["cli"].exists():
                print("skip", cand, "sd-cli missing", CANDIDATES[cand]["cli"]); break
            rec = run_one(cand, task, rung, steps=steps, frames=frames)
            if rec and rec["status"] == "DEVICE_LOST":
                print("ABORT: device lost in", cand, task, "- no blind retry (owner rule)"); report(); return 2
            if rec and rec["status"] in ("TIMEOUT", "FAIL"):
                print("stop climbing for", cand, "after", task, rec["status"]); break
    report()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["preflight", "run", "matrix", "report"])
    ap.add_argument("--candidate", choices=list(CANDIDATES), nargs="*")
    ap.add_argument("--task", choices=["t2v", "i2v", "outpaint"], nargs="*")
    ap.add_argument("--rung", choices=list(RUNGS), default="480p")
    ap.add_argument("--steps", type=int)
    ap.add_argument("--frames", type=int, help="override frame count (smoke only; a non-default count is not a gate result)")
    ap.add_argument("--cap", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "preflight":
        sys.exit(1 if preflight() else 0)
    if a.cmd == "report":
        report(); sys.exit(0)
    if preflight(verbose=True) and not a.dry_run:
        sys.exit(1)
    if a.cmd == "run":
        for cand in a.candidate or ["wan22-ti2v-5b-q8"]:
            for task in a.task or ["t2v"]:
                run_one(cand, task, a.rung, a.steps, a.frames, a.cap, a.dry_run)
        report()
    else:
        sys.exit(matrix(a.rung, a.candidate or list(CANDIDATES), a.task or ["t2v", "i2v", "outpaint"], a.steps, a.frames))
