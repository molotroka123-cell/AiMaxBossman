from __future__ import annotations

import itertools
import random
from collections import Counter

import pytest

from botlab.cards import card_str, hand_class, parse_card, parse_cards
from botlab.evaluator import (
    FLUSH,
    FULL_HOUSE,
    HIGH_CARD,
    PAIR,
    QUADS,
    STRAIGHT,
    STRAIGHT_FLUSH,
    TRIPS,
    TWO_PAIR,
    category,
    evaluate,
)


def ev(s: str) -> int:
    return evaluate(parse_cards(s))


def test_card_roundtrip_and_classes() -> None:
    for text in ("As", "Td", "2c", "9h"):
        assert card_str(parse_card(text)) == text
    assert hand_class(parse_cards("AsKs")) == "AKs"
    assert hand_class(parse_cards("Kd As")) == "AKo"
    assert hand_class(parse_cards("7h7c")) == "77"
    with pytest.raises(ValueError):
        parse_cards("AsAs")


@pytest.mark.parametrize(
    "hand,cat",
    [
        ("AsKsQsJsTs 2c 3d", STRAIGHT_FLUSH),
        ("5h4h3h2hAh Kc Qd", STRAIGHT_FLUSH),  # steel wheel
        ("9c9d9h9s 2c 3d 4h", QUADS),
        ("KcKdKh 2s2c 7d 8h", FULL_HOUSE),
        ("KcKdKh 2s2c2d 8h", FULL_HOUSE),  # two trips
        ("Ac9c7c5c2c Kd Qh", FLUSH),
        ("Ac Kd Qh Js Tc 2d 3h", STRAIGHT),
        ("Ac 2d 3h 4s 5c 9d Kh", STRAIGHT),  # wheel
        ("7c7d7h As Kd 2c 4h", TRIPS),
        ("AcAd KhKs 2c 3d 9h", TWO_PAIR),
        ("AcAd Kh Qs 9c 3d 2h", PAIR),
        ("Ac Jd 9h 7s 5c 3d 2h", HIGH_CARD),
    ],
)
def test_categories(hand: str, cat: int) -> None:
    assert category(ev(hand)) == cat


def test_ordering_across_and_within_categories() -> None:
    ordered = [
        "Ac Jd 9h 7s 5c 3d 2h",  # high card
        "2c2d Kh Qs 9c 7d 4h",  # pair of deuces
        "AcAd Kh Qs 9c 3d 2h",  # pair of aces
        "3c3d 2h2s Ac Kd 9h",  # two pair
        "AcAd KhKs 2c 3d 9h",
        "7c7d7h As Kd 2c 4h",  # trips
        "Ac 2d 3h 4s 5c 9d Kh",  # wheel
        "6c 2d 3h 4s 5c 9d Kh",  # six-high straight beats wheel
        "Ac Kd Qh Js Tc 2d 3h",  # broadway
        "Ac9c7c5c2c Kd Qh",  # flush
        "KcKdKh 2s2c 7d 8h",  # full house
        "9c9d9h9s 2c 3d 4h",  # quads
        "5h4h3h2hAh Kc Qd",  # steel wheel
        "AsKsQsJsTs 2c 3d",  # royal
    ]
    scores = [ev(h) for h in ordered]
    assert scores == sorted(scores)
    assert len(set(scores)) == len(scores)


def test_kickers_and_ties() -> None:
    assert ev("AcAd Kh 9s 7c 3d 2h") > ev("AhAs Qh 9d 7s 3c 2d")  # kicker K > Q
    assert ev("AcAd Kh 9s 7c 3d 2h") == ev("AhAs Kd 9d 7s 3c 2d")  # same five-card hand
    # two pair: third pair is ignored, best kicker counts
    assert ev("KcKd QhQs 2c2d Ah") == ev("KcKd QhQs Ac 2d 3h")
    # flush with 6 suited cards uses the top five
    assert ev("Ac Kc 9c 7c 5c 2c Qd") > ev("Ac Kc 9c 7c 4c 2c Qd")
    # board plays: both players tie
    board = "Ts Jd Qh Kc Ad"
    assert ev("2c 3d " + board) == ev("4h 5s " + board)


def test_exhaustive_five_card_category_counts() -> None:
    expected = {
        STRAIGHT_FLUSH: 40,
        QUADS: 624,
        FULL_HOUSE: 3744,
        FLUSH: 5108,
        STRAIGHT: 10200,
        TRIPS: 54912,
        TWO_PAIR: 123552,
        PAIR: 1098240,
        HIGH_CARD: 1302540,
    }
    counts = Counter(evaluate(c) >> 20 for c in itertools.combinations(range(52), 5))
    assert dict(counts) == expected


def test_seven_card_matches_best_five_of_seven_bruteforce() -> None:
    rng = random.Random(42)
    for _ in range(3000):
        seven = rng.sample(range(52), 7)
        brute = max(evaluate(list(five)) for five in itertools.combinations(seven, 5))
        assert evaluate(seven) == brute


def test_seven_card_category_frequencies_on_sample() -> None:
    # Known 7-card probabilities (133,784,560 hands); sample of 150k must be close.
    known = {
        HIGH_CARD: 0.1741, PAIR: 0.4383, TWO_PAIR: 0.2350, TRIPS: 0.0483, STRAIGHT: 0.0462,
        FLUSH: 0.0303, FULL_HOUSE: 0.0260, QUADS: 0.00168, STRAIGHT_FLUSH: 0.000311,
    }
    rng = random.Random(7)
    n = 150_000
    counts = Counter(evaluate(rng.sample(range(52), 7)) >> 20 for _ in range(n))
    for cat, p in known.items():
        sd = (p * (1 - p) / n) ** 0.5
        assert abs(counts[cat] / n - p) < 5 * sd + 1e-4, (cat, counts[cat] / n, p)
