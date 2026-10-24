"""15 s 9:16 «outfit change on the beat» dance clip, fully local (owner 24.10, Bossman Motion Studio).

    python tools/tiktok_outfit_clip.py gen --look casual --image <png> --prompt "..." [--steps 30] [--seed 1]
    python tools/tiktok_outfit_clip.py assemble

gen       one ~5 s image-to-video segment with LTX-2.5 dev (Q8_0 GGUF) through the local sd-cli (Vulkan, AMD).
assemble  three segments cut ON strong beats near 5 s and 10 s of the 15 s audio window (the drop is at 5 s),
          a 2-frame white flash at each look change, «Bossman Motion Studio» watermark bottom right, the audio muxed.
Fictional character only (owner's choice 24.10); every step writes into <work>/receipt.json (models, params, seconds).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import numpy as np

SD = r"C:\Users\asd\Bossman\media-runtime\sdcpp\vulkan\sd-cli.exe"
LTX = Path(r"D:\Bossman\models\media\ltx-2.5")
FILES = {"diffusion": LTX / "dev" / "ltx-2.5-22b-dev-transformer-Q8_0.gguf",
         "vae": LTX / "vae" / "ltx-2.5-video-vae-conv-bf16.safetensors",
         "audio_vae": LTX / "vae" / "ltx-2.5-audio-vae-bf16.safetensors",
         "llm": LTX / "text_encoders" / "gemma4-12b-with-proj-ltx-2.5-bf16.safetensors"}
WORK = Path(r"D:\Bossman\work\tiktok-trend-20261024")
ORDER = ("casual", "hype", "formal")
NEG = ("blurry, low quality, distorted face, extra limbs, extra fingers, deformed hands, flicker, text, watermark, "
       "logo, frame border, static, frozen")


def _receipt(update: dict) -> None:
    p = WORK / "receipt.json"
    doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"schema": "bossman.motion-studio.v1"}
    for k, v in update.items():
        if isinstance(v, dict) and isinstance(doc.get(k), dict):
            doc[k].update(v)
        else:
            doc[k] = v
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def gen(a) -> int:
    missing = [k for k, f in FILES.items() if not f.is_file()]
    if missing:
        raise SystemExit(f"LTX files missing: {missing}")
    out = WORK / f"seg-{a.look}.mp4"
    # Q6_K by choice 24.10: Q8_0 at 720x1280x121 pushed the PC to critical memory and the run was reaped
    # distilled (8 steps, cfg 1) after the dev model needed >90 min for one 5 s segment on this PC (24.10)
    diffusion = LTX / a.variant / f"ltx-2.5-22b-{a.variant}-transformer-{a.quant}.gguf"
    argv = [SD, "-M", "vid_gen", "--diffusion-model", str(diffusion), "--vae", str(FILES["vae"]),
            "--audio-vae", str(FILES["audio_vae"]), "--llm", str(FILES["llm"]), "-p", a.prompt, "-n", NEG,
            "--cfg-scale", str(a.cfg), "--sampling-method", "euler", "--steps", str(a.steps), "-W", str(a.width), "-H", str(a.height),
            # --diffusion-fa: without flash attention 720x1280x121 asked for a 35.9 GB compute buffer and crashed (24.10)
            "--video-frames", str(a.frames), "--fps", "24", "-s", str(a.seed), "--offload-to-cpu", "--vae-tiling",
            "--diffusion-fa", "--mmap",          # weights memory-mapped from the D: SSD instead of committed RAM
            "-i", a.image, "-o", str(out)]
    t0 = time.time()
    r = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    secs = round(time.time() - t0, 1)
    (WORK / f"seg-{a.look}.log").write_text((r.stdout or "")[-20000:] + "\n--- stderr ---\n" + (r.stderr or "")[-20000:],
                                           encoding="utf-8")
    avi = out.with_name(out.name + ".avi")             # sd-cli appends .avi to an .mp4 target (seen 24.10)
    if r.returncode == 0 and not out.is_file() and avi.is_file():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(avi), "-c:v", "libx264", "-preset", "slow", "-crf", "14",
                        "-pix_fmt", "yuv420p", str(out)], capture_output=True)
    ok = r.returncode == 0 and out.is_file() and out.stat().st_size > 10000
    _receipt({"segments": {a.look: {"model": f"LTX-2.5 22B {a.variant} {a.quant} (local sd-cli Vulkan)", "image": a.image,
                                    "prompt": a.prompt, "steps": a.steps, "cfg": a.cfg, "frames": a.frames, "fps": 24,
                                    "size": f"{a.width}x{a.height}", "seed": a.seed, "seconds": secs, "rc": r.returncode,
                                    "out": str(out) if ok else None}}})
    print(json.dumps({"look": a.look, "ok": ok, "seconds": secs, "rc": r.returncode}))
    return 0 if ok else 1


def beat_cuts(audio: Path, targets=(5.0, 10.0), window=0.35) -> list[float]:
    """The strongest onset within ±window of each target second (cuts land ON a beat)."""
    sr = 22050
    raw = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(audio), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True).stdout
    y = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    hop, win = 256, 1024
    fr = np.lib.stride_tricks.sliding_window_view(y, win)[::hop] * np.hanning(win)
    mag = np.abs(np.fft.rfft(fr, axis=1))
    flux = np.concatenate([[0], np.maximum(0, np.diff(np.log1p(mag), axis=0)).sum(1)])
    t = np.arange(len(flux)) * hop / sr
    cuts = []
    for c in targets:
        m = (t >= c - window) & (t <= c + window)
        cuts.append(round(float(t[m][np.argmax(flux[m])]), 3))
    return cuts


def assemble(a) -> int:
    audio = WORK / "audio-65-80.m4a"
    segs = [WORK / f"seg-{k}.mp4" for k in ORDER]
    missing = [s.name for s in segs if not s.is_file()]
    if missing:
        raise SystemExit(f"segments missing: {missing}")
    c1, c2 = beat_cuts(audio)
    d = [c1, c2 - c1, 15.0 - c2]
    font = "C\\:/Windows/Fonts/arialbd.ttf"
    parts = "".join(f"[{i}:v]trim=0:{d[i]:.3f},setpts=PTS-STARTPTS,scale=720:1280:flags=lanczos,unsharp=5:5:0.6,fps=24,setsar=1[v{i}];"
                    for i in range(3))
    flash = f"between(t,{c1:.3f},{c1 + 0.083:.3f})+between(t,{c2:.3f},{c2 + 0.083:.3f})"
    fc = (parts + "[v0][v1][v2]concat=n=3:v=1:a=0[cat];"
          f"[cat]drawbox=x=0:y=0:w=iw:h=ih:color=white@0.85:t=fill:enable='{flash}',"
          f"drawtext=fontfile='{font}':text='Bossman Motion Studio':fontsize=30:fontcolor=white@0.88:"
          "shadowcolor=black@0.55:shadowx=2:shadowy=2:x=w-tw-28:y=h-th-40[out]")
    out = WORK / "bossman-motion-studio-outfit-dance-720p.mp4"
    argv = ["ffmpeg", "-v", "error", "-y", *sum([["-i", str(s)] for s in segs], []), "-i", str(audio),
            "-filter_complex", fc, "-map", "[out]", "-map", "3:a", "-t", "15", "-c:v", "libx264", "-preset", "slow",
            "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    r = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    ok = r.returncode == 0 and out.is_file()
    _receipt({"assembly": {"cuts_s": [c1, c2], "durations_s": [round(x, 3) for x in d], "flash_frames": 2,
                           "watermark": "Bossman Motion Studio (bottom right)", "audio_window": "65-80 s, drop at 70 s",
                           "out": str(out) if ok else None, "error": None if ok else r.stderr[-800:]}})
    print(json.dumps({"ok": ok, "cuts": [c1, c2], "out": str(out)}))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--look", required=True, choices=ORDER)
    g.add_argument("--image", required=True)
    g.add_argument("--prompt", required=True)
    g.add_argument("--steps", type=int, default=30)
    g.add_argument("--cfg", type=float, default=3.0)
    g.add_argument("--frames", type=int, default=97)
    g.add_argument("--quant", default="Q6_K", choices=["Q6_K", "Q8_0", "Q5_K_M", "Q4_K_M"])
    g.add_argument("--variant", default="dev", choices=["dev", "distilled"])
    # 576x1024 + lanczos to 720x1280 in assemble: 720x1280x121 peaked under 3 GB free RAM on this PC (24.10)
    g.add_argument("--width", type=int, default=576)
    g.add_argument("--height", type=int, default=1024)
    g.add_argument("--seed", type=int, default=1)
    sub.add_parser("assemble")
    a = ap.parse_args(argv)
    return gen(a) if a.cmd == "gen" else assemble(a)


if __name__ == "__main__":
    raise SystemExit(main())
