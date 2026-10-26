"""Head-zoom pass for local_genjutsu: give the face the whole pixel budget of a second Animate run.

Why: at 480x832 the dancer's face is ~50 px, so Wan Animate cannot draw a recognisable face (ArcFace ~0.06 vs the reference).
A tracked 512x512 crop of the ORIGINAL video around the head is regenerated on its own and pasted back over an upscaled base.
Measured 26.10.2026 on one 3 s window (receipt.json "head_zoom"): ArcFace 0.06 -> 0.28-0.31, Higgsfield 0.40 -> still below.

Geometry (9:16 source, e.g. 1080x1920): prep() scales to the guide width and centre-crops the height, finish() upscales the guide
x1.5 and pads 16 px top and bottom, so the final 720x1280 frame is the source scaled by 720/src_w. Boxes are stored in SOURCE pixels.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))


def _source_sized_mask(mk: np.ndarray, w: int, h: int) -> np.ndarray:
    """Undo prep(): the guide/mask was scaled to the guide width and centre-cropped in height."""
    sh = int(round(mk.shape[0] * w / mk.shape[1]))
    scaled = cv2.resize(mk, (w, sh), interpolation=cv2.INTER_LINEAR)
    full = np.zeros((h, w), np.uint8)
    off = (h - sh) // 2
    if off >= 0:
        full[off:off + sh] = scaled
    else:
        full[:] = scaled[-off:-off + h]
    return full


def crop_heads(src: str, mask: str, t0: float, t1: float, out_dir: str, mask_t0: float = 0.0, size: int = 512,
               fps: float = 16.0, scale: float = 2.6) -> dict:
    """Write head_guide.mp4, head_mask.mp4 and boxes.json for the window [t0, t1] of src.
    mask is the person mask video (white = person) whose frame 0 is time mask_t0 of the source (prep() masks start at the window)."""
    from facelib_local import detect  # noqa: PLC0415

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cs, cm = cv2.VideoCapture(src), cv2.VideoCapture(mask)
    sfps, mfps = cs.get(5), cm.get(5)
    H, W = int(cs.get(4)), int(cs.get(3))
    mfr = []
    while True:
        ok, f = cm.read()
        if not ok:
            break
        mfr.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
    n = int(round((t1 - t0) * fps))
    gw = cv2.VideoWriter(str(out / "head_guide_raw.avi"), cv2.VideoWriter_fourcc(*"MJPG"), fps, (size, size))
    mw = cv2.VideoWriter(str(out / "head_mask_raw.avi"), cv2.VideoWriter_fourcc(*"MJPG"), fps, (size, size))
    boxes, prev, prev_top, found = [], None, None, 0
    for i in range(n):
        t = t0 + i / fps
        cs.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * sfps)))
        ok, fr = cs.read()
        if not ok:
            break
        mk_full = _source_sized_mask(mfr[min(len(mfr) - 1, max(0, int(round((t - mask_t0) * mfps))))], W, H)
        ys, xs = np.where(mk_full > 127)
        if len(xs) < 50:
            cx, cy, fh = prev if prev else (W // 2, H // 3, 120)
        else:
            x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
            ph = y1 - y0
            rx, ry = max(0, x0 - 40), max(0, y0 - 20)
            reg = fr[ry:min(H, y0 + int(ph * 0.32)), rx:min(W, x1 + 40)]
            big = cv2.resize(reg, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
            d = detect(big, 0.35)
            if len(d):
                b = max(d, key=lambda r: (r[0][2] - r[0][0]) * (r[0][3] - r[0][1]))[0]
                cx, cy, fh = rx + (b[0] + b[2]) / 6, ry + (b[1] + b[3]) / 6, (b[3] - b[1]) / 3
                found += 1
            else:
                cx = prev[0] if prev else (x0 + x1) / 2
                fh = prev[2] if prev else ph * 0.1
                cy = (prev[1] + (y0 - prev_top)) if (prev and prev_top is not None) else y0 + fh
            prev_top = y0
        if prev is not None:
            fh = float(np.clip(fh, 0.85 * prev[2], 1.18 * prev[2]))   # a false/oversized detection must not zoom the crop out
        prev = (cx, cy, fh) if prev is None else tuple(0.45 * a + 0.55 * b for a, b in zip((cx, cy, fh), prev))
        cx, cy, fh = prev
        s = int(max(size * 0.5, fh * scale))
        s += s % 2
        x = int(np.clip(cx - s / 2, 0, W - s))
        y = int(np.clip(cy - s / 2, 0, H - s))
        s = min(s, W - x, H - y)
        gw.write(cv2.resize(fr[y:y + s, x:x + s], (size, size), interpolation=cv2.INTER_CUBIC))
        mw.write(cv2.cvtColor(cv2.resize(mk_full[y:y + s, x:x + s], (size, size)), cv2.COLOR_GRAY2BGR))
        boxes.append({"i": i, "t": round(t, 3), "x": x, "y": y, "s": s})
    gw.release()
    mw.release()
    for name in ("head_guide", "head_mask"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out / f"{name}_raw.avi"), "-c:v", "libx264", "-crf", "14",
                        "-pix_fmt", "yuv420p", str(out / f"{name}.mp4")], check=True)
    meta = {"src": src, "t0": t0, "t1": t1, "size": size, "fps": fps, "frames": len(boxes), "face_found_frames": found,
            "src_w": W, "src_h": H, "boxes": boxes}
    (out / "boxes.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return {"frames": len(boxes), "face_found": found, "guide": str(out / "head_guide.mp4"), "mask": str(out / "head_mask.mp4"),
            "boxes": str(out / "boxes.json")}


def paste_back(base: str, head: str, boxes_json: str, out_mp4: str, head_mask: str | None = None, feather: float = 0.12) -> int:
    """Paste the generated head video over base (any size, same framing as the source), colour-matched and soft-blended
    through the person mask so the head's own background never leaks. Returns the number of pasted frames."""
    B = json.loads(Path(boxes_json).read_text(encoding="utf-8"))
    cb, ch = cv2.VideoCapture(base), cv2.VideoCapture(head)
    cm = cv2.VideoCapture(head_mask) if head_mask else None
    bw, bh, fps = int(cb.get(3)), int(cb.get(4)), cb.get(5)
    sx, sy = bw / B["src_w"], bh / B["src_h"]
    raw = out_mp4 + ".raw.avi"
    wr = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"MJPG"), fps, (bw, bh))
    n = 0
    for b in B["boxes"]:
        ok, fb = cb.read()
        ok2, fh = ch.read()
        if not (ok and ok2):
            break
        x, y, s = int(b["x"] * sx), int(b["y"] * sy), max(8, int(b["s"] * min(sx, sy)))
        x, y = min(x, bw - s), min(y, bh - s)
        head = cv2.resize(fh, (s, s), interpolation=cv2.INTER_AREA).astype(np.float32)
        pad = max(2, s // 6)
        ring = fb[max(0, y - pad):min(bh, y + s + pad), max(0, x - pad):min(bw, x + s + pad)]
        for c in range(3):
            hm, hs = head[..., c].mean(), head[..., c].std() + 1e-6
            rm, rs = ring[..., c].mean(), ring[..., c].std() + 1e-6
            head[..., c] = (head[..., c] - hm) * min(1.5, max(0.67, rs / hs)) + rm
        head = np.clip(head, 0, 255)
        m = np.zeros((s, s), np.float32)
        r = int(s * (0.5 - feather))
        cv2.ellipse(m, (s // 2, s // 2), (r, r), 0, 0, 360, 1.0, -1)
        k = max(3, int(s * feather) | 1)
        m = cv2.GaussianBlur(m, (k, k), 0)[..., None]
        if cm is not None:
            okm, fm = cm.read()
            if okm:
                pm = cv2.resize(cv2.cvtColor(fm, cv2.COLOR_BGR2GRAY), (s, s), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
                pm = cv2.dilate(pm, np.ones((max(3, s // 24),) * 2, np.uint8))
                kk = max(3, int(s * 0.06) | 1)
                m = m * cv2.GaussianBlur(pm, (kk, kk), 0)[..., None]
        roi = fb[y:y + s, x:x + s].astype(np.float32)
        fb[y:y + s, x:x + s] = np.clip(roi * (1 - m) + head * m, 0, 255).astype(np.uint8)
        wr.write(fb)
        n += 1
    wr.release()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", out_mp4], check=True)
    os.remove(raw)
    return n
