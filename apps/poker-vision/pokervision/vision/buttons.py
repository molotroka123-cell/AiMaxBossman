"""Action-button band of a TON-style table: which buttons are on screen, their labels, their amounts.

Deterministic, CPU only. A button label is recognised by nearest-exemplar matching of the WHITE text raster inside a
fixed slot (left / middle / right / "wait" pill); an amount is the YELLOW text of the same slot, read with the digit
glyph book. A slot whose raster is not close to a stored exemplar is UNKNOWN, never the nearest label.

What the band says (and what this reader concludes from it):

* three buttons (``fold|check_fold`` + ``check|call X`` + ``bet|raise_to X``)  -> it is the hero's turn;
* ONE button on the left (a pre-selected "Fold" / "Check / Fold" with a checkbox)  -> NOT the hero's turn;
* the "Wait for Blinds" pill, or the "Show cards" row                          -> not the hero's turn;
* anything else, or any slot that is not recognised                            -> UNKNOWN.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .glyphs import Glyph, GlyphBook, is_sep, merge_vertical_parts, read_glyphs, segment

LABEL_W, LABEL_H = 40, 12
SLOTS = ("left", "mid", "right", "pill")


def _inner(c: np.ndarray, frac: float = 0.05) -> np.ndarray:
    h, w = c.shape[:2]
    dy, dx = int(frac * h), int(frac * w)
    return c[dy:h - dy, dx:w - dx]


def white_text(c: np.ndarray) -> np.ndarray:
    mx, mn = c.max(2).astype(int), c.min(2).astype(int)
    return (mn > 150) & (mx - mn < 45)


def yellow_text(c: np.ndarray) -> np.ndarray:
    b, g, r = c[:, :, 0].astype(int), c[:, :, 1].astype(int), c[:, :, 2].astype(int)
    return (r > 150) & (g > 115) & (r - b > 70) & (g - b > 35)


def _despeckle(mask: np.ndarray, min_area: int = 6) -> np.ndarray:
    """Anti-aliased edges of the (yellow) amount leave 1-2 px bright specks in the white mask: they move the bbox."""
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = st[1:, cv2.CC_STAT_AREA] >= min_area
    return keep[lab]


def label_raster(mask: np.ndarray) -> np.ndarray | None:
    mask = _despeckle(mask)
    ys, xs = np.where(mask)
    if len(ys) < 12:
        return None
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
    h, w = crop.shape
    v = cv2.resize(crop, (LABEL_W, LABEL_H), interpolation=cv2.INTER_AREA)
    v = cv2.GaussianBlur(v, (3, 3), 0.8)
    return np.append(v.ravel(), min(w / max(h, 1), 14.0) * 1.5).astype(np.float32)


def slot_rect(slot: tuple, W: int, H: int) -> tuple[int, int, int, int]:
    x, y, w, h = slot
    return max(0, int(x * W)), max(0, int(y * H)), min(W, int((x + w) * W)), min(H, int((y + h) * H))


def slot_crop(bgr: np.ndarray, slot: tuple, kind: str) -> np.ndarray:
    H, W = bgr.shape[:2]
    x0, y0, x1, y1 = slot_rect(slot, W, H)
    c = _inner(bgr[y0:y1, x0:x1])
    if kind == "right" and c.size:                       # the "33%" / "2 BB" tab sits in the left part, the label is
        c = c[:int(0.55 * c.shape[0]), int(0.38 * c.shape[1]):]     # top-right ("Bet" / "Raise to"), the amount is below
    return c


def slot_vector(bgr: np.ndarray, slot: tuple, kind: str) -> np.ndarray | None:
    c = slot_crop(bgr, slot, kind)
    if c.size == 0:
        return None
    return label_raster(white_text(c))


@dataclass
class LabelBook:
    ex: dict[str, list[np.ndarray]] = field(default_factory=dict)
    tau: float = 0.0
    min_ratio: float = 1.6

    def add(self, cls: str, vec: np.ndarray, min_new: float = 0.08) -> None:
        lst = self.ex.setdefault(cls, [])
        if len(lst) >= 30:
            return
        if lst and float(np.sqrt(((np.stack(lst) - vec) ** 2).sum(1)).min()) < min_new:
            return
        lst.append(vec)

    def fit(self, slack: float = 1.7, floor: float = 0.9) -> None:
        worst = 0.0
        for lst in self.ex.values():
            for i, v in enumerate(lst):
                others = [o for j, o in enumerate(lst) if j != i]
                if others:
                    worst = max(worst, float(np.sqrt(((np.stack(others) - v) ** 2).sum(1)).min()))
        self.tau = max(floor, worst * slack)

    def classify(self, vec: np.ndarray) -> tuple[str | None, float, str]:
        if not self.ex:
            return None, 0.0, "label_book_empty"
        best = sorted((float(np.sqrt(((np.stack(l) - vec) ** 2).sum(1)).min()), k) for k, l in self.ex.items() if l)
        d1 = best[0][0]
        d2 = best[1][0] if len(best) > 1 else d1 * 10 + 1
        if d1 > self.tau:
            return None, 0.0, f"label_not_in_book(d={d1:.2f}>{self.tau:.2f})"
        if d2 < self.min_ratio * max(d1, 0.2):
            return None, 0.0, f"label_ambiguous({best[0][1]}/{best[1][1]})"
        return best[0][1], float(min(1.0, 1 - d1 / self.tau)), ""

    def to_json(self) -> dict:
        return {"ex": {k: [v.round(4).tolist() for v in l] for k, l in self.ex.items()}, "tau": self.tau, "min_ratio": self.min_ratio}

    @staticmethod
    def from_json(d: dict) -> "LabelBook":
        return LabelBook({k: [np.array(v, np.float32) for v in l] for k, l in d["ex"].items()}, float(d.get("tau", 0.0)),
                         float(d.get("min_ratio", 1.6)))


def yellow_glyphs(c: np.ndarray) -> list[Glyph]:
    m = yellow_text(c)
    if m.sum() < 6:
        return []
    gl = merge_vertical_parts(segment(m))
    if not gl:
        return []
    top = max(g.h for g in gl)
    base = float(np.median([g.y1 for g in gl if g.h >= 0.6 * top]))
    # digits, plus the decimal point: small, sitting on the digits' baseline
    return [g for g in gl if g.h >= 0.55 * top or (g.h >= 0.10 * top and g.y1 >= base - 0.12 * top and g.w <= 0.4 * top)]


def read_amount(c: np.ndarray, digits, decimals: int | None) -> tuple[str | None, str]:
    """Yellow amount text of a button -> '31.52' (digits via the canonical DigitBook, the point by shape)."""
    from .corner import digit_vec
    if digits is None:
        return None, "no_digit_book"
    gl = yellow_glyphs(c)
    if not gl:
        return None, "no_amount_text"
    hs = [g.h for g in gl if g.h >= 0.5 * max(q.h for q in gl)]
    med = float(np.median(hs))
    out = ""
    for g in sorted(gl, key=lambda q: q.x0):
        if is_sep(g, med):
            out += "."
            continue
        if not (0.55 * med <= g.h <= 1.5 * med):
            continue
        vec = digit_vec(c[max(0, g.y0 - 1):g.y1 + 1, max(0, g.x0 - 1):g.x1 + 1])
        if vec is None:
            return None, "digit_unreadable"
        ch, _, why = digits.classify(vec)
        if ch is None:
            return None, why
        out += ch
    return (out, "") if out else (None, "no_amount_text")


@dataclass
class BandRead:
    state: str                                   # turn | pre_action | wait_for_blinds | show_cards | none | unknown
    slots: dict = field(default_factory=dict)    # slot -> class | None
    buttons: list = field(default_factory=list)  # [(LABEL, amount_text | None)]
    why: str = ""
    confidence: float = 0.0


LABEL_TO_ACTION = {"fold": "FOLD", "check_fold": "FOLD", "check": "CHECK", "call": "CALL", "bet": "RAISE", "raise_to": "RAISE"}


def read_band(bgr: np.ndarray, layout: dict, books: dict[str, LabelBook], glyphs: GlyphBook, decimals: int | None = 2) -> BandRead:
    cls: dict[str, str | None] = {}
    conf: list[float] = []
    reasons: list[str] = []
    for kind in SLOTS:
        if kind not in layout:
            continue
        vec = slot_vector(bgr, tuple(layout[kind]), kind)
        if vec is None:
            cls[kind] = "empty"
            continue
        book = books.get(kind)
        if book is None:
            cls[kind] = None
            reasons.append(f"{kind}:no_book")
            continue
        c, cf, why = book.classify(vec)
        cls[kind] = c
        if c is None:
            reasons.append(f"{kind}:{why}")
        else:
            conf.append(cf)
    L, M, R, P = cls.get("left"), cls.get("mid"), cls.get("right"), cls.get("pill")
    if P == "wait_for_blinds" and L in ("empty", "other", None) and M in ("empty", "other", None):
        return BandRead("wait_for_blinds", cls, [], "", float(np.mean(conf)) if conf else 0.5)
    if L == "show" and M == "show" and R == "show":
        return BandRead("show_cards", cls, [], "", float(np.mean(conf)) if conf else 0.5)
    if L in ("fold", "check_fold") and M in ("check", "call") and R in ("bet", "raise_to"):
        buttons: list = []
        for kind, label in (("left", L), ("mid", M), ("right", R)):
            amount = None
            if label in ("call", "bet", "raise_to"):
                amount, why = read_amount(slot_crop(bgr, tuple(layout[kind]), kind if kind != "right" else "mid"), glyphs, decimals)
                if amount is None:
                    reasons.append(f"{kind}_amount:{why}")
            buttons.append((LABEL_TO_ACTION[label], amount))
        return BandRead("turn", cls, buttons, "; ".join(reasons), float(np.mean(conf)) if conf else 0.5)
    if L in ("fold", "check_fold") and M in ("empty",) and R in ("empty",):
        return BandRead("pre_action", cls, [], "", float(np.mean(conf)) if conf else 0.5)
    if all(v in ("empty", "other") for v in cls.values()) and cls:
        return BandRead("none", cls, [], "", 0.5)
    return BandRead("unknown", cls, [], "; ".join(reasons) or "band_not_recognised", 0.0)
