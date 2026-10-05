"""Multi-shot character swap for a whole clip ("own Genjutsu"): per-shot actor reference, smart region generation.

Builds on genjutsu.py. Steps (each timed in <job>/trace.jsonl):
  1. source frames at 16 fps in full resolution for --seconds;
  2. shot cuts from frame differences;
  3. per frame: YOLOX person (largest at a shot start, then tracked) + SAM2 mask at half resolution;
  4. per shot: segments of 4k+1 frames (<= 81) covering the shot; per segment a region:
     a close-up (actor box wider than --roi-max of the frame) uses the full frame, a wide shot
     crops a 9:16 box around the actor, so every generated pixel goes to the actor;
  5. control = crop with the actor replaced by grey; sd-cli VACE with that shot's reference
     (--parallel segments at once, RAM guard kills all below the floor);
  6. composite each generated crop into the full-resolution source inside the feathered mask with
     a ring colour match (background stays the untouched source), 16 -> --out-fps interpolation,
     the source audio.
--plan-only stops after step 4 and writes plan_preview.png (first control frame of every segment).
Use only your own media or media of people who consented.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genjutsu as g  # noqa: E402

GW, GH = 480, 832  # generation size (portrait, native for Wan2.1 VACE 1.3B)


def detect_cuts(frames_small: np.ndarray) -> list[int]:
    d = np.abs(np.diff(frames_small.astype(np.float32), axis=0)).mean((1, 2))
    thr = max(18.0, float(d.mean() + 4 * d.std()))
    return [int(i + 1) for i in np.where(d > thr)[0]]


def segments(start: int, end: int) -> list[tuple[int, int, int]]:
    """(seg_start, seg_len, use_from) covering [start, end); lengths 4k+1 in [17, 81]; the last one may end past `end`."""
    n = end - start
    k = max(1, -(-n // 81))
    per = -(-n // k)
    length = min(81, max(17, 4 * (-(-(per - 1) // 4)) + 1))
    return [(start + i * per, length, start + i * per) for i in range(k)]


def roi_for(masks: list[np.ndarray], fw: int, fh: int, roi_max: float) -> tuple[int, int, int, int]:
    box = None
    for m in masks:
        ys, xs = np.nonzero(m)
        if xs.size:
            b = (xs.min(), ys.min(), xs.max(), ys.max())
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    if box is None:
        return 0, 0, fw, fh
    x0, y0, x1, y1 = box
    w, h = max((x1 - x0) * 1.3, fw * 0.3), (y1 - y0) * 1.2  # at least 30% of the width: context for a tiny figure
    if w / h > GW / GH:
        h = w * GH / GW
    else:
        w = h * GW / GH
    if w >= fw * roi_max or h >= fh:
        return 0, 0, fw, fh
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    x0 = int(min(max(0, cx - w / 2), fw - w))
    y0 = int(min(max(0, cy - h / 2), fh - h))
    return x0, y0, int(x0 + w), int(y0 + h)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--job", type=Path, required=True)
    ap.add_argument("--seconds", type=float, default=13.0)
    ap.add_argument("--shot-refs", required=True, help="comma list of reference images, one per shot in order")
    ap.add_argument("--shot-prompts", type=Path, required=True, help="JSON list of prompts, one per shot")
    ap.add_argument("--shot-targets", default="", help="comma list per shot: largest (default) | center (whole person, not at the edge)")
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    # 2 at once on the single Radeon 8060S -> "device lost on Vulkan0" killed both (2026-10-05)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--roi-max", type=float, default=0.6)
    ap.add_argument("--out-fps", type=int, default=24)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--composite-only", action="store_true", help="no generation: assemble finished segments")
    args = ap.parse_args(argv)
    job = args.job
    job.mkdir(parents=True, exist_ok=True)
    trace = g.Trace(job)
    t_all = time.time()
    refs = [Path(p) for p in args.shot_refs.split(",")]
    prompts = json.loads(args.shot_prompts.read_text(encoding="utf-8"))
    trace("start", t_all, src=str(args.src), refs=[str(r) for r in refs], free_ram_gb=round(g.free_ram_gb(), 1))

    # 1. full-resolution source frames at 16 fps (+ spare frames for a last segment that runs past the end)
    t0 = time.time()
    full = job / "src_full"
    full.mkdir(exist_ok=True)
    total = int(round(args.seconds * 16))
    if not list(full.glob("*.png")):
        subprocess.run([g.ffmpeg_bin("ffmpeg"), "-v", "error", "-y", "-i", str(args.src), "-vf", "fps=16",
                        "-frames:v", str(total + 80), str(full / "%04d.png")], check=True)
    paths = sorted(full.glob("*.png"))
    total = min(total, len(paths))
    fw, fh = Image.open(paths[0]).size
    trace("frames", t0, count=len(paths), size=f"{fw}x{fh}", used=total)

    # 2. shots
    t0 = time.time()
    small = np.stack([np.asarray(Image.open(p).convert("L").resize((90, 160))) for p in paths[:total]])
    cuts = detect_cuts(small)
    bounds = [0, *cuts, total]
    shots = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    trace("shots", t0, shots=shots)
    if len(refs) < len(shots) or len(prompts) < len(shots):
        raise SystemExit(f"{len(shots)} shots need {len(shots)} references and prompts")

    # 3. masks of the actor (half resolution analysis)
    t0 = time.time()
    det, seg = g.Detector(), g.Segmenter()
    hw, hh = fw // 2, fh // 2
    targets = (args.shot_targets.split(",") + ["largest"] * len(shots))[:len(shots)]
    masks, track = [], None
    cached = sorted((job / "masks").glob("*.png")) if (job / "masks").is_dir() else []
    if len(cached) == len(paths) and (job / "masks" / "targets.txt").is_file() \
            and (job / "masks" / "targets.txt").read_text() == ",".join(targets):
        masks = [np.asarray(Image.open(c)) > 127 for c in cached]  # resume: same frames, same targets
    for i, p in enumerate(paths[len(masks):]):
        img = Image.open(p).convert("RGB").resize((hw, hh), Image.BILINEAR)
        if i in cuts:
            track = None
        mode = targets[sum(1 for c in cuts if c <= i)] if i < total else targets[-1]
        boxes = det(img)
        if mode == "center":
            # a confident whole person near the frame centre: not a foreground shoulder cut by the
            # edge, not a spectator in the crowd (both were picked on the owner's clip, 2026-10-05)
            e = 0.02 * hw
            boxes = [b for b in boxes if b[4] >= 0.6 and b[0] > e and b[1] > e and b[2] < hw - e and b[3] < hh - e
                     and abs((b[0] + b[2]) / 2 - hw / 2) < 0.2 * hw and abs((b[1] + b[3]) / 2 - hh / 2) < 0.2 * hh]
        target = None
        if len(boxes):
            if track is not None:
                cx, cy = (track[0] + track[2]) / 2, (track[1] + track[3]) / 2
            elif mode == "center":
                cx, cy = hw / 2, hh / 2
            if track is not None or mode == "center":
                target = min(boxes, key=lambda b: abs((b[0] + b[2]) / 2 - cx) + abs((b[1] + b[3]) / 2 - cy))
            else:
                target = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))  # the main (largest) person
            track = list(target[:4])
        # "center": no detection on this frame -> nobody to replace (the actor is not in view yet)
        use = track if (target is not None or mode != "center") else None
        m = seg(img, use) if use is not None else np.zeros((hh, hw), bool)
        masks.append(np.asarray(Image.fromarray(m.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(9))) > 127)
        (job / "masks").mkdir(exist_ok=True)
        Image.fromarray(masks[-1].astype(np.uint8) * 255).save(job / "masks" / f"{i + 1:04d}.png")
        if len(boxes):
            with (job / "masks" / "boxes.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"frame": i, "mode": mode, "target": None if target is None else
                                    [round(float(v), 2) for v in target], "area": int(masks[-1].sum())}) + "\n")
    (job / "masks").mkdir(exist_ok=True)
    (job / "masks" / "targets.txt").write_text(",".join(targets))
    trace("masks", t0, frames=len(masks), cached=len(cached) == len(paths))

    # 4. plan + control frames
    jobs = []
    for si, (a, b) in enumerate(shots):
        seen = [i for i in range(a, b) if masks[i].any()]
        if not seen:
            trace("skip_shot", t_all, shot=si, reason="actor not in view")
            continue
        # generate only where the actor is visible; a segment never reads frames of the next shot
        for s, length, use_from in segments(seen[0], seen[-1] + 1):
            idx = [min(i, b - 1) for i in range(s, s + length)]
            if not any(masks[i].any() for i in idx if i < b):
                trace("skip_segment", t_all, shot=si, start=s, reason="actor not in view")
                continue
            x0, y0, x1, y1 = roi_for([masks[i] for i in idx], hw, hh, args.roi_max)
            roi = (x0 * 2, y0 * 2, x1 * 2, y1 * 2)
            sd = job / f"seg_{si}_{s:04d}"
            (sd / "ctrl").mkdir(parents=True, exist_ok=True)
            for k, i in enumerate(idx, 1):
                frame = Image.open(paths[i]).convert("RGB").crop(roi).resize((GW, GH), Image.LANCZOS)
                mk = Image.fromarray(masks[i].astype(np.uint8) * 255).resize((fw, fh)).crop(roi).resize((GW, GH))
                Image.composite(Image.new("RGB", (GW, GH), (127, 127, 127)), frame, mk).save(sd / "ctrl" / f"{k:04d}.png")
            jobs.append({"shot": si, "start": s, "length": length, "use_from": use_from, "idx": idx, "roi": roi,
                         "dir": sd, "ref": refs[si], "prompt": prompts[si], "full_frame": roi == (0, 0, hw * 2, hh * 2)})
    (job / "plan.json").write_text(json.dumps(
        [{k: (str(v) if isinstance(v, Path) else v) for k, v in j.items() if k != "idx"} for j in jobs],
        ensure_ascii=False, indent=1), encoding="utf-8")
    trace("plan", t_all, segments=[(j["shot"], j["start"], j["length"], j["full_frame"], j["roi"]) for j in jobs])
    sheet = Image.new("RGB", (GW // 2 * len(jobs), GH // 2), "white")
    for n, jb in enumerate(jobs):
        sheet.paste(Image.open(jb["dir"] / "ctrl" / "0001.png").resize((GW // 2, GH // 2)), (n * GW // 2, 0))
    sheet.save(job / "plan_preview.png")
    if args.plan_only:
        return 0
    if args.composite_only:  # assemble from finished segments only; the rest of the frames stay the source
        jobs = [jb for jb in jobs if len(list((jb["dir"] / "gen").glob("*.png"))) >= jb["length"]]
        trace("composite_only", t_all, segments=[(j["shot"], j["start"]) for j in jobs])

    # 5. generation, --parallel segments at once
    stop, procs = threading.Event(), []

    def guard():
        while not stop.is_set():
            if g.free_ram_gb() < g.RAM_FLOOR_GB:
                for p in procs:
                    if p.poll() is None:
                        p.kill()
                trace("oom_guard", time.time(), killed=len(procs))
                return
            time.sleep(2)
    threading.Thread(target=guard, daemon=True).start()

    def run(jb):
        t1 = time.time()
        (jb["dir"] / "gen").mkdir(exist_ok=True)
        if len(list((jb["dir"] / "gen").glob("*.png"))) >= jb["length"]:
            return 0  # already generated (resume)
        cmd = [str(g.SD_CLI), "-M", "vid_gen", "--diffusion-model", str(g.MODELS["1.3b"]), "--vae", str(g.VAE),
               "--t5xxl", str(g.T5), "-p", jb["prompt"], "-n", g.NEGATIVE, "-i", str(jb["ref"]),
               "--control-video", str(jb["dir"] / "ctrl"), "--vace-strength", "1.0", "-W", str(GW), "-H", str(GH),
               "--video-frames", str(jb["length"]), "--fps", "16", "--steps", str(args.steps), "--cfg-scale", "6.0",
               "--sampling-method", "euler", "-s", str(args.seed), "--diffusion-fa", "--temporal-tiling",
               "--vae-tiling", "-o", str(jb["dir"] / "gen" / "%04d.png"), "-v"]
        with (jb["dir"] / "sd-cli.log").open("w", encoding="utf-8", errors="replace") as log:
            p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            procs.append(p)
            code = p.wait()
        trace("generate", t1, shot=jb["shot"], start=jb["start"], frames=jb["length"], full_frame=jb["full_frame"],
              roi=jb["roi"], exit=code, free_ram_gb=round(g.free_ram_gb(), 1))
        return code

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
        codes = list(ex.map(run, jobs))
    stop.set()
    trace("generate_all", t0, segments=len(jobs), parallel=args.parallel, exits=codes)
    if any(codes):
        raise SystemExit(f"generation failed: {codes}")

    # 6. composite into the full-resolution source
    t0 = time.time()
    out = job / "final16"
    out.mkdir(exist_ok=True)
    owner = {}
    for jb in jobs:
        for k, i in enumerate(jb["idx"]):
            if jb["use_from"] <= i < total and (i not in owner or owner[i][0]["start"] < jb["start"]):
                owner[i] = (jb, k)
    for i in range(total):
        src = np.asarray(Image.open(paths[i]).convert("RGB"), np.float32)
        if i not in owner:
            Image.fromarray(src.astype(np.uint8)).save(out / f"{i + 1:04d}.png")
            continue
        jb, k = owner[i]
        x0, y0, x1, y1 = jb["roi"]
        gen = Image.open(sorted((jb["dir"] / "gen").glob("*.png"))[k]).convert("RGB").resize((x1 - x0, y1 - y0), Image.LANCZOS)
        layer = src.copy()
        layer[y0:y1, x0:x1] = np.asarray(gen, np.float32)
        mfull = Image.fromarray(masks[i].astype(np.uint8) * 255).resize((fw, fh), Image.BILINEAR)
        m = np.asarray(mfull) > 127
        soft = np.asarray(mfull.filter(ImageFilter.GaussianBlur(8)), np.float32)[..., None] / 255
        layer = g.ring_match(layer, src, m)
        Image.fromarray((src * (1 - soft) + layer * soft).astype(np.uint8)).save(out / f"{i + 1:04d}.png")
    trace("composite", t0, frames=total)

    # 7. encode: 16 fps master, interpolated --out-fps with the source audio, source|result review
    t0 = time.time()
    ff = g.ffmpeg_bin("ffmpeg")
    m16, mout = job / "film_16fps.mp4", job / f"film_{args.out_fps}fps.mp4"
    subprocess.run([ff, "-v", "error", "-y", "-framerate", "16", "-i", str(out / "%04d.png"), "-i", str(args.src),
                    "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "15", "-preset", "slow",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(m16)], check=True)
    subprocess.run([ff, "-v", "error", "-y", "-i", str(m16), "-vf",
                    f"minterpolate=fps={args.out_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "15", "-preset", "slow", "-c:a", "copy", str(mout)], check=True)
    subprocess.run([ff, "-v", "error", "-y", "-framerate", "16", "-i", str(full / "%04d.png"), "-framerate", "16",
                    "-i", str(out / "%04d.png"), "-i", str(args.src), "-filter_complex",
                    "[0:v]scale=-2:960[a];[1:v]scale=-2:960[b];[a][b]hstack=inputs=2[v]", "-map", "[v]", "-map", "2:a?",
                    "-frames:v", str(total), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-c:a", "aac",
                    "-shortest", str(job / "review_source_vs_result.mp4")], check=True)
    trace("encode", t0, outputs=[str(m16), str(mout)])
    result = g.accept(mout, total, 16)
    trace("accept", t_all, **result)
    return 0 if result["full_decode"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
