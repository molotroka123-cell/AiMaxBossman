"""Deterministic canvas renderer; no cloud models or personal media.

python render.py stills --lang ru --times 4.5 12 19 26 35 51 65 73 78
python render.py video --lang ru --out chunk.mp4 --start 0 --end 600
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
FPS = 30


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["stills", "video"])
    parser.add_argument("--lang", choices=["ru", "en"], default="ru")
    parser.add_argument("--times", nargs="*", type=float, default=[])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int)
    parser.add_argument("--crf", type=int, default=20)
    args = parser.parse_args()
    data = json.loads((HERE / "data.json").read_text(encoding="utf-8"))
    i18n = json.loads((HERE / "i18n.json").read_text(encoding="utf-8"))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--font-render-hinting=none"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.add_init_script(f"window.BOSSMAN_DATA={json.dumps(data, ensure_ascii=False)};window.BOSSMAN_I18N={json.dumps(i18n, ensure_ascii=False)};")
        page.goto((HERE / "bossman_evolution.html").as_uri() + f"?lang={args.lang}")
        total = float(page.evaluate("window.ready"))
        frames = round(total * FPS)
        canvas = page.locator("canvas")
        print(f"lang={args.lang} duration={total} frames={frames}", flush=True)
        if args.mode == "stills":
            for t in args.times:
                if t < 0 or t > total:
                    raise ValueError(f"Time {t} outside 0..{total}")
                page.evaluate("renderAt", t)
                out = HERE.parent / f"still_{args.lang}_{t:05.1f}.png"
                canvas.screenshot(path=str(out))
                print(out, flush=True)
        else:
            if args.out is None or args.end is None or not (0 <= args.start < args.end <= frames):
                raise ValueError("video needs --out, --start and --end within the timeline")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            ff = subprocess.Popen([
                "ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS),
                "-c:v", "mjpeg", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(args.crf),
                "-vf", "scale=in_range=pc:out_range=tv,format=yuv420p", "-pix_fmt", "yuv420p",
                "-color_range", "tv", "-movflags", "+faststart", str(args.out),
            ], stdin=subprocess.PIPE)
            try:
                for i in range(args.start, args.end):
                    page.evaluate("renderAt", i / FPS)
                    assert ff.stdin is not None
                    ff.stdin.write(canvas.screenshot(type="jpeg", quality=92))
                    if i % 150 == 0:
                        print(f"frame {i}/{args.end}", flush=True)
            finally:
                assert ff.stdin is not None
                ff.stdin.close()
            if ff.wait() != 0:
                raise RuntimeError("ffmpeg render failed")
        browser.close()


if __name__ == "__main__":
    main()
