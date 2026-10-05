"""Find short text lines (numbers, labels) inside a zone without knowing where they are."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .glyphs import Glyph, GlyphBook, merge_vertical_parts, segment


def _projection_split(g: Glyph, med_w: float) -> list[Glyph] | None:
    """Split a glyph that is k glyphs wide at the k-1 column-ink minima nearest the expected boundaries."""
    k = int(round(g.w / med_w))
    if k < 2 or k > 6:
        return None
    col = g.mask.sum(0).astype(float)
    cuts = []
    for i in range(1, k):
        centre = int(round(i * g.w / k))
        lo, hi = max(centre - int(0.3 * med_w), 1), min(centre + int(0.3 * med_w) + 1, g.w - 1)
        if hi <= lo:
            return None
        j = lo + int(np.argmin(col[lo:hi]))
        if col[j] > 0.7 * col.max():
            return None                       # no real valley there: refuse to cut
        cuts.append(j)
    edges = [0] + cuts + [g.w]
    parts = []
    for a, b in zip(edges, edges[1:]):
        m = g.mask[:, a:b]
        ys, xs = np.where(m)
        if len(ys) < 4 or not (0.5 * med_w <= (xs.max() - xs.min() + 1) <= 1.5 * med_w):
            return None
        parts.append(Glyph(g.x0 + a + xs.min(), g.x0 + a + xs.max() + 1, g.y0 + ys.min(), g.y0 + ys.max() + 1,
                           m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]))
    return parts


def split_merged(gl: list[Glyph], gray: np.ndarray, v_min: int) -> list[Glyph]:
    """The coarse threshold keeps anti-aliasing halos that can glue two digits together. For glyphs clearly wider than
    their neighbours, re-segment that glyph alone with a local Otsu threshold; keep the split only if it is plausible."""
    if not gl:
        return gl
    tall = [g for g in gl if g.h >= 0.6 * max(q.h for q in gl)]
    if len(tall) >= 2:
        med_w = float(np.median([g.w for g in tall]))
    else:                                    # a lone blob: digits are ~0.72 x as wide as they are tall in the supported UI font
        med_w = 0.72 * max(g.h for g in tall)
    out: list[Glyph] = []
    for g in gl:
        if g.h >= 0.6 * max(q.h for q in gl) and g.w > 1.45 * med_w:
            patch = gray[g.y0:g.y1, g.x0:g.x1]
            thr, _ = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            parts = segment((patch > max(thr, v_min)) & g.mask)
            parts = [p for p in parts if p.h >= 0.6 * g.h]
            if len(parts) >= 2 and all(0.6 * med_w <= p.w <= 1.4 * med_w for p in parts):
                for p in parts:
                    out.append(Glyph(g.x0 + p.x0, g.x0 + p.x1, g.y0 + p.y0, g.y0 + p.y1, p.mask))
                continue
            cut = _projection_split(g, med_w)
            if cut:
                out.extend(cut)
                continue
        out.append(g)
    out.sort(key=lambda q: q.x0)
    return out


@dataclass
class TextLine:
    x0: int
    y0: int
    x1: int
    y1: int
    glyphs: list[Glyph]      # coordinates relative to the line box
    height: float

    @property
    def cx(self) -> float: return (self.x0 + self.x1) / 2
    @property
    def cy(self) -> float: return (self.y0 + self.y1) / 2


def text_mask(bgr: np.ndarray, v_min: int = 140, otsu: bool = False, block: int = 31) -> np.ndarray:
    """Light strokes on a darker background. ``otsu`` = locally adaptive threshold (buttons: white text on a red face,
    gold text on a dark face): a pixel is text if it is clearly brighter than its neighbourhood mean."""
    mx = bgr.max(2)
    if otsu and mx.size:
        blk = max(int(block) | 1, 7)
        mx8 = np.ascontiguousarray(mx.astype(np.uint8))
        white = bgr.min(2) >= 200                                  # white text: every channel high (a red/blue face has a low one)
        mean = cv2.blur(mx8, (blk, blk))
        dark_face = (mx8.astype(np.int16) - mean.astype(np.int16) > 40) & (mean < 120)   # coloured text on a dark face
        return white | dark_face
    return mx >= v_min


def spot_lines(bgr: np.ndarray, zone: tuple[int, int, int, int], h_range: tuple[float, float], v_min: int = 140, max_gap: float = 0.9, otsu: bool = False) -> list[TextLine]:
    """Candidate lines in zone (x0,y0,x1,y1 px). ``h_range`` = (min,max) glyph height in px."""
    x0, y0, x1, y1 = zone
    sub = bgr[y0:y1, x0:x1]
    if sub.size == 0:
        return []
    m = text_mask(sub, v_min, otsu, block=int(3 * h_range[1]))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    lo, hi = h_range
    cand = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        small_sep = h < lo and w < lo and area >= 2 and h >= 0.15 * lo
        if (lo <= h <= hi and w <= 1.6 * hi and area >= 4) or small_sep:
            cand.append((x, y, w, h, i))
    if not cand:
        return []
    canvas = np.zeros(m.shape, np.uint8)
    for x, y, w, h, i in cand:
        canvas[y:y + h, x:x + w][labels[y:y + h, x:x + w] == i] = 255
    k = max(3, int(round(max_gap * (lo + hi) / 2)))
    merged = cv2.dilate(canvas, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 3)))
    cn, cl, cs, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    lines = []
    for j in range(1, cn):
        x, y, w, h, area = cs[j]
        if w < 0.6 * lo:
            continue
        # tight box = union of member glyph pixels
        reg = canvas[y:y + h, x:x + w] > 0
        ys, xs = np.where(reg)
        if len(ys) < 6:
            continue
        gx0, gx1, gy0, gy1 = x + xs.min(), x + xs.max() + 1, y + ys.min(), y + ys.max() + 1
        line_mask = reg[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        gl = merge_vertical_parts(segment(line_mask))
        gl = split_merged(gl, sub[gy0:gy1, gx0:gx1].max(2), v_min)
        if not gl:
            continue
        hs = [g.h for g in gl if g.h >= 0.5 * max(q.h for q in gl)]
        lines.append(TextLine(x0 + gx0, y0 + gy0, x0 + gx1, y0 + gy1, gl, float(np.median(hs))))
    lines.sort(key=lambda t: (t.y0, t.x0))
    return lines
