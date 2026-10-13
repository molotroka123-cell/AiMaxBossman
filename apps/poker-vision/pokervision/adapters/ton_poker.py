"""Adapter #2: TON-style poker table inside a phone recording — OBSERVE and REPLAY only.

No layout is shipped: the owner/labeller supplies normalised regions and a few human-labelled frames, exemplars are
learned from them, and the profile stays ``verified=False`` (every field UNKNOWN) until it passes held-out verification.
This adapter can never act and never gives live advice: connecting an external client does not grant betting or any
bypass of its restrictions.

Reading (once verified), all deterministic and CPU-only:

* board    5 fixed slots; a slot is ``card`` / ``empty`` / ``unclear`` (dimmed, flipping, covered, backs). The count is only
           claimed when the slots are cards followed by empties and the number is 0 / 3 / 4 / 5.
* hero     the two FANNED cards are read from their index corners (the fan merges into one white blob for a card finder).
* pot, hero stack   glyph exemplars, amounts with cents (``money_format = cash2``).
* buttons  white label text per slot + yellow amounts: three buttons = hero's turn, a lone pre-selected button = not.

Anything that is not confidently recognised is UNKNOWN; a rank or suit never seen on a labelled frame is UNKNOWN too.
"""
from __future__ import annotations

import hashlib

import cv2
import numpy as np

from ..parse import parse_cash, parse_money
from ..schema import Field, TableState
from ..vision import corner as corner_mod
from ..vision.buttons import LabelBook, read_amount, read_band, slot_vector, yellow_glyphs
from ..vision.cards import find_card_boxes
from ..vision.corner import CornerBook, DigitBook, digit_vec
from ..vision.glyphs import GlyphBook, is_sep
from ..vision.pipshape import suit_from_centre_pip
from ..vision.textspot import spot_lines
from .base import Capabilities, Frame
from .roi import RoiAdapter, RoiProfile, _px


def _felt(sub: np.ndarray) -> np.ndarray:
    b, g, r = sub[:, :, 0].astype(int), sub[:, :, 1].astype(int), sub[:, :, 2].astype(int)
    return (g > r + 18) & (g > b + 8)


def _white(sub: np.ndarray) -> np.ndarray:
    mx, mn = sub.max(2).astype(int), sub.min(2).astype(int)
    return (mn > 190) & (mx - mn < 40)


class TonPokerAdapter(RoiAdapter):
    id = "ton_poker"
    title = "TON-style poker table (phone recording) — observe/replay only"
    capabilities = Capabilities(observe=True, replay=True, act=False, advise_live=False, multi_table=False,
                                reads=("hero_cards", "board", "pot", "hero_stack", "hero_turn", "actions"),
                                notes="needs owner-provided recordings or an allowed test table; calibration + held-out verification required")
    decimals = 2
    MIN_TABLE_FELT = 0.30
    model_assist = None          # optional ``vision.model_assist.ModelAssist``: fills only fields left UNKNOWN (off by default)

    def parse_amount(self, text: str):
        return parse_cash(text) or parse_money(text)

    # ------------------------------------------------------------------ profile access
    def _layout(self) -> dict | None:
        return self.profile.data.get("layout") if self.profile else None

    def _corner_book(self, width: int) -> tuple[CornerBook | None, str]:
        books = self.profile.data.get("corner_books", {})
        if not books:
            return None, "no_corner_book"
        key = min(books, key=lambda k: abs(int(k) - width))
        if abs(int(key) - width) > 0.08 * width:
            return None, f"corner_book_not_for_width_{width}px(have {sorted(books)})"
        cache = self.__dict__.setdefault("_cb_cache", {})
        if key not in cache:
            cache[key] = CornerBook.from_json(books[key])
        return cache[key], ""

    def _digits(self) -> DigitBook | None:
        raw = self.profile.data.get("digit_book")
        cache = self.__dict__.setdefault("_db_cache", {})
        if not raw:
            return None
        if cache.get("key") != id(raw):
            cache["key"] = id(raw)
            cache["book"] = DigitBook.from_json(raw)
        return cache["book"]

    def _number(self, frame, roi, t, src, name) -> Field:
        """Cash amount of one ROI: exactly one text line; every digit must be CLOSE to an exemplar of its class (open-set)
        with a clear margin to the next class, the point is recognised by shape, the whole text must parse as cash."""
        book = self._digits()
        if book is None:
            return Field.unknown(t, src, f"{name}: no_digit_book")
        H, W = frame.h, frame.w
        x0, y0, x1, y1 = _px(roi, W, H)
        hh = y1 - y0
        lines = spot_lines(frame.bgr, (x0, y0, x1, y1), (0.25 * hh, 1.05 * hh), v_min=self.profile.data.get("v_min", 140))
        if len(lines) != 1:
            return Field.unknown(t, src, f"{name}: {len(lines)} text lines in ROI")
        ln = lines[0]
        text, confs = "", []
        gl = list(ln.glyphs)
        i = 0
        while i < len(gl):
            g = gl[i]
            if is_sep(g, ln.height):
                text += "."
                i += 1
                continue
            # a thin-stroke digit can fall apart into two touching pieces ("2" -> 4 px + 6 px): try the pair as ONE glyph first
            if i + 1 < len(gl) and not is_sep(gl[i + 1], ln.height):
                g2 = gl[i + 1]
                if g2.x0 - g.x1 <= 1 and (g2.x1 - g.x0) <= 0.75 * ln.height:
                    win = frame.bgr[max(0, ln.y0 + min(g.y0, g2.y0) - 1):ln.y0 + max(g.y1, g2.y1) + 1,
                                    max(0, ln.x0 + g.x0 - 1):ln.x0 + g2.x1 + 1]
                    vec = digit_vec(win)
                    if vec is not None:
                        ch, cf, _ = book.classify(vec)
                        if ch is not None:
                            text += ch
                            confs.append(cf)
                            i += 2
                            continue
            win = frame.bgr[max(0, ln.y0 + g.y0 - 1):ln.y0 + g.y1 + 1, max(0, ln.x0 + g.x0 - 1):ln.x0 + g.x1 + 1]
            vec = digit_vec(win)
            ch, cf, why = book.classify(vec) if vec is not None else (None, 0.0, "digit_unreadable")
            if ch is None:
                # two glued digits ("11" is one blob at this size): split at the ink valley, both halves must be recognised
                pair = self._split_glued(frame.bgr, ln, g, book)
                if pair is None:
                    return Field.unknown(t, src, f"{name}: {why}")
                text += pair[0]
                confs.append(pair[1])
                i += 1
                continue
            text += ch
            confs.append(cf)
            i += 1
        m = self.parse_amount(text)
        if m is None or not confs:
            return Field.unknown(t, src, f"{name}: malformed({text})")
        return Field.ok(m, float(min(confs)), t, src)

    @staticmethod
    def _split_glued(bgr, ln, g, book: DigitBook):
        if g.w < 0.75 * g.h:
            return None
        col = g.mask.sum(0).astype(float)
        lo, hi = int(0.30 * g.w), int(0.70 * g.w) + 1
        if hi - lo < 2:
            return None
        cut = lo + int(np.argmin(col[lo:hi]))
        out, confs = "", []
        for a, b in ((0, cut), (cut, g.w)):
            if b - a < 0.2 * g.h:
                return None
            win = bgr[max(0, ln.y0 + g.y0 - 1):ln.y0 + g.y1 + 1, max(0, ln.x0 + g.x0 + a - 1):ln.x0 + g.x0 + b + 1]
            vec = digit_vec(win)
            if vec is None:
                return None
            ch, cf, _ = book.classify(vec)
            if ch is None:
                return None
            out += ch
            confs.append(cf)
        return out, float(min(confs))

    def _label_books(self) -> dict[str, LabelBook]:
        cache = self.__dict__.setdefault("_lb_cache", {})
        raw = self.profile.data.get("button_books", {})
        key = id(raw)
        if cache.get("key") != key:
            cache["key"] = key
            cache["books"] = {k: LabelBook.from_json(v) for k, v in raw.items()}
        return cache["books"]

    # ------------------------------------------------------------------ geometry
    def _table_visible(self, bgr: np.ndarray) -> bool:
        H, W = bgr.shape[:2]
        sub = bgr[int(0.25 * H):int(0.62 * H), int(0.10 * W):int(0.90 * W)]
        return float(_felt(sub).mean()) >= self.MIN_TABLE_FELT

    def _board_slots(self, bgr: np.ndarray, spec: dict) -> list[tuple[str, tuple[int, int, int, int] | None]]:
        H, W = bgr.shape[:2]
        out = []
        for k in range(int(spec.get("n", 5))):
            x = int((spec["x0"] + k * spec["pitch"]) * W)
            y = int(spec["y"] * H)
            w = int(spec["w"] * W)
            h = int(spec["h"] * H)
            sub = bgr[y:y + h, x:x + w]
            if sub.size == 0:
                out.append(("unclear", None))
                continue
            if float(_felt(sub).mean()) > 0.80:
                out.append(("empty", None))
                continue
            white = _white(sub)
            ys, xs = np.where(white)
            if white.mean() >= 0.50 and len(xs) and (xs.max() - xs.min() + 1) >= 0.92 * w and (ys.max() - ys.min() + 1) >= 0.90 * h:
                out.append(("card", (x + int(xs.min()), y + int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))))
            else:
                out.append(("unclear", None))
        return out

    def _fan(self, bgr: np.ndarray, spec: dict) -> tuple[int, int, int] | None:
        """(x, y, card_w) of the left fanned card, or None when no bright two-card fan is on screen."""
        H, W = bgr.shape[:2]
        x0, y0, x1, y1 = [v for v in (spec["search"][0] * W, spec["search"][1] * H, spec["search"][2] * W, spec["search"][3] * H)]
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        sub = bgr[y0:y1, x0:x1]
        if sub.size == 0:
            return None
        mx, mn = sub.max(2).astype(int), sub.min(2).astype(int)
        white = (((mn > 170) & (mx - mn < 40)).astype(np.uint8))
        white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(white)
        if n < 2:
            return None
        i = 1 + int(np.argmax(st[1:, 4]))
        x, y, w, h, area = [int(v) for v in st[i]]
        cw = spec["w"] * W
        # a fan: about 1.7 card widths wide (flag stripe may add a little), clearly taller than a badge
        if not (1.45 * cw <= w <= 2.1 * cw) or h < 0.45 * (cw / corner_mod.CARD_ASPECT) or area < 0.4 * w * h:
            return None
        return x0 + x, y0 + y, int(round(cw))

    # ------------------------------------------------------------------ per-slot evidence (for the model-assist path)
    @staticmethod
    def _evidence(bgr: np.ndarray, c, book: CornerBook | None, x: int, y: int, w: int, centre_pip: bool) -> dict:
        """What the deterministic reader SAW on one card, field by field, even when the card as a whole is UNKNOWN:
        red/black from the rank glyph's ink, strict (open-set) rank and suit from the book, the relaxed nearest classes
        (no thresholds: evidence only, never a reading) and the suit of the large centre pip when it is visible."""
        ev = {"box": [int(x), int(y), int(w)], "corner_ok": c.rank is not None, "red": bool(c.red) if c.rank is not None else None,
              "red_conf": round(float(c.red_conf), 3), "rank": None, "suit": None, "rank_near": None, "suit_near": None,
              "pip_suit": None, "pip_why": "" if centre_pip else "not_used_on_this_card"}
        if book is not None and c.rank is not None:
            r, s, _, _ = book.read_parts(c)
            ev["rank"], ev["suit"] = r, s
            rk = book._best(book.rank, c.rank)
            ev["rank_near"] = rk[0][1] if rk else None
            if c.pip is not None:
                sk = book._best(book.suit, c.pip, "hd" if c.red else "sc")
                ev["suit_near"] = sk[0][1] if sk else None
        if centre_pip and c.rank is not None:
            ev["pip_suit"], ev["pip_why"] = suit_from_centre_pip(bgr, x, y, w, bool(c.red))
        return ev

    # ------------------------------------------------------------------ reading
    def _read_rois(self, frame: Frame, st: TableState) -> TableState:
        L = self._layout()
        if not L:
            return super()._read_rois(frame, st)
        t = st.t_ms
        src = f"{self.id}.pixels"
        U = lambda w: Field.unknown(t, src, w)
        bgr = frame.bgr
        H, W = bgr.shape[:2]
        if not self._table_visible(bgr):
            why = "table_not_visible(overlay/popup/transition)"
            st.hero_cards = [U(why), U(why)]
            for n in ("board_count", "street", "pot", "to_call", "hero_stack", "dealer_slot", "num_seats", "hero_turn",
                      "actions", "hero_position"):
                setattr(st, n, U(why))
            st.quality = {"refused": why}
            return st
        book, bwhy = self._corner_book(W)
        # ---- board
        slots = self._board_slots(bgr, L["board_slots"])
        board: list[Field] = []
        kinds = [s for s, _ in slots]
        n_cards = 0
        while n_cards < len(kinds) and kinds[n_cards] == "card":
            n_cards += 1
        rest_empty = all(k == "empty" for k in kinds[n_cards:])
        ev_board: list[dict] = []
        for k, (kind, box) in enumerate(slots):
            if kind != "card":
                break
            c = corner_mod.extract(bgr, box[0], box[1], box[2])
            ev_board.append(self._evidence(bgr, c, book, box[0], box[1], box[2], centre_pip=True))
            if book is None:
                board.append(U(bwhy))
                continue
            card, conf, why = book.read(c)
            board.append(Field.ok(card, conf, t, src) if card else Field.unknown(t, src, why, conf))
        st.board = board
        if rest_empty and n_cards in (0, 3, 4, 5):
            st.board_count = Field.ok(n_cards, 0.6, t, src)
            st.street = Field.ok({0: "preflop", 3: "flop", 4: "turn", 5: "river"}[n_cards], 0.6, t, src)
        else:
            why = "board_slots_" + "/".join(kinds)
            st.board_count, st.street = U(why), U(why)
        # ---- hero fan
        fan = self._fan(bgr, L["hero_fan"])
        ev_hero: list[dict] | None = None
        if fan is not None:
            fx, fy, cw = fan
            xs = (fx, fx + int(round(L["hero_fan"]["offset"] * cw)))
            corners = [corner_mod.extract(bgr, x, fy, cw) for x in xs]
            # no centre-pip evidence on the fan: the left card's centre is under the right card, and on the right card the
            # big-pip shape was measured once confidently wrong (7h read as d under a cover, 12.10 recording B frame 63)
            ev_hero = [self._evidence(bgr, c, book, x, fy, cw, centre_pip=False) for c, x in zip(corners, xs)]
        if fan is None:
            st.hero_cards = [U("hero_fan_not_found(dimmed/covered/absent)")] * 2
        elif book is None:
            st.hero_cards = [U(bwhy)] * 2
        else:
            hero = []
            for c in corners:
                card, conf, why = book.read(c)
                hero.append(Field.ok(card, conf, t, src) if card else Field.unknown(t, src, why, conf))
            if all(h.known for h in hero) and hero[0].value == hero[1].value:
                hero = [U("duplicate_hero_cards")] * 2
            st.hero_cards = hero
        st.quality["evidence"] = {"board": ev_board, "board_count": n_cards if (rest_empty and n_cards in (0, 3, 4, 5)) else None,
                                  "fan": fan is not None, "hero": ev_hero, "frame_w": int(W)}
        # ---- amounts
        for name in ("pot", "hero_stack"):
            roi = self.profile.data["rois"].get(name)
            setattr(st, name, self._number(frame, roi, t, src, name) if roi else U("roi_not_defined"))
        st.to_call = U("roi_not_defined")
        # ---- buttons
        if "buttons" in L and self._label_books():
            band = read_band(bgr, L["buttons"], self._label_books(), self._digits(), self.decimals)
            st.quality["buttons"] = {"state": band.state, "slots": band.slots, "why": band.why}
            if band.state == "turn":
                acts = []
                ok = True
                for label, amt in band.buttons:
                    money = self.parse_amount(amt) if amt else None
                    if label in ("CALL", "RAISE") and money is None:
                        ok = False
                    acts.append((label, money.amount if money else None))
                st.hero_turn = Field.ok(True, band.confidence, t, src)
                st.actions = Field.ok(acts, band.confidence, t, src) if ok else U("button_amount_unreadable: " + band.why)
            elif band.state in ("pre_action", "wait_for_blinds", "show_cards"):
                st.hero_turn = Field.ok(False, band.confidence, t, src)
                st.actions = U(f"no_decision_buttons({band.state})")
            else:
                st.hero_turn = U(f"buttons_{band.state}: {band.why}")
                st.actions = U(f"buttons_{band.state}")
        else:
            st.hero_turn = U("roi_not_defined")
            st.actions = U("roi_not_defined")
        for n in ("dealer_slot", "num_seats", "hero_position"):
            setattr(st, n, U("roi_not_defined"))
        if L.get("seats") and book is not None:
            st.showdown = self._read_showdown(frame, L, book, t)
        # second source, ONLY for fields left UNKNOWN; reached only past the profile gate and with the table visible
        if self.model_assist is not None:
            st = self.model_assist.apply(frame, st)
        return st

    def reset(self) -> None:
        if self.model_assist is not None:
            self.model_assist.reset()

    # ------------------------------------------------------------------ showdown capture
    def _read_showdown(self, frame: Frame, L: dict, book: CornerBook, t: int) -> list[dict]:
        """Face-up cards of OPPONENTS and the award label above a seat. A record exists only when something was actually
        seen: at least one readable rank, or an award amount. Mucked / face-down hands produce no record (never invented);
        a card whose rank or suit is not recognised is None (the other half may still be known)."""
        bgr = frame.bgr
        H, W = bgr.shape[:2]
        offs = L["hero_fan"]["offset"]
        digits = self._digits()
        out: list[dict] = []
        for seat in L["seats"]:
            px, py, pw, ph = seat["plate"]
            x0, y0 = max(0, int((px - 0.05) * W)), max(0, int((py - 0.095) * H))
            x1, y1 = min(W, int((px + pw + 0.05) * W)), min(H, int((py + 0.012) * H))
            sub = bgr[y0:y1, x0:x1]
            if sub.size == 0:
                continue
            mx, mn = sub.max(2).astype(int), sub.min(2).astype(int)
            white = ((mn > 170) & (mx - mn < 40)).astype(np.uint8)
            white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            n, lab, st_, _ = cv2.connectedComponentsWithStats(white)
            best = None
            for i in range(1, n):
                x, y, w, h, a = [int(v) for v in st_[i]]
                if w >= 0.035 * W and w >= 1.1 * h and a >= 0.5 * w * h and (best is None or a > best[4]):
                    best = (x, y, w, h, a)
            if best is None:
                continue
            fx, fy, fw, fh, _ = best
            cw = int(round(fw / (1 + offs)))
            parts = []
            for x in (x0 + fx, x0 + fx + int(round(offs * cw))):
                r, s_, conf, why = book.read_parts(corner_mod.extract(bgr, x, y0 + fy, cw))
                parts.append({"rank": r, "suit": s_, "card": (r + s_) if (r and s_) else None, "why": "" if (r and s_) else why})
            award = self._award(bgr, x0 + fx, y0 + fy, fw, cw, digits)
            if not any(p["rank"] for p in parts) and award is None:
                continue
            out.append({"slot": seat["slot"], "label": seat.get("name"), "label_source": "profile_by_position",
                        "cards": [p["card"] for p in parts], "ranks": [p["rank"] for p in parts],
                        "why_unknown": [p["why"] for p in parts if p["why"]], "award": award, "t_ms": t})
        return out

    def _award(self, bgr: np.ndarray, fx: int, fy: int, fw: int, cw: int, digits) -> float | None:
        """'+303.10' above the seat's cards (orange text). None when absent or any digit is not confidently read."""
        H, W = bgr.shape[:2]
        x0, x1 = max(0, fx - int(0.9 * cw)), min(W, fx + fw + int(0.9 * cw))
        y0, y1 = max(0, fy - int(1.5 * cw)), max(0, fy - 1)
        sub = bgr[y0:y1, x0:x1]
        if sub.size == 0 or digits is None:
            return None
        b, g, r = sub[:, :, 0].astype(int), sub[:, :, 1].astype(int), sub[:, :, 2].astype(int)
        m = (r > 170) & (g > 100) & (g < 215) & (b < 150) & (r - b > 55)
        if int(m.sum()) < 12:
            return None
        n, lab, st_, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
        comps = [tuple(int(v) for v in st_[i][:5]) for i in range(1, n) if st_[i][4] >= 4]
        if not comps:
            return None
        top = max(c[3] for c in comps)
        if not (0.25 * cw <= top <= 0.9 * cw):
            return None
        base = float(np.median([c[1] + c[3] for c in comps if c[3] >= 0.75 * top]) if any(c[3] >= 0.75 * top for c in comps) else 0)
        row = sorted((c for c in comps if c[3] >= 0.08 * top), key=lambda c: c[0])
        text = ""
        for x, y, w, h, a in row:
            if h >= 0.75 * top:
                win = sub[max(0, y - 1):y + h + 1, max(0, x - 1):x + w + 1]
                vec = digit_vec(win)
                if vec is None:
                    return None
                ch, _, _ = digits.classify(vec)
                if ch is None:
                    # the "+" is wider than tall and short: it is the first glyph, never a digit
                    if not text and 0.7 <= w / max(h, 1) <= 1.4 and h <= 0.9 * top:
                        continue
                    return None
                text += ch
            elif h <= 0.35 * top and w <= 0.5 * top and y + h >= base - 0.2 * top:
                text += "."                       # decimal point: small, on the digits' baseline
        digits_only = text.replace(".", "")
        if "." not in text and len(digits_only) >= 4:
            return None                           # a lost point would turn 77.28 into 7728: refuse instead of guessing
        m2 = parse_cash(text)
        return m2.amount if m2 is not None else None

    # ------------------------------------------------------------------ calibration
    @staticmethod
    def _align(glyphs, text: str):
        """[(char, glyph)] for the digit glyphs of ``text`` ('35.18'); None when the glyph count does not fit."""
        if not glyphs:
            return None
        med = float(np.median([g.h for g in glyphs if g.h >= 0.5 * max(q.h for q in glyphs)]))
        pairs, k = [], 0
        for g in glyphs:
            if is_sep(g, med):
                if k < len(text) and text[k] == ".":
                    k += 1
                    continue
                return None
            if k >= len(text) or text[k] == ".":
                return None
            pairs.append((text[k], g))
            k += 1
        return pairs if k == len(text) else None

    def calibrate(self, labelled, rois: dict, note: str = "") -> RoiProfile:
        """labelled: [(Frame, truth)]. truth keys used (each only when present):
        ``hero_cards`` [2], ``board`` [n] (only for frames whose state is clean), ``pot`` / ``hero_stack`` (strings),
        ``buttons`` [[LABEL_KEY, amount|None], ...] with ``btn_state``. Layout regions come from ``rois`` (labeller input)."""
        f0 = labelled[0][0]
        layout = {k: rois[k] for k in ("board_slots", "hero_fan", "buttons", "seats") if k in rois}
        legacy = {k: rois[k] for k in ("hero_cards", "board", "pot", "hero_stack") if k in rois}
        data = {"adapter": self.id, "rois": legacy, "layout": layout or None, "ref_size": [f0.w, f0.h], "verified": False,
                "note": note, "v_min": 140, "money_format": "cash2"}
        prof = RoiProfile(data)
        books: dict[str, CornerBook] = {}
        lbooks: dict[str, LabelBook] = {}
        digits = DigitBook()
        self.profile = prof
        for fr, truth in labelled:
            H, W = fr.h, fr.w
            bgr = fr.bgr
            # ---- digits
            for key in ("pot", "hero_stack"):
                txt = truth.get(key)
                if txt and key in legacy:
                    x0, y0, x1, y1 = _px(legacy[key], W, H)
                    lines = spot_lines(bgr, (x0, y0, x1, y1), (0.25 * (y1 - y0), 1.05 * (y1 - y0)), v_min=140)
                    if len(lines) == 1:
                        pairs = self._align(lines[0].glyphs, txt)
                        if pairs:
                            ln = lines[0]
                            for ch, g in pairs:
                                vec = digit_vec(bgr[max(0, ln.y0 + g.y0 - 1):ln.y0 + g.y1 + 1, max(0, ln.x0 + g.x0 - 1):ln.x0 + g.x1 + 1])
                                if vec is not None:
                                    digits.add(ch, vec)
            if not layout:
                continue
            # ---- cards (corner book per frame width)
            cb = books.setdefault(str(W), CornerBook())
            if truth.get("board") and "board_slots" in layout:
                slots = self._board_slots(bgr, layout["board_slots"])
                cards = truth["board"]
                if [s for s, _ in slots if s == "card"] == ["card"] * len(cards) and all(s == "empty" for s, _ in slots[len(cards):]):
                    for (kind, box), card in zip(slots, cards):
                        cb.add(card, corner_mod.extract(bgr, box[0], box[1], box[2]))
            if truth.get("hero_cards") and "hero_fan" in layout:
                fan = self._fan(bgr, layout["hero_fan"])
                if fan is not None:
                    fx, fy, cw = fan
                    for x, card in zip((fx, fx + int(round(layout["hero_fan"]["offset"] * cw))), truth["hero_cards"]):
                        cb.add(card, corner_mod.extract(bgr, x, fy, cw))
            # ---- buttons
            if "buttons" in layout and truth.get("btn_state"):
                self._train_buttons(prof, lbooks, digits, fr, layout["buttons"], truth)
        for cb in books.values():
            cb.fit_radii()
        for lb in lbooks.values():
            lb.fit()
        digits.fit()
        prof.data["digit_book"] = digits.to_json()
        prof.data["corner_books"] = {k: v.to_json() for k, v in books.items()}
        prof.data["button_books"] = {k: v.to_json() for k, v in lbooks.items()}
        prof.data["id"] = f"{self.id}@" + hashlib.sha256(repr(sorted((k, str(v)) for k, v in rois.items())).encode()).hexdigest()[:8]
        self.__dict__.pop("_cb_cache", None)
        self.__dict__.pop("_lb_cache", None)
        self.__dict__.pop("_db_cache", None)
        return prof

    def _train_buttons(self, prof, lbooks, digits, fr, layout, truth) -> None:
        state = truth["btn_state"]
        want: dict[str, str] = {}
        btn = [(str(a), b) for a, b in truth.get("buttons", [])]
        key = {"FOLD": "fold", "CHECK_FOLD": "check_fold", "CHECK": "check", "CALL": "call", "BET": "bet", "RAISE_TO": "raise_to"}
        if state == "turn" and len(btn) == 3:
            for kind, (name, _) in zip(("left", "mid", "right"), btn):
                want[kind] = key[name.upper()]
            want["pill"] = "empty"
        elif state == "pre_action" and len(btn) == 1:
            want = {"left": key[btn[0][0].upper()], "mid": "empty", "right": "empty", "pill": "empty"}
        elif state == "wait_for_blinds":
            want = {"left": "empty", "mid": "empty", "right": "empty", "pill": "wait_for_blinds"}
        elif state == "show_cards":
            want = {"left": "show", "mid": "show", "right": "show", "pill": "other"}
        elif state in ("none", "overlay", "status_text"):
            want = {k: "other" for k in ("left", "mid", "right", "pill")}
        for kind, cls in want.items():
            if kind not in layout:
                continue
            vec = slot_vector(fr.bgr, tuple(layout[kind]), kind)
            if vec is None:
                continue                              # empty slot: recognised as "empty" by having no text at all
            lbooks.setdefault(kind, LabelBook()).add(cls, vec)
        if state == "turn":
            from ..vision.buttons import slot_crop
            for kind, (name, amt) in zip(("mid", "right"), btn[1:]):
                if amt:
                    c = slot_crop(fr.bgr, tuple(layout[kind]), "mid")
                    gl = yellow_glyphs(c)
                    pairs = self._align(gl, amt)
                    if pairs:
                        for ch, g in pairs:
                            vec = digit_vec(c[max(0, g.y0 - 1):g.y1 + 1, max(0, g.x0 - 1):g.x1 + 1])
                            if vec is not None:
                                digits.add(ch, vec)
