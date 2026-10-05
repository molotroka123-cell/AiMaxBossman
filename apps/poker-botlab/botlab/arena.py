"""Arena: many hands between profile bots, with per-bot statistics.

Cash-game style: every hand starts with fresh ``stack_bb`` stacks so results are
per-hand independent. With ``duplicate=True`` every deal (deck seed) is replayed
once per seat rotation, so every bot gets every seat's cards; this removes most
of the card luck. Confidence intervals are computed over deals (blocks), which
keeps them honest when the rotations of one deal are correlated.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .agent import ProfileAgent
from .engine import HandResult, play_hand
from .profiles import Profile

Z95 = 1.959964


def deal_seed(seed: int, deal: int) -> int:
    return (seed * 2_654_435_761 + deal * 40_503 + 7) & 0x7FFF_FFFF


@dataclass
class MeanCI:
    mean: float
    sd: float
    n: int
    lo: float
    hi: float

    @classmethod
    def of(cls, values: Sequence[float]) -> MeanCI:
        n = len(values)
        if n == 0:
            return cls(0.0, 0.0, 0, 0.0, 0.0)
        mean = statistics.fmean(values)
        sd = statistics.stdev(values) if n > 1 else 0.0
        half = Z95 * sd / math.sqrt(n) if n > 1 else float("inf")
        return cls(mean, sd, n, mean - half, mean + half)

    def to_dict(self) -> dict[str, float]:
        return {"mean": round(self.mean, 3), "sd": round(self.sd, 3), "n": self.n, "ci95_lo": round(self.lo, 3), "ci95_hi": round(self.hi, 3)}


@dataclass
class BotStats:
    label: str
    profile: str
    hands: int = 0
    net_chips: int = 0
    vpip: int = 0
    pfr: int = 0
    post_aggr: int = 0
    post_calls: int = 0
    saw_flop: int = 0
    showdowns: int = 0
    showdown_wins: int = 0
    block_bb: list[float] = field(default_factory=list)  # bb won per hand, averaged per deal

    def bb100(self) -> MeanCI:
        ci = MeanCI.of(self.block_bb)
        return MeanCI(ci.mean * 100, ci.sd * 100, ci.n, ci.lo * 100, ci.hi * 100)

    def to_dict(self) -> dict[str, Any]:
        h = max(1, self.hands)
        return {
            "label": self.label,
            "profile": self.profile,
            "hands": self.hands,
            "net_chips": self.net_chips,
            "bb_per_100": self.bb100().to_dict(),
            "vpip_pct": round(100 * self.vpip / h, 1),
            "pfr_pct": round(100 * self.pfr / h, 1),
            "af": round(self.post_aggr / self.post_calls, 2) if self.post_calls else None,
            "wtsd_pct": round(100 * self.showdowns / max(1, self.saw_flop), 1),
            "showdown_win_pct": round(100 * self.showdown_wins / self.showdowns, 1) if self.showdowns else None,
        }


@dataclass
class ArenaResult:
    bots: list[BotStats]
    hands: int
    deals: int
    big_blind: int
    seed: int
    duplicate: bool
    sample_hands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hands": self.hands,
            "deals": self.deals,
            "big_blind": self.big_blind,
            "seed": self.seed,
            "duplicate": self.duplicate,
            "bots": [b.to_dict() for b in self.bots],
            "chip_conservation": sum(b.net_chips for b in self.bots) == 0,
        }

    def table(self) -> str:
        head = f"{'bot':<22}{'hands':>7}{'bb/100':>9}{'95% CI':>20}{'VPIP':>7}{'PFR':>6}{'AF':>6}{'SD win%':>9}"
        lines = [head, "-" * len(head)]
        for b in self.bots:
            d = b.to_dict()
            ci = d["bb_per_100"]
            af = "-" if d["af"] is None else f"{d['af']:.2f}"
            sdw = "-" if d["showdown_win_pct"] is None else f"{d['showdown_win_pct']:.1f}"
            lines.append(
                f"{b.label:<22}{b.hands:>7}{ci['mean']:>9.1f}{'[' + format(ci['ci95_lo'], '.1f') + ', ' + format(ci['ci95_hi'], '.1f') + ']':>20}"
                f"{d['vpip_pct']:>7.1f}{d['pfr_pct']:>6.1f}{af:>6}{sdw:>9}"
            )
        return "\n".join(lines)


def _labels(profiles: Sequence[Profile]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for p in profiles:
        seen[p.name] = seen.get(p.name, 0) + 1
        out.append(p.name if seen[p.name] == 1 else f"{p.name}#{seen[p.name]}")
    if len(set(out)) != len(out):
        out = [f"{name}@{i}" for i, name in enumerate(out)]
    return out


def run_arena(
    profiles: Sequence[Profile],
    hands: int,
    seed: int,
    big_blind: int = 2,
    stack_bb: int = 100,
    duplicate: bool = True,
    keep_samples: int = 0,
) -> ArenaResult:
    """Play ``hands`` hands (rounded down to whole deals in duplicate mode)."""
    n = len(profiles)
    if not 2 <= n <= 6:
        raise ValueError("arena needs 2..6 bots")
    small_blind = max(1, big_blind // 2)
    agents = [ProfileAgent(p) for p in profiles]
    bots = [BotStats(label=lab, profile=p.name) for lab, p in zip(_labels(profiles), profiles)]
    rotations = n if duplicate else 1
    deals = max(1, hands // rotations)
    stack = stack_bb * big_blind
    samples: list[str] = []
    played = 0
    for d in range(deals):
        dseed = deal_seed(seed, d)
        block_chips = [0] * n
        for r in range(rotations):
            # entrant i sits in seat (i + r) % n; the button moves every deal
            seat_of = [(i + r) % n for i in range(n)]
            seated = [None] * n
            for i, s in enumerate(seat_of):
                seated[s] = agents[i]
            res: HandResult = play_hand(seated, [stack] * n, button=d % n, small_blind=small_blind,
                                        big_blind=big_blind, deck_seed=dseed, hand_id=played)
            played += 1
            if len(samples) < keep_samples:
                samples.append(res.summary())
            for i, s in enumerate(seat_of):
                b = bots[i]
                st = res.stats[s]
                b.hands += 1
                b.net_chips += res.deltas[s]
                block_chips[i] += res.deltas[s]
                b.vpip += st.vpip
                b.pfr += st.pfr
                b.post_aggr += st.post_aggr
                b.post_calls += st.post_calls
                b.saw_flop += st.saw_flop
                b.showdowns += st.showdown
                b.showdown_wins += st.won_showdown
        for i in range(n):
            bots[i].block_bb.append(block_chips[i] / big_blind / rotations)
    return ArenaResult(bots=bots, hands=played, deals=deals, big_blind=big_blind, seed=seed, duplicate=duplicate, sample_hands=samples)
