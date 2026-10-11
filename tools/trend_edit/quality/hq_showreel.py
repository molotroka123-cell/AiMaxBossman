"""(e) showreel from the HQ finished ads (a, b, c, d): same cut plan as build_ads.render_showreel, native 1080x1920 parts.

  python hq_showreel.py --a A.mp4 --b B.mp4 --c C.mp4 --d D.mp4 --out OUT.mp4
The parts already carry their own (crisp, native) type; only the end card text is added here.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import build_ads as ba  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    for k in "abcd":
        ap.add_argument(f"--{k}", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sharp", type=float, default=0.0)
    a = ap.parse_args()
    out = Path(a.out)
    work = out.parent / "work_e"
    work.mkdir(parents=True, exist_ok=True)
    real_popen = subprocess.Popen

    class _SP:
        def __getattr__(self, n):
            return getattr(subprocess, n)

        @staticmethod
        def Popen(cmd, *args, **kw):
            if isinstance(cmd, list) and cmd and cmd[0] == "ffmpeg" and "-crf" in cmd:
                cmd = list(cmd)
                cmd[cmd.index("-crf") + 1] = "4"
            return real_popen(cmd, *args, **kw)
    ba.subprocess = _SP()
    master = work / "showreel_master_hq.mp4"
    ba.render_showreel({k: getattr(a, k) for k in "abcd"}, master)
    spec = ba.showreel_spec()
    ba.finish(master, spec, out, crf=15)
    print("done", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
