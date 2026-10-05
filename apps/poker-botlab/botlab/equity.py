"""Equity estimates: Monte Carlo vs uniformly random opponent hands.

* ``mc_equity`` — bounded Monte Carlo for any street (share of the pot won,
  ties split).
* ``preflop_equity`` — table lookup for the 169 starting-hand classes vs
  1..5 random opponents. The table ships as ``data/preflop_equity.json`` and is
  produced by ``generate_preflop_table`` (``python -m botlab gen-preflop``); if
  the file is missing a smaller table is computed lazily and kept in memory.

"Random opponent hands" is a deliberate simplification: it is fast and
unbiased, but it ignores that opponents who keep betting hold stronger ranges.
The learner's profile parameters (value threshold, call slack, ...) are what
compensate for that, and that is exactly what ``learn`` tunes.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from .cards import FULL_DECK, Card, all_hand_classes, class_combos, hand_class, representative_hole
from .evaluator import evaluate

MAX_SIMS = 5000
MAX_OPPONENTS = 5
DATA_FILE = Path(__file__).resolve().parent / "data" / "preflop_equity.json"


def mc_equity(
    hole: Sequence[Card],
    board: Sequence[Card],
    n_opponents: int,
    sims: int,
    rng: random.Random,
) -> float:
    """Expected pot share of ``hole`` vs ``n_opponents`` random hands."""
    if not 1 <= n_opponents <= MAX_OPPONENTS:
        raise ValueError(f"n_opponents must be 1..{MAX_OPPONENTS}")
    sims = max(1, min(int(sims), MAX_SIMS))
    used = set(hole) | set(board)
    stub = [c for c in FULL_DECK if c not in used]
    need_board = 5 - len(board)
    draw = need_board + 2 * n_opponents
    hero_base = list(hole) + list(board)
    board_l = list(board)
    total = 0.0
    sample = rng.sample
    for _ in range(sims):
        drawn = sample(stub, draw)
        full_board = board_l + drawn[:need_board]
        hero = evaluate(hero_base + drawn[:need_board])
        best = hero
        ties = 1
        hero_best = True
        idx = need_board
        for _o in range(n_opponents):
            s = evaluate(full_board + drawn[idx : idx + 2])
            idx += 2
            if s > best:
                best = s
                hero_best = False
                ties = 1
            elif s == best:
                ties += 1
        if hero_best and best == hero:
            total += 1.0 / ties
    return total / sims


def generate_preflop_table(sims_per_class: int, seed: int = 20261005) -> dict[str, list[float]]:
    """Equity of every hand class vs 1..5 random opponents (index 0 = 1 opponent)."""
    rng = random.Random(seed)
    table: dict[str, list[float]] = {}
    for label in all_hand_classes():
        hole = representative_hole(label)
        table[label] = [round(mc_equity(hole, (), n, sims_per_class, rng), 4) for n in range(1, MAX_OPPONENTS + 1)]
    return table


def write_preflop_table(table: dict[str, list[float]], sims_per_class: int, path: Path = DATA_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sims_per_class": sims_per_class, "opponents": list(range(1, MAX_OPPONENTS + 1)), "equity": table}
    path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")


@lru_cache(maxsize=1)
def _preflop_table() -> dict[str, list[float]]:
    if DATA_FILE.is_file():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))["equity"]
    return generate_preflop_table(sims_per_class=300)


def preflop_equity(hole: Sequence[Card], n_opponents: int) -> float:
    n = max(1, min(n_opponents, MAX_OPPONENTS))
    return _preflop_table()[hand_class(hole)][n - 1]


@lru_cache(maxsize=1)
def _percentiles() -> dict[str, float]:
    """Share of all 1326 combos that are at least as strong (by heads-up equity).

    0.0x = premium (AA), ~1.0 = worst (72o). Used for "play the top X% of hands".
    """
    table = _preflop_table()
    ranked = sorted(all_hand_classes(), key=lambda h: table[h][0], reverse=True)
    out: dict[str, float] = {}
    cum = 0
    for label in ranked:
        cum += class_combos(label)
        out[label] = cum / 1326.0
    return out


def preflop_percentile(hole: Sequence[Card]) -> float:
    return _percentiles()[hand_class(hole)]
