"""Strategy sees ONLY what a player sees (the committed visible state). Equity is NOT optimal play or a profit guarantee.

Perception accuracy and strategy quality are measured separately (see eval/). The policy is a transparent
equity-vs-pot-odds rule built on botlab's Monte-Carlo equity (reused, no second poker engine).
"""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass, fields
from pathlib import Path

_BOTLAB = Path(__file__).resolve().parents[2] / "poker-botlab"


def _botlab():
    if str(_BOTLAB) not in sys.path:
        sys.path.insert(0, str(_BOTLAB))
    from botlab import equity as eq, cards as cd
    return eq, cd


@dataclass(frozen=True)
class VisibleInfo:
    """Everything the policy may use. No opponent hole cards, no deck, no hidden engine state: the type has no such field."""
    hero: tuple[str, str]
    board: tuple[str, ...]
    pot: float
    to_call: float
    stack: float
    n_opponents: int
    actions: tuple[str, ...]          # labels available on screen


FORBIDDEN = ("opponent_cards", "deck", "hidden", "engine_state", "seed")
assert not any(any(f in fld.name for f in FORBIDDEN) for fld in fields(VisibleInfo))


@dataclass
class Choice:
    action: str                       # FOLD CHECK CALL RAISE
    equity: float
    pot_odds: float
    reason: str


def decide(v: VisibleInfo, rng: random.Random, sims: int = 400, margin: float = 0.03) -> Choice:
    eq, cd = _botlab()
    hole = cd.parse_cards(list(v.hero))
    board = cd.parse_cards(list(v.board)) if v.board else []
    n = max(1, min(int(v.n_opponents), eq.MAX_OPPONENTS))
    e = eq.mc_equity(hole, board, n, sims, rng)
    po = v.to_call / (v.pot + v.to_call) if v.to_call > 0 else 0.0
    A = set(v.actions)
    if v.to_call <= 0:
        if "RAISE" in A and e > 0.62 + 0.04 * n:
            return Choice("RAISE", e, po, "strong equity, no bet to face")
        return Choice("CHECK" if "CHECK" in A else "CALL", e, po, "check: equity not high enough to bet")
    if e >= po + margin:
        if "RAISE" in A and e > 0.70:
            return Choice("RAISE", e, po, "equity far above pot odds")
        return Choice("CALL", e, po, "equity above pot odds")
    return Choice("FOLD", e, po, "equity below pot odds")
