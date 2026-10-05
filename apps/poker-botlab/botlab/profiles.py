"""Bot profiles = skill/style parameters for TRAINING opponents and the learner.

These are knobs of a simple parametric policy (see ``agent.py``). They describe
how loose/tight and passive/aggressive a training bot plays inside the local
engine or the owner's local trainer. There are intentionally no settings for
throwing hands, colluding, or behaving to influence real people.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Profile:
    name: str
    description: str = ""
    vpip: float = 0.30  # share of starting hands played (top-X% by preflop strength)
    pfr: float = 0.15  # share of starting hands raised preflop (<= vpip)
    value_threshold: float = 0.55  # postflop equity (vs random hands, scaled to heads-up) needed to bet for value
    aggression: float = 0.5  # probability of betting/raising when the hand qualifies
    bluff: float = 0.10  # probability of betting/raising with a weak hand
    call_slack: float = 0.10  # call if equity >= pot_odds * (1 - call_slack); >0 = sticky, <0 = folds too much
    bet_size: float = 0.60  # postflop bet/raise size as a fraction of the pot
    open_size: float = 3.0  # preflop open size in big blinds
    sims: int = 80  # Monte Carlo samples per postflop decision (bounded)
    training_opponent: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Profile:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


# Parameters searched by ``learn``: (name, low, high).
TUNABLE: tuple[tuple[str, float, float], ...] = (
    ("vpip", 0.08, 0.75),
    ("pfr", 0.03, 0.60),
    ("value_threshold", 0.35, 0.85),
    ("aggression", 0.05, 1.0),
    ("bluff", 0.0, 0.50),
    ("call_slack", -0.30, 0.50),
    ("bet_size", 0.25, 1.50),
    ("open_size", 2.0, 5.0),
)


def to_vector(p: Profile) -> list[float]:
    return [float(getattr(p, name)) for name, _, _ in TUNABLE]


def from_vector(base: Profile, vec: list[float], name: str) -> Profile:
    values: dict[str, float] = {}
    for (key, lo, hi), v in zip(TUNABLE, vec):
        values[key] = round(min(hi, max(lo, v)), 4)
    values["pfr"] = min(values["pfr"], values["vpip"])
    return replace(base, name=name, **values)


# ---- named training opponents (all are local training bots) ----
ROCK = Profile(
    name="Rock",
    description="Training opponent: ultra-tight, plays only premiums, rarely bluffs.",
    vpip=0.11, pfr=0.08, value_threshold=0.70, aggression=0.55, bluff=0.02, call_slack=-0.05, bet_size=0.6, open_size=3.0,
)
TAG = Profile(
    name="TAG Trainer",
    description="Training opponent: tight-aggressive solid regular.",
    vpip=0.22, pfr=0.18, value_threshold=0.60, aggression=0.75, bluff=0.10, call_slack=0.0, bet_size=0.65, open_size=2.5,
)
LAG = Profile(
    name="LAG Trainer",
    description="Training opponent: loose-aggressive, wide ranges and frequent pressure.",
    vpip=0.36, pfr=0.28, value_threshold=0.55, aggression=0.85, bluff=0.22, call_slack=0.05, bet_size=0.75, open_size=2.5,
)
STATION = Profile(
    name="Calling Station",
    description="Training opponent: loose-passive, calls far too much, almost never bluffs.",
    vpip=0.55, pfr=0.05, value_threshold=0.75, aggression=0.25, bluff=0.02, call_slack=0.45, bet_size=0.5, open_size=3.0,
)
MANIAC = Profile(
    name="Maniac",
    description="Training opponent: hyper-aggressive, huge bluff frequency and big sizes.",
    vpip=0.70, pfr=0.50, value_threshold=0.45, aggression=0.95, bluff=0.45, call_slack=0.20, bet_size=1.1, open_size=4.0,
)
SCARED = Profile(
    name="Scared Money",
    description="Training opponent: weak-tight, folds to pressure far too often.",
    vpip=0.25, pfr=0.06, value_threshold=0.70, aggression=0.30, bluff=0.03, call_slack=-0.25, bet_size=0.45, open_size=2.5,
)
LIMPER = Profile(
    name="Loose Limper",
    description="Training opponent: limps many hands, check-calls, bets only strong made hands.",
    vpip=0.48, pfr=0.08, value_threshold=0.68, aggression=0.40, bluff=0.05, call_slack=0.25, bet_size=0.55, open_size=2.0,
)
TRAPPER = Profile(
    name="Trapper",
    description="Training opponent: medium-tight, slow-plays strong hands (low aggression), sticky calls.",
    vpip=0.24, pfr=0.12, value_threshold=0.62, aggression=0.35, bluff=0.08, call_slack=0.15, bet_size=0.7, open_size=2.5,
)
STUDENT = Profile(
    name="Student (start)",
    description="Learner starting point: generic middle-of-the-road settings before training.",
    vpip=0.32, pfr=0.14, value_threshold=0.55, aggression=0.50, bluff=0.12, call_slack=0.10, bet_size=0.60, open_size=3.0,
    training_opponent=False,
)

TRAINING_OPPONENTS: dict[str, Profile] = {p.name: p for p in (ROCK, TAG, LAG, STATION, MANIAC, SCARED, LIMPER, TRAPPER)}

# Disjoint pools: learning only ever sees TRAIN; UNSEEN is used once, for the final evaluation.
TRAIN_POOL: tuple[Profile, ...] = (ROCK, STATION, MANIAC, TAG)
UNSEEN_POOL: tuple[Profile, ...] = (LAG, SCARED, LIMPER, TRAPPER)


def get_profile(name: str) -> Profile:
    key = name.strip().lower()
    for p in (*TRAINING_OPPONENTS.values(), STUDENT):
        if p.name.lower() == key or p.name.lower().split()[0] == key:
            return p
    raise KeyError(f"unknown profile {name!r}; known: {', '.join(TRAINING_OPPONENTS)}, {STUDENT.name}")
