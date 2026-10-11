"""Sharpness / flicker metrics for the old-vs-new ad comparison (CPU only, ffmpeg + OpenCV).

Pre-registered acceptance (written before the HQ renders were run):
  * sharpness: Laplacian variance of 6 fixed sample frames (person band, rows 250-1300 so the unchanged type is excluded)
    of the new file >= 1.5x the old file;
  * flicker: temporal noise in static areas not worse than +10 % vs the old file;
  * identity: ArcFace mean (face_gate.py) not lower than the old file by more than 0.02, every shown face frame >= 0.59 reported;
  * visual: no ringing / halos on 4 zoom crops per video.

  python metrics.py --old a.mp4 --new b.mp4 [--label x] > json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

import cv2
import numpy as np

W, H = 1080, 1920
BAND = (250, 1300)


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,nb_frames", "-show_entries", "format=duration,size", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    s = d["streams"][0]
    num, _, den = s["r_frame_rate"].partition("/")
    return {"w": s["width"], "h": s["height"], "fps": float(num) / float(den or 1),
            "duration": float(d["format"]["duration"]), "size": int(d["format"]["size"])}


def frames(path, w, h, scale_to=None):
    vf = f"scale={scale_to[0]}:{scale_to[1]}:flags=area" if scale_to else "null"
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         stdout=subprocess.PIPE)
    w2, h2 = scale_to or (w, h)
    n = w2 * h2
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        yield np.frombuffer(b, np.uint8).reshape(h2, w2)
    p.stdout.close()
    p.kill()


def lap_var(g):
    return float(cv2.Laplacian(g.astype(np.float32), cv2.CV_32F).var())


def sharpness(path, info, times=None):
    fr = info["duration"] * np.array([0.1, 0.25, 0.4, 0.55, 0.7, 0.85]) if times is None else np.array(times)
    out = []
    for t in fr:
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", "format=gray",
                            "-f", "rawvideo", "-"], capture_output=True)
        g = np.frombuffer(p.stdout, np.uint8)
        if g.size != info["w"] * info["h"]:
            continue
        g = g.reshape(info["h"], info["w"])
        out.append({"t": round(float(t), 2), "full": lap_var(g), "band": lap_var(g[BAND[0]:BAND[1]])})
    return out


def temporal_noise(path, step=1, size=(540, 960)):
    """Self-referential temporal noise: mean |I_t - (I_{t-1}+I_{t+1})/2| over pixels that are static and smooth
    (low-pass |I_{t+1}-I_{t-1}| < 4). Cut / flash frames are excluded (global mean change > 12). Returns mean, n."""
    vals = []
    buf = []
    for g in frames(path, W, H, size):
        buf.append(g.astype(np.float32))
        if len(buf) < 3:
            continue
        a, b, c = buf[-3:]
        if abs(float(a.mean()) - float(c.mean())) > 6 or abs(float(b.mean()) - float(a.mean())) > 12:
            continue
        d = np.abs(cv2.GaussianBlur(c - a, (0, 0), 2.0))
        m = d < 4.0
        if m.mean() < 0.05:
            continue
        r = np.abs(b - 0.5 * (a + c))
        vals.append(float(r[m].mean()))
        buf = buf[-3:]
    return {"mean": float(np.mean(vals)) if vals else None, "frames": len(vals)}


def paired_static_diff(old, new, size=(540, 960)):
    """Same content, two renders. Symmetric static mask = pixels whose LOW-PASS (sigma 3) temporal change is < 1.0 in BOTH files
    (so neither file's own noise picks the pixels); reports mean |I_{t+1}-I_t| of each file over that mask."""
    vo, vn = [], []
    po = pn = plo = pln = None
    for go, gn in zip(frames(old, W, H, size), frames(new, W, H, size)):
        go, gn = go.astype(np.float32), gn.astype(np.float32)
        lo_, ln_ = cv2.GaussianBlur(go, (0, 0), 3.0), cv2.GaussianBlur(gn, (0, 0), 3.0)
        if po is not None:
            m = (np.abs(lo_ - plo) < 1.0) & (np.abs(ln_ - pln) < 1.0)
            if m.mean() > 0.05 and abs(float(go.mean()) - float(po.mean())) < 6:
                vo.append(float(np.abs(go - po)[m].mean()))
                vn.append(float(np.abs(gn - pn)[m].mean()))
        po, pn, plo, pln = go, gn, lo_, ln_
    return {"old": float(np.mean(vo)) if vo else None, "new": float(np.mean(vn)) if vn else None, "frames": len(vo),
            "ratio": float(np.mean(vn) / max(1e-6, np.mean(vo))) if vo else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--same-content", action="store_true")
    a = ap.parse_args()
    io, in_ = probe(a.old), probe(a.new)
    res = {"old": a.old, "new": a.new, "info_old": io, "info_new": in_}
    so, sn = sharpness(a.old, io, None), sharpness(a.new, in_, [s["t"] for s in sharpness(a.old, io)] if False else None)
    # same sample times for both (fractions of the NEW duration applied to the OLD file when durations differ slightly)
    fr = [0.1, 0.25, 0.4, 0.55, 0.7, 0.85]
    so = sharpness(a.old, io, [f * io["duration"] for f in fr])
    sn = sharpness(a.new, in_, [f * io["duration"] for f in fr])
    res["sharp_old"], res["sharp_new"] = so, sn
    res["sharp_ratio_band"] = float(np.mean([n["band"] for n in sn]) / max(1e-6, np.mean([o["band"] for o in so])))
    res["sharp_ratio_full"] = float(np.mean([n["full"] for n in sn]) / max(1e-6, np.mean([o["full"] for o in so])))
    res["noise_old"], res["noise_new"] = temporal_noise(a.old), temporal_noise(a.new)
    if res["noise_old"]["mean"] and res["noise_new"]["mean"]:
        res["noise_ratio"] = res["noise_new"]["mean"] / res["noise_old"]["mean"]
    if a.same_content:
        res["paired_static"] = paired_static_diff(a.old, a.new)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
