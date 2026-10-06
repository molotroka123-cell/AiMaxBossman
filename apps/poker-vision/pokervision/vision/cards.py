"""Card finding + rank/suit reading from pixels only. Face-down cards are never reported as cards."""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

CARD_ASPECT = 0.70          # w / h of a full card
RANK_SIDE = 14


@dataclass
class CardBox:
    x: int
    y: int
    w: int
    h: int
    fill: float             # contour area / bbox area (rectangle-ness)
    visible_frac: float     # observed height / expected full height (<1 => occluded from below)
    sharp: float            # Laplacian variance of the crop

    @property
    def cx(self) -> float: return self.x + self.w / 2
    @property
    def cy(self) -> float: return self.y + self.h / 2


def find_card_boxes(bgr: np.ndarray, roi: tuple[int, int, int, int], min_w: int, max_w: int) -> list[CardBox]:
    """White rounded rectangles inside ``roi`` (x0,y0,x1,y1 pixels). Occlusion from below shortens the box."""
    x0, y0, x1, y1 = roi
    sub = bgr[y0:y1, x0:x1]
    if sub.size == 0:
        return []
    mx, mn = sub.max(2), sub.min(2)
    white = ((mn > 196) & ((mx.astype(int) - mn) < 38)).astype(np.uint8) * 255
    k = max(3, int(min_w * 0.12) | 1)
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    cnts, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    out: list[CardBox] = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if not (min_w <= w <= max_w) or h < 0.28 * w / CARD_ASPECT:
            continue
        area = cv2.contourArea(c)
        fill = float(area / max(w * h, 1))
        if fill < 0.78:
            continue
        exp_h = w / CARD_ASPECT
        vis = float(min(h / exp_h, 1.2))
        if vis > 1.12 or vis < 0.28:
            continue
        crop = gray[y:y + h, x:x + w]
        sharp = float(cv2.Laplacian(crop, cv2.CV_64F).var()) if crop.size else 0.0
        out.append(CardBox(x + x0, y + y0, w, h, fill, vis, sharp))
    out.sort(key=lambda b: b.x)
    return out


def find_back_boxes(bgr: np.ndarray, roi: tuple[int, int, int, int], min_w: int, max_w: int) -> list[tuple[int, int, int, int]]:
    """Face-down cards (blue back). A card that is being dealt/flipped shows its back: it is a card, just unreadable."""
    x0, y0, x1, y1 = roi
    sub = bgr[y0:y1, x0:x1].astype(int)
    if sub.size == 0:
        return []
    b, g, r = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    m = ((b > r + 30) & (b > g + 8) & (b > 55)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if min_w <= w <= max_w and 0.45 * w / CARD_ASPECT <= h <= 1.3 * w / CARD_ASPECT and cv2.contourArea(c) / (w * h) > 0.7:
            out.append((x + x0, y + y0, w, h))
    return out


def _ink(patch_bgr: np.ndarray) -> np.ndarray:
    return patch_bgr[:, :, 1] < 165          # green channel low: black ink and red ink alike


def _dark(patch_bgr: np.ndarray) -> np.ndarray:
    return np.clip(1.0 - patch_bgr[:, :, 1].astype(np.float32) / 235.0, 0, 1)


def _is_red(patch_bgr: np.ndarray, ink: np.ndarray) -> tuple[bool, float]:
    if ink.sum() < 6:
        return False, 0.0
    px = patch_bgr[ink].astype(int)
    r, g, b = px[:, 2].mean(), px[:, 1].mean(), px[:, 0].mean()
    diff = r - (g + b) / 2
    return bool(diff > 35), float(min(abs(diff) / 90, 1.0))


def _norm_mask(mask: np.ndarray, size=RANK_SIDE, dark: np.ndarray | None = None) -> tuple[np.ndarray, float] | None:
    """Crop to the ink bbox, pad to square, resize. With ``dark`` (0..1 darkness) sub-pixel shape information is kept."""
    ys, xs = np.where(mask)
    if len(ys) < 5:
        return None
    src = dark if dark is not None else mask.astype(np.float32)
    crop = src[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
    h, w = crop.shape
    s = max(h, w)
    pad = np.zeros((s, s), np.float32)
    pad[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = crop
    out = cv2.resize(pad, (size, size), interpolation=cv2.INTER_AREA)
    out = cv2.GaussianBlur(out, (3, 3), 0.8)          # tolerance to sub-pixel phase of the anti-aliased glyph
    return out, w / h


def split_corner(box: CardBox, bgr: np.ndarray):
    """Return (rank_mask, pip_mask, red) from the top-left index, split at the largest vertical ink gap."""
    x, y, w, h = box.x, box.y, box.w, box.h
    full_h = w / CARD_ASPECT
    cx0, cx1 = x + int(0.05 * w), x + int(0.50 * w)
    cy0, cy1 = y + int(0.03 * full_h), y + min(h, int(0.45 * full_h))
    patch = bgr[cy0:cy1, cx0:cx1]
    if patch.size == 0:
        return None
    ink = _ink(patch)
    rows = ink.sum(1) > 0
    if rows.sum() < 4:
        return None
    # row runs of ink
    runs, start = [], None
    for i, r in enumerate(rows):
        if r and start is None: start = i
        if not r and start is not None: runs.append((start, i)); start = None
    if start is not None: runs.append((start, len(rows)))
    min_run = max(3, int(0.06 * full_h))             # drops the thin card-border ink at the very top
    runs = [r for r in runs if r[1] - r[0] >= min_run]
    if len(runs) < 1:
        return None
    rank_run = runs[0]
    pip_run = runs[1] if len(runs) > 1 else None
    dk = _dark(patch)
    rank_mask = ink[rank_run[0]:rank_run[1]]
    pip_mask = ink[pip_run[0]:pip_run[1]] if pip_run else None
    red, _ = _is_red(patch, ink)
    return rank_mask, pip_mask, red, rank_run, pip_run, dk[rank_run[0]:rank_run[1]], (dk[pip_run[0]:pip_run[1]] if pip_run else None)


def center_pip(box: CardBox, bgr: np.ndarray):
    x, y, w, h = box.x, box.y, box.w, box.h
    full_h = w / CARD_ASPECT
    cy0, cy1 = y + int(0.375 * full_h), y + min(h, int(0.72 * full_h))
    if cy1 - cy0 < 0.22 * full_h or box.visible_frac < 0.70:
        return None
    patch = bgr[cy0:cy1, x + int(0.24 * w):x + int(0.76 * w)]
    ink = _ink(patch)
    red, rconf = _is_red(patch, ink)
    return ink, red, _dark(patch)


@dataclass
class CardBook:
    rank: dict[str, list[np.ndarray]] = field(default_factory=lambda: {r: [] for r in "23456789TJQKA"})
    suit: dict[str, list[np.ndarray]] = field(default_factory=lambda: {s: [] for s in "shdc"})
    max_per_class: int = 60

    def add(self, card: str, box: CardBox, bgr: np.ndarray) -> bool:
        sc = split_corner(box, bgr)
        if sc is None or sc[1] is None:
            return False
        rank_mask, pip_mask, red, _, _, rdk, pdk = sc
        if red != (card[1] in "hd"):
            return False
        rn = _norm_mask(rank_mask, dark=rdk)
        pn = _norm_mask(pip_mask, dark=pdk)
        if rn is None or pn is None:
            return False
        self._add(self.rank[card[0]], np.append(rn[0].ravel(), rn[1] * 2).astype(np.float32), self.max_per_class)
        self._add(self.suit[card[1]], pn[0].ravel().astype(np.float32), self.max_per_class)
        cp = center_pip(box, bgr)
        if cp is not None:
            cn = _norm_mask(cp[0], dark=cp[2])
            if cn is not None:
                self._add(self.suit[card[1]], cn[0].ravel().astype(np.float32), 2 * self.max_per_class)
        return True

    @staticmethod
    def _add(lst: list, vec: np.ndarray, cap: int, min_new_dist: float = 0.12) -> None:
        """Keep exemplars diverse: a rendering identical to a stored one adds nothing, so the cap is spent on new
        sub-pixel phases / sizes instead of on duplicates."""
        if len(lst) >= cap:
            return
        if lst and float(np.sqrt(((np.stack(lst) - vec) ** 2).sum(1)).min()) < min_new_dist:
            return
        lst.append(vec)

    @staticmethod
    def _knn(lib: dict, vec: np.ndarray, allowed=None):
        best = {}
        for k, lst in lib.items():
            if allowed is not None and k not in allowed or not lst:
                continue
            best[k] = float(np.sqrt(((np.stack(lst) - vec) ** 2).sum(1)).min())
        r = sorted(best.items(), key=lambda kv: kv[1])
        if not r:
            return None, 0.0, 9e9
        d1 = r[0][1]; d2 = r[1][1] if len(r) > 1 else d1 + 1
        return r[0][0], float(np.clip((d2 - d1) / max(d2, 1e-6), 0, 1)), d1

    def read(self, box: CardBox, bgr: np.ndarray, min_conf=0.10, rank_max_d=5.5, suit_max_d=5.0, lax: bool = False) -> tuple[str | None, float, str]:
        """Return (card|None, confidence, reason). Never guesses: weak match -> None with a reason."""
        if box.visible_frac < 0.28 and not lax:
            return None, 0.0, "too_occluded"
        if box.fill < 0.85 and box.visible_frac >= 0.95 and not lax:
            return None, 0.0, "not_rectangular"
        sc = split_corner(box, bgr)
        if sc is None:
            return None, 0.0, "no_index"
        rank_mask, pip_mask, red, _, _, rdk, pdk = sc
        rn = _norm_mask(rank_mask, dark=rdk)
        if rn is None:
            return None, 0.0, "rank_unreadable"
        rv = np.append(rn[0].ravel(), rn[1] * 2).astype(np.float32)
        rk, rconf, rd = self._knn(self.rank, rv)
        if rk is None or rd > rank_max_d or rconf < min_conf:
            return None, rconf, "rank_ambiguous"
        suits = "hd" if red else "sc"
        votes = []
        if pip_mask is not None:
            pn = _norm_mask(pip_mask, dark=pdk)
            if pn is not None:
                votes.append(self._knn(self.suit, pn[0].ravel().astype(np.float32), suits))
        cp = center_pip(box, bgr)
        if cp is not None and cp[1] == red:
            cn = _norm_mask(cp[0], dark=cp[2])
            if cn is not None:
                votes.append(self._knn(self.suit, cn[0].ravel().astype(np.float32), suits))
        votes = [v for v in votes if v[0] is not None]
        if not votes:
            return None, 0.0, "suit_unreadable"
        if len({v[0] for v in votes}) > 1:
            votes.sort(key=lambda v: -v[1])
            if not (votes[0][1] >= 0.5 and votes[1][1] < 0.3) and not lax:
                return None, min(v[1] for v in votes), "suit_votes_disagree"
            votes = votes[:1]                  # one vote is decisive and the other is weak: trust the decisive one
        sk, sconf, sd = max(votes, key=lambda v: v[1])
        if len(votes) == 1 and not lax and (sconf < 0.5 or sd > 2.5):
            # a single, small corner pip (centre pip covered) is easy to confuse at another scale (spade vs club):
            # accept it only on a clear margin and a close match
            return None, sconf, "suit_single_vote_weak"
        if sd > suit_max_d or sconf < min_conf:
            return None, sconf, "suit_ambiguous"
        conf = float(min(rconf, sconf) * 0.5 + 0.5 * min(1.0, box.visible_frac))
        return rk + sk, conf, ""

    def to_json(self) -> dict:
        return {"rank": {k: [v.round(4).tolist() for v in l] for k, l in self.rank.items()},
                "suit": {k: [v.round(4).tolist() for v in l] for k, l in self.suit.items()}}

    @staticmethod
    def from_json(d: dict) -> "CardBook":
        cb = CardBook()
        cb.rank = {k: [np.array(v, np.float32) for v in l] for k, l in d["rank"].items()}
        cb.suit = {k: [np.array(v, np.float32) for v in l] for k, l in d["suit"].items()}
        return cb
