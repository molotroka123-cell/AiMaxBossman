"""Parametric poker policy driven by a ``Profile``.

Inputs per decision: hole cards, board, pot, amount to call, stack, number of
opponents still in the hand. Pipeline:

1. strength: preflop -> percentile of the starting hand + table equity;
   postflop -> Monte Carlo equity vs random hands (bounded by ``profile.sims``).
2. price: pot odds ``to_call / (pot + to_call)``.
3. style: profile thresholds/frequencies turn (strength, price) into
   fold / check / call / bet / raise and a size.

The policy always returns a legal action for the given ``LegalActions``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .engine import Action, LegalActions, Observation
from .equity import mc_equity, preflop_equity, preflop_percentile
from .profiles import Profile

MAX_MC_OPPONENTS = 3  # multiway equity is estimated vs at most 3 random hands (speed bound)


@dataclass
class Decision:
    action: Action
    equity: float
    strength: float
    pot_odds: float
    reason: str
    extra: dict[str, Any] = field(default_factory=dict)


class ProfileAgent:
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.name = profile.name

    def act(self, obs: Observation, rng: random.Random) -> Action:
        return self.decide(obs, rng).action

    # ------------------------------------------------------------------
    def decide(self, obs: Observation, rng: random.Random) -> Decision:
        if obs.street == "preflop":
            return self._preflop(obs, rng)
        return self._postflop(obs, rng)

    def _preflop(self, obs: Observation, rng: random.Random) -> Decision:
        p = self.profile
        legal = obs.legal
        n_opp = max(1, obs.n_active_opponents)
        pct = preflop_percentile(obs.hole)
        eq = preflop_equity(obs.hole, n_opp)
        po = _pot_odds(obs)
        bb = obs.big_blind
        facing_raise = obs.current_bet > bb
        if not facing_raise:
            if pct <= p.pfr and _can_raise(legal):
                limpers = max(0, (obs.pot - bb - bb // 2) // bb)
                target = round(bb * (p.open_size + limpers))
                return Decision(_raise(legal, target), eq, pct, po, "open-raise", {"percentile": pct})
            if pct <= p.vpip:
                return Decision(_passive(legal), eq, pct, po, "limp/complete" if legal.can_call else "check", {"percentile": pct})
            return Decision(_check_or_fold(legal), eq, pct, po, "outside range", {"percentile": pct})

        # facing a raise: narrower ranges, re-raise only the top of the range
        reraise_share = p.pfr * (0.30 if obs.raises_this_street <= 1 else 0.12)
        if pct <= reraise_share and _can_raise(legal):
            target = round(obs.current_bet * (3.0 if obs.raises_this_street <= 1 else 2.3))
            return Decision(_raise(legal, target), eq, pct, po, "re-raise", {"percentile": pct})
        call_share = p.vpip * (0.50 if obs.raises_this_street <= 1 else 0.25)
        if pct <= call_share and eq >= po * (1.0 - p.call_slack):
            return Decision(_passive(legal), eq, pct, po, "call raise in range", {"percentile": pct})
        return Decision(_check_or_fold(legal), eq, pct, po, "fold to raise", {"percentile": pct})

    def _postflop(self, obs: Observation, rng: random.Random) -> Decision:
        p = self.profile
        legal = obs.legal
        n = max(1, min(obs.n_active_opponents, MAX_MC_OPPONENTS))
        eq = mc_equity(obs.hole, obs.board, n, p.sims, rng)
        strength = eq ** (1.0 / n)  # per-opponent scale, comparable to a heads-up threshold
        po = _pot_odds(obs)
        roll = rng.random()
        if obs.to_call == 0:
            if strength >= p.value_threshold and roll < p.aggression and _can_raise(legal):
                return Decision(_raise(legal, round(p.bet_size * obs.pot)), eq, strength, po, "value bet")
            if strength < p.value_threshold and roll < p.bluff and _can_raise(legal):
                return Decision(_raise(legal, round(p.bet_size * obs.pot)), eq, strength, po, "bluff bet")
            return Decision(_passive(legal), eq, strength, po, "check")
        raise_threshold = (1.0 + p.value_threshold) / 2.0
        if strength >= raise_threshold and roll < p.aggression and _can_raise(legal):
            target = obs.current_bet + round(p.bet_size * (obs.pot + obs.to_call))
            return Decision(_raise(legal, target), eq, strength, po, "value raise")
        if eq >= po * (1.0 - p.call_slack):
            return Decision(_passive(legal), eq, strength, po, "call: equity vs pot odds")
        if roll < p.bluff * 0.25 and _can_raise(legal):
            target = obs.current_bet + round(p.bet_size * (obs.pot + obs.to_call))
            return Decision(_raise(legal, target), eq, strength, po, "bluff raise")
        return Decision(_check_or_fold(legal), eq, strength, po, "fold: price too high")


def _pot_odds(obs: Observation) -> float:
    return obs.to_call / (obs.pot + obs.to_call) if obs.to_call > 0 else 0.0


def _can_raise(legal: LegalActions) -> bool:
    return legal.can_bet or legal.can_raise


def _raise(legal: LegalActions, target: int) -> Action:
    """Bet/raise to ``target`` clamped to the legal range; near-all-in sizes shove."""
    kind = "bet" if legal.can_bet else "raise"
    amount = max(legal.min_to, min(legal.max_to, int(target)))
    if amount >= 0.85 * legal.max_to:
        amount = legal.max_to
    return Action(kind, amount)


def _passive(legal: LegalActions) -> Action:
    return Action("check") if legal.can_check else Action("call")


def _check_or_fold(legal: LegalActions) -> Action:
    return Action("check") if legal.can_check else Action("fold")
