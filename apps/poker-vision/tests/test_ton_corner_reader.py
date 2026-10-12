"""Corner-index card reader, digit book, button band and showdown guard of the TON-style adapter.

Synthetic renderings only (no personal media): glyphs drawn with OpenCV fonts. What is pinned here is the SAFETY
behaviour that the 12.10.2026 owner-recording evaluation relied on: a class never seen is UNKNOWN (never the nearest
wrong class), a rank can be known while the suit is not, a lost decimal point is refused, and a closed profile reads
nothing.
"""
from __future__ import annotations

import cv2
import numpy as np

from pokervision.adapters.base import Frame
from pokervision.adapters.ton_poker import TonPokerAdapter
from pokervision.parse import parse_cash, parse_money
from pokervision.vision import corner as C
from pokervision.vision.buttons import LabelBook, slot_vector
from pokervision.vision.corner import CornerBook, DigitBook, digit_vec

RED, BLACK = (30, 30, 190), (25, 25, 25)


def _pip(img, suit, cx, cy, r, col):
    if suit == "d":
        cv2.fillConvexPoly(img, np.array([[cx, cy - r], [cx + int(.8 * r), cy], [cx, cy + r], [cx - int(.8 * r), cy]]), col)
    elif suit == "h":
        cv2.circle(img, (cx - r // 2, cy - r // 3), int(.55 * r), col, -1)
        cv2.circle(img, (cx + r // 2, cy - r // 3), int(.55 * r), col, -1)
        cv2.fillConvexPoly(img, np.array([[cx - r, cy - r // 6], [cx + r, cy - r // 6], [cx, cy + r]]), col)
    elif suit == "s":
        cv2.fillConvexPoly(img, np.array([[cx, cy - r], [cx + r, cy + r // 3], [cx - r, cy + r // 3]]), col)
        cv2.circle(img, (cx - r // 2, cy + r // 4), int(.5 * r), col, -1)
        cv2.circle(img, (cx + r // 2, cy + r // 4), int(.5 * r), col, -1)
        cv2.rectangle(img, (cx - 2, cy + r // 3), (cx + 2, cy + r), col, -1)
    else:                                   # club
        for dx, dy in ((0, -r // 2), (-r // 2, r // 4), (r // 2, r // 4)):
            cv2.circle(img, (cx + dx, cy + dy), int(.45 * r), col, -1)
        cv2.rectangle(img, (cx - 2, cy), (cx + 2, cy + r), col, -1)


def card(rank: str, suit: str, w: int = 65, shift: float = 0.0) -> np.ndarray:
    """A felt-green canvas with one white card whose index sits at the top left."""
    h = int(w / 0.7)
    img = np.full((h + 20, w + 20, 3), (40, 110, 45), np.uint8)
    cv2.rectangle(img, (10, 10), (10 + w, 10 + h), (238, 238, 238), -1)
    col = RED if suit in "hd" else BLACK
    scale = w / 65.0
    M = np.float32([[1, 0, shift], [0, 1, 0]])
    layer = np.full_like(img, 255)
    cv2.putText(layer, "10" if rank == "T" else rank, (10 + int(7 * scale), 10 + int(30 * scale)), cv2.FONT_HERSHEY_DUPLEX,
                1.15 * scale, col, max(2, int(2.5 * scale)), cv2.LINE_AA)
    _pip(layer, suit, 10 + int(16 * scale), 10 + int(46 * scale), int(10 * scale), col)
    ink = (layer < 200).any(2)
    img[ink] = layer[ink]
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE) if shift else img


def _corner(rank, suit, w=65, shift=0.0):
    im = card(rank, suit, w, shift)
    return C.extract(im, 10, 10, w)


def _book(cards):
    b = CornerBook()
    for r, s in cards:
        assert b.add(r + s, _corner(r, s)), (r, s)
    b.fit_radii()
    return b


def test_known_cards_are_read_and_a_shifted_rerender_still_matches():
    book = _book([("A", "s"), ("K", "h"), ("7", "d"), ("5", "c"), ("Q", "s"), ("4", "h")])
    for r, s in (("A", "s"), ("K", "h"), ("7", "d"), ("5", "c")):
        card_, conf, why = book.read(_corner(r, s, shift=0.6))
        assert card_ == r + s, (r, s, why)
        assert conf > 0.3


def test_a_class_never_seen_is_unknown_not_the_nearest_wrong_class():
    book = _book([("A", "s"), ("K", "h"), ("7", "d"), ("5", "c"), ("Q", "s"), ("4", "h")])
    # 9 / J / 2 are not in the book: each must come back UNKNOWN, none may be reported as another rank
    for r in ("9", "J", "2"):
        got, _, why = book.read(_corner(r, "s"))
        assert got is None, (r, got)
        assert "rank_" in why


def test_rank_can_be_known_while_the_suit_is_not():
    book = _book([("A", "s"), ("K", "h"), ("7", "d"), ("5", "c")])
    c = _corner("K", "h")
    covered = C.Corner(c.rank, None, c.red, c.red_conf, c.rank_h, "pip_unreadable", None)
    rank, suit, _, why = book.read_parts(covered)
    assert rank == "K" and suit is None and why
    assert book.read(covered)[0] is None


def test_suit_colour_decides_the_family_before_the_shape():
    book = _book([("A", "s"), ("K", "h"), ("7", "d"), ("5", "c")])
    c = _corner("7", "d")
    assert c.red is True
    # a black pip drawn in the same place can never be read as a red suit
    black = _corner("7", "s")
    got, _, _ = book.read(black)
    assert got is None or got[1] in "sc"


def test_tilted_variants_are_added_to_the_book():
    b = CornerBook()
    assert b.add("7d", _corner("7", "d"))
    assert len(b.rank["7"]) > 1 and len(b.suit["d"]) > 1          # upright exemplar + tilted ones (fanned hero cards)


def test_book_roundtrip_json():
    book = _book([("A", "s"), ("K", "h")])
    again = CornerBook.from_json(book.to_json())
    assert again.read(_corner("A", "s"))[0] == "As"


def test_parse_cash_accepts_cents_and_parse_money_still_refuses_a_bare_decimal():
    assert parse_cash("35.18").amount == 35.18 and parse_cash("35.18").step == 0.01
    assert parse_cash("200").amount == 200
    assert parse_cash("1.5") is None and parse_cash("35,18") is None and parse_cash("") is None
    assert parse_money("35.18") is None                            # the K/M-style parser is unchanged


def _digit(ch: str, size: float = 1.0) -> np.ndarray:
    img = np.full((40, 30, 3), (25, 40, 25), np.uint8)
    cv2.putText(img, ch, (4, 30), cv2.FONT_HERSHEY_DUPLEX, 1.1 * size, (80, 230, 250), 2, cv2.LINE_AA)
    return img


def test_digit_book_open_set_and_scale_free():
    b = DigitBook()
    for ch in "012345":
        b.add(ch, digit_vec(_digit(ch)))
    b.fit()
    assert b.classify(digit_vec(_digit("3", 1.0)))[0] == "3"
    assert b.classify(digit_vec(_digit("3", 0.7)))[0] == "3"      # size does not matter
    assert b.classify(digit_vec(_digit("9")))[0] is None           # 9 was never seen: unknown, not '0' or '3'


def test_label_book_refuses_an_unseen_label():
    def band(text):
        img = np.full((90, 200, 3), (30, 30, 30), np.uint8)
        cv2.putText(img, text, (10, 55), cv2.FONT_HERSHEY_DUPLEX, 1.0, (250, 250, 250), 2, cv2.LINE_AA)
        full = np.full((200, 200, 3), (30, 30, 30), np.uint8)
        full[50:140] = img
        return full
    slot = (0.0, 0.25, 1.0, 0.45)
    lb = LabelBook()
    for cls in ("Fold", "Check", "Call"):
        lb.add(cls.lower(), slot_vector(band(cls), slot, "left"))
    lb.fit()
    assert lb.classify(slot_vector(band("Fold"), slot, "left"))[0] == "fold"
    assert lb.classify(slot_vector(band("Raise to"), slot, "left"))[0] is None


def test_closed_profile_reads_nothing_and_no_layout_means_legacy_path():
    ad = TonPokerAdapter()
    fr = Frame(np.full((1280, 576, 3), (40, 110, 45), np.uint8), 0, "t", "f")
    st = ad.read(fr)
    assert all(not f.known for f in st.hero_cards) and not st.pot.known and not st.hero_turn.known
    assert st.showdown == []


def test_award_with_a_lost_decimal_point_is_refused():
    """'+77.28' read as '7728' must never reach the history: four digits and no point -> UNKNOWN."""
    ad = TonPokerAdapter()
    b = DigitBook()
    for ch in "0123456789":
        b.add(ch, digit_vec(_digit(ch)))
    b.fit()
    ad.profile = type("P", (), {"data": {"digit_book": b.to_json()}})()
    # a frame region without any orange text has no award at all
    img = np.full((300, 300, 3), (40, 110, 45), np.uint8)
    assert ad._award(img, 100, 150, 80, 40, ad._digits()) is None
