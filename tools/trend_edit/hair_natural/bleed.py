"""Mask-bleed control for the hair recolour (owner 11.10: 'the mask sticks out a little beyond the hair').

refine_alpha(frame, alpha, skin, person) does, in order:
  (a) edge-aware erosion of the outer contour (1-2 px) ONLY where the background there is brighter than the hair (and, when the
      target chroma is given, lower-chroma than the dye): a bright / pale rim carrying the new colour is what reads as spill; where
      the background is as dark as the hair the contour is left alone (eroding would cut real strands),
      plus a sharper alpha ramp in that zone (soft tails are the spill),
  (c) clamp: skin (face parsing) is excluded unless the pixel is hair-dark, nothing outside the dilated person matte is recoloured,
      pixels much brighter than the frame's own hair are dropped (bright_gate: shirt, wall, skin).
(b) premultiplied composite + foreground-colour decontamination lives in dye_model.apply_preset / foreground (I = a F + (1-a) Bg).

Measurement: leak_metrics (changed pixels outside the GT dilated 2 px, as a share of the GT hair area).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from dye_model import to_lab, smoothstep, foreground


@dataclass
class BleedParams:
    erode_px: int = 2                   # maximum outer-contour erosion
    brighter_lo: float = 3.0            # background L above hair L: weight 0 at lo .. 1 at hi
    brighter_hi: float = 12.0
    target_chroma: float = 0.0          # >0: also trim where the background chroma is below this by 4..12
    sharpen_lo: float = 0.12            # alpha ramp in the outer zone: smoothstep(lo, hi, a)
    sharpen_hi: float = 0.80
    skin_clamp: bool = True
    person_clamp: bool = True
    person_grow: int = 7
    bright_gate: bool = True


def _bright_gate(a, L, core):
    if core.sum() < 150:
        return a
    p97 = float(np.percentile(L[core], 97))
    p50 = float(np.percentile(L[core], 50))
    top = max(p97 + 6.0, p50 + 22.0)
    return a * (1.0 - smoothstep(top, top + 12.0, cv2.GaussianBlur(L, (0, 0), 1.0)))


def refine_alpha(frame: np.ndarray, alpha: np.ndarray, skin: np.ndarray | None = None, person: np.ndarray | None = None,
                 p: BleedParams | None = None) -> np.ndarray:
    p = p or BleedParams()
    a = np.clip(alpha.astype(np.float32), 0, 1)
    I = frame.astype(np.float32)
    lab = to_lab(frame)
    L = lab[:, :, 0]
    core = a > 0.8
    if core.sum() < 150:
        return a
    # local hair lightness / chroma (masked blur of the confident core)
    cw = core.astype(np.float32)
    den = cv2.GaussianBlur(cw, (0, 0), 14) + 1e-3
    Lh = cv2.GaussianBlur(L * cw, (0, 0), 14) / den
    Lh = np.where(den > 0.01, Lh, float(np.median(L[core])))
    _, Bg = foreground(I, a)
    labbg = to_lab(Bg)
    Lbg = labbg[:, :, 0]
    Cbg = np.hypot(labbg[:, :, 1], labbg[:, :, 2])
    w = smoothstep(p.brighter_lo, p.brighter_hi, Lbg - Lh)
    if p.target_chroma > 0:
        w = np.maximum(w, 0.8 * smoothstep(4.0, 12.0, p.target_chroma - Cbg))
    # outer zone: partially covered pixels within ~4 px of clear background (not the face side: skin is handled below)
    outside = (a < 0.04).astype(np.uint8)
    if skin is not None:
        outside = outside & (cv2.GaussianBlur(skin.astype(np.float32), (0, 0), 3.0) < 0.25).astype(np.uint8)
    near = cv2.dilate(outside, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))) > 0
    zone = (near & (a > 0.0)).astype(np.float32)
    zone = cv2.GaussianBlur(zone, (0, 0), 1.0)
    wz = np.clip(w * zone, 0, 1)
    if p.erode_px > 0:
        k = 2 * p.erode_px + 1
        a_er = cv2.erode(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        a = a - wz * (a - a_er)
    if p.sharpen_hi > p.sharpen_lo:
        a_sh = smoothstep(p.sharpen_lo, p.sharpen_hi, a)
        a = a * (1 - wz) + a_sh * wz
    # (c) exclusions
    if p.skin_clamp and skin is not None:
        sk = cv2.GaussianBlur(skin.astype(np.float32), (0, 0), 1.0)
        dark = 1.0 - smoothstep(float(np.median(L[core])) + 6.0, float(np.median(L[core])) + 16.0, L)
        a = a * (1.0 - smoothstep(0.35, 0.7, sk) * (1.0 - 0.85 * dark))
    if p.person_clamp and person is not None:
        k = p.person_grow | 1
        pd = cv2.GaussianBlur(cv2.dilate((person > 0.25).astype(np.uint8), np.ones((k, k), np.uint8)).astype(np.float32), (0, 0), 1.0)
        a = a * pd
    if p.bright_gate:
        a = _bright_gate(a, L, core)
    return np.clip(a, 0, 1)


def root_map(skin: np.ndarray, alpha: np.ndarray, radius_frac: float = 0.02) -> np.ndarray:
    """1 at the skin / hair boundary (scalp line, parting), falling off with distance (px) into the hair."""
    sk = (cv2.GaussianBlur(skin.astype(np.float32), (0, 0), 1.0) > 0.5).astype(np.uint8)
    d = cv2.distanceTransform(1 - sk, cv2.DIST_L2, 3)
    R = max(4.0, radius_frac * alpha.shape[0])
    return (np.exp(-d / R) * (alpha > 0.1)).astype(np.float32)


def leak_metrics(out_bgr: np.ndarray, orig_bgr: np.ndarray, gt: np.ndarray, grow_px: int = 2, thr: float = 4.0) -> dict:
    """changed = dE76 > thr (3x3 opened).  leak = changed pixels outside GT dilated grow_px / GT area, at thr (any change) and at
    2 x thr (a change an eye can see on a dark patch).  Also IoU of changed vs GT."""
    d = np.sqrt(((to_lab(out_bgr) - to_lab(orig_bgr)) ** 2).sum(axis=2))
    g = gt > 0.5
    k = 2 * grow_px + 1
    gd = cv2.dilate(g.astype(np.uint8), np.ones((k, k), np.uint8)) > 0
    res = {}
    for tag, t in (("leak_outside_gt_dil", thr), ("leak_visible_dE8", 2 * thr)):
        ch = cv2.morphologyEx((d > t).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
        res[tag] = round(float((ch & ~gd).sum()) / max(1, int(g.sum())), 4)
        if tag == "leak_outside_gt_dil":
            res["changed_iou_gt"] = round(float((ch & g).sum()) / max(1, int((ch | g).sum())), 3)
            res["changed_px"] = int(ch.sum())
    res["gt_px"] = int(g.sum())
    return res
