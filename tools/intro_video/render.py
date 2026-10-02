"""Render the Bossman intro splash (tools/intro_video/intro.html) to video.

The timeline in intro.html is deterministic (``renderFrame(t)``), so every frame
is rendered off-line with sub-frame motion blur and encoded by ffmpeg:

    python tools/intro_video/render.py --out /tmp/intro        # full render
    python tools/intro_video/render.py --out /tmp/intro 1.7 3.9  # preview PNGs

Output: bossman-intro.mp4 (1920x1080) and bossman-intro.webm, the latter being
what command-center/ui/intro/ ships. Fonts (Onest, IBM Plex Mono, OFL) are
fetched from Google Fonts into tools/intro_video/fonts/ on first run.
"""
import argparse
import base64
import pathlib
import re
import shutil
import subprocess
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
FPS, DUR = 60, 5.0
FONTS_CSS = ("https://fonts.googleapis.com/css2?family=Onest:wght@500;800"
             "&family=IBM+Plex+Mono:wght@400;500&display=swap")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


def fetch_fonts() -> None:
    target = HERE / "fonts"
    if target.is_dir() and len(list(target.glob("*.woff2"))) == 4:
        return
    target.mkdir(exist_ok=True)
    request = urllib.request.Request(FONTS_CSS, headers={"User-Agent": UA})
    css = urllib.request.urlopen(request, timeout=30).read().decode()
    for subset, body in re.findall(r"/\* (\S+) \*/\s*@font-face\s*{([^}]*)}", css):
        if subset != "latin":
            continue
        family = re.search(r"font-family: '([^']+)'", body).group(1).replace(" ", "")
        weight = re.search(r"font-weight: (\d+)", body).group(1)
        url = re.search(r"url\((\S+?)\)", body).group(1)
        (target / f"{family}-{weight}.woff2").write_bytes(urllib.request.urlopen(url, timeout=30).read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--chromium", help="Chromium executable (default: Playwright's)")
    parser.add_argument("times", nargs="*", type=float, help="render preview PNGs at these times only")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    fetch_fonts()
    frames = args.out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        launch = {"args": ["--allow-file-access-from-files"]}
        if args.chromium:
            launch["executable_path"] = args.chromium
        browser = pw.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto((HERE / "intro.html").as_uri())
        page.evaluate("window.ready")
        times = args.times or [i / FPS for i in range(int(FPS * DUR))]
        for i, t in enumerate(times):
            data = page.evaluate(f"renderFrame({t})")
            name = f"preview_{t:.2f}.png" if args.times else f"f{i:04d}.png"
            (frames / name).write_bytes(base64.b64decode(data.split(",", 1)[1]))
        browser.close()
    if args.times:
        return
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    source = ["-framerate", str(FPS), "-i", str(frames / "f%04d.png")]
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", *source, "-c:v", "libx264", "-preset", "slow",
                    "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(args.out / "bossman-intro.mp4")], check=True)
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", *source, "-c:v", "libvpx-vp9", "-b:v", "0",
                    "-crf", "31", "-row-mt", "1", "-cpu-used", "1", "-pix_fmt", "yuv420p", "-an",
                    str(args.out / "bossman-intro.webm")], check=True)


if __name__ == "__main__":
    main()
