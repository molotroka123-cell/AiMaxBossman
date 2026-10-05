from __future__ import annotations

import random
from collections.abc import Sequence

from botlab.cards import FULL_DECK, Card, parse_cards
from botlab.engine import MAX_SEATS, Action, LegalActions, Observation


def build_deck(holes: Sequence[str], board: str) -> list[Card]:
    """Deck where seat i gets ``holes[i]`` and the board is ``board`` (engine layout)."""
    deck: list[Card | None] = [None] * 52
    used: set[Card] = set()
    for i, h in enumerate(holes):
        a, b = parse_cards(h)
        deck[2 * i], deck[2 * i + 1] = a, b
        used.update((a, b))
    for j, c in enumerate(parse_cards(board)):
        deck[2 * MAX_SEATS + j] = c
        used.add(c)
    rest = iter(c for c in FULL_DECK if c not in used)
    return [c if c is not None else next(rest) for c in deck]


class ScriptAgent:
    """Plays a fixed list of actions; records every observation it sees."""

    def __init__(self, name: str, script: Sequence[Action]) -> None:
        self.name = name
        self.script = list(script)
        self.seen: list[Observation] = []

    def act(self, obs: Observation, rng: random.Random) -> Action:
        self.seen.append(obs)
        if self.script:
            return self.script.pop(0)
        return Action("check") if obs.legal.can_check else Action("call")


class RandomLegalAgent:
    """Uniformly random legal action with random legal sizes (fuzzing)."""

    name = "random"

    def act(self, obs: Observation, rng: random.Random) -> Action:
        legal: LegalActions = obs.legal
        options = []
        if legal.can_fold:
            options.append("fold")
        if legal.can_check:
            options.append("check")
        if legal.can_call:
            options.append("call")
        if legal.can_bet:
            options.append("bet")
        if legal.can_raise:
            options.append("raise")
        kind = rng.choice(options)
        if kind in ("bet", "raise"):
            amount = legal.max_to if rng.random() < 0.3 else rng.randint(legal.min_to, legal.max_to)
            return Action(kind, amount)
        return Action(kind)
