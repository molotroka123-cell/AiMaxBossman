"""Index-corner card reading for faces with a LARGE top-left index (rank glyph above a small suit pip).

Why a second reader next to ``vision.cards``: on the owner's TON-style recordings the hero's two cards are
FANNED (the right card overlaps the left one), so the white-rounded-rectangle finder sees one blob and finds no card
(measured 12.10.2026: hero cards 0 of 364 frames). Both indices of a fan stay fully visible, so the cards are read
from their corners and the card box is never needed.

Rules that keep this honest (same philosophy as ``cards.CardBook``):
* a glyph that is not close to a stored exemplar of its class is UNKNOWN, never the nearest wrong class
  (open-set rejection: ``rank_tau`` / ``suit_tau`` are measured from the exemplars themselves);
* the colour decides red (hearts/diamonds) versus black (spades/clubs) before the shape is looked at;
* a rank or a suit that was never seen on a labelled frame stays UNKNOWN — it is never synthesised from a font.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

CARD_ASPECT = 0.70
SIDE = 16
RANKS = "23456789TJQKA"
SUITS = "shdc"


@dataclass
class Corner:
    rank: np.ndarray | None
    pip: np.ndarray | None
    red: bool
    red_conf: float
    rank_h: int = 0
    why: str = ""
    src: dict | None = None             # {"rank": (mask, dark), "pip": (mask, dark)}: lets the book add tilted variants

    @property
    def usable(self) -> bool:
        return self.rank is not None and self.pip is not None


TARGET_SY = 3.2          # vertical std-dev (px) of a glyph in the canonical window (SIDE x SIDE)


def _norm(mask: np.ndarray, dark: np.ndarray, angle: float = 0.0) -> tuple[np.ndarray, float] | None:
    """Canonical, sub-pixel-phase-free glyph: darkness-weighted centroid -> window centre, vertical second moment ->
    scale. The integer-pixel bounding box of a thresholded mask moved with the glyph's sub-pixel phase (measured:
    same rank at two board slots differed by 1.25 while two different ranks differed by 1.39)."""
    ys, xs = np.where(mask)
    if len(ys) < 6:
        return None
    y0, y1 = max(0, ys.min() - 1), min(dark.shape[0], ys.max() + 2)
    x0, x1 = max(0, xs.min() - 2), min(dark.shape[1], xs.max() + 3)
    win = dark[y0:y1, x0:x1].astype(np.float32)
    win = np.where(win > 0.12, win, 0.0)                 # background haze is not ink
    if angle:
        # fanned cards are tilted a few degrees: an upright exemplar must also match a tilted glyph
        pad = max(win.shape) // 2 + 2
        big = np.pad(win, pad)
        R = cv2.getRotationMatrix2D((big.shape[1] / 2, big.shape[0] / 2), angle, 1.0)
        win = cv2.warpAffine(big, R, (big.shape[1], big.shape[0]), flags=cv2.INTER_LINEAR)
    m = float(win.sum())
    if m < 1e-3:
        return None
    gy, gx = np.mgrid[0:win.shape[0], 0:win.shape[1]].astype(np.float32)
    cy, cx = float((win * gy).sum() / m), float((win * gx).sum() / m)
    sy = float(np.sqrt((win * (gy - cy) ** 2).sum() / m))
    sx = float(np.sqrt((win * (gx - cx) ** 2).sum() / m))
    if sy < 1.0:
        return None
    scale = TARGET_SY / sy
    if scale < 1.0:
        win = cv2.GaussianBlur(win, (0, 0), 0.45 / scale)
    M = np.array([[scale, 0, SIDE / 2 - scale * (cx + 0.5)], [0, scale, SIDE / 2 - scale * (cy + 0.5)]], np.float32)
    out = cv2.warpAffine(win, M, (SIDE, SIDE), flags=cv2.INTER_LINEAR, borderValue=0)
    out = out / max(float(out.sum()), 1e-6) * 40.0       # same ink mass: robust to stroke weight / resolution
    return out, float(np.clip(sx / sy, 0.2, 3.0))


def _runs(rows: np.ndarray, min_run: int) -> list[tuple[int, int]]:
    runs, start = [], None
    for i, r in enumerate(rows):
        if r and start is None:
            start = i
        if not r and start is not None:
            runs.append((start, i)); start = None
    if start is not None:
        runs.append((start, len(rows)))
    return [r for r in runs if r[1] - r[0] >= min_run]


def extract(bgr: np.ndarray, x: int, y: int, w: int, vis_h: int | None = None) -> Corner:
    """Rank + pip features of the card whose top-left corner is (x, y) and whose full width is ``w`` pixels.

    The patch stops at 0.575 of the card height: below it a hero's level badge / flag overlaps the card of a fan, and the
    pip itself ends at ~0.56. Ink is restricted to the colour class of the rank glyph (red ink or black ink), so the green
    felt in the rounded corner and a black badge never become part of a red pip."""
    full_h = w / CARD_ASPECT
    H, W = bgr.shape[:2]
    y0 = max(0, y + int(0.015 * full_h))
    lim = int(0.575 * full_h)
    y1 = min(H, y + (min(vis_h, lim) if vis_h else lim))
    x0 = max(0, x + int(0.02 * w))
    x1 = min(W, x + int(0.46 * w))
    if y1 - y0 < 0.25 * full_h or x1 - x0 < 6:
        return Corner(None, None, False, 0.0, 0, "corner_outside_frame")
    patch = bgr[y0:y1, x0:x1]
    b, g, r = patch[:, :, 0].astype(int), patch[:, :, 1].astype(int), patch[:, :, 2].astype(int)
    ink_any = g < 165
    dk = np.clip(1.0 - g.astype(np.float32) / 235.0, 0, 1)
    min_run = max(3, int(0.07 * full_h))
    runs = _runs(ink_any.sum(1) > 0, min_run)
    if not runs:
        return Corner(None, None, False, 0.0, 0, "no_ink")
    ra = runs[0]
    # colour class of the rank glyph decides which ink belongs to the card index
    rank_px = ink_any[ra[0]:ra[1]]
    diff = (r - (g + b) // 2)[ra[0]:ra[1]][rank_px]
    red = bool(len(diff) and diff.mean() > 35)
    rconf = float(min(abs(diff.mean()) / 90, 1.0)) if len(diff) else 0.0
    if red:
        ink = ink_any & ((r - (g + b) // 2) > 30)
    else:
        ink = ink_any & (np.abs(r - g) < 30) & (g < 140)
    runs = _runs(ink.sum(1) > 0, min_run)
    if not runs:
        return Corner(None, None, red, rconf, 0, "no_ink")
    ra = runs[0]
    rank_h = ra[1] - ra[0]
    rank = _norm(ink[ra[0]:ra[1]], dk[ra[0]:ra[1]])
    if rank is None:
        return Corner(None, None, red, rconf, rank_h, "rank_unreadable")
    pip = None
    if len(runs) > 1:
        pb = runs[1]
        pe = min(pb[1], pb[0] + int(1.0 * rank_h))          # the big centre pip may touch the corner pip
        pip = _norm(ink[pb[0]:pe], dk[pb[0]:pe])
    rv = np.append(rank[0].ravel(), rank[1] * 2).astype(np.float32)
    src = {"rank": (ink[ra[0]:ra[1]], dk[ra[0]:ra[1]])}
    if pip is None:
        return Corner(rv, None, red, rconf, rank_h, "pip_unreadable", src)
    src["pip"] = (ink[pb[0]:pe], dk[pb[0]:pe])
    return Corner(rv, pip[0].ravel().astype(np.float32), red, rconf, rank_h, "", src)


def _d(lst: list[np.ndarray], v: np.ndarray) -> float:
    return float(np.sqrt(((np.stack(lst) - v) ** 2).sum(1)).min())


@dataclass
class CornerBook:
    rank: dict[str, list[np.ndarray]] = field(default_factory=lambda: {r: [] for r in RANKS})
    suit: dict[str, list[np.ndarray]] = field(default_factory=lambda: {s: [] for s in SUITS})
    # open-set radii; refreshed by ``fit_radii`` after adding exemplars
    rank_tau: float = 0.0
    suit_tau: float = 0.0
    min_ratio: float = 1.8              # nearest wrong class must be at least this much farther than the match
    cap: int = 120

    # ---- building
    TILTS = (-6.0, -3.0, 3.0, 6.0)

    def add(self, card: str, c: Corner, tilts: tuple[float, ...] | None = None) -> bool:
        if not c.usable or (c.red != (card[1] in "hd")):
            return False
        self._put(self.rank[card[0]], c.rank)
        self._put(self.suit[card[1]], c.pip)
        if c.src:
            for a in (self.TILTS if tilts is None else tilts):
                rk = _norm(*c.src["rank"], angle=a)
                pp = _norm(*c.src["pip"], angle=a)
                if rk is not None:
                    self._put(self.rank[card[0]], np.append(rk[0].ravel(), rk[1] * 2).astype(np.float32))
                if pp is not None:
                    self._put(self.suit[card[1]], pp[0].ravel().astype(np.float32))
        return True

    def _put(self, lst, vec, min_new=0.10):
        if len(lst) >= self.cap:
            return
        if lst and _d(lst, vec) < min_new:
            return
        lst.append(vec)

    def fit_radii(self, slack: float = 1.6, floor: float = 0.6, cap: float = 1.5) -> None:
        """Radius = how far a genuine glyph of a KNOWN class lands from the nearest stored exemplar of its class,
        measured leave-one-out on the exemplars themselves, times ``slack`` (never below ``floor``)."""
        def loo(lib):
            worst = 0.0
            for lst in lib.values():
                for i, v in enumerate(lst):
                    others = [o for j, o in enumerate(lst) if j != i]
                    if others:
                        worst = max(worst, _d(others, v))
            return worst
        self.rank_tau = min(cap, max(floor, loo(self.rank) * slack))
        self.suit_tau = min(cap, max(floor, loo(self.suit) * slack))

    def missing(self) -> str:
        return "".join(r for r in RANKS if not self.rank[r]) + "".join(s for s in SUITS if not self.suit[s])

    # ---- reading
    @staticmethod
    def _best(lib, vec, allowed=None):
        res = []
        for k, lst in lib.items():
            if lst and (allowed is None or k in allowed):
                res.append((_d(lst, vec), k))
        res.sort()
        return res

    def read_parts(self, c: Corner) -> tuple[str | None, str | None, float, str]:
        """(rank|None, suit|None, confidence, reason). A rank can be known while the suit is not (a banner or a badge
        covers the pip): the parts are reported separately, ``read`` only returns a card when both are known."""
        if c.rank is None:
            return None, None, 0.0, c.why or "corner_unreadable"
        rk = self._best(self.rank, c.rank)
        if not rk:
            return None, None, 0.0, "rank_book_empty"
        d1 = rk[0][0]
        d2 = rk[1][0] if len(rk) > 1 else d1 * 10 + 1
        rank, why = rk[0][1], ""
        if d1 > self.rank_tau:
            rank, why = None, f"rank_not_in_book(d={d1:.2f}>{self.rank_tau:.2f})"
        elif d2 < self.min_ratio * max(d1, 0.15):
            rank, why = None, f"rank_ambiguous({rk[0][1]}/{rk[1][1]} d={d1:.2f}/{d2:.2f})"
        suit, swhy, s1 = None, c.why or "pip_unreadable", 0.0
        if c.pip is not None:
            sk = self._best(self.suit, c.pip, "hd" if c.red else "sc")
            if not sk:
                swhy = "suit_book_empty"
            else:
                s1 = sk[0][0]
                s2 = sk[1][0] if len(sk) > 1 else s1 * 10 + 1
                if s1 > self.suit_tau:
                    swhy = f"suit_not_in_book(d={s1:.2f}>{self.suit_tau:.2f})"
                elif s2 < self.min_ratio * max(s1, 0.15):
                    swhy = f"suit_ambiguous({sk[0][1]}/{sk[1][1]} d={s1:.2f}/{s2:.2f})"
                else:
                    suit, swhy = sk[0][1], ""
        conf = 0.0
        if rank and suit:
            conf = float(min(1.0, 0.5 * (1 - d1 / self.rank_tau) + 0.5 * (1 - s1 / self.suit_tau)) * 0.5
                         + 0.5 * min(c.red_conf + 0.2, 1.0))
        return rank, suit, conf, (why or swhy)

    def read(self, c: Corner) -> tuple[str | None, float, str]:
        if not c.usable:
            return None, 0.0, c.why or "corner_unreadable"
        rank, suit, conf, why = self.read_parts(c)
        if rank is None or suit is None:
            return None, 0.0, why
        return rank + suit, conf, ""

    # ---- (de)serialisation
    def to_json(self) -> dict:
        return {"rank": {k: [v.round(4).tolist() for v in l] for k, l in self.rank.items()},
                "suit": {k: [v.round(4).tolist() for v in l] for k, l in self.suit.items()},
                "rank_tau": self.rank_tau, "suit_tau": self.suit_tau, "min_ratio": self.min_ratio}

    @staticmethod
    def from_json(d: dict) -> "CornerBook":
        cb = CornerBook()
        cb.rank = {k: [np.array(v, np.float32) for v in l] for k, l in d["rank"].items()}
        cb.suit = {k: [np.array(v, np.float32) for v in l] for k, l in d["suit"].items()}
        cb.rank_tau = float(d.get("rank_tau", 0.0))
        cb.suit_tau = float(d.get("suit_tau", 0.0))
        cb.min_ratio = float(d.get("min_ratio", 1.8))
        return cb


# --------------------------------------------------------------------------------------------- digits (amounts)
def digit_vec(win_bgr: np.ndarray) -> np.ndarray | None:
    """Canonical feature of ONE bright glyph on a darker ground (a pot / stack / button digit). Same sub-pixel-phase-free
    normalisation as the card corners, so a digit does not change with where it sits in the frame."""
    if win_bgr.size == 0 or min(win_bgr.shape[:2]) < 3:
        return None
    v = win_bgr.max(2).astype(np.float32)
    lo, hi = float(np.percentile(v, 5)), float(np.percentile(v, 99))
    if hi - lo < 40:
        return None
    w = np.clip((v - lo) / (hi - lo), 0, 1)
    f = _norm(w > 0.45, w)
    if f is None:
        return None
    return np.append(f[0].ravel(), f[1] * 2).astype(np.float32)


@dataclass
class DigitBook:
    ex: dict[str, list[np.ndarray]] = field(default_factory=dict)
    tau: float = 0.0
    min_ratio: float = 1.6
    cap: int = 40

    def add(self, ch: str, vec: np.ndarray, min_new: float = 0.06) -> None:
        lst = self.ex.setdefault(ch, [])
        if len(lst) >= self.cap or (lst and _d(lst, vec) < min_new):
            return
        lst.append(vec)

    def fit(self, slack: float = 1.6, floor: float = 0.7, cap: float = 1.5) -> None:
        worst = 0.0
        for lst in self.ex.values():
            for i, v in enumerate(lst):
                others = [o for j, o in enumerate(lst) if j != i]
                if others:
                    worst = max(worst, _d(others, v))
        self.tau = min(cap, max(floor, worst * slack))

    def missing(self) -> str:
        return "".join(c for c in "0123456789" if not self.ex.get(c))

    def classify(self, vec: np.ndarray) -> tuple[str | None, float, str]:
        best = sorted((_d(l, vec), k) for k, l in self.ex.items() if l)
        if not best:
            return None, 0.0, "digit_book_empty"
        d1 = best[0][0]
        d2 = best[1][0] if len(best) > 1 else d1 * 10 + 1
        if d1 > self.tau:
            return None, 0.0, f"digit_not_in_book(d={d1:.2f}>{self.tau:.2f})"
        if d2 < self.min_ratio * max(d1, 0.15):
            return None, 0.0, f"digit_ambiguous({best[0][1]}/{best[1][1]} d={d1:.2f}/{d2:.2f})"
        return best[0][1], float(min(1.0, 1 - d1 / self.tau)), ""

    def to_json(self) -> dict:
        return {"ex": {k: [v.round(4).tolist() for v in l] for k, l in self.ex.items()}, "tau": self.tau, "min_ratio": self.min_ratio}

    @staticmethod
    def from_json(d: dict) -> "DigitBook":
        return DigitBook({k: [np.array(v, np.float32) for v in l] for k, l in d["ex"].items()}, float(d.get("tau", 0.0)),
                         float(d.get("min_ratio", 1.6)))
