"""Strategy sees ONLY what a player sees (the committed visible state). Equity is NOT optimal play or a profit guarantee.

Perception accuracy and strategy quality are measured separately (see eval/). The policy combines botlab's Monte-Carlo
equity with GTO-level metrics (pot odds, required equity, MDF = pot / (pot + bet), alpha = bet / (pot + bet), SPR,
geometric bet sizing, board texture classification, and street EV estimates).
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
    from botlab import cards as cd, equity as eq, gto
    return eq, cd, gto


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
    mdf: float = 1.0
    alpha: float = 0.0
    spr: float = 0.0
    geometric_bet_frac: float = 0.0
    board_texture: str = "preflop"
    ev_call: float | None = None
    ev_fold: float = 0.0


def decide(v: VisibleInfo, rng: random.Random, sims: int = 400, margin: float = 0.03) -> Choice:
    eq, cd, gto = _botlab()
    hole = cd.parse_cards(list(v.hero))
    board = cd.parse_cards(list(v.board)) if v.board else []
    n = max(1, min(int(v.n_opponents), eq.MAX_OPPONENTS))
    e = eq.mc_equity(hole, board, n, sims, rng)
    po = v.to_call / (v.pot + v.to_call) if v.to_call > 0 else 0.0
    mdf_v = gto.mdf(v.pot, v.to_call, pot_includes_bet=False)
    alpha_v = gto.optimal_bluff_alpha(v.pot, v.to_call, pot_includes_bet=False)
    spr_v = gto.spr(v.stack, max(1.0, v.pot))
    streets_rem = 3 if not board else {3: 3, 4: 2, 5: 1}.get(len(board), 1)
    geo_f = gto.geometric_bet_fraction(v.stack, max(1.0, v.pot), streets_rem)
    tex = gto.classify_board(board).primary
    ev_c = (e * (v.pot + v.to_call) - (1.0 - e) * v.to_call) if v.to_call > 0 else None

    A = set(v.actions)
    if v.to_call <= 0:
        if "RAISE" in A and e > 0.62 + 0.04 * n:
            return Choice("RAISE", e, po, "strong equity, no bet to face", mdf_v, alpha_v, spr_v, geo_f, tex, ev_c, 0.0)
        return Choice("CHECK" if "CHECK" in A else "CALL", e, po, "check: equity not high enough to bet", mdf_v, alpha_v, spr_v, geo_f, tex, ev_c, 0.0)
    if e >= po + margin:
        if "RAISE" in A and e > 0.70:
            return Choice("RAISE", e, po, "equity far above pot odds", mdf_v, alpha_v, spr_v, geo_f, tex, ev_c, 0.0)
        return Choice("CALL", e, po, "equity above pot odds", mdf_v, alpha_v, spr_v, geo_f, tex, ev_c, 0.0)
    return Choice("FOLD", e, po, "equity below pot odds", mdf_v, alpha_v, spr_v, geo_f, tex, ev_c, 0.0)
