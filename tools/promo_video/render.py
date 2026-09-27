"""Render the Bossman "32 days" promo (tools/promo_video/promo.html) and mux it with the soundtrack.

Pipeline:  voice.py (Kokoro TTS lines)  ->  music.py (synthesized score + VO mix -> soundtrack.wav)
           ->  render.py (deterministic canvas frames, sub-frame motion blur, ffmpeg).

    python tools/promo_video/render.py --work /tmp/promo --chromium <chrome>           # full render
    python tools/promo_video/render.py --work /tmp/promo 3.0 13.2                      # preview PNGs

Fonts come from tools/intro_video/fonts (fetched by tools/intro_video/render.py on first run).
Per-day commit counts in promo.html are real `git log --date=short` counts for 2026-08-27..09-27.
"""
import argparse
import base64
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
FPS, DUR = 60, 15.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=pathlib.Path, required=True, help="dir with soundtrack.wav; frames go here")
    parser.add_argument("--chromium", help="Chromium executable (default: Playwright's)")
    parser.add_argument("times", nargs="*", type=float)
    args = parser.parse_args()
    sys.path.insert(0, str(HERE.parent / "intro_video"))
    from render import fetch_fonts  # type: ignore  # noqa: E402
    from playwright.sync_api import sync_playwright

    fetch_fonts()
    frames = args.work / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        launch = {"args": ["--allow-file-access-from-files"]}
        if args.chromium:
            launch["executable_path"] = args.chromium
        browser = pw.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto((HERE / "promo.html").as_uri())
        page.evaluate("window.ready")
        times = args.times or [i / FPS for i in range(int(FPS * DUR))]
        for i, t in enumerate(times):
            data = page.evaluate(f"renderFrame({t})")
            name = f"preview_{t:05.2f}.png" if args.times else f"f{i:04d}.png"
            (frames / name).write_bytes(base64.b64decode(data.split(",", 1)[1]))
        browser.close()
    if args.times:
        return
    out = args.work / "bossman-32-days.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames / "f%04d.png"),
                    "-i", str(args.work / "soundtrack.wav"), "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart",
                    str(out)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-vf", "scale=1280:720:flags=lanczos",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "160k", "-movflags", "+faststart", str(args.work / "bossman-32-days-telegram.mp4")],
                   check=True)


if __name__ == "__main__":
    main()
