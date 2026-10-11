"""Deterministic capture of constructor_screencast_natural.html (real genjutsu_core.js recolour, natural preset family).

  python capture_screencast.py --site DIR --out frames_dir --seconds 6 --fps 30
then: ffmpeg -framerate 30 -i frames_dir/f%04d.png -c:v libx264 -crf 12 -pix_fmt yuv420p screencast_natural.mp4
"""
import argparse
import http.server
import socketserver
import threading
from functools import partial
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=a.site)
    handler.log_message = lambda *args, **kw: None
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    n = int(a.seconds * a.fps)
    with sync_playwright() as p:
        try:
            br = p.chromium.launch()
        except Exception:
            br = p.chromium.launch(channel="msedge")
        pg = br.new_page(viewport={"width": 1080, "height": 1920})
        for i in range(n):
            t = i / max(1, n - 1)
            pg.goto(f"http://127.0.0.1:{port}/index.html?t={t:.4f}&a=/_assets")
            pg.wait_for_function("document.title === 'ready'", timeout=30000)
            pg.screenshot(path=str(out / f"f{i:04d}.png"))
        br.close()
    srv.shutdown()
    print("frames", n)


if __name__ == "__main__":
    main()
