"""A small synthetic test package for the Motion workflow: no personal media, nothing leaves the PC.

    python tools/motion_workflow/synthetic_package.py OUT_DIR [--size 1280x720] [--fps 30] [--scene-seconds 4]

Writes into OUT_DIR (ffmpeg ``lavfi`` only, no external files):

  source.mp4    three visibly different scenes (colour bars / gradients / fractal) + a voice-like tone track
  music.wav     a short arpeggio, 10 s by default (a replacement soundtrack to bind as ``@music``)
  title.txt     a one-line title text (a ``text`` reference)
  package.json  every parameter used, so the package can be regenerated and compared
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
TITLE = "ПРЕМЬЕРА: Bossman Motion"


def _ffmpeg() -> str:
    found = os.environ.get("BOSSMAN_FFMPEG") or shutil.which("ffmpeg")
    if not found:
        raise SystemExit("ffmpeg not found (set BOSSMAN_FFMPEG or put ffmpeg on PATH)")
    return found


def _run(argv: list[str]) -> None:
    done = subprocess.run(argv, capture_output=True, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {done.stderr.decode('utf-8', 'replace')[-600:]}")


def make_package(folder: str | os.PathLike, *, size: str = "1280x720", fps: int = 30, scene_seconds: float = 4.0,
                 music_seconds: float = 10.0, ffmpeg: str | None = None) -> dict:
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg or _ffmpeg()
    total = scene_seconds * 3
    graph = (f"testsrc2=size={size}:rate={fps}:duration={scene_seconds}[a];"
             f"gradients=size={size}:rate={fps}:duration={scene_seconds}:speed=0.05:c0=0x103070:c1=0xf0a020:nb_colors=3[b];"
             f"mandelbrot=size={size}:rate={fps}[c0];[c0]trim=duration={scene_seconds},setpts=PTS-STARTPTS[c];"
             "[a][b][c]concat=n=3:v=1:a=0[v]")
    voice = ("aevalsrc='0.35*sin(2*PI*(140+12*sin(2*PI*3*t))*t)*(0.6+0.4*sin(2*PI*2*t))':"
             f"s=48000:d={total}")
    source = out / "source.mp4"
    _run([ff, "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", graph, "-f", "lavfi", "-i", voice,
          "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-crf", "18",
          "-c:a", "aac", "-b:a", "128k", "-shortest", str(source)])
    music = out / "music.wav"
    arpeggio = ("aevalsrc='0.3*sin(2*PI*220*t)*exp(-3*mod(t,0.5))+0.25*sin(2*PI*330*t)*exp(-3*mod(t,0.5))':"
                f"s=44100:d={music_seconds}")
    _run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", arpeggio, str(music)])
    (out / "title.txt").write_bytes(TITLE.encode("utf-8"))
    info = {"size": size, "fps": fps, "scene_seconds": scene_seconds, "scenes": 3, "duration": total,
            "music_seconds": music_seconds, "title": TITLE, "generator": "ffmpeg lavfi testsrc2 / gradients / mandelbrot",
            "files": {"source": source.name, "music": music.name, "title": "title.txt"}}
    (out / "package.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--scene-seconds", type=float, default=4.0)
    ap.add_argument("--music-seconds", type=float, default=10.0)
    args = ap.parse_args()
    info = make_package(args.out, size=args.size, fps=args.fps, scene_seconds=args.scene_seconds,
                        music_seconds=args.music_seconds)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
