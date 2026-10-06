"""Generic ROI-profile adapter for layouts we have not hand-built (e.g. TON Poker in Telegram).

Nothing is assumed about the UI: the owner/labeller supplies normalised regions + a few labelled frames; glyph and card
exemplars are learned from them. The profile starts ``verified=False``; ``read`` refuses (all UNKNOWN) until
``verify`` passes on HELD-OUT labelled frames. A frame whose aspect ratio differs from the calibration aspect is refused.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..parse import parse_money
from ..schema import Field, TableState
from ..vision.cards import CardBook, find_card_boxes
from ..vision.glyphs import GlyphBook, read_glyphs
from ..vision.textspot import spot_lines
from .base import Capabilities, DetectResult, Frame, TableAdapter, VerificationReport

NUM = frozenset("0123456789$KM")


@dataclass
class RoiProfile:
    data: dict
    glyphs: GlyphBook = field(default_factory=GlyphBook)
    cards: CardBook = field(default_factory=CardBook)

    def save(self, path: Path) -> None:
        d = dict(self.data); d["glyph_book"] = self.glyphs.to_json(); d["card_book"] = self.cards.to_json()
        Path(path).write_text(json.dumps(d), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> "RoiProfile":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        gb, cb = GlyphBook.from_json(d.pop("glyph_book", {})), CardBook.from_json(d.pop("card_book")) if "card_book" in d else CardBook()
        return RoiProfile(d, gb, cb)


def _px(r, W, H):
    return int(r[0] * W), int(r[1] * H), int((r[0] + r[2]) * W), int((r[1] + r[3]) * H)


class RoiAdapter(TableAdapter):
    id = "roi"
    min_verify_frames = 8

    def __init__(self, profile: RoiProfile | None = None):
        self.profile = profile

    def profile_id(self) -> str:
        return self.profile.data.get("id", f"{self.id}@uncalibrated") if self.profile else f"{self.id}@uncalibrated"

    # ---- gate
    def _refusal(self, frame: Frame) -> str | None:
        p = self.profile
        if p is None:
            return "layout_not_calibrated"
        if not p.data.get("verified"):
            return "layout_not_verified_on_held_out_frames"
        ref = p.data["ref_size"]
        if abs((frame.w / frame.h) / (ref[0] / ref[1]) - 1) > 0.03:
            return "aspect_ratio_differs_from_calibration (layout/window changed: recalibrate)"
        return None

    def detect(self, frame: Frame) -> DetectResult:
        why = self._refusal(frame)
        return DetectResult(0.0 if why else 0.6, [why] if why else ["verified profile, aspect matches"], bool(self.profile))

    def read(self, frame: Frame) -> TableState:
        t = frame.t_ms
        st = TableState(frame.frame_id, t, frame.source, self.profile_id())
        why = self._refusal(frame)
        U = lambda w: Field.unknown(t, f"{self.id}.pixels", w)
        if why:
            st.hero_cards = [U(why), U(why)]
            for n in ("board_count", "street", "pot", "to_call", "hero_stack", "dealer_slot", "num_seats", "hero_turn", "actions", "hero_position"):
                setattr(st, n, U(why))
            st.quality = {"refused": why}
            return st
        return self._read_rois(frame, st)

    def _read_rois(self, frame: Frame, st: TableState) -> TableState:
        P = self.profile.data["rois"]; H, W = frame.h, frame.w; t = st.t_ms
        src = f"{self.id}.pixels"
        U = lambda w: Field.unknown(t, src, w)
        def cards(roi, n_max):
            x0, y0, x1, y1 = _px(roi, W, H)
            ww = x1 - x0
            boxes = find_card_boxes(frame.bgr, (x0, y0, x1, y1), max(int(0.04 * ww), 12), int(0.45 * ww))
            out = []
            for b in boxes[:n_max]:
                c, conf, why = self.profile.cards.read(b, frame.bgr)
                out.append(Field.ok(c, conf, t, src) if c else Field.unknown(t, src, why, conf))
            return out
        if "hero_cards" in P:
            h = cards(P["hero_cards"], 2)
            st.hero_cards = (h + [U("card_not_found")] * 2)[:2]
        if "board" in P:
            st.board = cards(P["board"], 5)
            n = len(st.board)
            st.board_count = Field.ok(n, 0.6, t, src) if n in (0, 3, 4, 5) else U(f"board_layout_inconsistent n={n}")
            st.street = Field.ok({0: "preflop", 3: "flop", 4: "turn", 5: "river"}[n], 0.6, t, src) if st.board_count.known else U("board_count_unknown")
        for name in ("pot", "to_call", "hero_stack"):
            if name in P:
                setattr(st, name, self._number(frame, P[name], t, src, name))
            else:
                setattr(st, name, U("roi_not_defined"))
        for name in ("dealer_slot", "num_seats", "hero_turn", "actions", "hero_position"):
            setattr(st, name, U("roi_not_defined"))
        return st

    def _number(self, frame, roi, t, src, name) -> Field:
        H, W = frame.h, frame.w
        x0, y0, x1, y1 = _px(roi, W, H)
        hh = y1 - y0
        lines = spot_lines(frame.bgr, (x0, y0, x1, y1), (0.25 * hh, 1.05 * hh), v_min=self.profile.data.get("v_min", 140))
        if len(lines) != 1:
            return Field.unknown(t, src, f"{name}: {len(lines)} text lines in ROI")
        ln = lines[0]
        r = read_glyphs(ln.glyphs, ln.height, self.profile.glyphs, allowed=NUM)
        if r.text is None:
            return Field.unknown(t, src, f"{name}: {r.reason}", r.confidence)
        m = parse_money(r.text.lstrip("$"))
        return Field.ok(m, r.confidence, t, src) if m else Field.unknown(t, src, f"{name}: malformed")

    # ---- calibration + verification
    def calibrate(self, labelled, rois: dict, note: str = "") -> RoiProfile:
        """labelled: [(Frame, truth)] with truth = {"hero_cards": [..], "board": [..], "pot": "123", ...} (strings)."""
        f0 = labelled[0][0]
        prof = RoiProfile({"adapter": self.id, "rois": rois, "ref_size": [f0.w, f0.h], "verified": False, "note": note, "v_min": 140})
        for fr, truth in labelled:
            H, W = fr.h, fr.w
            for key in ("hero_cards", "board"):
                if key in rois and truth.get(key):
                    x0, y0, x1, y1 = _px(rois[key], W, H)
                    boxes = find_card_boxes(fr.bgr, (x0, y0, x1, y1), max(int(0.04 * (x1 - x0)), 12), int(0.45 * (x1 - x0)))
                    if len(boxes) == len(truth[key]):
                        for b, c in zip(boxes, truth[key]):
                            prof.cards.add(c, b, fr.bgr)
            for key in ("pot", "to_call", "hero_stack"):
                if key in rois and truth.get(key):
                    x0, y0, x1, y1 = _px(rois[key], W, H)
                    lines = spot_lines(fr.bgr, (x0, y0, x1, y1), (0.25 * (y1 - y0), 1.05 * (y1 - y0)), v_min=140)
                    chars = [c for c in truth[key] if c not in ", "]
                    if len(lines) == 1 and len(lines[0].glyphs) == len(chars) + truth[key].count(","):
                        gl = [g for g in lines[0].glyphs]
                        k = 0
                        for g in gl:
                            from ..vision.glyphs import is_sep
                            if is_sep(g, lines[0].height):
                                continue
                            if k < len(chars):
                                prof.glyphs.add(chars[k], g, key); k += 1
        prof.data["id"] = f"{self.id}@" + hashlib.sha256(json.dumps(rois, sort_keys=True).encode()).hexdigest()[:8]
        self.profile = prof
        return prof

    def verify(self, heldout, min_card_acc=0.98, min_money_acc=0.98, max_unknown=0.35) -> VerificationReport:
        """Gate: the profile becomes usable only if held-out frames are read with no confident errors."""
        p = self.profile
        assert p is not None
        p.data["verified"] = True            # temporarily open the gate to read the held-out frames
        c_ok = c_n = m_ok = m_n = unk = tot = 0
        wrong = 0
        for fr, truth in heldout:
            st = self.read(fr)
            for key, fields_ in (("hero_cards", st.hero_cards), ("board", st.board)):
                if truth.get(key) is None:
                    continue
                for i, c in enumerate(truth[key]):
                    tot += 1
                    f = fields_[i] if i < len(fields_) else None
                    if f is None or not f.known: unk += 1; continue
                    c_n += 1; c_ok += int(f.value == c)
            for key in ("pot", "to_call", "hero_stack"):
                if truth.get(key):
                    tot += 1
                    f = getattr(st, key)
                    if not f.known: unk += 1; continue
                    m_n += 1; m_ok += int(f.value.agrees(parse_money(truth[key].lstrip("$"))))
        card_acc = c_ok / c_n if c_n else None
        money_acc = m_ok / m_n if m_n else None
        unknown = unk / tot if tot else None
        ok = (len(heldout) >= self.min_verify_frames and (card_acc is None or card_acc >= min_card_acc)
              and (money_acc is None or money_acc >= min_money_acc) and (unknown is not None and unknown <= max_unknown))
        p.data["verified"] = bool(ok)
        return VerificationReport(p.data.get("id", ""), len(heldout), card_acc, money_acc, unknown, bool(ok),
                                  "" if ok else "not enough held-out frames or accuracy/unknown thresholds not met")
