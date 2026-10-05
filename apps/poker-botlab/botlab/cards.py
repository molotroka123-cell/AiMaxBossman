"""Card primitives.

A card is an ``int`` in ``0..51``: ``rank = card >> 2`` (0 = deuce .. 12 = ace) and
``suit = card & 3`` (0 = clubs, 1 = diamonds, 2 = hearts, 3 = spades).
The text form is the usual two characters, e.g. ``"As"``, ``"Td"``, ``"2c"``.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence

RANKS = "23456789TJQKA"
SUITS = "cdhs"
FULL_DECK: tuple[int, ...] = tuple(range(52))

Card = int


def make_card(rank: int, suit: int) -> Card:
    if not (0 <= rank < 13 and 0 <= suit < 4):
        raise ValueError(f"bad rank/suit {rank}/{suit}")
    return (rank << 2) | suit


def rank_of(card: Card) -> int:
    return card >> 2


def suit_of(card: Card) -> int:
    return card & 3


def parse_card(text: str) -> Card:
    text = text.strip()
    if len(text) != 2:
        raise ValueError(f"card must be 2 chars, got {text!r}")
    r, s = text[0].upper(), text[1].lower()
    if r not in RANKS or s not in SUITS:
        raise ValueError(f"bad card {text!r}")
    return make_card(RANKS.index(r), SUITS.index(s))


def parse_cards(text: str | Iterable[str]) -> list[Card]:
    """Parse ``"As Kd"`` / ``"AsKd"`` / ``["As", "Kd"]`` into card ints."""
    if isinstance(text, str):
        compact = text.replace(" ", "").replace(",", "")
        if len(compact) % 2:
            raise ValueError(f"odd card string {text!r}")
        items = [compact[i : i + 2] for i in range(0, len(compact), 2)]
    else:
        items = list(text)
    cards = [parse_card(t) for t in items]
    if len(set(cards)) != len(cards):
        raise ValueError(f"duplicate cards in {text!r}")
    return cards


def card_str(card: Card) -> str:
    return RANKS[card >> 2] + SUITS[card & 3]


def cards_str(cards: Sequence[Card]) -> str:
    return " ".join(card_str(c) for c in cards)


def new_deck(rng: random.Random) -> list[Card]:
    deck = list(FULL_DECK)
    rng.shuffle(deck)
    return deck


def hand_class(hole: Sequence[Card]) -> str:
    """Canonical 169-class label: ``"AA"``, ``"AKs"``, ``"T9o"``."""
    a, b = hole
    ra, rb = rank_of(a), rank_of(b)
    if ra < rb:
        ra, rb = rb, ra
    if ra == rb:
        return RANKS[ra] * 2
    return RANKS[ra] + RANKS[rb] + ("s" if suit_of(a) == suit_of(b) else "o")


def all_hand_classes() -> list[str]:
    out: list[str] = []
    for hi in range(12, -1, -1):
        for lo in range(hi, -1, -1):
            if hi == lo:
                out.append(RANKS[hi] * 2)
            else:
                out.append(RANKS[hi] + RANKS[lo] + "s")
                out.append(RANKS[hi] + RANKS[lo] + "o")
    return out


def class_combos(label: str) -> int:
    if len(label) == 2:
        return 6
    return 4 if label[2] == "s" else 12


def representative_hole(label: str) -> tuple[Card, Card]:
    hi, lo = RANKS.index(label[0]), RANKS.index(label[1])
    if len(label) == 2:
        return make_card(hi, 0), make_card(lo, 1)
    if label[2] == "s":
        return make_card(hi, 0), make_card(lo, 0)
    return make_card(hi, 0), make_card(lo, 1)
