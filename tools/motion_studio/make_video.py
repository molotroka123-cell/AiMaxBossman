"""Scene spec -> finished video: voice-over, original score, deterministic frames, MP4.

    python tools/motion_studio/make_video.py SPEC.json --work DIR \
        [--tts-models DIR_WITH_KOKORO] [--chromium PATH] [--preview 3.0 13.2 ...] [--no-voice]

Steps (each one checkable on its own):
  1. validate the spec (spec.py) - a bad spec never reaches the renderer;
  2. synthesize every `vo` line with Kokoro (kokoro-onnx, local, offline); measured clip
     lengths are checked for real overlaps, not only the validator's estimate;
  3. compose the score from the spec (score.py) and mix the voice with ducking;
  4. render frames with engine.html in headless Chromium (Playwright);
  5. mux H.264 1080p + AAC, plus a 720p cut for Telegram.

Kokoro needs ESPEAK_DATA_PATH pointing at espeakng_loader's espeak-ng-data through a
short path: espeak-ng silently fails on long data paths.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score as score_mod  # noqa: E402
import spec as spec_mod  # noqa: E402

FPS = 60


def synth_voice(spec: dict, models: Path, work: Path) -> tuple[np.ndarray, list[dict]]:
    import soundfile as sf
    from kokoro_onnx import EspeakConfig, Kokoro
    from scipy.signal import resample_poly
    tts = Kokoro(str(models / "kokoro-v1.0.onnx"), str(models / "voices-v1.0.bin"),
                 espeak_config=EspeakConfig(data_path=os.environ.get("ESPEAK_DATA_PATH")))
    n = int(score_mod.SR * spec["meta"]["duration"])
    track = np.zeros(n)
    report = []
    (work / "voice").mkdir(parents=True, exist_ok=True)
    for sc in spec["scenes"]:
        for vo in sc.get("vo", []):
            a, sr = tts.create(vo["text"], voice=spec["meta"].get("voice", "am_fenrir"), speed=1.05, lang="en-us")
            idx = np.where(np.abs(a) > 0.012)[0]
            a = a[max(idx[0] - 240, 0):idx[-1] + 1500]
            a = resample_poly(a, score_mod.SR, sr)
            a = a / (np.abs(a).max() + 1e-9) * .9
            i = int(vo["t"] * score_mod.SR)
            j = min(i + len(a), n)
            track[i:j] += a[:j - i]
            report.append({"t": vo["t"], "text": vo["text"], "seconds": round(len(a) / score_mod.SR, 3)})
            sf.write(work / "voice" / f"{len(report):02d}.wav", a, score_mod.SR)
    report.sort(key=lambda r: r["t"])
    for a, b in zip(report, report[1:]):
        if b["t"] < a["t"] + a["seconds"] - .15:
            raise SystemExit(f"voice-over overlap: '{a['text']}' ends at {a['t'] + a['seconds']:.2f} s, "
                             f"'{b['text']}' starts at {b['t']} s - move it or shorten it")
    (work / "voice" / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return track, report


def render_frames(spec: dict, hits: list[float], work: Path, chromium: str | None, times: list[float]) -> None:
    from playwright.sync_api import sync_playwright
    sys.path.insert(0, str(HERE.parent / "intro_video"))
    from render import fetch_fonts  # type: ignore
    fetch_fonts()
    frames = work / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        kw = {"args": ["--allow-file-access-from-files"]}
        if chromium:
            kw["executable_path"] = chromium
        browser = pw.chromium.launch(**kw)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto((HERE / "engine.html").as_uri())
        page.evaluate("window.ready")
        page.evaluate("([s, h]) => window.loadSpec(s, h)", [spec, hits])
        all_times = times or [i / FPS for i in range(int(round(FPS * spec["meta"]["duration"])))]
        for i, t in enumerate(all_times):
            data = page.evaluate(f"renderFrame({t})")
            name = f"preview_{t:05.2f}.png" if times else f"f{i:05d}.png"
            (frames / name).write_bytes(base64.b64decode(data.split(",", 1)[1]))
            if errors:
                raise SystemExit(f"engine error at t={t}: {errors[0]}")
        browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--tts-models", type=Path, help="dir with kokoro-v1.0.onnx and voices-v1.0.bin")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--chromium")
    ap.add_argument("--preview", nargs="*", type=float, default=[])
    args = ap.parse_args()
    spec = spec_mod.load(args.spec)
    hits = spec_mod.hits(spec)
    args.work.mkdir(parents=True, exist_ok=True)
    if args.preview:
        render_frames(spec, hits, args.work, args.chromium, args.preview)
        print("preview frames:", args.work / "frames")
        return
    vo = None
    if not args.no_voice:
        if not args.tts_models:
            raise SystemExit("--tts-models is required (or pass --no-voice)")
        vo, report = synth_voice(spec, args.tts_models, args.work)
        print(f"voice: {len(report)} lines")
    import soundfile as sf
    sf.write(args.work / "soundtrack.wav", score_mod.compose(spec, hits, vo).astype(np.float32), score_mod.SR)
    render_frames(spec, hits, args.work, args.chromium, [])
    out = args.work / "video.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(args.work / "frames" / "f%05d.png"),
                    "-i", str(args.work / "soundtrack.wav"), "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", str(out)],
                   check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-vf", "scale=1280:720:flags=lanczos",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(args.work / "video-telegram.mp4")], check=True)
    print("video:", out)


if __name__ == "__main__":
    main()
