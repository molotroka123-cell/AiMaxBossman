"""Raise-panel reader for the owner's Poker Train: preset buttons (value + box), the big "Raise to" amount, CONFIRM.

Used only by the executor's amount step. Values are read with a SEPARATE exemplar book learned from labelled panel frames
(tools/calibrate_panel.py); anything not read confidently is None, and the executor halts rather than guess."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..control.executor import Fresh, Panel, Preset
from ..control.geometry import Rect
from ..control.locator import Target
from ..schema import Money
from ..parse import parse_money
from ..vision.glyphs import GlyphBook, read_glyphs
from ..vision.textspot import spot_lines

from .poker_train import platform_profile                  # noqa: E402

PANEL_PROFILE = platform_profile("poker_train_panel.json")
NUM = frozenset("0123456789KM")
CELL_W = (120, 230)      # CSS px
CELL_H = (36, 62)


def find_cells(bgr: np.ndarray, zone: tuple, s: float) -> list[tuple]:
    """Rounded preset cells = contours with the cell aspect; outer and inner border give two contours: keep the outer one."""
    x0, y0, x1, y1 = zone
    sub = bgr[max(y0, 0):y1, x0:x1]
    if sub.size == 0:
        return []
    e = cv2.dilate(cv2.Canny(cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY), 12, 40), np.ones((3, 3), np.uint8))
    cs, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cs:
        x, y, w, h = cv2.boundingRect(c)
        if CELL_W[0] * s <= w <= CELL_W[1] * s and CELL_H[0] * s <= h <= CELL_H[1] * s:
            boxes.append((x + x0, y + max(y0, 0), w, h))
    boxes.sort(key=lambda b: -(b[2] * b[3]))
    keep: list[tuple] = []
    for b in boxes:
        r = Rect(*b)
        if not any(Rect(*k).iou(r) > 0.5 or Rect(*k).inter_area(r) > 0.6 * r.w * r.h for k in keep):
            keep.append(b)
    return sorted(keep, key=lambda b: (round(b[1] / max(1.0, 20 * s)), b[0]))


class TrainerPanelReader:
    def __init__(self, book: GlyphBook | None = None, path: Path = PANEL_PROFILE):
        if book is None:
            d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            book = GlyphBook.from_json(d.get("glyphs", {}))
            self.book_rt = GlyphBook.from_json(d.get("glyphs_raise_to", {}))
        else:
            self.book_rt = book
        self.book = book

    def _read_num(self, bgr, zone, hr, s, allow_any=False) -> tuple[float | None, float]:
        lines = spot_lines(bgr, zone, (hr[0] * s, hr[1] * s), otsu=True)
        if not lines:
            return None, 0.0
        ln = max(lines, key=lambda q: q.cy)           # value is the lower line of a preset cell
        r = read_glyphs(list(ln.glyphs), ln.height, self.book, allowed=NUM, min_conf=0.25, max_dist=4.0)
        if r.text is None:
            return None, r.confidence
        m = parse_money(r.text)
        return (m.amount if m else None), r.confidence

    def read(self, fresh: Fresh) -> Panel:
        st, fr = fresh.state, fresh.frame
        btns = (st.quality or {}).get("buttons") or []
        conf = [b for b in btns if b["label"] == "CONFIRM"]
        band = (st.quality or {}).get("band")
        if not conf or not band or len(conf) != 1:
            return Panel(False)
        by0, by1, s = band
        c = conf[0]
        panel = Panel(True, None, [], Target("CONFIRM", Rect(c["x"], c["y"], c["w"], c["h"]), c.get("conf", 0.0), "vision"))
        img = fr.bgr
        zone = (0, int(by0 - 250 * s), img.shape[1], int(by0 - 4 * s))
        for (x, y, w, h) in find_cells(img, zone, s):
            v, conf_v = self._read_num(img, (x + 3, y + 3, x + w - 3, y + h - 3), (5.0, 18.0), s)
            if v is not None:
                panel.presets.append(Preset(f"cell@{x},{y}", v, Rect(x, y, w, h)))
        # "Raise to": the big gold number (digit height ~20 CSS px) above the slider; its y depends on how many preset rows the panel has,
        # so scan the whole panel band and accept only a line with that digit height (card corners and labels have other heights)
        rz = (int(img.shape[1] * 0.25), int(by0 - 262 * s), int(img.shape[1] * 0.75), int(by0 - 96 * s))
        lines = [q for q in spot_lines(img, rz, (14.0 * s, 30.0 * s), v_min=150)
                 if 16.0 * s <= q.height <= 26.0 * s and (q.x1 - q.x0) >= 0.45 * q.height and abs(q.cx - img.shape[1] / 2) <= 0.2 * img.shape[1]
                 and (not panel.presets or q.y1 < min(p.box.y for p in panel.presets) - 4 * s)]
        vals = set()
        for ln in lines:                                  # the same number can be segmented twice (glow); several lines must agree, else UNKNOWN
            r = read_glyphs(list(ln.glyphs), ln.height, self.book_rt, allowed=NUM, min_conf=0.25, max_dist=4.0)
            m = parse_money(r.text) if r.text else None
            vals.add(m.amount if m else None)
        if len(vals) == 1 and None not in vals:
            panel.raise_to = next(iter(vals))
        return panel
