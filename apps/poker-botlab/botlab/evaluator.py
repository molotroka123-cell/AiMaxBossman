"""Texas Hold'em hand evaluator (5, 6 or 7 cards).

``evaluate(cards)`` returns an ``int`` score; a higher score is a stronger hand and
equal scores are exact ties. The category sits in the high bits
(``score >> 20``) and up to five kicker ranks follow in 4-bit nibbles, so the
score is directly comparable across categories.

The evaluator works on rank counts and per-suit rank bitmasks instead of trying
all 21 five-card subsets; it is pure Python and needs no lookup files.
"""

from __future__ import annotations

from collections.abc import Sequence

from .cards import Card

HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH = range(9)
CATEGORY_NAMES = (
    "High Card",
    "Pair",
    "Two Pair",
    "Three of a Kind",
    "Straight",
    "Flush",
    "Full House",
    "Four of a Kind",
    "Straight Flush",
)


def _build_straight_table() -> list[int]:
    """STRAIGHT_HIGH[mask] = high rank of the best straight in a 13-bit mask, else -1."""
    table = [-1] * 8192
    windows = [(high, 0b11111 << (high - 4)) for high in range(12, 3, -1)]
    wheel = (1 << 12) | 0b1111  # A-2-3-4-5
    for mask in range(8192):
        for high, w in windows:
            if mask & w == w:
                table[mask] = high
                break
        else:
            if mask & wheel == wheel:
                table[mask] = 3  # five-high
    return table


def _build_top_ranks_table() -> list[tuple[int, ...]]:
    """TOP[mask] = ranks present in the mask, highest first."""
    return [tuple(r for r in range(12, -1, -1) if mask >> r & 1) for mask in range(8192)]


STRAIGHT_HIGH = _build_straight_table()
TOP_RANKS = _build_top_ranks_table()


def _pack(category: int, kickers: Sequence[int]) -> int:
    score = category
    for i in range(5):
        score = (score << 4) | (kickers[i] if i < len(kickers) else 0)
    return score


def evaluate(cards: Sequence[Card]) -> int:
    n = len(cards)
    if not 5 <= n <= 7:
        raise ValueError(f"evaluate needs 5-7 cards, got {n}")
    suit_masks = [0, 0, 0, 0]
    suit_counts = [0, 0, 0, 0]
    counts = [0] * 13
    rank_mask = 0
    for c in cards:
        r = c >> 2
        s = c & 3
        suit_masks[s] |= 1 << r
        suit_counts[s] += 1
        counts[r] += 1
        rank_mask |= 1 << r

    flush_suit = -1
    for s in range(4):
        if suit_counts[s] >= 5:
            flush_suit = s
            break
    if flush_suit >= 0:
        fmask = suit_masks[flush_suit]
        sf = STRAIGHT_HIGH[fmask]
        if sf >= 0:
            return _pack(STRAIGHT_FLUSH, (sf,))
        # With 5+ suited cards among at most 7, quads or a full house would need
        # at least 8 cards, so the flush is the best possible hand here.
        return _pack(FLUSH, TOP_RANKS[fmask][:5])

    quads = -1
    trips: list[int] = []
    pairs: list[int] = []
    for r in range(12, -1, -1):
        k = counts[r]
        if k == 4:
            quads = r
        elif k == 3:
            trips.append(r)
        elif k == 2:
            pairs.append(r)

    if quads >= 0:
        kicker = next(r for r in TOP_RANKS[rank_mask] if r != quads)
        return _pack(QUADS, (quads, kicker))
    if trips and (len(trips) >= 2 or pairs):
        t = trips[0]
        p = trips[1] if len(trips) >= 2 else -1
        if pairs and pairs[0] > p:
            p = pairs[0]
        return _pack(FULL_HOUSE, (t, p))
    st = STRAIGHT_HIGH[rank_mask]
    if st >= 0:
        return _pack(STRAIGHT, (st,))
    if trips:
        t = trips[0]
        kick = [r for r in TOP_RANKS[rank_mask] if r != t][:2]
        return _pack(TRIPS, (t, *kick))
    if len(pairs) >= 2:
        p1, p2 = pairs[0], pairs[1]
        kick = next(r for r in TOP_RANKS[rank_mask] if r != p1 and r != p2)
        return _pack(TWO_PAIR, (p1, p2, kick))
    if pairs:
        p = pairs[0]
        kick = [r for r in TOP_RANKS[rank_mask] if r != p][:3]
        return _pack(PAIR, (p, *kick))
    return _pack(HIGH_CARD, TOP_RANKS[rank_mask][:5])


def category(score: int) -> int:
    return score >> 20


def category_name(score: int) -> str:
    return CATEGORY_NAMES[category(score)]
