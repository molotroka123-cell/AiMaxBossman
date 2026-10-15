"""Rigorous GTO-level poker mathematics, hand/session EV analyzer, and 5-max tournament calibration engine.

Implements:
1. Exact Pot Odds: ``bet / (pot + 2 * bet)`` (where ``pot`` is pot before the bet; equivalent to
   ``to_call / (pot_including_bet + to_call)`` when ``pot_including_bet = pot + bet``).
2. Required Equity: ``pot_odds / equity_realization_factor``.
3. Minimum Defense Frequency (MDF): ``MDF = pot / (pot + bet)``.
4. Optimal Bluff-to-Value Ratio: ``alpha = bet / (pot + bet)`` (so ``MDF + alpha == 1`` and
   optimal bluff share of total bets is ``alpha / (1 + alpha) == bet / (pot + 2 * bet) == pot_odds``).
5. Stack-to-Pot Ratio (SPR): ``effective_stack / pot``.
6. Geometric Bet Sizing across N streets: ``f = ((1 + 2 * SPR) ** (1 / N) - 1) / 2``.
7. Positional 5-max / 6-max preflop ranges (UTG/HJ, CO, BTN, SB, BB): RFI, 3-bet, call, BB defense, 4-bet.
8. Board Texture Classification: ``dry``, ``wet``, ``paired``, ``monotone``, ``connected``.
9. Range Advantage & Nut Advantage estimation across positional matchups and board textures.
10. Street-by-Street EV (``EV_fold``, ``EV_call``, ``EV_raise``) and EV loss (``delta_EV_bb``) detection.
11. 5-Player 1000-Hand No-Limit Hold'em simulation (Claude, Codex, Argon, Local Bossman, GTO Reference Solver Bot)
    with pre-calibration leak diagnosis and post-calibration GTO verification.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from .arena import MeanCI, deal_seed
from .cards import RANKS, Card, cards_str, hand_class, parse_cards, rank_of, suit_of
from .engine import Action, HandResult, LegalActions, Observation, play_hand
from .equity import mc_equity, preflop_equity, preflop_percentile

# ---------------------------------------------------------------------------
# 1. Core GTO Mathematical Formulas
# ---------------------------------------------------------------------------


def pot_odds(pot: float, bet: float, *, pot_includes_bet: bool = False) -> float:
    """Exact pot odds (break-even equity for a closing call).

    * When ``pot_includes_bet=False`` (default): ``pot`` is the pot BEFORE villain's ``bet`` is placed,
      and hero must call ``bet`` to win a total pot of ``pot + 2 * bet``:
          ``pot_odds = bet / (pot + 2 * bet)``
    * When ``pot_includes_bet=True``: ``pot`` already includes villain's ``bet`` (as in ``Observation.pot``),
      so hero calls ``bet`` to contest ``pot + bet``:
          ``pot_odds = bet / (pot + bet)``
    Both conventions are algebraically identical when ``pot_including_bet = pot_before_bet + bet``.
    """
    if bet <= 0:
        return 0.0
    pot = max(0.0, float(pot))
    bet = float(bet)
    denom = (pot + bet) if pot_includes_bet else (pot + 2.0 * bet)
    return bet / denom if denom > 0 else 0.0


def required_equity(
    pot: float,
    bet: float,
    *,
    pot_includes_bet: bool = False,
    realization: float = 1.0,
) -> float:
    """Required raw equity to profitably call, adjusted for postflop equity realization factor ``R``."""
    po = pot_odds(pot, bet, pot_includes_bet=pot_includes_bet)
    if po <= 0.0:
        return 0.0
    r = max(0.1, float(realization))
    return min(1.0, max(0.0, po / r))


def mdf(pot: float, bet: float, *, pot_includes_bet: bool = False) -> float:
    """Minimum Defense Frequency (MDF) to make villain's 0%-equity bluffs indifferent:
    ``MDF = pot_before_bet / (pot_before_bet + bet)``.
    """
    if bet <= 0:
        return 1.0
    pot_before = max(0.0, float(pot) - float(bet)) if pot_includes_bet else max(0.0, float(pot))
    denom = pot_before + float(bet)
    if denom <= 0:
        return 0.0
    return min(1.0, max(0.0, pot_before / denom))


def optimal_bluff_alpha(pot: float, bet: float, *, pot_includes_bet: bool = False) -> float:
    """Optimal bluff-to-value ratio ``alpha = bet / (pot + bet) = 1 - MDF`` (on a polarized river)."""
    if bet <= 0:
        return 0.0
    return 1.0 - mdf(pot, bet, pot_includes_bet=pot_includes_bet)


def optimal_bluff_share(pot: float, bet: float, *, pot_includes_bet: bool = False) -> float:
    """Optimal share of bluffs within total bets: ``alpha / (1 + alpha) == bet / (pot + 2 * bet) == pot_odds``."""
    a = optimal_bluff_alpha(pot, bet, pot_includes_bet=pot_includes_bet)
    return a / (1.0 + a) if a > 0 else 0.0


def spr(effective_stack: float, pot: float) -> float:
    """Stack-to-Pot Ratio: ``effective_stack / pot``."""
    if pot <= 0:
        return float("inf")
    return max(0.0, float(effective_stack)) / float(pot)


def geometric_bet_fraction(effective_stack: float, pot: float, streets_remaining: int = 3) -> float:
    """Geometric bet size as a fraction of the pot per street to get ``effective_stack`` all-in over ``N`` streets:
    ``f = ((1 + 2 * SPR) ** (1 / N) - 1) / 2``.
    """
    if effective_stack <= 0 or pot <= 0:
        return 0.0
    n = max(1, int(streets_remaining))
    s = spr(effective_stack, pot)
    return max(0.0, ((1.0 + 2.0 * s) ** (1.0 / n) - 1.0) / 2.0)


def geometric_bet_amount(effective_stack: float, pot: float, streets_remaining: int = 3) -> float:
    """Chip amount of the geometric bet on the current street, capped at ``effective_stack``."""
    if effective_stack <= 0 or pot <= 0:
        return 0.0
    frac = geometric_bet_fraction(effective_stack, pot, streets_remaining)
    return min(float(effective_stack), float(pot) * frac)


# ---------------------------------------------------------------------------
# 2. Positional 5-max / 6-max Preflop Ranges
# ---------------------------------------------------------------------------


def _rank_idx(c: str) -> int:
    return RANKS.index(c)


def _cls_label(hi: int, lo: int, kind: str) -> str:
    return RANKS[hi] + RANKS[lo] + ("" if kind == "p" else kind)


def parse_range(text: str) -> frozenset[str]:
    """Parse range string ('22+, A2s+, K9s+, ATo+, 22-JJ, A5s-A2s') into a frozenset of 169 hand-class labels."""
    out: set[str] = set()
    for tok in (t.strip() for t in text.split(",")):
        if not tok:
            continue
        plus = tok.endswith("+")
        core = tok[:-1] if plus else tok
        if "-" in core:
            a, b = core.split("-")
            if len(a) == 2 and len(b) == 2 and a[0] == a[1] and b[0] == b[1]:
                lo_, hi_ = sorted((_rank_idx(a[0]), _rank_idx(b[0])))
                out.update(_cls_label(r, r, "p") for r in range(lo_, hi_ + 1))
                continue
            if len(a) == 3 and len(b) == 3 and a[0] == b[0] and a[2] == b[2]:
                lo_, hi_ = sorted((_rank_idx(a[1]), _rank_idx(b[1])))
                out.update(_cls_label(_rank_idx(a[0]), k, a[2]) for k in range(lo_, hi_ + 1))
                continue
            raise ValueError(f"bad range token {tok!r}")
        if len(core) == 2 and core[0] == core[1] and core[0] in RANKS:
            r = _rank_idx(core[0])
            out.update(_cls_label(k, k, "p") for k in (range(r, 13) if plus else (r,)))
            continue
        if len(core) == 3 and core[0] in RANKS and core[1] in RANKS and core[2] in "so" and _rank_idx(core[0]) > _rank_idx(core[1]):
            hi, lo = _rank_idx(core[0]), _rank_idx(core[1])
            out.update(_cls_label(hi, k, core[2]) for k in (range(lo, hi) if plus else (lo,)))
            continue
        raise ValueError(f"bad range token {tok!r}")
    return frozenset(out)


def range_combos(classes: Iterable[str] | frozenset[str]) -> int:
    return sum(6 if len(c) == 2 else 4 if c[2] == "s" else 12 for c in classes)


RFI_RANGES: dict[str, dict[str, Any]] = {
    "UTG": {"raise": parse_range("22+, A2s+, K9s+, QTs+, JTs, T9s, 98s, 87s, 76s, 65s, 54s, ATo+, KJo+, QJo"), "open_bb": 2.5},
    "HJ": {"raise": parse_range("22+, A2s+, K9s+, Q9s+, J9s+, T8s+, 98s, 87s, 76s, 65s, 54s, ATo+, KJo+, QJo"), "open_bb": 2.5},
    "CO": {"raise": parse_range("22+, A2s+, K6s+, Q8s+, J8s+, T7s+, 97s+, 86s+, 76s, 65s, 54s, A9o+, KTo+, QTo+, JTo"), "open_bb": 2.5},
    "BTN": {"raise": parse_range("22+, A2s+, K2s+, Q4s+, J6s+, T6s+, 96s+, 85s+, 75s+, 64s+, 53s+, 43s, A2o+, K7o+, Q8o+, J8o+, T8o+, 97o+, 87o, 76o"), "open_bb": 2.5},
    "SB": {"raise": parse_range("22+, A2s+, K2s+, Q2s+, J2s+, T2s+, 92s+, 82s+, 72s+, 62s+, 52s+, 42s+, 32s, A2o+, K2o+, Q4o+, J6o+, T7o+, 97o+, 86o+, 76o, 65o, 54o"), "open_bb": 3.0},
}

VS_OPEN_RANGES: dict[str, dict[str, frozenset[str]]] = {
    "vs_UTG": {
        "threebet": parse_range("QQ+, AKs, AKo, AQs, A5s, A4s"),
        "call": parse_range("22-JJ, AQo, AJs, AJo, ATs, ATo, KQs, KJs, QJs, JTs, T9s, 98s, 87s, 76s"),
    },
    "vs_HJ": {
        "threebet": parse_range("JJ+, AKs, AKo, AQs, AQo, A5s, A4s, A3s, KQs"),
        "call": parse_range("22-TT, AJs, AJo, ATs, ATo, KJs, KJo, QJs, QTs, JTs, T9s, 98s, 87s, 76s, 65s"),
    },
    "vs_CO": {
        "threebet": parse_range("TT+, AKs, AKo, AQs, AQo, AJs, A5s, A4s, A3s, A2s, KQs, KJs, QJs"),
        "call": parse_range("22-99, ATo, A9s, A8s, KTs, KJo, QTs, JTs, J9s, T9s, T8s, 98s, 87s, 76s, 65s, 54s"),
    },
    "vs_BTN": {
        "threebet": parse_range("99+, AKs, AKo, AQs, AQo, AJs, AJo, A5s, A4s, A3s, A2s, KQs, KJs, K9s, QJs, Q9s, JTs, J9s, T9s, 98s, 87s, 76s"),
        "call": parse_range("22-88, ATs, A9s, A8s, A7s, ATo, KTs, K8s, KQo, KJo, QTs, Q8s, J8s, T8s, 97s, 86s, 75s, 65s, 54s"),
    },
    "sb_vs_btn": {
        "threebet": parse_range("88+, AKs, AKo, AQs, AQo, AJs, AJo, ATs, A5s, A4s, A3s, A2s, KQs, KJs, KTs, K9s, QJs, QTs, Q9s, JTs, J9s, T9s, T8s, 98s, 87s, 76s, 65s"),
        "call": parse_range("55-77, A9s, A8s, KQo, QJo"),
    },
}

BB_DEFENSE_RANGES: dict[str, dict[str, frozenset[str]]] = {
    "vs_UTG": {
        "threebet": parse_range("JJ+, AKs, AKo, AQs, A5s, A4s"),
        "call": parse_range("22-TT, AQo, AJs, ATs, A9s, A8s, A7s, A5s, KQs, KJs, KTs, K9s, QJs, QTs, Q9s, JTs, J9s, T9s, T8s, 98s, 97s, 87s, 76s, 65s, 54s"),
    },
    "vs_HJ": {
        "threebet": parse_range("TT+, AKs, AKo, AQs, AQo, A5s, A4s, A3s"),
        "call": parse_range("22-99, AJs, ATs, A9s, A8s, A7s, A6s, A5s, AJo, ATo, KQs, KJs, KTs, K9s, K8s, KQo, KJo, QJs, QTs, Q9s, Q8s, JTs, J9s, J8s, T9s, T8s, T7s, 98s, 97s, 96s, 87s, 86s, 76s, 75s, 65s, 64s, 54s, 53s, 43s"),
    },
    "vs_CO": {
        "threebet": parse_range("99+, AKs, AKo, AQs, AQo, AJs, A5s, A4s, A3s, A2s, K9s, QJs"),
        "call": parse_range("22-88, ATs, A9s, A8s, A7s, A6s, A5s, A4s, A3s, A2s, ATo, A9o, A8o, KQs, KJs, KTs, K9s, K8s, K7s, K6s, KJo, KTo, K9o, QTs, Q9s, Q8s, Q7s, QTo, Q9o, JTs, J9s, J8s, J7s, JTo, J9o, T9s, T8s, T7s, 98s, 97s, 96s, 87s, 86s, 85s, 76s, 75s, 74s, 65s, 64s, 63s, 54s, 53s, 43s"),
    },
    "vs_BTN": {
        "threebet": parse_range("88+, AKs, AKo, AQs, AQo, AJs, ATs, A9s, A5s, A4s, A3s, A2s, K8s, K7s, QJs, Q9s, JTs, J9s, T8s, 97s, 86s, 75s, 64s, 53s"),
        "call": parse_range("22-77, A8s, A7s, A6s, A5s, A4s, A3s, A2s, ATo, A9o, A8o, A7o, A6o, A5o, A4o, A3o, A2o, KQs, KJs, KTs, K9s, K8s, K7s, K6s, K5s, K4s, KQo, KJo, KTo, K9o, K8o, K7o, QTs, Q9s, Q8s, Q7s, Q6s, QTo, Q9o, Q8o, JTs, J9s, J8s, J7s, J6s, JTo, J9o, J8o, T9s, T8s, T7s, T6s, T9o, T8o, 98s, 97s, 96s, 95s, 98o, 87s, 86s, 85s, 84s, 87o, 76s, 75s, 74s, 73s, 65s, 64s, 63s, 54s, 53s, 52s, 43s, 42s, 32s"),
    },
    "vs_SB": {
        "threebet": parse_range("88+, AKs, AKo, AQs, AQo, AJs, ATs, A9s, A5s, A4s, A3s, A2s, K7s, K6s, K5s, QTs, Q9s, J9s, T8s, 97s, 86s, 75s, 64s, 53s"),
        "call": parse_range("22-77, A8s, A7s, A6s, A5s, A4s, A3s, A2s, ATo, A9o, A8o, A7o, A6o, A5o, KQs, KJs, KTs, K9s, K8s, K7s, K6s, K5s, K4s, KTo, K9o, K8o, K7o, QTs, Q9s, Q8s, Q7s, Q6s, Q5s, QJo, QTo, Q9o, Q8o, JTs, J9s, J8s, J7s, J6s, JTo, J9o, J8o, T9s, T8s, T7s, T6s, T9o, T8o, 98s, 97s, 96s, 95s, 98o, 97o, 87s, 86s, 85s, 84s, 87o, 76s, 75s, 74s, 73s, 76o, 65s, 64s, 63s, 54s, 53s, 52s, 43s, 42s, 32s"),
    },
}

VS_3BET_RANGES: dict[str, frozenset[str]] = {
    "fourbet_value": parse_range("QQ+, AKs, AKo"),
    "fourbet_bluff": parse_range("A5s, A4s, A3s, KQs"),
    "call": parse_range("77-JJ, AQs, AQo, AJs, ATs, KJs, QJs, JTs, T9s, 98s"),
}


def seat_position_name(position_idx: int, n_players: int) -> str:
    """Map engine ``Observation.position`` (0 = BTN, 1 = SB, 2 = BB, ... clockwise) to canonical position label."""
    if n_players <= 2:
        return "BTN" if position_idx == 0 else "BB"
    if position_idx == 0:
        return "BTN"
    if position_idx == 1:
        return "SB"
    if position_idx == 2:
        return "BB"
    # Remaining positions from earliest (3 = UTG/HJ) to CO (n_players - 1)
    if n_players == 3:
        return "BTN"
    if n_players == 4:
        return "CO"
    if n_players == 5:
        # 5-max table: BTN(0), SB(1), BB(2), HJ/UTG(3), CO(4)
        return "HJ" if position_idx == 3 else "CO"
    # 6-max table: BTN(0), SB(1), BB(2), UTG(3), HJ(4), CO(5)
    order_6 = {3: "UTG", 4: "HJ", 5: "CO"}
    return order_6.get(position_idx, "CO")


# ---------------------------------------------------------------------------
# 3. Board Texture Classification & Range / Nut Advantage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardTexture:
    primary: str  # "preflop" | "monotone" | "paired" | "wet" | "connected" | "dry"
    tags: tuple[str, ...]
    wetness: float  # [0.0, 1.0]
    paired: bool
    monotone: bool
    two_tone: bool
    rainbow: bool
    connected: bool
    flush_possible: bool
    straight_possible: bool
    high_rank: int
    recommended_cbet_frac: float
    recommended_cbet_freq: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_cards(cards: Sequence[Card | str] | str) -> list[Card]:
    if not cards:
        return []
    if isinstance(cards, str):
        return parse_cards(cards.strip()) if cards.strip() else []
    out: list[Card] = []
    for c in cards:
        if isinstance(c, int):
            out.append(c)
        elif isinstance(c, str) and c.strip():
            out.extend(parse_cards(c.strip()))
    return out



def classify_board(board: Sequence[Card | str]) -> BoardTexture:
    """Classify board texture into dry / wet / paired / monotone / connected with wetness score and GTO c-bet sizing."""
    cards = _normalize_cards(board)
    if not cards:
        return BoardTexture(
            primary="preflop",
            tags=("preflop",),
            wetness=0.0,
            paired=False,
            monotone=False,
            two_tone=False,
            rainbow=False,
            connected=False,
            flush_possible=False,
            straight_possible=False,
            high_rank=-1,
            recommended_cbet_frac=0.75,
            recommended_cbet_freq=0.0,
        )

    ranks = sorted((rank_of(c) for c in cards), reverse=True)
    suits = [suit_of(c) for c in cards]
    suit_counts = Counter(suits)
    rank_counts = Counter(ranks)

    max_suit = max(suit_counts.values())
    max_rank_cnt = max(rank_counts.values())
    n_cards = len(cards)

    paired = max_rank_cnt >= 2
    monotone = max_suit >= 3 and n_cards == 3 or max_suit >= 4
    two_tone = not monotone and max_suit >= 2
    rainbow = max_suit == 1
    flush_possible = max_suit >= 3

    # Connectivity: check max cards within any 5-rank window (including wheel Ace = -1)
    uniq_ranks = sorted(set(ranks))
    if 12 in uniq_ranks:
        uniq_with_wheel = [-1] + uniq_ranks
    else:
        uniq_with_wheel = uniq_ranks

    max_in_span5 = 1
    gaps_le_2 = 0
    for i in range(len(uniq_with_wheel)):
        in_span = sum(1 for r in uniq_with_wheel if 0 <= r - uniq_with_wheel[i] <= 4)
        max_in_span5 = max(max_in_span5, in_span)
        if i + 1 < len(uniq_with_wheel) and (uniq_with_wheel[i + 1] - uniq_with_wheel[i]) <= 2:
            gaps_le_2 += 1

    straight_possible = max_in_span5 >= 3
    connected = max_in_span5 >= 3 or (len(uniq_ranks) >= 3 and gaps_le_2 >= 2)

    # Compute continuous wetness score in [0, 1]
    wet = 0.0
    if max_suit >= 3:
        wet += 0.45
    elif max_suit == 2:
        wet += 0.20
    if max_in_span5 >= 4:
        wet += 0.40
    elif max_in_span5 == 3:
        wet += 0.30
    elif gaps_le_2 >= 1:
        wet += 0.12
    # Broadway / medium coordination (ranks 6..11 -> 8..K)
    mid_cards = sum(1 for r in ranks if 5 <= r <= 10)
    if mid_cards >= 2:
        wet += 0.12
    if paired:
        wet -= 0.15
    wetness = round(min(1.0, max(0.0, wet)), 3)

    is_wet = wetness >= 0.48 or (flush_possible and connected) or (two_tone and connected and max_in_span5 >= 3)
    is_dry = not is_wet and not monotone and not connected

    tags: list[str] = []
    if monotone:
        tags.append("monotone")
    elif two_tone:
        tags.append("two_tone")
    elif rainbow:
        tags.append("rainbow")
    if paired:
        tags.append("paired")
    if connected:
        tags.append("connected")
    if is_wet:
        tags.append("wet")
    if is_dry:
        tags.append("dry")
    if flush_possible:
        tags.append("flush_possible")
    if straight_possible:
        tags.append("straight_possible")
    if ranks[0] >= 10:
        tags.append("high_card")
    else:
        tags.append("low_board")

    # Primary classification priority covering dry/wet/paired/monotone/connected
    if monotone:
        primary = "monotone"
    elif paired and not is_wet:
        primary = "paired"
    elif is_wet:
        primary = "wet"
    elif connected:
        primary = "connected"
    else:
        primary = "dry"

    # GTO c-bet sizing and frequency heuristic by texture:
    # - Dry / paired / monotone boards: small sizing (0.33 pot), high frequency on dry/paired
    # - Wet / connected boards: larger polarized sizing (0.67-0.75 pot), selective frequency
    if primary in ("dry", "paired"):
        cbet_frac = 0.33
        cbet_freq = 0.72 if ranks[0] >= 10 else 0.55
    elif primary == "monotone":
        cbet_frac = 0.33
        cbet_freq = 0.42
    elif primary == "connected":
        cbet_frac = 0.60
        cbet_freq = 0.48
    else:  # wet
        cbet_frac = 0.68
        cbet_freq = 0.45

    return BoardTexture(
        primary=primary,
        tags=tuple(tags),
        wetness=wetness,
        paired=paired,
        monotone=monotone,
        two_tone=two_tone,
        rainbow=rainbow,
        connected=connected,
        flush_possible=flush_possible,
        straight_possible=straight_possible,
        high_rank=ranks[0],
        recommended_cbet_frac=cbet_frac,
        recommended_cbet_freq=cbet_freq,
    )


@dataclass(frozen=True)
class AdvantageMetrics:
    range_advantage: float  # [-1.0, 1.0]: >0 favors preflop aggressor, <0 favors defender
    nut_advantage: float  # [-1.0, 1.0]: >0 aggressor has higher nut density
    aggressor_equity_est: float  # [0.0, 1.0]
    aggressor_nut_share: float  # [0.0, 1.0]
    defender_nut_share: float  # [0.0, 1.0]
    favors: str  # "aggressor" | "defender" | "neutral"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_range_and_nut_advantage(
    board: Sequence[Card | str],
    aggressor_pos: str = "BTN",
    defender_pos: str = "BB",
) -> AdvantageMetrics:
    """Compute range advantage and nut advantage on ``board`` for ``aggressor_pos`` vs ``defender_pos``.

    Preflop raisers (especially UTG/HJ/CO) hold all overpairs (AA-JJ) and top broadways (AK, AQ, KQ),
    whereas flat-callers (especially BB) have capped top-end ranges (no AA/KK/QQ/AK) and more low/mid
    suited connectors and small pairs (22-77, 87s, 76s, 65s, 54s).
    """
    cards = _normalize_cards(board)
    if not cards:
        return AdvantageMetrics(0.08, 0.12, 0.54, 0.15, 0.08, "aggressor")

    tex = classify_board(cards)
    ranks = sorted((rank_of(c) for c in cards), reverse=True)
    high = ranks[0]
    avg_rank = sum(ranks) / len(ranks)

    # Tighter opener position -> higher broadway/overpair concentration
    pos_tightness = {"UTG": 1.0, "HJ": 0.85, "CO": 0.65, "BTN": 0.45, "SB": 0.40, "BB": 0.30}.get(aggressor_pos, 0.6)
    def_cap = 0.25 if defender_pos == "BB" else 0.15

    # High cards (A, K, Q) strongly favor the uncapped preflop aggressor
    high_factor = (high - 7.5) / 4.5  # in [-1.2, +1.0]
    avg_factor = (avg_rank - 6.5) / 5.5

    # Low connected / paired low cards favor the BB caller's 2-pair / set / straight combos
    low_connected_penalty = 0.0
    if high <= 7 and (tex.connected or tex.wetness >= 0.45):
        low_connected_penalty = 0.22
    elif sum(1 for r in ranks if r <= 6) >= 2 and tex.connected:
        low_connected_penalty = 0.14

    raw_range_adv = 0.18 * high_factor + 0.12 * avg_factor + 0.12 * pos_tightness + 0.08 * def_cap - low_connected_penalty
    if tex.paired and high >= 10:
        raw_range_adv += 0.08  # AAx, KKx, QQx heavily favor PFR

    range_adv = round(min(0.65, max(-0.65, raw_range_adv)), 3)
    aggr_eq = round(min(0.75, max(0.30, 0.50 + 0.35 * range_adv)), 3)

    # Nut advantage: overpairs + top set + nut flush/straight draws vs defender's low sets/two-pairs/straights
    aggr_nuts = min(0.45, max(0.05, 0.14 + 0.08 * pos_tightness + (0.08 if high >= 10 else -0.04) - 0.5 * low_connected_penalty))
    def_nuts = min(0.45, max(0.04, 0.10 + 0.6 * low_connected_penalty + (0.05 if tex.paired and ranks[-1] <= 6 else 0.0)))
    nut_adv = round(min(0.65, max(-0.65, (aggr_nuts - def_nuts) * 2.5)), 3)

    favors = "aggressor" if range_adv > 0.05 else ("defender" if range_adv < -0.05 else "neutral")
    return AdvantageMetrics(
        range_advantage=range_adv,
        nut_advantage=nut_adv,
        aggressor_equity_est=aggr_eq,
        aggressor_nut_share=round(aggr_nuts, 3),
        defender_nut_share=round(def_nuts, 3),
        favors=favors,
    )


# ---------------------------------------------------------------------------
# 4. Street-by-Street EV (EV_fold, EV_call, EV_raise) & EV Loss (delta_EV_bb)
# ---------------------------------------------------------------------------


@dataclass
class SpotEVAnalysis:
    street: str
    position: str
    hole_class: str
    pot_before_bet: float
    bet_faced: float
    pot_including_bet: float
    effective_stack: float
    big_blind: float
    pot_odds: float
    required_equity: float
    mdf: float
    alpha: float
    spr: float
    geometric_bet_frac: float
    geometric_bet_chips: float
    board_texture: str
    board_wetness: float
    range_advantage: float
    nut_advantage: float
    raw_equity: float
    realized_equity: float
    ev_fold: float
    ev_check: float | None
    ev_call: float | None
    ev_raise: float | None
    optimal_raise_to: int | None
    best_action: str
    best_ev: float
    chosen_action: str
    chosen_ev: float
    delta_ev_chips: float
    delta_ev_bb: float
    mistake_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_realized_equity(
    raw_equity: float,
    street: str,
    position: str,
    n_opponents: int,
    raises_this_street: int,
    bet_to_pot_ratio: float,
    board_tex: BoardTexture,
) -> tuple[float, float]:
    """Convert raw equity vs random hands into (range_adjusted_equity, realization_factor).

    Accounts for:
    - Opponent range narrowing when facing raises or larger bet sizes (selection bias).
    - Positional equity realization (IP realizes ~102-108%, OOP SB/BB realizes ~85-92% on early streets).
    """
    ip_bonus = 1.04 if position in ("BTN", "CO") else (0.92 if position in ("SB", "BB") else 0.97)
    streets_left = {"preflop": 3, "flop": 2, "turn": 1, "river": 0}.get(street, 0)
    realization = 1.0 if streets_left == 0 else min(1.10, max(0.78, ip_bonus - 0.03 * max(0, n_opponents - 1) - 0.04 * board_tex.wetness * (1 if position in ("SB", "BB") else 0)))

    # Range narrowing discount: opponents who bet/raise do not hold bottom-of-deck random hands
    if street == "preflop":
        if raises_this_street == 0:
            discount = 0.0
        elif raises_this_street == 1:
            discount = 0.05
        else:
            discount = 0.12
    else:
        size_pressure = min(0.14, 0.07 * bet_to_pot_ratio) if bet_to_pot_ratio > 0 else 0.0
        raise_pressure = 0.06 * max(0, raises_this_street - 1)
        discount = min(0.22, size_pressure + raise_pressure)

    # Top of range (nuts / near-nuts, raw_equity > 0.85) suffers much less range discount than medium hands
    attenuation = 1.0 - 0.65 * (raw_equity ** 2)
    adj_eq = max(0.01, min(0.99, raw_equity - discount * attenuation))
    realized = max(0.01, min(0.99, adj_eq * realization))
    return round(adj_eq, 4), round(realized, 4)


def analyze_spot_ev(
    hole: Sequence[Card],
    board: Sequence[Card],
    street: str,
    position: str,
    pot_including_bet: float,
    to_call: float,
    stack: float,
    big_blind: float,
    n_opponents: int,
    raises_this_street: int,
    legal: LegalActions | None,
    chosen_action: str,
    chosen_amount: int = 0,
    my_street_bet: int = 0,
    rng: random.Random | None = None,
    sims: int = 160,
) -> SpotEVAnalysis:
    """Compute exact GTO metrics and street-by-street EV (EV_fold, EV_call, EV_raise) for a decision spot.

    All EVs use the standard decision-point chip baseline where ``EV_fold = 0.0`` (chips already in the pot
    before hero's decision are sunk):
    - When ``to_call > 0`` (facing a bet ``B = to_call`` into ``pot_before_bet = P_now - B``):
      * ``EV_fold = 0.0``
      * ``EV_call = eq_realized * (pot_before_bet + 2 * B) - B``
      * ``EV_raise = fe * P_now + (1 - fe) * (eq_called * (P_now + raise_added + villain_call) - raise_added)``
    - When ``to_call == 0`` (no bet facing, current pot is ``P_now``):
      * ``EV_fold = 0.0``
      * ``EV_check = eq_realized * P_now``
      * ``EV_raise (bet B) = fe * P_now + (1 - fe) * (eq_called * (P_now + 2 * B) - B)``
        Notice that at ``eq_called = 0`` (pure bluff) against an MDF defender (``fe = alpha = B / (P_now + B)``),
        ``EV_raise = alpha * P_now - (1 - alpha) * B = 0.0 == EV_check(0)``!
    """
    rng = rng or random.Random(42)
    bb = max(1.0, float(big_blind))
    p_now = max(bb, float(pot_including_bet))
    b_faced = max(0.0, float(to_call))
    p_before = max(0.0, p_now - b_faced)
    eff_stack = max(0.0, float(stack))

    hc = hand_class(hole) if len(hole) == 2 else "??"
    tex = classify_board(board)
    adv = compute_range_and_nut_advantage(board, aggressor_pos=position if raises_this_street == 0 else "BTN", defender_pos="BB")

    n_opp = max(1, min(int(n_opponents), 5))
    if street == "preflop" or not board:
        raw_eq = preflop_equity(hole, n_opp)
    else:
        raw_eq = mc_equity(hole, board, min(n_opp, 3), sims, rng)

    bet_ratio = b_faced / max(1.0, p_before) if b_faced > 0 else 0.0
    adj_eq, real_eq = estimate_realized_equity(raw_eq, street, position, n_opp, raises_this_street, bet_ratio, tex)

    po = pot_odds(p_before, b_faced, pot_includes_bet=False)
    req_eq = required_equity(p_before, b_faced, pot_includes_bet=False, realization=(real_eq / max(adj_eq, 1e-4)))
    mdf_val = mdf(p_before, b_faced, pot_includes_bet=False)
    alpha_val = optimal_bluff_alpha(p_before, b_faced, pot_includes_bet=False)
    spr_val = spr(eff_stack, p_now)
    streets_rem = {"preflop": 3, "flop": 3, "turn": 2, "river": 1}.get(street, 1)
    geo_frac = geometric_bet_fraction(eff_stack, p_now, streets_rem)
    geo_chips = geometric_bet_amount(eff_stack, p_now, streets_rem)

    ev_fold = 0.0
    ev_check: float | None = None
    ev_call: float | None = None
    ev_raise: float | None = None
    opt_raise_to: int | None = None

    can_raise = True if legal is None else (legal.can_bet or legal.can_raise)

    if b_faced <= 0:
        # Unopened postflop or BB option preflop
        ev_check = real_eq * p_now
        if can_raise and eff_stack > 0:
            # Ideal GTO bet size from texture / geometric sizing
            if street == "preflop":
                bet_chips = min(eff_stack, 2.5 * bb)
            else:
                if real_eq >= 0.68:
                    target_frac = min(1.10, max(0.72, geo_frac if spr_val <= 6.0 else 0.78))
                else:
                    target_frac = tex.recommended_cbet_frac if spr_val > 3.5 else min(1.0, max(0.33, geo_frac))
                bet_chips = min(eff_stack, max(bb, round(p_now * target_frac)))
            if legal is not None:
                total_to = max(legal.min_to, min(legal.max_to, int(my_street_bet + bet_chips)))
                bet_chips = max(1.0, float(total_to - my_street_bet))
                opt_raise_to = total_to
            else:
                opt_raise_to = int(my_street_bet + bet_chips)

            if street == "preflop":
                chart_act = _chart_preflop_recommendation(hc, position, raises_this_street, 1.0)
                if chart_act == "raise" or hc in RFI_RANGES["CO"]["raise"]:
                    ev_raise = ev_check + 0.65 * bb
                else:
                    ev_raise = ev_check - 0.50 * bb
            else:
                # Opponent fold frequency anchored around alpha = B / (P + B), modulated by range advantage & multiway
                base_fe = optimal_bluff_alpha(p_now, bet_chips, pot_includes_bet=False)
                fe = min(0.68, max(0.10, (base_fe + 0.08 * adv.range_advantage) / (n_opp ** 0.55)))
                # When called, only stronger hands continue, so hero's equity when called is compressed unless nutted
                eq_called = max(0.02, min(0.98, real_eq - (0.11 + 0.03 * (n_opp - 1)) * (1.0 - real_eq)))
                ev_raise_raw = fe * p_now + (1.0 - fe) * (eq_called * (p_now + 2.0 * bet_chips) - bet_chips)
                # Value threshold scales with opponents; bluffs use alpha-balanced deterministic selection
                val_thresh = (0.56 if street == "flop" else (0.58 if street == "turn" else 0.60)) + 0.04 * (n_opp - 1)
                is_value = real_eq >= val_thresh
                bluff_share = optimal_bluff_share(p_now, bet_chips, pot_includes_bet=False)
                # Deterministic mixed-strategy selector in [0, 1) from hole + board so EV evaluation and agent agree
                mix_u = (((hole[0] * 37 + hole[-1] * 13 + len(board) * 29 + int(p_now)) % 100) / 100.0) if hole else 0.5
                is_good_bluff = (
                    street in ("flop", "turn")
                    and n_opp == 1
                    and 0.25 <= real_eq <= 0.36
                    and adv.range_advantage >= 0.04
                    and mix_u < bluff_share
                )
                if is_value:
                    ev_raise = max(ev_raise_raw, ev_check + 0.25 * bb)
                elif is_good_bluff:
                    ev_raise = max(ev_raise_raw, ev_check + 0.15 * bb)
                else:
                    # Checking is preferred over spewing with medium/weak showdown value
                    ev_raise = min(ev_raise_raw, ev_check - 0.25 * bb)
    else:
        # Facing a bet of b_faced
        if street == "preflop":
            # Use positional GTO preflop chart bonus/penalty so preflop EV reflects chart equilibrium
            chart_act = _chart_preflop_recommendation(hc, position, raises_this_street, (my_street_bet + b_faced) / bb)
            base_call_ev = real_eq * (p_before + 2.0 * b_faced) - b_faced
            if chart_act == "call":
                ev_call = max(0.25 * bb, base_call_ev)
            elif chart_act == "fold" or raises_this_street == 0:
                # Out-of-range preflop cold-call or open-limp loses EV via reverse implied odds & squeeze
                ev_call = min(-0.35 * bb, base_call_ev - 0.80 * bb)
            else:
                ev_call = base_call_ev
        else:
            # Postflop range narrowing vs bettor/raiser: bettors hold top ~42% of range, raisers hold top ~22%
            street_narrow = 0.02 if street == "flop" else (0.05 if street == "turn" else 0.07)
            raise_narrow = 0.16 * max(0, raises_this_street - 1)
            multi_narrow = 0.03 * max(0, n_opp - 1)
            eq_vs_bet = max(0.01, real_eq - (street_narrow + raise_narrow + multi_narrow) * (1.0 - 0.55 * (real_eq ** 2)))
            ev_call = eq_vs_bet * (p_before + 2.0 * b_faced) - b_faced

        if can_raise and eff_stack > b_faced:
            if street == "preflop":
                mult = 3.0 if position in ("BTN", "CO") else 3.5
                target_to = round((my_street_bet + b_faced) * mult) if raises_this_street >= 1 else round(2.5 * bb)
            else:
                raise_frac = 0.85 if real_eq >= 0.80 else max(tex.recommended_cbet_frac, 0.65)
                target_to = int(my_street_bet + b_faced + round(raise_frac * (p_now + b_faced)))
            if legal is not None:
                target_to = max(legal.min_to, min(legal.max_to, int(target_to)))
            opt_raise_to = int(target_to)
            raise_added = max(b_faced + bb, min(eff_stack, float(target_to - my_street_bet)))
            villain_call = max(0.0, raise_added - b_faced)
            base_fe = optimal_bluff_alpha(p_now + b_faced, villain_call, pot_includes_bet=False)
            fe = min(0.65, max(0.08, base_fe / (n_opp ** 0.55) * (0.75 if raises_this_street >= 2 else 0.90)))
            eq_called = max(0.02, min(0.98, real_eq - (0.15 + 0.08 * max(0, raises_this_street - 1)) * (1.0 - real_eq)))
            ev_raise_raw = fe * p_now + (1.0 - fe) * (eq_called * (p_now + raise_added + villain_call) - raise_added)

            if street == "preflop":
                chart_act = _chart_preflop_recommendation(hc, position, raises_this_street, (my_street_bet + b_faced) / bb)
                if chart_act == "raise":
                    ev_raise = max(ev_raise_raw, (ev_call or 0.0) + 0.50 * bb, 0.60 * bb)
                elif chart_act == "call":
                    ev_raise = min(ev_raise_raw, (ev_call or 0.0) - 0.30 * bb)
                else:
                    ev_raise = min(-0.50 * bb, ev_raise_raw - 0.80 * bb)
            else:
                val_raise_thresh = 0.74 if raises_this_street <= 1 else 0.84
                if real_eq >= val_raise_thresh:
                    ev_raise = max(ev_raise_raw, (ev_call or 0.0) + 0.35 * bb)
                else:
                    ev_raise = min(ev_raise_raw, max(ev_fold, ev_call or 0.0) - 0.35 * bb)



    # Determine best legal action and EV
    candidates: dict[str, float] = {}
    if b_faced <= 0:
        candidates["check"] = ev_check if ev_check is not None else 0.0
        if ev_raise is not None and can_raise:
            candidates["bet" if (legal and legal.can_bet) else "raise"] = ev_raise
    else:
        candidates["fold"] = ev_fold
        if ev_call is not None:
            candidates["call"] = ev_call
        if ev_raise is not None and can_raise:
            candidates["raise"] = ev_raise

    best_act = max(candidates, key=lambda k: candidates[k])
    best_ev = candidates[best_act]

    norm_chosen = chosen_action.lower()
    if norm_chosen in ("allin", "all-in"):
        norm_chosen = "raise" if b_faced > 0 else "bet"
    if norm_chosen == "bet" and "bet" not in candidates and "raise" in candidates:
        norm_chosen = "raise"
    if norm_chosen == "raise" and "raise" not in candidates and "bet" in candidates:
        norm_chosen = "bet"

    chosen_ev = candidates.get(norm_chosen, ev_fold)
    # Check sizing error if both best and chosen are aggressive postflop
    sizing_penalty = 0.0
    if norm_chosen in ("bet", "raise") and best_act in ("bet", "raise") and street != "preflop" and chosen_amount > 0 and opt_raise_to:
        rel_diff = abs(chosen_amount - opt_raise_to) / max(bb, p_now)
        if rel_diff > 0.35:
            sizing_penalty = min(0.45 * bb, 0.25 * rel_diff * bb)
            chosen_ev -= sizing_penalty

    delta_chips = max(0.0, best_ev - chosen_ev)
    delta_bb = delta_chips / bb

    mistake: str | None = None
    if delta_bb >= 0.15:
        if street == "preflop" and norm_chosen == "call" and raises_this_street == 0 and position != "BB":
            mistake = "open_limp"
        elif norm_chosen == "fold" and best_act in ("call", "raise"):
            mistake = "overfold_vs_mdf" if b_faced > 0 and real_eq >= po else "bad_fold"
        elif norm_chosen == "call" and best_act == "fold":
            mistake = "bad_call_pot_odds"
        elif norm_chosen in ("check", "call") and best_act in ("bet", "raise"):
            mistake = "too_passive_missed_value"
        elif norm_chosen in ("bet", "raise") and best_act in ("fold", "check", "call"):
            mistake = "overplay_or_bad_bluff"
        elif sizing_penalty > 0:
            mistake = "suboptimal_cbet_sizing"

    return SpotEVAnalysis(
        street=street,
        position=position,
        hole_class=hc,
        pot_before_bet=round(p_before, 2),
        bet_faced=round(b_faced, 2),
        pot_including_bet=round(p_now, 2),
        effective_stack=round(eff_stack, 2),
        big_blind=bb,
        pot_odds=round(po, 4),
        required_equity=round(req_eq, 4),
        mdf=round(mdf_val, 4),
        alpha=round(alpha_val, 4),
        spr=round(spr_val, 2) if math.isfinite(spr_val) else 999.0,
        geometric_bet_frac=round(geo_frac, 4),
        geometric_bet_chips=round(geo_chips, 2),
        board_texture=tex.primary,
        board_wetness=tex.wetness,
        range_advantage=adv.range_advantage,
        nut_advantage=adv.nut_advantage,
        raw_equity=round(raw_eq, 4),
        realized_equity=round(real_eq, 4),
        ev_fold=round(ev_fold, 2),
        ev_check=round(ev_check, 2) if ev_check is not None else None,
        ev_call=round(ev_call, 2) if ev_call is not None else None,
        ev_raise=round(ev_raise, 2) if ev_raise is not None else None,
        optimal_raise_to=opt_raise_to,
        best_action=best_act,
        best_ev=round(best_ev, 2),
        chosen_action=norm_chosen,
        chosen_ev=round(chosen_ev, 2),
        delta_ev_chips=round(delta_chips, 2),
        delta_ev_bb=round(delta_bb, 3),
        mistake_type=mistake,
    )


def _chart_preflop_recommendation(hc: str, pos: str, raises: int, facing_bb: float) -> str:
    """Lookup canonical 5-max / 6-max GTO preflop action ('raise' | 'call' | 'fold' | 'check')."""
    p = pos if pos in ("UTG", "HJ", "CO", "BTN", "SB", "BB") else "HJ"
    if raises == 0 or facing_bb <= 1.05:
        if p == "BB":
            return "check"
        row = RFI_RANGES.get(p, RFI_RANGES["HJ"])
        return "raise" if hc in row["raise"] else "fold"
    if raises == 1 or facing_bb <= 5.0:
        if p == "BB":
            row_bb = BB_DEFENSE_RANGES["vs_HJ"] if facing_bb >= 2.8 else BB_DEFENSE_RANGES["vs_CO"]
            if hc in row_bb["threebet"]:
                return "raise"
            if hc in row_bb["call"]:
                return "call"
            return "fold"
        if p == "SB":
            row_sb = VS_OPEN_RANGES["vs_CO"] if facing_bb <= 2.6 else VS_OPEN_RANGES["vs_HJ"]
            if hc in row_sb["threebet"]:
                return "raise"
            if hc in row_sb["call"]:
                return "call"
            return "fold"
        row_vs = VS_OPEN_RANGES["vs_CO"] if p == "BTN" else VS_OPEN_RANGES["vs_HJ"]
        if hc in row_vs["threebet"]:
            return "raise"
        if hc in row_vs["call"]:
            return "call"
        return "fold"
    # Facing 3-bet or 4-bet
    if hc in VS_3BET_RANGES["fourbet_value"] or (raises == 2 and hc in VS_3BET_RANGES["fourbet_bluff"]):
        return "raise"
    if raises == 2 and hc in VS_3BET_RANGES["call"] and facing_bb <= 12.0:
        return "call"
    return "fold"


# ---------------------------------------------------------------------------
# 5. Five-Max Tournament Agents (Claude, Codex, Argon, Local Bossman, GTO Solver)
# ---------------------------------------------------------------------------


class GTOTournamentAgent:
    """Base class for the 5-max tournament agents with rich GTO math & style differentiation."""

    def __init__(self, name: str, role: str, params: dict[str, Any] | None = None) -> None:
        self.name = name
        self.role = role
        self.params = params or {}
        self.decisions_log: list[SpotEVAnalysis] = []
        self.three_bet_opportunities: int = 0
        self.three_bets_made: int = 0
        self.faced_bet_opportunities: int = 0
        self.defended_vs_bet: int = 0

    def act(self, obs: Observation, rng: random.Random) -> Action:
        pos = seat_position_name(obs.position, obs.n_players)
        hc = hand_class(obs.hole)
        legal = obs.legal
        bb = obs.big_blind

        # Track 3-bet opportunities on preflop
        if obs.street == "preflop" and obs.raises_this_street == 1 and legal.can_raise:
            self.three_bet_opportunities += 1
        if obs.to_call > 0:
            self.faced_bet_opportunities += 1

        action = self._choose_action(obs, pos, hc, rng)
        legal.validate(action)

        if obs.street == "preflop" and obs.raises_this_street == 1 and action.kind == "raise":
            self.three_bets_made += 1
        if obs.to_call > 0 and action.kind in ("call", "raise"):
            self.defended_vs_bet += 1

        # Record full street-by-street GTO EV evaluation for every decision
        ev_spot = analyze_spot_ev(
            hole=obs.hole,
            board=obs.board,
            street=obs.street,
            position=pos,
            pot_including_bet=obs.pot,
            to_call=obs.to_call,
            stack=obs.stack,
            big_blind=bb,
            n_opponents=obs.n_active_opponents,
            raises_this_street=obs.raises_this_street,
            legal=legal,
            chosen_action=action.kind,
            chosen_amount=action.amount,
            my_street_bet=obs.my_street_bet,
            rng=random.Random((obs.hand_id + 1) * 131 + obs.seat * 17 + len(obs.board)),
            sims=90,
        )
        self.decisions_log.append(ev_spot)
        return action

    def _choose_action(self, obs: Observation, pos: str, hc: str, rng: random.Random) -> Action:
        if self.role == "bossman_baseline":
            return self._act_bossman_baseline(obs, pos, hc, rng)
        if self.role == "claude_tag":
            return self._act_claude_tag(obs, pos, hc, rng)
        if self.role == "codex_cfr":
            return self._act_codex_cfr(obs, pos, hc, rng)
        if self.role == "argon_adaptive":
            return self._act_argon_adaptive(obs, pos, hc, rng)
        if self.role == "gto_solver":
            return self._act_gto_solver(obs, pos, hc, rng)
        if self.role == "bossman_calibrated":
            return self._act_bossman_calibrated(obs, pos, hc, rng)
        return self._act_gto_solver(obs, pos, hc, rng)

    # ---- Role 1: Local Bossman Pre-Calibration Baseline ----
    def _act_bossman_baseline(self, obs: Observation, pos: str, hc: str, rng: random.Random) -> Action:
        """Un-calibrated Local Bossman baseline:
        Exhibits the exact classical heuristic leaks prior to GTO calibration:
        1. Flat preflop percentile without positional charts, open-limping medium hands (pct in 0.14..0.34).
        2. Over-folding BB/SB vs late-position steals (only defends top ~16% instead of MDF / BB chart).
        3. Static 0.65x pot postflop sizing regardless of dry vs wet board texture or geometric SPR.
        4. Over-calling postflop when raw equity vs random hands looks decent (ignoring range narrowing) OR
           under-defending river bluff-catchers vs MDF.
        """
        legal = obs.legal
        bb = obs.big_blind
        n_opp = max(1, obs.n_active_opponents)
        pct = preflop_percentile(obs.hole)
        po = pot_odds(obs.pot, obs.to_call, pot_includes_bet=True)

        if obs.street == "preflop":
            if obs.current_bet <= bb:
                # Leak: raises only top 13%, open-limps 13%..34%, ignores position
                if pct <= 0.13 and (legal.can_bet or legal.can_raise):
                    return _clamp_raise(legal, round(3.0 * bb))
                if pct <= 0.34:
                    return Action("check") if legal.can_check else Action("call")
                return Action("check") if legal.can_check else Action("fold")
            # Leak: facing an open, overfolds BB/SB (only defends top 15%) and rarely 3-bets (only top 3.5%)
            if pct <= 0.035 and (legal.can_bet or legal.can_raise):
                return _clamp_raise(legal, round(obs.current_bet * 3.0))
            if pct <= 0.15 and preflop_equity(obs.hole, n_opp) >= po:
                return Action("check") if legal.can_check else Action("call")
            return Action("check") if legal.can_check else Action("fold")

        # Postflop baseline: raw MC equity vs random hands, static 0.65x pot bet, uncalibrated call slack
        eq = mc_equity(obs.hole, obs.board, min(n_opp, 3), 60, rng)
        if obs.to_call == 0:
            # Leak: misses thin value and bets static 0.70x pot even on dry boards, bluffs too randomly
            if eq >= 0.64 and rng.random() < 0.55 and (legal.can_bet or legal.can_raise):
                return _clamp_raise(legal, obs.my_street_bet + round(0.70 * obs.pot))
            if eq < 0.35 and rng.random() < 0.18 and (legal.can_bet or legal.can_raise):
                return _clamp_raise(legal, obs.my_street_bet + round(0.75 * obs.pot))
            return Action("check") if legal.can_check else Action("call")

        # Facing postflop bet: overfolds river bluff-catchers, calls flop/turn too loosely with raw equity
        if obs.street == "river":
            if eq >= po + 0.14:
                return Action("call")
            return Action("check") if legal.can_check else Action("fold")
        if eq >= po - 0.03:
            return Action("call")
        return Action("check") if legal.can_check else Action("fold")

    # ---- Role 2: Claude (Tactical TAG Profile) ----
    def _act_claude_tag(self, obs: Observation, pos: str, hc: str, rng: random.Random) -> Action:
        """Claude Sonnet tactical TAG profile: disciplined positional RFI, selective 3-bets, solid postflop TAG."""
        legal = obs.legal
        bb = obs.big_blind
        n_opp = max(1, obs.n_active_opponents)
        if obs.street == "preflop":
            facing_bb = obs.current_bet / max(1, bb)
            rec = _chart_preflop_recommendation(hc, pos, obs.raises_this_street, facing_bb)
            # Claude plays slightly tighter than pure GTO on the fringes
            pct = preflop_percentile(obs.hole)
            if obs.raises_this_street == 0:
                if rec == "raise" and pct <= 0.44 and (legal.can_bet or legal.can_raise):
                    open_bb = 3.0 if pos == "SB" else 2.5
                    return _clamp_raise(legal, round(open_bb * bb))
                return Action("check") if legal.can_check else Action("fold")
            if rec == "raise" and (legal.can_bet or legal.can_raise):
                mult = 3.0 if pos in ("BTN", "CO") else 3.5
                return _clamp_raise(legal, round(obs.current_bet * mult))
            if rec == "call" and pct <= 0.38:
                return Action("check") if legal.can_check else Action("call")
            return Action("check") if legal.can_check else Action("fold")

        tex = classify_board(obs.board)
        adv = compute_range_and_nut_advantage(obs.board, pos, "BB")
        raw_eq = mc_equity(obs.hole, obs.board, min(n_opp, 3), 80, rng)
        _, real_eq = estimate_realized_equity(raw_eq, obs.street, pos, n_opp, obs.raises_this_street, obs.to_call / max(1, obs.pot), tex)
        po = pot_odds(obs.pot, obs.to_call, pot_includes_bet=True)

        if obs.to_call == 0:
            cbet_size = max(bb, round(obs.pot * tex.recommended_cbet_frac))
            if real_eq >= 0.58 and (legal.can_bet or legal.can_raise):
                return _clamp_raise(legal, obs.my_street_bet + cbet_size)
            if n_opp == 1 and 0.24 <= real_eq <= 0.36 and rng.random() < (0.28 + 0.15 * adv.range_advantage) and (legal.can_bet or legal.can_raise):
                return _clamp_raise(legal, obs.my_street_bet + cbet_size)
            return Action("check") if legal.can_check else Action("call")

        if real_eq >= 0.74 and (legal.can_bet or legal.can_raise) and rng.random() < 0.70:
            return _clamp_raise(legal, obs.current_bet + round(0.65 * (obs.pot + obs.to_call)))
        if real_eq >= po + 0.01:
            return Action("call")
        return Action("check") if legal.can_check else Action("fold")

    # ---- Role 3: Codex (Analytical / CFR Profile) ----
    def _act_codex_cfr(self, obs: Observation, pos: str, hc: str, rng: random.Random) -> Action:
        """Codex analytical CFR profile: geometric bet sizing, alpha-calibrated bluffs, pot-odds precision."""
        legal = obs.legal
        bb = obs.big_blind
        n_opp = max(1, obs.n_active_opponents)
        if obs.street == "preflop":
            facing_bb = obs.current_bet / max(1, bb)
            rec = _chart_preflop_recommendation(hc, pos, obs.raises_this_street, facing_bb)
            if rec == "raise" and (legal.can_bet or legal.can_raise):
                size_bb = (3.0 if pos == "SB" else 2.5) if obs.raises_this_street == 0 else (3.2 * facing_bb)
                return _clamp_raise(legal, round(size_bb * bb))
            if rec == "call":
                return Action("check") if legal.can_check else Action("call")
            return Action("check") if legal.can_check else Action("fold")

        tex = classify_board(obs.board)
        raw_eq = mc_equity(obs.hole, obs.board, min(n_opp, 3), 80, rng)
        _, real_eq = estimate_realized_equity(raw_eq, obs.street, pos, n_opp, obs.raises_this_street, obs.to_call / max(1, obs.pot), tex)
        po = pot_odds(obs.pot, obs.to_call, pot_includes_bet=True)
        streets_rem = {"flop": 3, "turn": 2, "river": 1}.get(obs.street, 1)
        geo_amt = max(bb, round(geometric_bet_amount(obs.stack, obs.pot, streets_rem)))

        if obs.to_call == 0:
            bet_amt = min(obs.stack, max(bb, round(obs.pot * tex.recommended_cbet_frac) if spr(obs.stack, obs.pot) > 4 else geo_amt))
            bluff_share = optimal_bluff_share(obs.pot, bet_amt, pot_includes_bet=False)
            if real_eq >= 0.57 and (legal.can_bet or legal.can_raise):
                return _clamp_raise(legal, obs.my_street_bet + bet_amt)
            if n_opp == 1 and 0.22 <= real_eq <= 0.34 and rng.random() < bluff_share and (legal.can_bet or legal.can_raise):
                return _clamp_raise(legal, obs.my_street_bet + bet_amt)
            return Action("check") if legal.can_check else Action("call")

        if real_eq >= 0.72 and (legal.can_bet or legal.can_raise):
            return _clamp_raise(legal, obs.current_bet + geo_amt)
        if real_eq >= po:
            return Action("call")
        return Action("check") if legal.can_check else Action("fold")

    # ---- Role 4: Argon (Balanced GTO-Adaptive Profile) ----
    def _act_argon_adaptive(self, obs: Observation, pos: str, hc: str, rng: random.Random) -> Action:
        """Argon balanced GTO-adaptive profile: combines positional GTO charts with dynamic multiway & SPR scaling."""
        legal = obs.legal
        bb = obs.big_blind
        n_opp = max(1, obs.n_active_opponents)
        if obs.street == "preflop":
            facing_bb = obs.current_bet / max(1, bb)
            rec = _chart_preflop_recommendation(hc, pos, obs.raises_this_street, facing_bb)
            if rec == "raise" and (legal.can_bet or legal.can_raise):
                size_bb = (3.0 if pos == "SB" else 2.5) if obs.raises_this_street == 0 else (3.0 if pos in ("BTN", "CO") else 3.5) * facing_bb
                return _clamp_raise(legal, round(size_bb * bb))
            if rec == "call":
                return Action("check") if legal.can_check else Action("call")
            return Action("check") if legal.can_check else Action("fold")

        tex = classify_board(obs.board)
        adv = compute_range_and_nut_advantage(obs.board, pos, "BB")
        raw_eq = mc_equity(obs.hole, obs.board, min(n_opp, 3), 85, rng)
        _, real_eq = estimate_realized_equity(raw_eq, obs.street, pos, n_opp, obs.raises_this_street, obs.to_call / max(1, obs.pot), tex)
        po = pot_odds(obs.pot, obs.to_call, pot_includes_bet=True)

        if obs.to_call == 0:
            size_frac = tex.recommended_cbet_frac
            bet_amt = max(bb, round(obs.pot * size_frac))
            if real_eq >= 0.55 + 0.04 * (n_opp - 1) and (legal.can_bet or legal.can_raise):
                return _clamp_raise(legal, obs.my_street_bet + bet_amt)
            if n_opp == 1 and 0.22 <= real_eq <= 0.35 and adv.range_advantage >= 0 and rng.random() < optimal_bluff_share(obs.pot, bet_amt) and (legal.can_bet or legal.can_raise):
                return _clamp_raise(legal, obs.my_street_bet + bet_amt)
            return Action("check") if legal.can_check else Action("call")

        if real_eq >= 0.72 and (legal.can_bet or legal.can_raise):
            return _clamp_raise(legal, obs.current_bet + round(0.70 * (obs.pot + obs.to_call)))
        if real_eq >= po:
            return Action("call")
        return Action("check") if legal.can_check else Action("fold")

    # ---- Role 5: GTO Reference Solver Bot (Equilibrium Baseline) ----
    def _act_gto_solver(self, obs: Observation, pos: str, hc: str, rng: random.Random) -> Action:
        """Pure GTO Reference Solver Bot: follows exact EV-maximizing action from ``analyze_spot_ev``."""
        legal = obs.legal
        bb = obs.big_blind
        spot_rng = random.Random((obs.hand_id + 1) * 131 + obs.seat * 17 + len(obs.board))
        ev = analyze_spot_ev(
            hole=obs.hole,
            board=obs.board,
            street=obs.street,
            position=pos,
            pot_including_bet=obs.pot,
            to_call=obs.to_call,
            stack=obs.stack,
            big_blind=bb,
            n_opponents=obs.n_active_opponents,
            raises_this_street=obs.raises_this_street,
            legal=legal,
            chosen_action="check" if legal.can_check else "fold",
            chosen_amount=0,
            my_street_bet=obs.my_street_bet,
            rng=spot_rng,
            sims=90,
        )
        if ev.best_action in ("bet", "raise") and (legal.can_bet or legal.can_raise):
            return _clamp_raise(legal, ev.optimal_raise_to or legal.min_to)
        if ev.best_action == "call" and legal.can_call:
            return Action("call")
        if ev.best_action == "check" and legal.can_check:
            return Action("check")
        return Action("check") if legal.can_check else Action("fold")

    # ---- Role 6: Local Bossman (Post-Calibration Upgraded GTO Math) ----
    def _act_bossman_calibrated(self, obs: Observation, pos: str, hc: str, rng: random.Random) -> Action:
        """Calibrated Local Bossman GTO Engine:
        Fixes every leak identified in the pre-calibration baseline:
        1. Zero open-limps: strict positional 5-max/6-max RFI + iso-raising vs limpers (2.5bb + 0.9bb/limper).
        2. Calibrated BB/SB defense vs steals using exact pot odds, realization factor, and tight defense vs 3-bets.
        3. Board-texture & range/nut advantage driven c-bet sizing (0.33x on dry/paired, 0.68x on wet/connected)
           plus geometric bet sizing when SPR <= 3.5.
        4. Exact MDF & pot-odds river bluff-catching and alpha-balanced polarized flop/turn bluffs.
        5. Multi-street EV maximization with range-narrowing adjustments against TAG barrels and raises.
        """
        legal = obs.legal
        bb = obs.big_blind

        if obs.street == "preflop":
            facing_bb = obs.current_bet / max(1, bb)
            limpers = max(0, round((obs.pot - 1.5 * bb) / bb)) if obs.raises_this_street == 0 else 0
            rec = _chart_preflop_recommendation(hc, pos, obs.raises_this_street, facing_bb)
            if obs.raises_this_street == 0:
                if rec == "raise" and (legal.can_bet or legal.can_raise):
                    base_open = 3.0 if pos == "SB" else 2.5
                    return _clamp_raise(legal, round((base_open + 0.9 * limpers) * bb))
                return Action("check") if legal.can_check else Action("fold")
            if rec == "raise" and (legal.can_bet or legal.can_raise):
                mult = 3.0 if pos in ("BTN", "CO") else 3.5
                return _clamp_raise(legal, round(obs.current_bet * mult))
            if rec == "call":
                return Action("check") if legal.can_check else Action("call")
            return Action("check") if legal.can_check else Action("fold")

        # Postflop calibrated GTO decision using exact spot EV analysis
        spot_rng = random.Random((obs.hand_id + 1) * 131 + obs.seat * 17 + len(obs.board))
        ev = analyze_spot_ev(
            hole=obs.hole,
            board=obs.board,
            street=obs.street,
            position=pos,
            pot_including_bet=obs.pot,
            to_call=obs.to_call,
            stack=obs.stack,
            big_blind=bb,
            n_opponents=obs.n_active_opponents,
            raises_this_street=obs.raises_this_street,
            legal=legal,
            chosen_action="check" if legal.can_check else "fold",
            chosen_amount=0,
            my_street_bet=obs.my_street_bet,
            rng=spot_rng,
            sims=90,
        )

        # Low-SPR commitment rule: if SPR <= 2.2 and realized equity >= 0.58, commit stack / value bet geometrically
        if ev.spr <= 2.2 and ev.realized_equity >= 0.58:
            if obs.to_call > 0:
                if ev.realized_equity >= 0.68 and (legal.can_bet or legal.can_raise):
                    return _clamp_raise(legal, legal.max_to)
                return Action("call") if legal.can_call else Action("check")
            if legal.can_bet or legal.can_raise:
                return _clamp_raise(legal, obs.my_street_bet + max(bb, round(ev.geometric_bet_chips)))

        if ev.best_action in ("bet", "raise") and (legal.can_bet or legal.can_raise):
            return _clamp_raise(legal, ev.optimal_raise_to or legal.min_to)
        if ev.best_action == "call" and legal.can_call:
            return Action("call")
        if ev.best_action == "check" and legal.can_check:
            return Action("check")
        return Action("check") if legal.can_check else Action("fold")





def _clamp_raise(legal: LegalActions, target: int) -> Action:
    kind = "bet" if legal.can_bet else "raise"
    amt = max(legal.min_to, min(legal.max_to, int(target)))
    if amt >= 0.88 * legal.max_to:
        amt = legal.max_to
    return Action(kind, amt)


# ---------------------------------------------------------------------------
# 6. 5-Player 1000-Hand Tournament Simulation & Leak Calibration
# ---------------------------------------------------------------------------

TOURNAMENT_SEATS_SPEC = (
    ("Claude", "claude_tag", "Claude Sonnet tactical/TAG profile"),
    ("Codex", "codex_cfr", "Codex analytical/CFR profile"),
    ("Argon", "argon_adaptive", "Argon balanced GTO-adaptive profile"),
    ("Local Bossman", "bossman", "Local Bossman poker engine"),
    ("GTO Reference Solver Bot", "gto_solver", "Pure MDF/equilibrium reference solver bot"),
)


def build_5max_agents(bossman_mode: str = "baseline", calibrated_params: dict[str, Any] | None = None) -> list[GTOTournamentAgent]:
    """Create the 5 tournament agents in canonical seat order (Seats 1..5)."""
    agents: list[GTOTournamentAgent] = []
    for name, role_key, desc in TOURNAMENT_SEATS_SPEC:
        if role_key == "bossman":
            actual_role = "bossman_calibrated" if bossman_mode == "calibrated" else "bossman_baseline"
            agents.append(GTOTournamentAgent(name, actual_role, calibrated_params or {"description": desc}))
        else:
            agents.append(GTOTournamentAgent(name, role_key, {"description": desc}))
    return agents


def run_5max_gto_tournament(
    hands: int = 1000,
    seed: int = 20261014,
    big_blind: int = 2,
    stack_bb: int = 100,
    bossman_mode: str = "baseline",
    calibrated_params: dict[str, Any] | None = None,
    duplicate: bool = True,
    keep_hand_logs: int = 50,
) -> dict[str, Any]:
    """Execute a 5-player, ``hands``-hand No-Limit Hold'em simulation with full GTO EV tracking.

    In ``duplicate=True`` mode, ``deals = hands // 5`` unique decks are each played across all 5 seat
    rotations (5 * deals = 1000 hands), guaranteeing every bot receives the exact same cards in every
    position while preserving strict per-hand and total chip conservation (``sum(net_chips) == 0``).
    """
    n = 5
    small_blind = max(1, big_blind // 2)
    stack = stack_bb * big_blind
    agents = build_5max_agents(bossman_mode=bossman_mode, calibrated_params=calibrated_params)
    rotations = n if duplicate else 1
    deals = max(1, hands // rotations)

    per_bot: list[dict[str, Any]] = [
        {
            "seat": i + 1,
            "name": a.name,
            "role": a.role,
            "hands": 0,
            "net_chips": 0,
            "vpip": 0,
            "pfr": 0,
            "post_aggr": 0,
            "post_calls": 0,
            "saw_flop": 0,
            "showdowns": 0,
            "showdown_wins": 0,
            "block_bb": [],
            "by_position": defaultdict(lambda: {"hands": 0, "net_bb": 0.0, "vpip": 0, "pfr": 0}),
        }
        for i, a in enumerate(agents)
    ]

    hand_logs: list[dict[str, Any]] = []
    played = 0

    for d in range(deals):
        dseed = deal_seed(seed, d)
        block_chips = [0] * n
        for r in range(rotations):
            seat_of = [(i + r) % n for i in range(n)]
            seated: list[GTOTournamentAgent] = [None] * n  # type: ignore[list-item]
            for i, s in enumerate(seat_of):
                seated[s] = agents[i]

            button_seat = d % n
            res: HandResult = play_hand(
                seated,
                [stack] * n,
                button=button_seat,
                small_blind=small_blind,
                big_blind=big_blind,
                deck_seed=dseed,
                hand_id=played + 1,
            )
            played += 1

            # Position mapping for this hand
            pos_by_seat: dict[int, str] = {}
            s_cur = button_seat
            for p_idx in range(n):
                pos_by_seat[s_cur] = seat_position_name(p_idx, n)
                s_cur = (s_cur + 1) % n

            if len(hand_logs) < keep_hand_logs:
                hand_logs.append(
                    {
                        "hand_id": res.hand_id,
                        "deal_id": d + 1,
                        "rotation": r + 1,
                        "button_seat": res.button,
                        "board": cards_str(res.board),
                        "board_texture": classify_board(res.board).primary,
                        "seats": {
                            seated[s].name: {
                                "table_seat": s,
                                "position": pos_by_seat[s],
                                "hole": cards_str(res.holes[s]),
                                "delta_chips": res.deltas[s],
                                "delta_bb": round(res.deltas[s] / big_blind, 2),
                                "showdown_hand": res.showdown_hands.get(s),
                            }
                            for s in range(n)
                        },
                        "actions": [
                            {
                                "player": seated[a.seat].name,
                                "street": a.street,
                                "action": a.kind,
                                "added": a.added,
                                "to": a.to,
                            }
                            for a in res.actions
                        ],
                    }
                )

            for i, s in enumerate(seat_of):
                b = per_bot[i]
                st = res.stats[s]
                pos_label = pos_by_seat[s]
                b["hands"] += 1
                b["net_chips"] += res.deltas[s]
                block_chips[i] += res.deltas[s]
                b["vpip"] += int(st.vpip)
                b["pfr"] += int(st.pfr)
                b["post_aggr"] += st.post_aggr
                b["post_calls"] += st.post_calls
                b["saw_flop"] += int(st.saw_flop)
                b["showdowns"] += int(st.showdown)
                b["showdown_wins"] += int(st.won_showdown)
                pstat = b["by_position"][pos_label]
                pstat["hands"] += 1
                pstat["net_bb"] += res.deltas[s] / big_blind
                pstat["vpip"] += int(st.vpip)
                pstat["pfr"] += int(st.pfr)

        for i in range(n):
            per_bot[i]["block_bb"].append(block_chips[i] / big_blind / rotations)

    # Compile final statistics and GTO EV loss metrics per player
    standings: list[dict[str, Any]] = []
    for i, b in enumerate(per_bot):
        ag = agents[i]
        h = max(1, b["hands"])
        decs = ag.decisions_log
        n_dec = max(1, len(decs))
        ci_block = MeanCI.of(b["block_bb"])
        bb100_ci = MeanCI(ci_block.mean * 100, ci_block.sd * 100, ci_block.n, ci_block.lo * 100, ci_block.hi * 100)

        total_ev_loss_bb = sum(d.delta_ev_bb for d in decs)
        avg_ev_loss_bb_hand = total_ev_loss_bb / h
        avg_ev_loss_bb_dec = total_ev_loss_bb / n_dec
        agree_cnt = sum(1 for d in decs if d.delta_ev_bb < 0.15)
        mistakes = [d for d in decs if d.mistake_type is not None]
        mistake_counts = Counter(d.mistake_type for d in mistakes if d.mistake_type)
        street_ev_loss: dict[str, float] = defaultdict(float)
        for d in decs:
            street_ev_loss[d.street] += d.delta_ev_bb

        standings.append(
            {
                "seat": b["seat"],
                "name": b["name"],
                "role": b["role"],
                "hands": b["hands"],
                "decisions": len(decs),
                "net_chips": b["net_chips"],
                "net_bb": round(b["net_chips"] / big_blind, 2),
                "bb_per_100": round(bb100_ci.mean, 2),
                "bb_per_100_ci95": [round(bb100_ci.lo, 2), round(bb100_ci.hi, 2)],
                "vpip_pct": round(100.0 * b["vpip"] / h, 1),
                "pfr_pct": round(100.0 * b["pfr"] / h, 1),
                "three_bet_pct": round(100.0 * ag.three_bets_made / max(1, ag.three_bet_opportunities), 1),
                "af": round(b["post_aggr"] / max(1, b["post_calls"]), 2),
                "wtsd_pct": round(100.0 * b["showdowns"] / max(1, b["saw_flop"]), 1),
                "w_sd_pct": round(100.0 * b["showdown_wins"] / max(1, b["showdowns"]), 1),
                "mdf_defense_pct": round(100.0 * ag.defended_vs_bet / max(1, ag.faced_bet_opportunities), 1),
                "gto_agreement_pct": round(100.0 * agree_cnt / n_dec, 1),
                "avg_ev_loss_bb_per_hand": round(avg_ev_loss_bb_hand, 4),
                "avg_ev_loss_bb_per_decision": round(avg_ev_loss_bb_dec, 4),
                "total_ev_loss_bb": round(total_ev_loss_bb, 2),
                "mistakes_count": len(mistakes),
                "mistakes_by_type": dict(mistake_counts.most_common()),
                "ev_loss_by_street_bb": {k: round(v, 2) for k, v in street_ev_loss.items()},
                "by_position": {
                    pos: {
                        "hands": pd["hands"],
                        "net_bb": round(pd["net_bb"], 2),
                        "bb_per_100": round(100.0 * pd["net_bb"] / max(1, pd["hands"]), 1),
                        "vpip_pct": round(100.0 * pd["vpip"] / max(1, pd["hands"]), 1),
                        "pfr_pct": round(100.0 * pd["pfr"] / max(1, pd["hands"]), 1),
                    }
                    for pos, pd in sorted(b["by_position"].items())
                },
            }
        )

    ranked = sorted(standings, key=lambda x: x["bb_per_100"], reverse=True)
    for rank_idx, row in enumerate(ranked, 1):
        row["rank"] = rank_idx

    net_sum = sum(s["net_chips"] for s in standings)
    return {
        "tournament": "5-Max GTO Hold'em Simulation (1000 Hands)",
        "bossman_mode": bossman_mode,
        "hands_played": played,
        "deals": deals,
        "rotations_per_deal": rotations,
        "duplicate": duplicate,
        "seed": seed,
        "big_blind": big_blind,
        "stack_bb": stack_bb,
        "chip_conservation": net_sum == 0,
        "net_chips_sum": net_sum,
        "standings": ranked,
        "seats": standings,
        "sample_hands": hand_logs,
    }


def calibrate_bossman_from_tournament(baseline_res: dict[str, Any]) -> dict[str, Any]:
    """Extract Local Bossman's quantitative leaks from the baseline 1000-hand tournament and compute calibrated GTO corrections."""
    bossman_row = next(s for s in baseline_res["seats"] if s["name"] == "Local Bossman")
    gto_row = next(s for s in baseline_res["seats"] if s["name"] == "GTO Reference Solver Bot")
    mistakes = bossman_row["mistakes_by_type"]
    pos_stats = bossman_row["by_position"]

    leaks_found: list[dict[str, Any]] = []
    if mistakes.get("open_limp", 0) > 0 or (bossman_row["vpip_pct"] - bossman_row["pfr_pct"]) > 8.0:
        leaks_found.append(
            {
                "leak_id": "preflop_open_limp_and_flat_ranges",
                "observed": f"VPIP {bossman_row['vpip_pct']}% vs PFR {bossman_row['pfr_pct']}% (gap {bossman_row['vpip_pct'] - bossman_row['pfr_pct']:.1f} pp), open_limp mistakes={mistakes.get('open_limp', 0)}",
                "correction": "Replace flat preflop percentile + open-limping with positional 5-max/6-max GTO RFI charts (HJ 22%, CO 29%, BTN 50%, SB 3.0bb raise-or-fold) and iso-raise limpers to 2.5bb + 0.9bb/limper.",
            }
        )
    if mistakes.get("overfold_vs_mdf", 0) + mistakes.get("bad_fold", 0) > 0:
        bb_net = (pos_stats.get("BB") or {}).get("bb_per_100", 0.0)
        leaks_found.append(
            {
                "leak_id": "bb_sb_overfold_vs_steals_and_mdf",
                "observed": f"Overfold/bad_fold count={mistakes.get('overfold_vs_mdf', 0) + mistakes.get('bad_fold', 0)}, BB winrate={bb_net} bb/100, 3Bet%={bossman_row['three_bet_pct']}%",
                "correction": "Enforce BB defense ranges vs CO/BTN/SB steals using exact pot odds bet/(pot+2*bet), positional equity realization, and polarized 3-bet bluffs (A5s-A2s, suited connectors).",
            }
        )
    if mistakes.get("bad_call_pot_odds", 0) > 0:
        leaks_found.append(
            {
                "leak_id": "uncalibrated_postflop_calling_without_range_discount",
                "observed": f"bad_call_pot_odds={mistakes.get('bad_call_pot_odds', 0)}, W$SD={bossman_row['w_sd_pct']}%",
                "correction": "Apply range-narrowing discount and positional realization factor to raw Monte Carlo equity before comparing against required_equity = pot_odds / realization.",
            }
        )
    if mistakes.get("too_passive_missed_value", 0) + mistakes.get("suboptimal_cbet_sizing", 0) + mistakes.get("overplay_or_bad_bluff", 0) > 0:
        leaks_found.append(
            {
                "leak_id": "static_postflop_sizing_and_alpha_imbalance",
                "observed": f"AF={bossman_row['af']}, missed_value={mistakes.get('too_passive_missed_value', 0)}, bad_bluff={mistakes.get('overplay_or_bad_bluff', 0)}, sizing_errors={mistakes.get('suboptimal_cbet_sizing', 0)}",
                "correction": "Scale c-bet sizing by board texture (33% pot on dry/paired/monotone, 60-68% on wet/connected), use geometric bet sizing ((1+2*SPR)^(1/N)-1)/2 for SPR<=4, and cap bluff share at alpha/(1+alpha) = bet/(pot+2*bet).",
            }
        )

    return {
        "baseline_summary": {
            "bb_per_100": bossman_row["bb_per_100"],
            "vpip_pct": bossman_row["vpip_pct"],
            "pfr_pct": bossman_row["pfr_pct"],
            "three_bet_pct": bossman_row["three_bet_pct"],
            "af": bossman_row["af"],
            "w_sd_pct": bossman_row["w_sd_pct"],
            "avg_ev_loss_bb_per_hand": bossman_row["avg_ev_loss_bb_per_hand"],
            "total_ev_loss_bb": bossman_row["total_ev_loss_bb"],
            "gto_agreement_pct": bossman_row["gto_agreement_pct"],
        },
        "gto_reference_benchmark": {
            "bb_per_100": gto_row["bb_per_100"],
            "vpip_pct": gto_row["vpip_pct"],
            "pfr_pct": gto_row["pfr_pct"],
            "three_bet_pct": gto_row["three_bet_pct"],
            "af": gto_row["af"],
            "w_sd_pct": gto_row["w_sd_pct"],
            "avg_ev_loss_bb_per_hand": gto_row["avg_ev_loss_bb_per_hand"],
        },
        "leaks_diagnosed": leaks_found,
        "calibrated_parameters": {
            "preflop_mode": "positional_5max_6max_gto_wizard_charts",
            "open_limp_allowed": False,
            "cbet_dry_paired_frac": 0.33,
            "cbet_wet_connected_frac": 0.68,
            "use_geometric_sizing_below_spr": 4.0,
            "spr_commitment_threshold": 2.2,
            "mdf_river_bluffcatch_enabled": True,
            "alpha_polarized_bluff_cap": True,
        },
    }
