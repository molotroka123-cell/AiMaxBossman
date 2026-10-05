"""Template (exemplar) glyph OCR for UI numbers. CPU only, numpy + OpenCV.

Not trained weights: a calibration step stores labelled glyph exemplars. Recognition is k-NN over size-normalised
masks with an explicit reject threshold; anything ambiguous becomes UNKNOWN instead of a guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

GLYPH_W, GLYPH_H = 12, 18
SEPARATORS = ",."


@dataclass
class Glyph:
    x0: int
    x1: int
    y0: int
    y1: int
    mask: np.ndarray  # cropped bool mask

    @property
    def w(self) -> int: return self.x1 - self.x0
    @property
    def h(self) -> int: return self.y1 - self.y0


def foreground(gray: np.ndarray) -> np.ndarray:
    """Binary text mask. Polarity is chosen so that the foreground is the minority of the ROI."""
    if gray.size == 0:
        return np.zeros_like(gray, dtype=bool)
    g = cv2.GaussianBlur(gray, (3, 3), 0) if min(gray.shape) > 24 else gray
    thr, _ = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = g > thr
    return bright if bright.mean() <= 0.5 else ~bright


def segment(mask: np.ndarray, min_h_frac: float = 0.0) -> list[Glyph]:
    """Connected components -> glyphs ordered left to right. Tiny speckles dropped."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 2:
            continue
        out.append(Glyph(x, x + w, y, y + h, (labels[y:y + h, x:x + w] == i)))
    out.sort(key=lambda g: g.x0)
    return out


def merge_vertical_parts(glyphs: list[Glyph]) -> list[Glyph]:
    """Merge components that overlap horizontally (e.g. colon dots, split strokes)."""
    out: list[Glyph] = []
    for g in glyphs:
        if out and (min(g.x1, out[-1].x1) - max(g.x0, out[-1].x0)) >= 0.6 * min(g.w, out[-1].w):
            p = out.pop()
            x0, x1, y0, y1 = min(p.x0, g.x0), max(p.x1, g.x1), min(p.y0, g.y0), max(p.y1, g.y1)
            m = np.zeros((y1 - y0, x1 - x0), bool)
            m[p.y0 - y0:p.y1 - y0, p.x0 - x0:p.x1 - x0] |= p.mask
            m[g.y0 - y0:g.y1 - y0, g.x0 - x0:g.x1 - x0] |= g.mask
            out.append(Glyph(x0, x1, y0, y1, m))
        else:
            out.append(g)
    return out


def features(g: Glyph) -> np.ndarray:
    m = g.mask.astype(np.float32)
    h, w = m.shape
    side = max(h, w)
    pad = np.zeros((side, side), np.float32)
    pad[(side - h) // 2:(side - h) // 2 + h, (side - w) // 2:(side - w) // 2 + w] = m
    f = cv2.resize(pad, (GLYPH_W, GLYPH_W), interpolation=cv2.INTER_AREA)
    f = cv2.GaussianBlur(f, (3, 3), 0.8)
    aspect = np.float32(min(w / max(h, 1), 1.5))
    return np.concatenate([f.ravel(), [aspect * 2.0]]).astype(np.float32)


ALIASES = {"I": "l"}          # visually identical in the sans font used by the supported UIs


def canon(text: str) -> str:
    return "".join(ALIASES.get(c, c) for c in text)


@dataclass
class GlyphBook:
    """k-NN exemplar store. ``classify`` returns (char, confidence); confidence in [0,1] from the best/second margin."""
    exemplars: dict[str, list[np.ndarray]] = field(default_factory=dict)
    max_per_class: int = 60              # cap per (class, tag): keeps font-size/weight diversity (pot vs seat vs bet digits)
    _counts: dict = field(default_factory=dict)

    def add(self, ch: str, g: Glyph, tag: str = "") -> None:
        ch = ALIASES.get(ch, ch)
        n = self._counts.get((ch, tag), 0)
        if n < self.max_per_class:
            f = features(g)
            lst = self.exemplars.setdefault(ch, [])
            if not any(np.array_equal(f, e) for e in lst[-30:]):      # identical renderings add nothing
                lst.append(f)
            self._counts[(ch, tag)] = n + 1

    def _matrix(self):
        if not hasattr(self, "_cache") or self._cache[0] != sum(map(len, self.exemplars.values())):
            labels, rows = [], []
            for ch, lst in self.exemplars.items():
                for f in lst:
                    labels.append(ch); rows.append(f)
            self._cache = (len(rows), np.array(labels), np.stack(rows) if rows else np.zeros((0, 1)))
        return self._cache[1], self._cache[2]

    def classify(self, g: Glyph, allowed: frozenset | None = None) -> tuple[str | None, float, float]:
        """k-NN over exemplars. ``allowed`` restricts the alphabet (digits only for numbers: '1' vs 'l' cannot be confused)."""
        labels, mat = self._matrix()
        if len(labels) == 0:
            return None, 0.0, 9e9
        d = np.sqrt(((mat - features(g)) ** 2).sum(1))
        if allowed is not None:
            keep = np.array([lb in allowed for lb in labels])
            if not keep.any():
                return None, 0.0, 9e9
            labels, d = labels[keep], d[keep]
        best_per: dict[str, float] = {}
        for ch, dist in zip(labels, d):
            if dist < best_per.get(ch, 9e9):
                best_per[ch] = float(dist)
        ranked = sorted(best_per.items(), key=lambda kv: kv[1])
        d1 = ranked[0][1]
        d2 = ranked[1][1] if len(ranked) > 1 else d1 + 1.0
        conf = float(np.clip((d2 - d1) / max(d2, 1e-6), 0, 1))
        return ranked[0][0], conf, d1

    def to_json(self) -> dict:
        return {ch: [f.round(4).tolist() for f in lst] for ch, lst in self.exemplars.items()}

    @staticmethod
    def from_json(d: dict) -> "GlyphBook":
        return GlyphBook({ch: [np.array(f, np.float32) for f in lst] for ch, lst in d.items()})


def line_glyphs(gray: np.ndarray) -> tuple[list[Glyph], dict]:
    """Segment one text line; drop components whose height is an outlier (borders, card edges)."""
    fg = foreground(gray)
    gl = merge_vertical_parts(segment(fg))
    info = {"fg_frac": float(fg.mean()) if fg.size else 0.0, "raw": len(gl)}
    if not gl:
        return [], info
    hs = np.array([g.h for g in gl if g.h >= 0.35 * max(x.h for x in gl)] or [1])
    med = float(np.median(hs))
    keep = [g for g in gl if 0.55 * med <= g.h <= 1.5 * med or (g.h < 0.45 * med and g.y1 >= np.median([x.y1 for x in gl]) - 2)]
    info["median_h"] = med
    info["dropped"] = len(gl) - len(keep)
    return keep, info


@dataclass
class LineRead:
    text: str | None
    confidence: float
    reason: str = ""
    chars: list = field(default_factory=list)


def is_sep(g: Glyph, med_h: float) -> bool:
    return g.h <= 0.45 * med_h and g.w <= 0.5 * med_h


def read_glyphs(gl: list[Glyph], med_h: float, book: GlyphBook, min_conf: float = 0.10, max_dist: float = 4.8, allowed: frozenset | None = None) -> LineRead:
    """Read already-segmented glyphs. Separators are recognised by shape (small, low) and resolved by digit grouping."""
    if not gl:
        return LineRead(None, 0.0, "no_glyphs")
    chars, confs = [], []
    for g in gl:
        if is_sep(g, med_h):
            chars.append(("SEP", g)); confs.append(0.9)
            continue
        ch, conf, dist = book.classify(g, allowed)
        if ch is None or dist > max_dist or conf < min_conf:
            return LineRead(None, conf, "glyph_ambiguous", [(c if c != "SEP" else "?") for c, _ in chars] + ["?"])
        chars.append((ch, g)); confs.append(conf)
    text = ""
    for i, (c, _) in enumerate(chars):
        if c != "SEP":
            text += c
            continue
        tail = "".join(x for x, _ in chars[i + 1:])
        digits = ""
        for x in tail:
            if x.isdigit(): digits += x
            else: break
        rest = tail[len(digits):]
        if len(digits) == 3 and rest in ("", "K", "M") and text and text[-1].isdigit():
            text += ","
        elif len(digits) == 1 and rest in ("K", "M") and text and text[-1].isdigit():
            text += "."
        else:
            return LineRead(None, 0.2, "separator_unclear", [c for c, _ in chars])
    return LineRead(text, float(np.min(confs)), "", [c for c, _ in chars])


def read_line(gray: np.ndarray, book: GlyphBook, **kw) -> LineRead:
    gl, info = line_glyphs(gray)
    if not gl:
        return LineRead(None, 0.0, "no_glyphs")
    if info["fg_frac"] > 0.42:
        return LineRead(None, 0.0, "foreground_too_dense")
    return read_glyphs(gl, info["median_h"], book, **kw)
