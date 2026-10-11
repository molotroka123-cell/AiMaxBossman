"""V1 | V2 side-by-side video of two deliveries of the same source (owner 10.10: «посмотрим улучшения»).

Left = V1, right = V2, labelled; audio of V2; H.264 crf 20 (the owner's pult limit is 49 MB: when the file is too big
the halves are scaled down by 0.75 steps, the crf is never raised above 20).

  python side_by_side.py v1.mp4 v2.mp4 out.mp4
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

LIMIT_BYTES = 49 * 1024 * 1024
FONT = "C\\:/Windows/Fonts/arial.ttf"


def build_cmd(v1: Path, v2: Path, out: Path, scale: float, crf: int = 20) -> list[str]:
    half = f"scale=trunc(iw*{scale}/2)*2:trunc(ih*{scale}/2)*2" if scale != 1.0 else "null"
    label = ("drawtext=fontfile='{f}':text='{t}':x=12:y=12:fontsize=h/28:fontcolor=white:box=1:boxcolor=black@0.55:"
             "boxborderw=6")
    graph = (f"[0:v]{half},{label.format(f=FONT, t='V1')}[a];[1:v]{half},{label.format(f=FONT, t='V2')}[b];"
             f"[a][b]hstack=inputs=2[v]")
    return [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-i", str(v1), "-i", str(v2), "-filter_complex", graph,
            "-map", "[v]", "-map", "1:a:0?", "-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-fps_mode", "passthrough", "-movflags", "+faststart", str(out)]


def make(v1: Path, v2: Path, out: Path) -> dict:
    scale = 1.0
    for _ in range(5):
        r = subprocess.run(build_cmd(v1, v2, out, scale), capture_output=True, text=True)
        if r.returncode != 0 or not out.is_file():
            raise RuntimeError("ffmpeg failed: " + (r.stderr or "")[-400:])
        if out.stat().st_size <= LIMIT_BYTES:
            return {"out": str(out), "bytes": out.stat().st_size, "scale": scale, "crf": 20}
        scale *= 0.75
    raise RuntimeError("side-by-side does not fit 49 MB at crf 20 even scaled down")


if __name__ == "__main__":
    print(make(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
