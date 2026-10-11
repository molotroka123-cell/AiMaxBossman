"""Promo 6 "Genjutsu Studio" v4 (22-25 s, 9:16): only capabilities that really work on video today.

Segments (each a different, strong shot; no split screens, effects only on beat hits of <= 6 frames):
  1. FACE SWAP   same take: original face -> her face (hard cut on a beat), then her face on three OTHER people (match cuts)
  2. HAIR COLOUR natural presets, only windows of the v3 HQ master with a clean matte (picked by hand from the contact sheet)
  3. BACKGROUND  RVM matte, only clean windows of the HQ background master
  4. CONSTRUCTOR the real genjutsu_core.js recolour, natural preset family (capture_screencast.py), native 1080x1920
  5. CTA card    with the honest line; clothes / hairstyle are NOT claimed (see the end card)

Titles are drawn by ffmpeg at native 1080x1920 (edit_core.final_pass): white on a dark box, safe zone away from the face.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import outfit_cut as oc  # noqa: E402
import build_ads as ba  # noqa: E402
import fx  # noqa: E402

Q, W, H, FPS, P = oc.Q, oc.W, oc.H, oc.FPS, ba.GRID.period
PLAN = json.loads((Q / "promo_v4_plan.json").read_text(encoding="utf-8")) if (Q / "promo_v4_plan.json").exists() else {}


@dataclass
class Seg:
    b0: float
    nb: float
    kind: str                 # swap_orig | swap_her | cut | master | screencast | card
    a: dict


def hit_fx(fr, t_in, strength=1.0, head=(540, 760)):
    """<= 6 frames (0.2 s): a short flash and, in the first 3 frames only, a small RGB split away from the face."""
    if t_in < 0.2:
        dx = int(round(10 * strength)) if t_in < 0.1 else 0
        if dx >= 1:
            fr = ba.face_protect(fx.rgb_split(fr, dx, 0), fr, head)
        fr = ba.face_protect(fx.flash(fr, 0.30 * strength * math.exp(-t_in / 0.05)), fr, head)
    return fr


def card(t: float) -> np.ndarray:
    fr = np.zeros((H, W, 3), np.uint8)
    ys = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    fr[:, :, 0] = (28 + 30 * ys).astype(np.uint8)
    fr[:, :, 1] = (14 + 10 * ys).astype(np.uint8)
    fr[:, :, 2] = (20 + 14 * ys).astype(np.uint8)
    return fr


class Master:
    def __init__(self, path: Path):
        self.cap = cv2.VideoCapture(str(path))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0

    def at(self, t: float) -> np.ndarray:
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * self.fps)))
        ok, f = self.cap.read()
        return f if ok else np.zeros((H, W, 3), np.uint8)


def build_segments() -> list[Seg]:
    """The timeline in beats (period 0.4598 s): 50 beats = 23.0 s."""
    S = PLAN["segments"]
    return [Seg(s["b0"], s["nb"], s["kind"], s) for s in S]


def render(out_video: Path) -> None:
    segs = build_segments()
    total_beats = max(s.b0 + s.nb for s in segs)
    n = int(round(total_beats * P * FPS))
    # sources
    tee_cfg_orig = dict(oc.CLIPS["tee"])
    tee_cfg_orig.update(path=oc.TEST / "ref-clip-01.mp4", sr="sr_orig_f", srx=3)
    src = {k: oc.Src(k) for k in ("tee", "dress", "pilot", "btee", "uni04", "uni05")}
    src["tee_orig"] = oc.Src("tee", tee_cfg_orig)
    masters: dict[str, Master] = {}
    sc_frames = sorted((Q / "screencast" / "frames").glob("f*.png"))
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
                             "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "6", "-pix_fmt", "yuv420p", str(out_video)],
                            stdin=subprocess.PIPE)
    for k in range(n):
        tau = k / FPS
        beat = tau / P
        sg = next((s for s in segs if s.b0 <= beat < s.b0 + s.nb), segs[-1])
        t_in = tau - sg.b0 * P
        d = sg.nb * P
        a = sg.a
        if sg.kind == "card":
            fr = card(t_in)
        elif sg.kind in ("swap_orig", "swap_her"):
            which = src["tee_orig"] if sg.kind == "swap_orig" else src["tee"]
            s_t = a["t0"] + a.get("speed", 0.8) * (t_in + (a.get("off", 0.0)))
            z = a.get("z0", 1.0) + (a.get("z1", 1.06) - a.get("z0", 1.0)) * min(1.0, t_in / d)
            fr, head = oc.draw_shot(which, s_t, z)
            if sg.kind == "swap_her":
                fr = hit_fx(fr, t_in, 1.0, head)
            fr = ba.grade(fr)
        elif sg.kind == "cut":
            sp = oc.ShotPlan(sg.b0, sg.nb, a["clip"], [(0.0, a.get("speed", 1.0)), (d, a.get("speed", 1.0))], a.get("z0", 1.0),
                             a.get("z1", 1.06), 1.0)
            s_t = a["t0"] + a.get("speed", 1.0) * t_in
            z = (sp.z0 + (sp.z1 - sp.z0) * min(1.0, t_in / d)) * fx.punch_scale(t_in, 0.0, peak=0.06, decay=0.12)
            fr, head = oc.draw_shot(src[a["clip"]], s_t, z)
            fr = hit_fx(fr, t_in, 1.0, head)
            fr = ba.grade(fr)
        elif sg.kind == "master":
            m = masters.setdefault(a["file"], Master(Path(a["file"])))
            fr = m.at(a["t0"] + t_in * a.get("speed", 1.0))
            z = a.get("z0", 1.0) + (a.get("z1", 1.0) - a.get("z0", 1.0)) * min(1.0, t_in / d)
            if z > 1.001:
                fr = ba._cover(fr, W, H, (0.5, 0.42), z)
            fr = hit_fx(fr, t_in, 0.8)
        elif sg.kind == "screencast":
            i = min(len(sc_frames) - 1, int((a.get("t0", 0.0) + t_in * a.get("speed", 1.0)) * FPS))
            fr = cv2.imread(str(sc_frames[i]))
            fr = hit_fx(fr, t_in, 0.5)
        else:
            raise ValueError(sg.kind)
        proc.stdin.write(np.ascontiguousarray(fr).tobytes())
    proc.stdin.close()
    proc.wait()


def texts(total_beats: float) -> ba.Spec:
    T: list = []
    _T = ba._T
    for t in PLAN.get("texts", []):
        kw = dict(t.get("kw", {}))
        kw.setdefault("box", True)
        _T(T, t["text"], t["b0"], t["b1"], **kw)
    return ba.Spec([], T, total_beats, "promo_v4", audio_loop=(20, 8))


def main() -> int:
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Q / "hq" / "promo-6-genjutsu-studio_hq_v4.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / "work_promo"
    work.mkdir(exist_ok=True)
    master = work / "promo_v4_master.mp4"
    render(master)
    segs = build_segments()
    tb = max(s.b0 + s.nb for s in segs)
    ba.finish(master, texts(tb), out, crf=15)
    print("done", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
