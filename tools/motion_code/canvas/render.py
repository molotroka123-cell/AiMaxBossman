"""Render motion.html + scenes.json frame-by-frame (deterministic) and mux with an audio track.
usage: python render.py scenes.json out.mp4 [audio] [--fps 60] [--preview 0,3.2,9]"""
import argparse, json, os, pathlib, subprocess, sys
from playwright.sync_api import sync_playwright
HERE = pathlib.Path(__file__).parent
ap = argparse.ArgumentParser(); ap.add_argument("spec"); ap.add_argument("out"); ap.add_argument("audio", nargs="?")
ap.add_argument("--fps", type=int, default=60); ap.add_argument("--preview", default="")
a = ap.parse_args()
spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(os.environ["LOCALAPPDATA"], "ms-playwright"))
with sync_playwright() as p:
    b = p.chromium.launch(args=["--force-color-profile=srgb"])
    pg = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    pg.add_init_script(f"window.SPEC = {json.dumps(spec)};")
    pg.goto((HERE / "motion.html").as_uri()); pg.wait_for_function("window.render && document.fonts.ready")
    total = pg.evaluate("window.TOTAL")
    if a.preview:
        for t in [float(x) for x in a.preview.split(",")]:
            pg.evaluate(f"render({t})"); pg.locator("canvas").screenshot(path=str(pathlib.Path(a.out).with_name(f"preview_{t:05.2f}.png")))
        b.close(); sys.exit(0)
    n = int(round(total * a.fps))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(a.fps), "-i", "-"]
    if a.audio: cmd += ["-i", a.audio]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if a.audio: cmd += ["-c:a", "aac", "-b:a", "256k", "-af", f"afade=t=out:st={total-0.6}:d=0.6", "-shortest"]
    cmd += ["-t", str(total), a.out]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        pg.evaluate(f"render({i / a.fps})")
        ff.stdin.write(pg.locator("canvas").screenshot(type="png"))
        if i % 120 == 0: print(f"frame {i}/{n}", flush=True)
    ff.stdin.close(); ff.wait(); b.close()
    print("done", a.out, ff.returncode)
