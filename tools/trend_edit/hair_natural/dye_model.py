"""Natural hair-dye model (owner feedback 11.10: colour still acid, unnatural, mask spills over the hair).

The "acid" look is a target chroma applied uniformly over every pixel of the hair. Real dyed hair on dark hair behaves like this:
  * shadows / roots: darker and less saturated (little light reaches the pigment, the under-colour shows through),
  * chroma RISES with lightness up to a ceiling (a saturated pigment needs light to read),
  * highlights keep the hue but turn specular (whiter, desaturated),
  * part of the underlying hair colour shows through (u0).
The model is a handful of parametric curves in CIE Lab over t = luminance rank of the pixel inside its own frame's hair
(0 = darkest strand, 1 = brightest highlight), so the same preset works on any frame / lighting:

    L(t)  = L0 + (L1 - L0) t^gam                           (+ specular lift above ts)
    C(t)  = C0 + (Cmax - C0) smoothstep(tc - w, tc + w, t)  (x (1 - sd * spec))
    h(t)  = h0 + dh (t - 0.5)                               (degrees, hue drifts with light)
    roots: L *= 1 - rd * r,  C *= 1 - rc * r                (r = 1 at the scalp line, falls off with distance)
    under: (a, b) = (1 - u0 (1 - t)) (a, b)_dye + u0 (1 - t) (a, b)_source

Pure numpy / OpenCV (CPU).  Fit: fit_presets.py.  Colour maths: OpenCV float Lab (L 0..100, a/b about +-127).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np

PARAM_KEYS = ["L0", "L1", "gam", "C0", "Cmax", "tc", "w", "h0", "dh", "ts", "sd", "sl", "rd", "rc", "u0"]


# ------------------------------------------------------------------ colour helpers
def to_lab(bgr_u8: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(bgr_u8, 0, 255).astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab)


def lab_to_bgr(lab: np.ndarray) -> np.ndarray:
    return np.clip(cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_Lab2BGR), 0, 1) * 255.0


def smoothstep(lo, hi, x):
    t = np.clip((x - lo) / max(1e-6, (hi - lo)), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 on (..., 3) float Lab arrays (Sharma et al. 2005), kL = kC = kH = 1."""
    L1, a1, b1 = (lab1[..., i].astype(np.float64) for i in range(3))
    L2, a2, b2 = (lab2[..., i].astype(np.float64) for i in range(3))
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cm = 0.5 * (C1 + C2)
    G = 0.5 * (1 - np.sqrt(Cm ** 7 / (Cm ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, dh)
    dh = np.where(dh < -180, dh + 360, dh)
    dh = np.where(C1p * C2p == 0, 0, dh)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lpm = 0.5 * (L1 + L2)
    Cpm = 0.5 * (C1p + C2p)
    hs = h1p + h2p
    hpm = np.where(np.abs(h1p - h2p) <= 180, hs / 2, np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2))
    hpm = np.where(C1p * C2p == 0, hs, hpm)
    T = (1 - 0.17 * np.cos(np.radians(hpm - 30)) + 0.24 * np.cos(np.radians(2 * hpm)) + 0.32 * np.cos(np.radians(3 * hpm + 6))
         - 0.20 * np.cos(np.radians(4 * hpm - 63)))
    dth = 30 * np.exp(-(((hpm - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(Cpm ** 7 / (Cpm ** 7 + 25.0 ** 7))
    Sl = 1 + 0.015 * (Lpm - 50) ** 2 / np.sqrt(20 + (Lpm - 50) ** 2)
    Sc = 1 + 0.045 * Cpm
    Sh = 1 + 0.015 * Cpm * T
    Rt = -np.sin(np.radians(2 * dth)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


# ------------------------------------------------------------------ the preset
@dataclass
class DyePreset:
    name: str
    L0: float = 20.0
    L1: float = 60.0
    gam: float = 1.0
    C0: float = 10.0
    Cmax: float = 30.0
    tc: float = 0.45
    w: float = 0.35
    h0: float = 0.0
    dh: float = 0.0
    ts: float = 0.9
    sd: float = 0.2
    sl: float = 0.1
    rd: float = 0.0
    rc: float = 0.0
    u0: float = 0.0
    kind: str = "natural"                     # natural | stylised
    label_ru: str = ""
    label_en: str = ""
    source: str = ""
    swatch: str = "#808080"                   # sRGB hex of the mid-tone (UI chip)

    def curves(self, t):
        """t -> (L, C, h_deg) before root / under-colour / specular terms."""
        t = np.clip(t, 0, 1)
        L = self.L0 + (self.L1 - self.L0) * np.power(t, self.gam)
        C = self.C0 + (self.Cmax - self.C0) * smoothstep(self.tc - self.w, self.tc + self.w, t)
        h = self.h0 + self.dh * (t - 0.5)
        return L, C, h

    def lab_of_t(self, t, root=0.0, src_ab=None) -> np.ndarray:
        t = np.asarray(t, np.float32)
        L, C, h = self.curves(t)
        spec = smoothstep(self.ts, 1.0, t)
        C = C * (1.0 - self.sd * spec)
        L = L + self.sl * spec * np.clip(96.0 - L, 0, None)
        L = L * (1.0 - self.rd * root)
        C = C * (1.0 - self.rc * root)
        a = C * np.cos(np.radians(h))
        b = C * np.sin(np.radians(h))
        if src_ab is not None and self.u0 > 0:
            u = self.u0 * (1.0 - t)
            a = (1 - u) * a + u * src_ab[0]
            b = (1 - u) * b + u * src_ab[1]
        return np.stack([L, a, b], axis=-1)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DyePreset":
        k = set(cls.__dataclass_fields__)
        return cls(**{a: b for a, b in d.items() if a in k})


def load_presets(path) -> dict:
    z = json.loads(Path(path).read_text(encoding="utf-8"))
    return {p["name"]: DyePreset.from_dict(p) for p in z["presets"]}


# ------------------------------------------------------------------ per-frame application
class DyeState:
    """Temporal smoothing of the frame's hair luminance quantiles (so the rank map does not flicker)."""

    def __init__(self):
        self.q = None


NQ = 129


def hair_rank(L: np.ndarray, core: np.ndarray, state, temporal: bool = True):
    """Luminance rank t (0..1) of every pixel w.r.t. the hair pixels of this frame."""
    samp = np.sort(L[core])
    qs = np.linspace(0, 1, NQ)
    q = samp[np.minimum((qs * (len(samp) - 1)).astype(int), len(samp) - 1)]
    if temporal and state is not None and state.q is not None:
        q = 0.6 * q + 0.4 * state.q
    if state is not None:
        state.q = q
    q_u = q + np.arange(NQ) * 1e-5
    return np.interp(L, q_u, qs).astype(np.float32), q


def foreground(I: np.ndarray, al: np.ndarray):
    """Foreground colour estimate F (old background removed) and background estimate Bg.  I = a F + (1 - a) Bg."""
    a3 = al[:, :, None]
    bgw = cv2.erode((al < 0.04).astype(np.float32), np.ones((3, 3), np.uint8))
    num = cv2.GaussianBlur(I * bgw[:, :, None], (0, 0), 7)
    den = cv2.GaussianBlur(bgw, (0, 0), 7)[:, :, None]
    Bg = np.where(den > 0.02, num / np.maximum(den, 1e-3), I)
    F = np.where(a3 > 0.25, (I - (1 - a3) * Bg) / np.maximum(a3, 0.25), I)
    return np.clip(F, 0, 255), Bg


def apply_preset(frame: np.ndarray, alpha: np.ndarray, p: DyePreset, state=None, root=None,
                 detail_gain: float = 1.0, strength: float = 1.0) -> np.ndarray:
    """out = I + alpha (T(F) - F): premultiplied composite with the foreground colour estimate, background never touched."""
    I = frame.astype(np.float32)
    al = np.clip(alpha.astype(np.float32), 0, 1)
    F, _ = foreground(I, al)
    lab = to_lab(F)
    L = lab[:, :, 0]
    core = al > 0.6
    if core.sum() < 80:
        return frame
    L = np.minimum(L, float(np.percentile(L[core], 97)) + 4.0)    # a bright rim must not read as a highlight
    Ls = cv2.GaussianBlur(L, (0, 0), 1.8)
    t, q = hair_rank(Ls, core, state)
    r = 0.0 if root is None else root
    tgt = p.lab_of_t(t, r, (lab[:, :, 1], lab[:, :, 2]))
    Lr = cv2.GaussianBlur(tgt[:, :, 0], (0, 0), 1.2)
    det = L - cv2.GaussianBlur(L, (0, 0), 0.9)
    rng_src = max(1.0, float(q[-8] - q[8]))
    rng_ref = max(1.0, float(p.lab_of_t(np.array(0.94))[0] - p.lab_of_t(np.array(0.06))[0]))
    scale = min(2.2, rng_ref / rng_src) * 0.6
    newL = np.clip(Lr + 1.6 * detail_gain * scale * det, 0, 100)
    new = np.dstack([newL, tgt[:, :, 1], tgt[:, :, 2]]).astype(np.float32)
    Fn = lab_to_bgr(new)
    a3 = (al * strength)[:, :, None]
    return np.clip(I + a3 * (Fn - F), 0, 255).astype(np.uint8)
