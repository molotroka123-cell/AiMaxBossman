"""A card book with missing rank/suit classes must not read any card: an unseen rank would land on the nearest stored
class with a clean nearest-neighbour margin (confident wrong card). Lax reads (training-time pseudo-labels) are unchanged."""
import numpy as np

from pokervision.vision import cards as C


def _book(ranks: str, suits: str) -> C.CardBook:
    b = C.CardBook()
    for i, r in enumerate(ranks):
        b.rank[r].append(np.full(8, float(i), np.float32))
    for i, s in enumerate(suits):
        b.suit[s].append(np.full(4, float(i), np.float32))
    return b


def _box() -> C.CardBox:
    return C.CardBox(0, 0, 40, 56, 0.97, 1.0, 100.0)


def test_missing_classes_lists_every_absent_rank_and_suit():
    assert _book("", "").missing_classes() == "23456789TJQKA" + "shdc"
    assert _book("2356TK", "shdc").missing_classes() == "4789JQA"
    assert _book("23456789TJQKA", "shd").missing_classes() == "c"
    assert _book("23456789TJQKA", "shdc").missing_classes() == ""


def test_partial_book_never_reads_a_card(monkeypatch):
    called = []
    monkeypatch.setattr(C, "split_corner", lambda box, bgr: called.append(1) or None)
    book = _book("2356TK", "shdc")
    card, conf, why = book.read(_box(), np.zeros((100, 100, 3), np.uint8))
    assert card is None and conf == 0.0 and why == "book_incomplete:4789JQA"
    assert not called, "an incomplete book must refuse before looking at the pixels"


def test_lax_read_is_not_gated_by_coverage(monkeypatch):
    seen = []
    monkeypatch.setattr(C, "split_corner", lambda box, bgr: seen.append(1) or None)
    book = _book("2356TK", "shdc")
    card, conf, why = book.read(_box(), np.zeros((100, 100, 3), np.uint8), min_conf=-1.0, rank_max_d=1e9, suit_max_d=1e9, lax=True)
    assert seen and card is None and why == "no_index"


def test_full_book_proceeds_to_matching(monkeypatch):
    seen = []
    monkeypatch.setattr(C, "split_corner", lambda box, bgr: seen.append(1) or None)
    book = _book("23456789TJQKA", "shdc")
    card, conf, why = book.read(_box(), np.zeros((100, 100, 3), np.uint8))
    assert seen and card is None and why == "no_index"
