"""Delivery pass of an HQ master: small luma unsharp (after the temporally stabilised restoration), titles at native
1080x1920, audio, H.264 crf 15 yuv420p (+ a Telegram copy <= 49 MB).

  python finish_hq.py PRESET MASTER OUT.mp4 [--sharp 0.25] [--tg]
PRESET is a build_ads preset name (its titles / audio plan); MASTER the video-only near-lossless master.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import build_ads as ba  # noqa: E402


def tg_copy(src: Path, dst: Path, limit_mb: float = 47.0) -> None:
    """Tuned x264 two-step: crf 19; if the file is above the limit, a bitrate-capped retry from the duration."""
    if src.stat().st_size <= limit_mb * 1024 * 1024:           # already small enough: the same encode
        import shutil
        shutil.copyfile(src, dst)
        return
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                               capture_output=True, text=True).stdout.strip())
    for crf, cap in ((19, None), (22, None), (None, int(limit_mb * 8192 / dur * 0.9))):
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "slow", "-tune", "film", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
        if crf is not None:
            cmd += ["-crf", str(crf)]
        else:
            cmd += ["-b:v", f"{cap}k", "-maxrate", f"{int(cap * 1.3)}k", "-bufsize", f"{cap * 2}k"]
        subprocess.run(cmd + [str(dst)], check=True)
        if dst.stat().st_size <= limit_mb * 1024 * 1024:
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("preset")
    ap.add_argument("master")
    ap.add_argument("out")
    ap.add_argument("--sharp", type=float, default=0.25)
    ap.add_argument("--tg", action="store_true")
    ap.add_argument("--crf", type=int, default=15)
    a = ap.parse_args()
    master = Path(a.master)
    sharp = master.with_name(master.stem + "_sharp.mp4")
    if a.sharp > 0:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(master), "-vf", f"unsharp=5:5:{a.sharp}:5:5:0", "-c:v", "libx264",
                        "-crf", "4", "-preset", "fast", "-pix_fmt", "yuv420p", str(sharp)], check=True)
        master = sharp
    spec = ba.PRESETS[a.preset]()
    out = Path(a.out)
    ba.finish(master, spec, out, crf=a.crf)
    if a.tg:
        tg_copy(out, out.with_name(out.stem + "_tg.mp4"))
    print("done", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
