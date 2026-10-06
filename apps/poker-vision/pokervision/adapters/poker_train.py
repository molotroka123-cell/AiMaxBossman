"""Adapter #1: the owner's own Poker Train (React app served on loopback).

Pixels in, TableState out. The layout is *anchored on the hero's cards*: their width gives the CSS-pixel scale, their
top edge the vertical origin, and a full-width probe row under them gives the app column. All other zones are expressed
in CSS pixels / column fractions measured at calibration time (``calibrate``), so DPI, window size and browser zoom
are handled by scale estimation instead of fixed pixel coordinates.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..schema import Field, Money, Seat, TableState, UNKNOWN
from ..parse import parse_money
from ..vision.cards import CardBook, CardBox, find_back_boxes, find_card_boxes, split_corner
from ..vision.glyphs import GlyphBook, WordBook, canon, is_sep, read_glyphs, features
from ..vision.textspot import TextLine, spot_lines
from .base import Capabilities, DetectResult, Frame, TableAdapter

PROFILE_PATH = Path(__file__).with_name("profiles") / "poker_train.json"
SRC = "poker_train.pixels"
NUM_ALPHABET = frozenset("0123456789$KM")
ACTION_ALPHABET = frozenset("ACDEFHIKLMNORS0123456789l")      # letters of FOLD CHECK CALL RAISE ALL IN CONFIRM + digits
ACTION_VOCAB = {canon(k): v for k, v in {"FOLD": "FOLD", "CHECK": "CHECK", "CALL": "CALL", "RAISE": "RAISE", "ALLIN": "ALL IN", "CONFIRM": "CONFIRM"}.items()}


@dataclass
class Profile:
    data: dict
    glyphs: GlyphBook
    cards: CardBook
    words: WordBook = field(default_factory=WordBook)

    @property
    def id(self) -> str:
        return self.data.get("id", "poker_train@uncalibrated")

    def to_json(self) -> dict:
        d = dict(self.data)
        d["glyph_book"] = self.glyphs.to_json()
        d["card_book"] = self.cards.to_json()
        d["word_book"] = self.words.to_json()
        return d

    @staticmethod
    def from_json(d: dict) -> "Profile":
        gb = GlyphBook.from_json(d.get("glyph_book", {}))
        cb = CardBook.from_json(d["card_book"]) if "card_book" in d else CardBook()
        wb = WordBook.from_json(d.get("word_book", {}))
        meta = {k: v for k, v in d.items() if k not in ("glyph_book", "card_book", "word_book")}
        return Profile(meta, gb, cb, wb)

    def save(self, path: Path = PROFILE_PATH) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(self.to_json(), separators=(",", ":"))
        path.write_text(blob, encoding="utf-8")
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    @staticmethod
    def load(path: Path = PROFILE_PATH) -> "Profile | None":
        if not path.exists():
            return None
        return Profile.from_json(json.loads(path.read_text(encoding="utf-8")))


def numeric_suffix(glyphs: list, exp_px: float) -> list:
    """The number on a line is its right-hand run of glyphs with no word-space inside: label text (``Pot:``, ``Stack:``)
    sits left of a gap wider than any digit gap. Gap limit = 0.40 x the calibrated digit height."""
    run = [glyphs[-1]] if glyphs else []
    for g in reversed(glyphs[:-1]):
        if run[0].x0 - g.x1 > 0.40 * exp_px:
            break
        run.insert(0, g)
    return run


def _angle(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dy, dx)) % 360


def _adiff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


class PokerTrainAdapter(TableAdapter):
    id = "poker_train"
    title = "Poker Train (own trainer, loopback)"
    capabilities = Capabilities(
        observe=True, replay=True, act=True, advise_live=True, multi_table=False,
        reads=("hero_cards", "board", "pot", "to_call", "hero_stack", "seat_stacks", "bets", "dealer", "actions", "street"),
        notes="act/advise only through the loopback-guarded actuator on the owner's own trainer",
    )

    def __init__(self, profile: Profile | None = None, naive: bool = False):
        """``naive=True`` is the BASELINE used by the evaluation: the same pixels and exemplars but no UNKNOWN discipline
        (no reject thresholds, no geometry/height/chip/layout checks) — it always answers."""
        self.profile = profile or Profile.load()
        self.naive = naive
        self.scale_hint: float | None = None          # scale confirmed on a fully visible hero pair (per adapter instance = per session)

    def reset(self) -> None:
        self.scale_hint = None

    # ------------------------------------------------------------------ detection
    def profile_id(self) -> str:
        return self.profile.id if self.profile else "poker_train@uncalibrated"

    def detect(self, frame: Frame) -> DetectResult:
        if not self.profile:
            return DetectResult(0.0, ["no profile: calibrate first"], False)
        anchor = self._anchor(frame)
        if anchor is None:
            return DetectResult(0.1, ["no hero-card anchor in frame"], True)
        score, why = 0.5, ["hero card pair anchor"]
        P = self.profile.data
        # the board row (when present) and the labelled pot line are further, independent confirmations of the layout
        st = self.read(frame)
        if st.pot.known:
            score += 0.25; why.append("labelled 'Pot:' line read")
        if st.board_count.known and st.board_count.value in (3, 4, 5):
            score += 0.15; why.append("board row layout consistent")
        return DetectResult(min(score, 1.0), why, True)

    # ------------------------------------------------------------------ anchor
    def _anchor(self, frame: Frame) -> dict | None:
        P = self.profile.data
        H, W = frame.h, frame.w
        boxes = find_card_boxes(frame.bgr, (0, int(0.30 * H), W, int(0.82 * H)), min_w=12, max_w=int(0.30 * W))
        # a card has an index (rank glyph) in its top-left corner; a bare white shape (button highlight, avatar rim) does not
        boxes = [b for b in boxes if split_corner(b, frame.bgr) is not None]
        # a card that is only partly visible (raise panel over its lower half) is trusted only when its width matches the
        # scale confirmed on an earlier frame; stray white letters/shapes look like short "cards" otherwise
        full = [b for b in boxes if b.visible_frac >= 0.9]
        if not full and self.scale_hint:
            want = P["hero_card_css_w"] * self.scale_hint
            full = [b for b in boxes if abs(b.w / want - 1) <= 0.08]
        boxes = full
        if not boxes:
            return None
        # hero = lowest group of 1-2 boxes of equal width and equal top
        boxes.sort(key=lambda b: -b.y)
        grp = [boxes[0]]
        for b in boxes[1:]:
            if abs(b.y - grp[0].y) <= 0.12 * grp[0].w and 0.85 <= b.w / grp[0].w <= 1.15:
                grp.append(b)
        grp.sort(key=lambda b: b.x)
        if len(grp) > 2:
            return None
        wpx = float(np.median([b.w for b in grp]))
        s = wpx / P["hero_card_css_w"]
        if not (0.5 <= s <= 6.0):
            return None
        top = float(np.median([b.y for b in grp]))
        if all(b.visible_frac >= 0.9 for b in grp):
            self.scale_hint = s
        return {"s": s, "top": top, "boxes": grp}

    # ------------------------------------------------------------------ read
    def read(self, frame: Frame) -> TableState:
        t, fid = frame.t_ms, frame.frame_id
        st = TableState(fid, t, frame.source, self.profile_id())
        U = lambda why: Field.unknown(t, SRC, why)
        if not self.profile:
            raise RuntimeError("PokerTrainAdapter has no calibrated profile")
        a = self._anchor(frame)
        gray = cv2.cvtColor(frame.bgr, cv2.COLOR_BGR2GRAY)
        st.quality = {"sharp": float(cv2.Laplacian(gray, cv2.CV_64F).var()), "w": frame.w, "h": frame.h}
        if a is None:
            st.hero_cards = [U("no_anchor"), U("no_anchor")]
            for n in ("board_count", "street", "pot", "to_call", "hero_stack", "dealer_slot", "num_seats", "hero_turn", "actions", "hero_position"):
                setattr(st, n, U("no_anchor"))
            st.quality["anchor"] = False
            return st
        s, top = a["s"], a["top"]
        st.quality.update({"anchor": True, "scale": round(s, 3)})
        P = self.profile.data
        col = None
        cx = float(np.mean([b.cx for b in a["boxes"]])) if len(a["boxes"]) == 2 else None
        # --- hero cards
        hero: list[Field] = []
        boxes: list[dict] = []
        st.quality["boxes"] = boxes
        for b in a["boxes"]:
            f_ = self._read_card(frame, b, t, s, P["hero_card_css_w"], "hero")
            hero.append(f_)
            boxes.append({"field": "hero_card", "x": int(b.x), "y": int(b.y), "w": int(b.w), "h": int(b.h), "ok": f_.known, "conf": round(f_.confidence, 2), "label": f_.value if f_.known else f_.reason})
        while len(hero) < 2:
            hero.append(U("card_not_found"))
        st.hero_cards = hero
        st.quality["hero_x"] = [round(b.cx, 1) for b in a["boxes"]]
        # --- board
        self._read_board(frame, st, a, col, cx)
        # --- texts
        self._read_texts(frame, st, a, col, cx)
        # --- actions
        self._read_actions(frame, st, a, col)
        # --- street from board count
        n = st.board_count.value if st.board_count.known else None
        smap = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}
        st.street = Field.ok(smap[n], st.board_count.confidence, t, SRC) if n in smap else U("board_count_unknown")
        st.hero_position = U("derived_later")
        return st

    # ---- cards
    def _read_card(self, frame: Frame, b: CardBox, t: int, s: float, css_w: float, kind: str) -> Field:
        if abs(b.w / (css_w * s) - 1) > 0.12 and not self.naive:
            return Field.unknown(t, SRC, "card_scale_mismatch_animating")
        if self.naive:
            card, conf, why = self.profile.cards.read(b, frame.bgr, min_conf=-1.0, rank_max_d=1e9, suit_max_d=1e9, lax=True)
        else:
            card, conf, why = self.profile.cards.read(b, frame.bgr)
        if card is None:
            return Field.unknown(t, SRC, why, conf)
        return Field.ok(card, conf, t, SRC)

    def _read_board(self, frame: Frame, st: TableState, a: dict, col, cx):
        P, s, top, t = self.profile.data, a["s"], a["top"], st.t_ms
        y0 = int(top + (P["board_dy_css"] - 28) * s)
        y1 = int(top + (P["board_dy_css"] + P["board_card_h_css"] + 20) * s)
        boxes = find_card_boxes(frame.bgr, (0, max(y0, 0), frame.w, min(y1, frame.h)), int(0.80 * P["board_card_css_w"] * s), int(1.2 * P["board_card_css_w"] * s))
        boxes = [b for b in boxes if abs(b.y - (top + P["board_dy_css"] * s)) <= 0.12 * b.w and b.visible_frac > 0.9]
        fields = []
        for b in boxes:
            f_ = self._read_card(frame, b, t, s, P["board_card_css_w"], "board")
            fields.append(f_)
            st.quality.setdefault("boxes", []).append({"field": "board_card", "x": int(b.x), "y": int(b.y), "w": int(b.w), "h": int(b.h), "ok": f_.known, "conf": round(f_.confidence, 2), "label": f_.value if f_.known else f_.reason})
        st.board = fields
        st.quality["board_x"] = [round(b.cx, 1) for b in boxes]
        n = len(boxes)
        backs = find_back_boxes(frame.bgr, (0, max(y0, 0), frame.w, min(y1, frame.h)), int(0.8 * P["board_card_css_w"] * s), int(1.3 * P["board_card_css_w"] * s)) if not self.naive else []
        if backs:
            # a card back in the board zone = a card is being dealt/flipped: the count is not knowable on this frame
            st.board_count = Field.unknown(t, SRC, f"card_back_in_board_zone n_white={n} n_back={len(backs)} (dealing)")
            st.board = [f_.demote("board_in_motion") for f_ in fields]
            return
        if n == 0:
            st.board_count = Field.ok(0, 0.7, t, SRC)   # no card, no card back: empty board zone
            return
        pitch = [(boxes[i + 1].x - boxes[i].x) / (s * P["board_pitch_css"]) for i in range(n - 1)]
        centred = cx is None or abs(np.mean([b.cx for b in boxes]) - cx) <= 0.10 * boxes[0].w * n
        if cx is None and n >= 3:
            st.quality["board_cx"] = float(np.mean([b.cx for b in boxes]))
        if not self.naive and (n not in (3, 4, 5) or any(abs(p - 1) > 0.12 for p in pitch) or not centred):
            st.board_count = Field.unknown(t, SRC, f"board_layout_inconsistent n={n}")
            st.board = [f.demote("board_layout_inconsistent") for f in fields]
            return
        conf = float(np.mean([f.confidence for f in fields if f.known] or [0.4]))
        all_ok = all(f.known for f in fields)
        st.board_count = Field.ok(n, 0.9 if all_ok else 0.5, t, SRC)

    # ---- texts: pot, to call, hero stack, seat stacks, bets, dealer
    def _lines(self, frame, zone, hr, s, v_min=None, otsu=False):
        return spot_lines(frame.bgr, zone, (hr[0] * s, hr[1] * s), v_min=v_min or self.profile.data["num_v_min"], otsu=otsu)

    def _read_one(self, line: TextLine):
        return read_glyphs(line.glyphs, line.height, self.profile.glyphs, allowed=ACTION_ALPHABET)

    @staticmethod
    def _chip_score(bgr: np.ndarray, line: TextLine, s: float) -> float:
        """Bet pills sit on a chip-stack icon centred just below the number; seat stack pills have none (measured 0.22+
        vs 0.0). The icon must be centred under THIS number (a neighbour's chips do not count)."""
        x0, x1 = int(line.cx - 16 * s), int(line.cx + 16 * s)
        y0, y1 = int(line.y1 + 1 * s), int(line.y1 + 14 * s)
        x0c = max(x0, 0)
        sub = bgr[max(y0, 0):y1, x0c:x1]
        if sub.size == 0:
            return 0.0
        m = sub.max(2) >= 115
        if not m.any():
            return 0.0
        ys, xs = np.where(m)
        centroid = x0c + xs.mean()
        if abs(centroid - line.cx) > 5 * s or (xs.max() - xs.min() + 1) > 30 * s:
            return 0.0
        return float(m[:, max(int(line.cx - 14 * s) - x0c, 0):int(line.cx + 14 * s) - x0c].mean())

    def _numeric(self, line: TextLine, key: str, s: float, allow_dollar: bool = False, c_px: float | None = None):
        """Read the number on a line. Glyphs to the right of a colon form the number (the label is ignored).
        Every digit must have the height measured at calibration for this field (clipped/covered digits are refused)."""
        exp = self.profile.data["num_h_css"][key] * s
        tol = self.profile.data["num_h_tol"]
        gl = numeric_suffix(list(line.glyphs), exp)
        if not gl:
            return None, 0.0, "no_glyphs"
        self._last_cy = line.y0 + (min(g.y0 for g in gl) + max(g.y1 for g in gl)) / 2     # centre of the number itself
        for g in gl:
            if is_sep(g, exp):
                continue
            if not self.naive and not (1 - tol <= g.h / exp <= 1 + tol + (0.35 if allow_dollar else 0.0)):
                return None, 0.0, f"glyph_height_off({g.h}px vs {exp:.1f})"
        L = self.profile.data.get("label_w_css", {}).get(key)
        if L is not None and c_px is not None and not self.naive:
            x1 = line.x0 + gl[-1].x1
            w = gl[-1].x1 - gl[0].x0
            if abs(2 * (x1 - c_px) - w - L * s) > self.profile.data["label_w_tol_css"] * s:
                return None, 0.0, "geometry_mismatch(clipped_or_foreign_text)"
        r = read_glyphs(gl, exp, self.profile.glyphs, allowed=NUM_ALPHABET, **({"min_conf": -1.0, "max_dist": 1e9} if self.naive else {"min_conf": self.profile.data.get("digit_min_conf", 0.30), "max_dist": self.profile.data.get("digit_max_dist", 3.5)}))
        if r.text is None:
            return None, r.confidence, r.reason
        text = r.text
        if allow_dollar:
            if not text.startswith("$"):
                return None, r.confidence, "no_dollar_prefix"
        elif text.startswith("$"):
            return None, r.confidence, "unexpected_dollar"
        m = parse_money(text)
        if m is None:
            return None, r.confidence, "malformed_number"
        return m, r.confidence, ""

    def _read_texts(self, frame: Frame, st: TableState, a: dict, col, cx):
        P, s, top, t = self.profile.data, a["s"], a["top"], st.t_ms
        U = lambda why: Field.unknown(t, SRC, why)
        H, W = frame.h, frame.w
        c = cx if cx is not None else st.quality.get("board_cx")
        tol_y = P["field_y_tol_css"] * s

        def narrow(zone):
            if c is None:
                return zone
            return (max(int(c - 130 * s), zone[0]), zone[1], min(int(c + 130 * s), zone[2]), zone[3])

        def field_from(zone, key, ycenter_dy, conflict_name):
            zone = narrow(zone)
            """Exactly one acceptable numeric line centred near the calibrated y; none -> unknown; several -> conflict."""
            cands, reasons = [], []
            for ln in self._lines(frame, zone, P["num_zone_h_css"][key], s):
                m, conf, why = self._numeric(ln, key, s, c_px=c)
                if abs(self._last_cy - (top + ycenter_dy * s)) > tol_y:
                    continue
                if m is not None:
                    cands.append((m, conf))
                    st.quality.setdefault("boxes", []).append({"field": conflict_name, "x": int(ln.x0), "y": int(ln.y0), "w": int(ln.x1 - ln.x0), "h": int(ln.y1 - ln.y0), "ok": True, "conf": round(conf, 2), "label": m.raw})
                else:
                    reasons.append(why)
            if not cands:
                return Field.unknown(t, SRC, f"{conflict_name}: " + (reasons[0] if reasons else "not_found"))
            if len({round(m.amount) for m, _ in cands}) > 1:
                return Field.unknown(t, SRC, f"{conflict_name}: conflicting_reads")
            return Field.ok(cands[0][0], min(c_ for _, c_ in cands), t, SRC)

        zi = (0, max(int(top + P["info_dy"][0] * s), 0), W, max(int(top + P["info_dy"][1] * s), 1))
        st.pot = field_from(zi, "pot", P["cy_dy"]["pot"], "pot")
        st.to_call = field_from(zi, "to_call", P["cy_dy"]["to_call"], "to_call")
        zs = (0, int(top + P["stack_dy"][0] * s), W, min(int(top + P["stack_dy"][1] * s), H))
        st.hero_stack = field_from(zs, "hero_stack", P["cy_dy"]["hero_stack"], "hero_stack")
        # table zone: seat stacks ("$...") and bets (plain numbers)
        zt = (0, max(int(top + P["table_dy"][0] * s), 0), W, int(top + P["table_dy"][1] * s))
        if c is None:
            st.seats, st.dealer_slot, st.num_seats = [], U("table_center_unknown"), U("table_center_unknown")
            return
        tc = (c, top + P["table_center_dy"] * s)
        seats, bets, unreadable = [], [], 0
        for ln in self._lines(frame, zt, P["num_zone_h_css"]["seat"], s):
            if abs(ln.cy - (top + P["cy_dy"]["pot"] * s)) < 14 * s or abs(ln.cy - (top + P["cy_dy"]["to_call"] * s)) < 8 * s:
                continue                        # pot / to-call lines belong to the centre block
            ang = _angle(ln.cx - tc[0], ln.cy - tc[1])
            m, conf, why = self._numeric(ln, "seat", s, allow_dollar=True)
            if m is not None:
                seats.append((ang, m, conf, ln))
                st.quality.setdefault("boxes", []).append({"field": "seat_stack", "x": int(ln.x0), "y": int(ln.y0), "w": int(ln.x1 - ln.x0), "h": int(ln.y1 - ln.y0), "ok": True, "conf": round(conf, 2), "label": m.raw}); continue
            mb, confb, whyb = self._numeric(ln, "bet", s)
            if mb is not None and (self.naive or self._chip_score(frame.bgr, ln, s) >= P["bet_chip_min"]):
                bets.append((ang, mb, confb, ln))
                st.quality.setdefault("boxes", []).append({"field": "bet", "x": int(ln.x0), "y": int(ln.y0), "w": int(ln.x1 - ln.x0), "h": int(ln.y1 - ln.y0), "ok": True, "conf": round(confb, 2), "label": mb.raw}); continue
            if mb is not None:
                unreadable += 1          # a bare number without a chip icon: could be a seat stack whose "$" is covered
                continue
            unreadable += 1
        slots = P["slot_angles"]
        def slot_of(ang, tol):
            j = min(range(len(slots)), key=lambda i: _adiff(slots[i], ang))
            return j if _adiff(slots[j], ang) <= tol else None
        seat_objs: dict[int, Seat] = {}
        for ang, m, conf, ln in seats:
            j = slot_of(ang, P["slot_tol_deg"])
            if j is None:
                continue
            seat_objs.setdefault(j, Seat(j)).stack = Field.ok(m, conf, t, SRC)
            seat_objs[j].occupied = Field.ok(True, conf, t, SRC)
        for ang, m, conf, ln in bets:
            j = slot_of(ang, P["bet_tol_deg"])
            if j is None:
                continue
            seat_objs.setdefault(j, Seat(j)).bet = Field.ok(m, conf, t, SRC)
        chip, why = self._dealer_chip(frame, zt, s, P)
        if chip is None:
            st.dealer_slot = U(why)
        else:
            pills = [(slot_of(ang, P["slot_tol_deg"]), ln) for ang, m, conf, ln in seats if slot_of(ang, P["slot_tol_deg"]) is not None]
            hero_xy = (c, top + (P["hero_h_css"] / 2) * s)
            st.dealer_slot = self._dealer(chip, pills, hero_xy, tc, slots, s, P, t)
            st.quality.setdefault("boxes", []).append({"field": "dealer_chip", "x": int(chip[0] - 10 * s), "y": int(chip[1] - 10 * s), "w": int(20 * s), "h": int(20 * s), "ok": st.dealer_slot.known, "label": st.dealer_slot.value if st.dealer_slot.known else st.dealer_slot.reason})
        st.seats = [seat_objs[k] for k in sorted(seat_objs)]
        visible = sum(1 for sd in st.seats if sd.stack.known)
        st.num_seats = Field.ok(visible, 0.6 if unreadable == 0 else 0.4, t, SRC)
        st.quality["unreadable_lines"] = unreadable

    def _dealer_chip(self, frame, zone, s, P):
        """Pixel centre of the dealer chip (gold disc holding one dark letter), or (None, reason)."""
        x0, y0, x1, y1 = zone
        sub = frame.bgr[max(y0, 0):y1, x0:x1]
        if sub.size == 0:
            return None, "no_zone"
        hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
        m = ((hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 35) & (hsv[:, :, 1] > 150) & (hsv[:, :, 2] > 170)).astype(np.uint8)
        n, lab, stats, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
        D = P["dealer_css_d"] * s
        cands = []
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if abs(w / D - 1) < 0.30 and abs(h / D - 1) < 0.30 and area / (w * h) > 0.60 and self._is_D(sub, x, y, w, h):
                cands.append((x0 + cen[i][0], max(y0, 0) + cen[i][1]))
        if len(cands) != 1:
            return None, "dealer_chip_not_found" if not cands else "dealer_chip_ambiguous"
        return cands[0], ""

    def _dealer(self, chip, seat_pills, hero_xy, tc, slots, s, P, t) -> Field:
        """The chip sits on the seat's avatar, a fixed vector away from that seat's stack pill (learned at calibration).
        The seat is the slot of the predicted pill position, so a clipped/unreadable pill does not hide the dealer."""
        (cx, cy) = chip
        d_hero = math.hypot(hero_xy[0] - cx, hero_xy[1] - cy) if hero_xy else 1e9
        cand = set()
        for ox, oy in P["dealer_offsets"]:
            px, py = cx - ox * s, cy - oy * s                      # where the seat's stack pill should be
            ang = _angle(px - tc[0], py - tc[1])
            j = min(range(len(slots)), key=lambda i: _adiff(slots[i], ang))
            if _adiff(slots[j], ang) <= P["slot_tol_deg"]:
                cand.add(j)
        if len(cand) == 1:
            return Field.ok(next(iter(cand)), 0.7, t, SRC)
        if len(cand) > 1:
            return Field.unknown(t, SRC, "dealer_chip_matches_several_seats")
        if d_hero <= P["dealer_hero_css"] * s:
            return Field.ok("hero", 0.6, t, SRC)
        return Field.unknown(t, SRC, "dealer_chip_not_attached_to_a_seat")

    def _is_D(self, sub, x, y, w, h) -> bool:
        """The chip is gold with a dark letter in it; require exactly one dark blob of letter size (rejects gold avatars/coins)."""
        crop = sub[y:y + h, x:x + w]
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        dark = (g < 90).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
        blobs = [stats[i] for i in range(1, n) if stats[i][4] >= 4 and stats[i][3] >= 0.35 * h]
        return len(blobs) == 1 and 0.2 * w <= blobs[0][2] <= 0.7 * w

    # ---- actions
    def _read_actions(self, frame: Frame, st: TableState, a: dict, col):
        """Find the action-button band by its saturated middle button, read every label line inside the band, and
        pair amounts with the nearest label. No fixed window width is assumed."""
        P, s, top, t = self.profile.data, a["s"], a["top"], st.t_ms
        U = lambda why: Field.unknown(t, SRC, why)
        y_start = int(top + P["action_dy0"] * s)
        if y_start >= frame.h - 20:
            st.hero_turn, st.actions = U("below_frame"), U("below_frame")
            return
        c = float(np.mean([b.cx for b in a["boxes"]])) if len(a["boxes"]) == 2 else st.quality.get("board_cx")
        if c is None:
            st.hero_turn, st.actions = U("center_unknown"), U("center_unknown")
            return
        mid = frame.bgr[y_start:, max(int(c - 0.12 * 520 * s), 0):int(c + 0.12 * 520 * s)]
        hsv = cv2.cvtColor(mid, cv2.COLOR_BGR2HSV)
        satrow = ((hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 40)).mean(1)
        ys = np.where(satrow > 0.55)[0]
        run = None
        if len(ys):
            runs, start = [], ys[0]
            for p_, q in zip(ys, ys[1:]):
                if q != p_ + 1:
                    runs.append((start, p_)); start = q
            runs.append((start, ys[-1]))
            runs = [r for r in runs if (r[1] - r[0]) >= 0.45 * P["action_btn_h_css"] * s]
            run = max(runs, key=lambda r: r[1] - r[0]) if runs else None
        if run is None:
            st.hero_turn = Field.ok(False, 0.55, t, SRC)      # no button band: not hero's turn (or buttons hidden)
            st.actions = Field.ok([], 0.55, t, SRC)
            return
        by0, by1 = y_start + run[0], y_start + run[1]
        st.quality["band"] = (int(by0), int(by1), float(s))
        zone = (0, by0 - int(2 * s), frame.w, by1 + int(2 * s))
        lines = spot_lines(frame.bgr, zone, (P["action_h_css"][0] * s, P["action_h_css"][1] * s), v_min=P["action_v_min"], max_gap=1.6, otsu=True)
        # 1) labels: whole-word matches against the fixed button vocabulary; 2) amounts: a digit line directly under a label
        words, rest = [], []
        for ln in lines:
            w, wconf, wd = self.profile.words.classify(ln.glyphs)
            wide = (ln.x1 - ln.x0) >= 2.2 * ln.height                      # labels are words; an amount like "10" is not wide enough to be one
            if w is not None and wide and wconf >= 0.12 and wd <= self.profile.data.get("word_max_dist", 3.5):
                words.append((ln.cx, ln.cy, ln.height, w, wconf, ln))
            else:
                rest.append(ln)
        toks = [(x, y, h, w, c) for x, y, h, w, c, _ in words]
        used = set()
        for x, y, h, w, c, lnw in words:
            under = [ln for ln in rest if id(ln) not in used and 0 < ln.cy - y < 3.2 * h and abs(ln.cx - x) < 2.5 * h]
            if not under:
                continue
            ln = min(under, key=lambda q: q.cy)
            r = read_glyphs(ln.glyphs, ln.height, self.profile.glyphs, allowed=NUM_ALPHABET, min_conf=0.2, max_dist=4.0)
            if r.text:
                m_ = parse_money(r.text)
                if m_:
                    toks.append((ln.cx, ln.cy, ln.height, m_.raw, r.confidence)); used.add(id(ln))
        unreadable_lines = len(rest) - len(used)
        toks.sort()
        labels = [(x, y, txt, conf) for x, y, h, txt, conf in toks if txt in ACTION_VOCAB]
        nums = [(x, y, txt) for x, y, h, txt, conf in toks if txt[:1].isdigit()]
        junk = [txt for x, y, h, txt, conf in toks if txt not in ACTION_VOCAB and not txt[:1].isdigit()]
        if junk or not labels:
            return self._actions_unknown(st, "button_vocabulary_mismatch" if toks else "button_text_unreadable")
        acts = []
        for x, y, txt, conf in labels:
            amt = None
            below = [(abs(nx - x), ntxt) for nx, ny, ntxt in nums if ny > y and abs(nx - x) < 40 * s]
            if below:
                amt = parse_money(min(below)[1])
                if amt is None:
                    return self._actions_unknown(st, "amount_malformed")
            value = amt.amount if amt else None
            if value is not None and ACTION_VOCAB[txt] == "CALL" and st.to_call.known and abs(st.to_call.value.amount - value) > max(st.to_call.value.step, 1.0):
                # two independent reads of the same number (the "To call" field and the CALL button) disagree: trust neither
                st.to_call = Field.unknown(st.t_ms, SRC, f"to_call_disagrees_with_CALL_button ({st.to_call.value.raw} vs {value:g})")
                return self._actions_unknown(st, "CALL_amount_disagrees_with_to_call_field")
            if value is None and ACTION_VOCAB[txt] == "CALL" and st.to_call.known:
                value = st.to_call.value.amount            # the amount on the button is tiny; the "To call" field says the same thing
            acts.append((ACTION_VOCAB[txt], value))
        names = [a for a, _ in acts]
        complete = (len(acts) == 3 and names[0] == "FOLD" and names[1] in ("CHECK", "CALL", "ALL IN")
                    and names[2] in ("RAISE", "CONFIRM", "ALL IN")
                    and all(amt is not None for a, amt in acts if a in ("CALL", "ALL IN")))
        st.hero_turn = Field.ok(True, 0.8, t, SRC) if "FOLD" in names else Field.unknown(t, SRC, "band_without_fold_button")
        if not complete and not self.naive:
            st.actions = Field.unknown(t, SRC, f"incomplete_button_set {names}")
            return
        st.actions = Field.ok(acts, min(0.8, min(c for _, _, _, c in labels)), t, SRC)
        # click targets: the button is a full-height cell around its label; the box is the label line widened sideways (always inside
        # the button) and as tall as the detected button band. Used by the executor/locator and drawn by the Bossman overlay.
        btns = []
        for x, y, h, w, c, lnw in words:
            lab = ACTION_VOCAB.get(w)
            if lab is None:
                continue
            ww = lnw.x1 - lnw.x0
            bx0 = int(lnw.x0 - 0.5 * ww); bx1 = int(lnw.x1 + 0.5 * ww)
            amt_ = next((a_[1] for a_ in acts if a_[0] == lab), None)
            btns.append({"label": lab, "x": bx0, "y": int(by0), "w": bx1 - bx0, "h": int(by1 - by0), "amount": amt_, "conf": round(float(c), 3)})
            st.quality.setdefault("boxes", []).append({"field": "button", "x": bx0, "y": int(by0), "w": bx1 - bx0, "h": int(by1 - by0), "ok": True,
                                                       "label": lab + (f" {amt_:g}" if amt_ is not None else "")})
        st.quality["buttons"] = btns

    def _looks_numeric(self, ln: TextLine) -> bool:
        """Cheap pre-test: digit lines are not wider than ~0.8 x height per glyph and have no tall letter-like variance."""
        return all(g.w <= 1.1 * ln.height for g in ln.glyphs) and ln.y1 - ln.y0 <= 1.6 * ln.height and self.profile.glyphs.classify(ln.glyphs[0], NUM_ALPHABET)[1] >= 0.3

    def _actions_unknown(self, st, why):
        st.hero_turn = Field.unknown(st.t_ms, SRC, why)
        st.actions = Field.unknown(st.t_ms, SRC, why)

    # ------------------------------------------------------------------ calibration
    def calibrate(self, labelled, hero_card_css_w: float = 56.0, source_note: str = "") -> Profile:
        return calibrate_profile(labelled, source_note)


# ============================================================================ calibration from labelled frames
def _px(r: dict, dpr: float) -> tuple[int, int, int, int]:
    return int(r["x"] * dpr), int(r["y"] * dpr), int((r["x"] + r["w"]) * dpr), int((r["y"] + r["h"]) * dpr)


def calibrate_profile(labelled, source_note: str = "") -> Profile:
    """Learn layout offsets (CSS units), glyph and card exemplars from labelled frames. Uses stable frames only.

    Everything is measured from the labelled TRAIN frames: card size/pitch, where the number lines sit relative to the
    hero's cards, digit heights per field, seat slot angles, glyph and card exemplars."""
    gb, cb, wb = GlyphBook(max_per_class=40), CardBook(max_per_class=60), WordBook()
    keys = ("hero_w", "hero_h", "board_dy", "board_w", "board_h", "board_pitch", "seat_ang", "bet_ang", "dealer_d", "btn_h",
            "table_center_dy", "pot_dy", "call_dy", "stack_dy_c")
    meas: dict[str, list] = {k: [] for k in keys}
    heights: dict[str, list] = {k: [] for k in ("pot", "to_call", "hero_stack", "seat", "bet")}
    NUM_V_MIN = 150
    L_meas: dict[str, list] = {"pot": [], "to_call": [], "hero_stack": []}
    dealer_vecs: list = []
    n_dealer_frames = 0
    n_used = 0

    def spot_in(img, rect, dpr, hr, pad=3, **kw):
        x0, y0, x1, y1 = _px(rect, dpr)
        p_ = int(pad * dpr)
        zone = (max(x0 - p_, 0), max(y0 - p_, 0), min(x1 + p_, img.shape[1]), min(y1 + p_, img.shape[0]))
        return spot_lines(img, zone, (hr[0] * dpr, hr[1] * dpr), **kw)

    def learn_number(img, rect, text, dpr, key, hr, with_label=False, c_px=None):
        """text = the characters of the *number* (no label). ``with_label``: line also holds label glyphs + a colon."""
        if rect is None or not text:
            return
        lines = spot_in(img, rect, dpr, hr, v_min=NUM_V_MIN)
        if len(lines) != 1:
            return
        gl = list(lines[0].glyphs)
        if with_label:
            gl = numeric_suffix(gl, 0.9 * lines[0].height)
        chars = [c for c in text if c != " "]
        if len(gl) != len(chars):
            return
        digit_h = [g.h for g, ch in zip(gl, chars) if ch.isdigit()]
        for g, ch in zip(gl, chars):
            if ch in ",.":
                if g.h > 0.45 * max(digit_h or [g.h + 1]):
                    return
                continue
            gb.add(ch, g, key)
        if digit_h:
            heights[key].append(float(np.median(digit_h)) / dpr)
        if c_px is not None and key in L_meas:
            x1 = lines[0].x0 + gl[-1].x1
            w = gl[-1].x1 - gl[0].x0
            L_meas[key].append((2 * (x1 - c_px) - w) / dpr)

    for lb in labelled:
        t = lb.truth
        if not lb.row["stable"] or t["animating"] or len(t["hero_cards"]) != 2:
            continue
        dpr = t["dpr"]
        img = lb.frame.bgr
        hero = t["hero_cards"]
        # the anchor is what the DETECTOR sees (white blob incl. border), not the DOM rect: measure offsets from it
        hb = []
        for hc in hero:
            x0_, y0_, x1_, y1_ = _px(hc, dpr)
            bx = find_card_boxes(img, (max(x0_ - 8, 0), max(y0_ - 10, 0), min(x1_ + 8, img.shape[1]), min(y1_ + 10, img.shape[0])), int(0.8 * hc["w"] * dpr), int(1.2 * hc["w"] * dpr))
            if len(bx) == 1 and bx[0].visible_frac > 0.9:
                hb.append(bx[0])
        if len(hb) != 2:
            continue
        top_css = float(np.median([b.y for b in hb])) / dpr
        hc_px = float(np.mean([b.cx for b in hb]))
        n_used += 1
        meas["hero_w"].append(float(np.median([b.w for b in hb])) / dpr); meas["hero_h"].append(float(np.median([b.h for b in hb])) / dpr)
        bb = []
        for bc in t["board"]:
            x0_, y0_, x1_, y1_ = _px(bc, dpr)
            bx = find_card_boxes(img, (max(x0_ - 8, 0), max(y0_ - 10, 0), min(x1_ + 8, img.shape[1]), min(y1_ + 10, img.shape[0])), int(0.8 * bc["w"] * dpr), int(1.2 * bc["w"] * dpr))
            if len(bx) == 1 and bx[0].visible_frac > 0.9:
                bb.append(bx[0])
        for b in bb:
            meas["board_w"].append(b.w / dpr); meas["board_h"].append(b.h / dpr); meas["board_dy"].append(b.y / dpr - top_css)
        for k in range(len(bb) - 1):
            meas["board_pitch"].append((bb[k + 1].x - bb[k].x) / dpr)
        cx = t["vw"] / 2
        tc_y = (t["board"][0]["y"] + t["board"][0]["h"] / 2) if t["board"] else None
        if bb:
            tc_y = bb[0].cy / dpr
            meas["table_center_dy"].append(tc_y - top_css)
        if t.get("pot_vis", 1) >= 0.99 and t.get("pot_text"):
            meas["pot_dy"].append(t["pot_rect"]["cy"] - top_css)
            learn_number(img, t["pot_rect"], t["pot_text"], dpr, "pot", (6.0, 26.0), c_px=hc_px)
        if t.get("to_call_vis", 1) >= 0.99 and t.get("to_call_text"):
            meas["call_dy"].append(t["to_call_rect"]["cy"] - top_css)
            learn_number(img, t["to_call_rect"], t["to_call_text"], dpr, "to_call", (5.0, 18.0), c_px=hc_px)
        if t.get("hero_stack_vis", 1) >= 0.99 and t.get("hero_stack_text"):
            meas["stack_dy_c"].append(t["hero_stack_line_rect"]["cy"] - top_css)
            learn_number(img, t["hero_stack_line_rect"], t["hero_stack_text"], dpr, "hero_stack", (5.0, 16.0), with_label=True, c_px=hc_px)
        for sd in t["seats"]:
            if sd.get("vis", 1) < 0.99:
                continue
            learn_number(img, sd, sd["text"], dpr, "seat", (4.0, 16.0))
            if tc_y is not None:
                meas["seat_ang"].append(_angle(sd["cx"] - cx, sd["cy"] - tc_y))
        for b in t["bets"]:
            if b.get("vis", 1) < 0.99:
                continue
            learn_number(img, b, b["text"], dpr, "bet", (4.0, 16.0))
            if tc_y is not None:
                meas["bet_ang"].append(_angle(b["cx"] - cx, b["cy"] - tc_y))
        if t["dealer"]:
            meas["dealer_d"].append(t["dealer"]["w"])
            for sd in t["seats"]:
                if sd.get("vis", 1) >= 0.99:
                    dealer_vecs.append((t["dealer"]["cx"] - sd["cx"], t["dealer"]["cy"] - sd["cy"]))
            n_dealer_frames += 1
        for c in hero + t["board"]:
            if c.get("vis_corner", 1) < 0.99:
                continue
            x0, y0, x1, y1 = _px(c, dpr)
            pad = int(8 * dpr)
            zone = (max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, img.shape[1]), min(y1 + pad, img.shape[0]))
            boxes = find_card_boxes(img, zone, int(0.8 * c["w"] * dpr), int(1.2 * c["w"] * dpr))
            if len(boxes) == 1 and boxes[0].visible_frac > 0.95:
                cb.add(c["card"], boxes[0], img)
        vocab = [b for b in t["buttons"] if b["label"] in ("FOLD", "CHECK", "CALL", "RAISE", "ALL IN", "CONFIRM")]
        if vocab:
            maxy = max(b["y"] for b in vocab)
            for b in (b for b in vocab if b["y"] >= maxy - 12):
                meas["btn_h"].append(b["h"])
                lab = b["label"].replace(" ", "")
                lns = spot_lines(img, _px(b, dpr), (5.0 * dpr, 20.0 * dpr), otsu=True, max_gap=1.6)
                bx0, by0_, bx1, by1_ = _px(b, dpr)
                lns = [q for q in lns if abs(q.cx - (bx0 + bx1) / 2) < 0.22 * (bx1 - bx0)]       # the label is centred in its button (not the floating P/F badge)
                if lns:
                    ln = min(lns, key=lambda q: q.y0)
                    chars = list(lab)
                    wb.add(canon(lab), ln.glyphs)                     # the whole label as one raster (robust to merged/odd glyphs)
                    if b.get("amount") is not None:                   # the small white amount under CALL / ALL IN: learn its own digit shapes
                        below = [q for q in lns if q.cy > ln.cy + 0.6 * ln.height]
                        if below:
                            ln2 = min(below, key=lambda q: q.cy)
                            txt = f"{int(b['amount']):,}"
                            if len(ln2.glyphs) == len([c for c in txt if c != ","]) + txt.count(","):
                                for g, ch in zip(ln2.glyphs, txt):
                                    if ch not in ",.":
                                        gb.add(ch, g, "btnamt")
                    if len(ln.glyphs) == len(chars):
                        for g, ch in zip(ln.glyphs, chars):
                            gb.add(ch, g, 'btn')
    med = lambda k, d=0.0: float(np.median(meas[k])) if meas[k] else d

    def cluster(angs, tol=7.0):
        angs = sorted(angs); out: list[list[float]] = []
        for a in angs:
            if out and abs(a - np.mean(out[-1])) <= tol:
                out[-1].append(a)
            else:
                out.append([a])
        return [float(np.median(c)) for c in out if len(c) >= 3]
    slots = cluster(meas["seat_ang"] + meas["bet_ang"])
    # the dealer chip is drawn on the seat's avatar: learn the chip-minus-pill vectors that occur repeatedly
    from collections import Counter as _C
    vc = _C((round(v[0] / 4) * 4, round(v[1] / 4) * 4) for v in dealer_vecs)
    top_n = vc.most_common(1)[0][1] if vc else 0
    dealer_offsets = [[float(k[0]), float(k[1])] for k, n in vc.most_common(3) if n >= max(6, 0.35 * top_n)]
    hcss = {k: (float(np.median(v)) if v else 8.0) for k, v in heights.items()}
    pot_dy, call_dy, stack_dy = med("pot_dy", -236.0), med("call_dy", -220.0), med("stack_dy_c", 98.0)
    data = {
        "version": 2, "app": "poker_train", "note": source_note, "n_calibration_frames": n_used,
        "hero_card_css_w": med("hero_w", 56.0), "hero_h_css": med("hero_h", 80.0),
        "board_dy_css": med("board_dy", -212.0), "board_card_css_w": med("board_w", 54.0), "board_card_h_css": med("board_h", 76.0),
        "board_pitch_css": med("board_pitch", 62.0),
        "num_v_min": NUM_V_MIN, "digit_min_conf": 0.30, "digit_max_dist": 3.5, "num_h_css": hcss, "num_h_tol": 0.22, "field_y_tol_css": 14.0,
        "cy_dy": {"pot": pot_dy, "to_call": call_dy, "hero_stack": stack_dy},
        "label_w_css": {k: (float(np.median(v)) if v else None) for k, v in L_meas.items()}, "label_w_tol_css": 5.0,
        "info_dy": [pot_dy - 24.0, call_dy + 14.0],
        "num_zone_h_css": {"pot": [hcss["pot"] * 0.6, hcss["pot"] * 1.6], "to_call": [hcss["to_call"] * 0.6, hcss["to_call"] * 1.6],
                           "hero_stack": [hcss["hero_stack"] * 0.6, hcss["hero_stack"] * 1.6], "seat": [4.0, 14.0]},
        "stack_dy": [stack_dy - 16.0, stack_dy + 16.0],
        "table_dy": [-440.0, -30.0], "table_center_dy": med("table_center_dy", -172.0),
        "slot_angles": slots, "slot_tol_deg": 9.0, "bet_tol_deg": 14.0, "dealer_tol_deg": 20.0, "bet_chip_min": 0.12,
        "dealer_css_d": med("dealer_d", 20.0), "dealer_offsets": dealer_offsets, "dealer_off_tol_css": 9.0, "dealer_hero_css": 90.0,
        "action_dy0": 60.0, "action_btn_h_css": med("btn_h", 54.0), "action_h_css": [6.0, 20.0], "action_v_min": 150, "word_max_dist": 3.5,
    }
    data["id"] = "poker_train@" + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:8]
    return Profile(data, gb, cb, wb)
