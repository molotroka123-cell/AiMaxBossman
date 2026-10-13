"""Resolution-free suit evidence from the LARGE centre pip of a face-up card (shape, not exemplars).

Why: the exemplar book of ``corner.CornerBook`` is per frame width and knows only suits it has seen at that width
(1320 px recording: no diamonds). The big pip is a clean silhouette at any size, so its outline separates the four
suits by two scale-free numbers, measured on the owner's recordings (5 hands, 576 and 1320 px, 760 board cards):

* ``w_top``  mean relative width of the top quarter of the silhouette
             spade 0.26-0.28 | club 0.43-0.46 | heart 0.79-0.82 | diamond 0.25-0.26
* ``sol``    area / convex-hull area
             spade 0.91-0.93 | club 0.81-0.83 | heart 0.93-0.95 | diamond 0.90-0.93
* ``aspect`` bbox width / height   heart 1.03-1.06 | diamond 0.82-0.88

Both numbers must agree with the same suit, otherwise the answer is None (UNKNOWN). The colour family (red / black ink
of the rank glyph) is decided first by the caller, so only {h,d} or {s,c} are ever distinguished.
"""
from __future__ import annotations

import cv2
import numpy as np

CARD_ASPECT = 0.70


def centre_pip_mask(bgr: np.ndarray, x: int, y: int, w: int, red: bool) -> np.ndarray | None:
    h = int(w / CARD_ASPECT)
    H, W = bgr.shape[:2]
    x0, x1 = max(0, x + int(0.38 * w)), min(W, x + int(0.98 * w))
    y0, y1 = max(0, y + int(0.50 * h)), min(H, y + int(0.98 * h))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    p = bgr[y0:y1, x0:x1]
    b, g, r = p[:, :, 0].astype(int), p[:, :, 1].astype(int), p[:, :, 2].astype(int)
    ink = g < 165
    if red:
        return ink & ((r - (g + b) // 2) > 30)
    return ink & (np.abs(r - g) < 30) & (g < 140)


def pip_features(mask: np.ndarray) -> dict | None:
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
    if n < 2:
        return None
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    comp = (lab == i).astype(np.uint8)
    cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    area = float(cv2.contourArea(c))
    hull = float(cv2.contourArea(cv2.convexHull(c)))
    x, y, w, h = cv2.boundingRect(c)
    if area < 40 or hull <= 0 or h < 10 or w < 10:
        return None
    prof = comp[y:y + h, x:x + w].sum(1) / float(w)
    return {"sol": area / hull, "w_top": float(prof[:max(1, int(0.25 * h))].mean()), "aspect": w / float(h), "h": h}


def suit_from_centre_pip(bgr: np.ndarray, x: int, y: int, w: int, red: bool) -> tuple[str | None, str]:
    """(suit | None, reason). ``x, y, w``: top-left corner and width of a FULLY visible card."""
    mask = centre_pip_mask(bgr, x, y, w, red)
    if mask is None:
        return None, "centre_pip_outside_frame"
    f = pip_features(mask)
    if f is None:
        return None, "centre_pip_not_found"
    if red:
        # a heart has the notch between its lobes (sol <= 0.95 measured); a solid red blob (badge, chip) is no heart
        if f["w_top"] >= 0.60 and f["aspect"] >= 0.97 and f["sol"] <= 0.97:
            return "h", ""
        if f["w_top"] <= 0.40 and f["aspect"] <= 0.95:
            return "d", ""
    else:
        if f["w_top"] <= 0.34 and f["sol"] >= 0.875:
            return "s", ""
        if f["w_top"] >= 0.37 and f["sol"] <= 0.87:
            return "c", ""
    return None, f"centre_pip_shape_ambiguous(w_top={f['w_top']:.2f},sol={f['sol']:.2f},aspect={f['aspect']:.2f})"
