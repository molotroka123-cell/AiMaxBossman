"""High-resolution re-render of the ad presets of build_ads.py (no FaceFusion, no titles in the pixels).

Root cause of the soft look: the face-swapped source is a 668x720 picture that build_ads scales x2.67 to 1080x1920.
Here the source frames are first restored by Real-ESRGAN x4plus (realesrgan-ncnn-vulkan, BSD-3, Vulkan; see sr_frames.py),
kept at 3x of the 720x720 crop (2160x2160) and the SAME spec / edits are rendered from them:

  * every edit that build_ads computes at 720x720 (hair recolour, face orig|swap) is computed unchanged at 720 and lifted
    to high resolution as a DELTA on top of the restored frame, so the restored strands/skin texture survives the recolour;
  * the background teleport is composited at high resolution (person matte resized, plate resampled at 3x);
  * type is NOT in the master: ec.final_pass draws it at native 1080x1920 (crisp), exactly like the old finish().

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/quality/hq_render.py hair_cycle --variant v3 --out ad-b_hq.mp4
Variants: plain (build_ads as sent), v3 (natural hair presets = hair_natural/render_v3.install()).
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TE = HERE.parent
sys.path.insert(0, str(TE))
sys.path.insert(0, str(TE / "hair_natural"))

S = 3                                   # restored frames are S x the 720x720 crop
QDIR = Path.home() / "Videos" / "Bossman-Genjutsu" / "ads-20261011" / "quality"
SHARP = float(os.environ.get("HQ_SHARP", "0.3"))    # residual unsharp, sigma 1.0 (old files: sigma 1.6, amount 0.5 on a x2.67 bicubic)


def install(variant: str):
    if variant == "v3":
        os.environ["BUILD_ADS_FUSED"] = "1"
    import cv2
    import numpy as np
    import build_ads as ba
    import hair_v2 as hv
    if variant == "v3":
        import render_v3
        render_v3.install()

    class HiArr(np.ndarray):
        hr = None

    def with_hr(lo, hr):
        v = lo.view(HiArr)
        v.hr = hr
        return v

    # ---- restored source frames
    store: dict = {}

    def hr_frame(tag: str, i: int, lo):
        key = (tag, i)
        if key not in store:
            for suffix in ("_f", "_s", ""):          # face-blended (face_blend.py) > stabilised (stabilize_sr.py) > raw
                p = QDIR / f"sr_{tag}{suffix}" / f"{i:05d}.png"
                if p.exists():
                    break
            im = cv2.imread(str(p)) if p.exists() else None
            if im is None:                                    # outside the restored range: plain bicubic (never used by the presets)
                im = cv2.resize(lo, (720 * S, 720 * S), interpolation=cv2.INTER_CUBIC)
                print("WARN no SR frame", tag, i, file=sys.stderr)
            else:
                im = cv2.resize(im, (720 * S, 720 * S), interpolation=cv2.INTER_AREA)
            store[key] = im
        return store[key]

    def frame(self, t, which="swap", blend=True):
        x = min(max(0.0, t * self.fps), self.n - 1.001)
        i0 = int(math.floor(x))
        w = x - i0
        arr = self.swap if which == "swap" else self.orig
        f = arr[i0]
        h = hr_frame(which, i0, f)
        if blend and w > 0.08:
            f = cv2.addWeighted(f, 1 - w, arr[i0 + 1], w, 0)
            h = cv2.addWeighted(h, 1 - w, hr_frame(which, i0 + 1, arr[i0 + 1]), w, 0)
        return with_hr(f, h), i0
    ba.Source.frame = frame

    # ---- crop to the 9:16 canvas at high resolution
    def crop_to_canvas(img, head, zoom, head_y_frac=0.36):
        if getattr(img, "hr", None) is not None:
            img = img.hr
        k = img.shape[0] / 720.0
        ch = 720.0 / zoom
        cw = ba.CROP_W / zoom
        cx = min(max(head[0], ba.PIC_X0 - 280 + cw / 2), ba.PIC_X1 - 280 - cw / 2)
        y0 = min(max(head[1] - head_y_frac * ch, 0.0), 720.0 - ch)
        x0 = cx - cw / 2
        sx, sy = ba.OUT_W / (cw * k), ba.OUT_H / (ch * k)
        m = np.array([[sx, 0, -x0 * k * sx], [0, sy, -y0 * k * sy]], np.float32)
        out = cv2.warpAffine(np.ascontiguousarray(img), m, (ba.OUT_W, ba.OUT_H),
                             flags=cv2.INTER_AREA if (sx < 1.0) else cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        hx = (head[0] - x0) * ba.OUT_W / cw
        hy = (head[1] - y0) * ba.OUT_H / ch
        blur = cv2.GaussianBlur(out, (0, 0), 1.0)
        return cv2.addWeighted(out, 1 + SHARP, blur, -SHARP, 0), (hx, hy)
    ba.crop_to_canvas = crop_to_canvas

    # ---- high-resolution plates
    plates: dict = {}

    def plate_hr(ex, key: str, t: float):
        if key not in plates:
            if key in ("neon", "neon2"):
                im = cv2.imread(str(ba.PLATE_NEON))
                s = 840.0 * S / im.shape[0]
                im = cv2.resize(im, (int(im.shape[1] * s), 840 * S), interpolation=cv2.INTER_CUBIC)
                if key == "neon2":
                    im = im[:, ::-1].copy()
                    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
                    hsv[:, :, 0] = (hsv[:, :, 0].astype(int) + 70) % 180
                    im = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
                plates[key] = im
            else:                                             # procedural studio: smooth, resample the small one
                ex.plate(key, 0.0)
                small = ex._plates[key]
                plates[key] = cv2.resize(small, (small.shape[1] * S, small.shape[0] * S), interpolation=cv2.INTER_CUBIC)
        im = plates[key]
        h, w = im.shape[:2]
        pan = 0.5 + 0.5 * math.sin(t * 0.7)
        x0 = int((w - 720 * S) * pan)
        y0 = max(0, (h - 720 * S) // 2)
        return np.ascontiguousarray(im[y0:y0 + 720 * S, x0:x0 + 720 * S])

    def composite_bg_hr(img, al_person, plate):
        a2 = hv.smoothstep(0.08, 0.92, np.clip(al_person, 0, 1))
        a = a2[:, :, None]
        pm = plate.reshape(-1, 3).mean(0) / max(1.0, float(plate.mean()))
        subj = img.astype(np.float32) * (0.86 + 0.14 * pm[None, None, :])
        edge = cv2.GaussianBlur(np.clip(4.0 * a2 * (1.0 - a2), 0, 1), (0, 0), 3.0 * S)[:, :, None]
        rim = cv2.GaussianBlur(plate, (0, 0), 9 * S).astype(np.float32) * (0.55 * edge)
        out = subj * a + plate.astype(np.float32) * (1 - a) + rim
        return np.clip(out, 0, 255).astype(np.uint8)

    # ---- compose: the 720 edit lifted as a delta + HR background
    compose0 = ba.compose

    def compose(src, ex, st, t_abs, al_hair, idx, img_swap, img_orig):
        if getattr(img_swap, "hr", None) is None:
            return compose0(src, ex, st, t_abs, al_hair, idx, img_swap, img_orig)
        lo_s, lo_o = np.asarray(img_swap), np.asarray(img_orig)
        st_nb = {k: v for k, v in st.items() if k != "bg"}
        im_lo = compose0(src, ex, st_nb, t_abs, al_hair, idx, lo_s, lo_o)
        use_orig = st.get("face") == "orig"
        base_lo = lo_o if use_orig else lo_s
        base_hr = (img_orig if use_orig else img_swap).hr
        delta = im_lo.astype(np.float32) - base_lo.astype(np.float32)
        if float(np.abs(delta).max()) > 0.5:
            up = cv2.resize(delta, (720 * S, 720 * S), interpolation=cv2.INTER_CUBIC)
            out = np.clip(base_hr.astype(np.float32) + up, 0, 255).astype(np.uint8)
        else:
            out = base_hr
        bgk = st.get("bg")
        if bgk and bgk != "real":
            z = np.load(ba.CACHE / f"{idx:05d}.npz")
            person = z["person"][:, 280:1000].astype(np.float32) / 255.0
            person = cv2.resize(person, (720 * S, 720 * S), interpolation=cv2.INTER_CUBIC)
            out = composite_bg_hr(out, person, plate_hr(ex, bgk, t_abs))
        return out
    ba.compose = compose

    # ---- near-lossless master (the delivery encode happens once, in final_pass)
    real_popen = subprocess.Popen

    class _SP:
        def __getattr__(self, n):
            return getattr(subprocess, n)

        @staticmethod
        def Popen(cmd, *a, **k):
            if isinstance(cmd, list) and cmd and cmd[0] == "ffmpeg" and "-crf" in cmd:
                cmd = list(cmd)
                cmd[cmd.index("-crf") + 1] = "4"
            return real_popen(cmd, *a, **k)
    ba.subprocess = _SP()
    return ba, hv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("preset")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default="")
    ap.add_argument("--variant", default="plain", choices=["plain", "v3"])
    ap.add_argument("--crf", type=int, default=15)
    a = ap.parse_args(argv)
    ba, hv = install(a.variant)
    spec = ba.PRESETS[a.preset]()
    src = ba.Source()
    out = Path(a.out)
    work = Path(a.work) if a.work else out.parent / "work_hq"
    work.mkdir(parents=True, exist_ok=True)
    master = work / f"{a.preset}_master_hq.mp4"
    ba.render(spec, src, master, clean=False, metrics=False)
    ba.finish(master, spec, out, crf=a.crf)
    print("done", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
