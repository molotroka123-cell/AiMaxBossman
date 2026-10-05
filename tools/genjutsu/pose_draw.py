"""OpenPose/ControlNet-style skeleton drawing for COCO-WholeBody (133 keypoints), numpy + Pillow only.

Drafted by a free cloud model (OpenRouter nvidia/nemotron-3-super-120b-a12b:free, $0) for the
local Genjutsu stack; reviewed, completed and tested by Claude (the draft was cut off mid-function).
No OpenCV: under Smart App Control only already-allowed binaries are used.
"""
from __future__ import annotations

import colorsys

import numpy as np
from PIL import Image, ImageDraw

# OpenPose-18 limb pairs, 0-based (limbSeq [2,3],[2,6],... minus one).
LIMBS = [(1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10), (1, 11), (11, 12),
         (12, 13), (1, 0), (0, 14), (14, 16), (0, 15), (15, 17)]
COLORS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0), (0, 255, 0),
          (0, 255, 85), (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255),
          (170, 0, 255), (255, 0, 255), (255, 0, 170), (255, 0, 85)]
HAND_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (0, 9), (9, 10), (10, 11), (11, 12),
              (0, 13), (13, 14), (14, 15), (15, 16), (0, 17), (17, 18), (18, 19), (19, 20)]
# COCO-17 index for each OpenPose-18 slot; -1 = neck (midpoint of the shoulders).
COCO_TO_OPENPOSE = [0, -1, 6, 8, 10, 5, 7, 9, 12, 14, 16, 11, 13, 15, 2, 1, 4, 3]
THRESHOLD = 0.3


def to_openpose18(kpts: np.ndarray, scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    out_k = np.zeros((18, 2), np.float32)
    out_s = np.zeros((18,), np.float32)
    for i, src in enumerate(COCO_TO_OPENPOSE):
        if src < 0:
            out_k[i] = (kpts[5] + kpts[6]) / 2
            out_s[i] = min(scores[5], scores[6])
        else:
            out_k[i], out_s[i] = kpts[src], scores[src]
    return out_k, out_s


def _limb(draw: ImageDraw.ImageDraw, a, b, width: float, color) -> None:
    """A filled ellipse-like stick, as in the reference OpenPose renderer."""
    (x1, y1), (x2, y2) = a, b
    length = float(np.hypot(x2 - x1, y2 - y1))
    if length < 1:
        return
    angle = np.arctan2(y2 - y1, x2 - x1)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    t = np.linspace(0, 2 * np.pi, 24)
    ex, ey = (length / 2) * np.cos(t), (width / 2) * np.sin(t)
    xs = mx + ex * np.cos(angle) - ey * np.sin(angle)
    ys = my + ex * np.sin(angle) + ey * np.cos(angle)
    draw.polygon(list(zip(xs.tolist(), ys.tolist())), fill=(*color, 153))


def draw_pose(size: tuple[int, int], people: list[tuple[np.ndarray, np.ndarray]]) -> Image.Image:
    """people: [(kpts (133,2) pixel xy, scores (133,))] -> RGB skeletons on black."""
    w, h = size
    k = max(w, h) / 512.0
    canvas = Image.new("RGB", size, (0, 0, 0))
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    for kpts, scores in people:
        kpts, scores = np.asarray(kpts, np.float32), np.asarray(scores, np.float32)
        body, bs = to_openpose18(kpts[:17], scores[:17])
        for (i, j), color in zip(LIMBS, COLORS):
            if bs[i] >= THRESHOLD and bs[j] >= THRESHOLD:
                _limb(draw, body[i], body[j], 8 * k, color)
        r = 4 * k
        for (x, y), s, color in zip(body, bs, COLORS):
            if s >= THRESHOLD:
                draw.ellipse([x - r, y - r, x + r, y + r], fill=(*color, 255))
        for start in (91, 112):  # left hand, right hand
            hk, hs = kpts[start:start + 21], scores[start:start + 21]
            for e, (i, j) in enumerate(HAND_EDGES):
                if hs[i] >= THRESHOLD and hs[j] >= THRESHOLD:
                    rr, gg, bb = colorsys.hsv_to_rgb(e / len(HAND_EDGES), 1.0, 1.0)
                    draw.line([tuple(hk[i]), tuple(hk[j])], fill=(int(rr * 255), int(gg * 255), int(bb * 255), 255),
                              width=max(1, int(2 * k)))
            for (x, y), s in zip(hk, hs):
                if s >= THRESHOLD:
                    draw.ellipse([x - 1.5 * k, y - 1.5 * k, x + 1.5 * k, y + 1.5 * k], fill=(0, 0, 255, 255))
        for (x, y), s in zip(kpts[23:91], scores[23:91]):
            if s >= THRESHOLD:
                draw.ellipse([x - 1.2 * k, y - 1.2 * k, x + 1.2 * k, y + 1.2 * k], fill=(255, 255, 255, 255))
    canvas.paste(overlay, (0, 0), overlay)
    return canvas
