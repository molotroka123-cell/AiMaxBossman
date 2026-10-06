"""Measurable learning: cross-entropy search over the learner's profile.

Protocol (all seeds fixed and recorded in the report):

1. Start from ``STUDENT`` (or a given profile). Each generation samples a
   population of parameter vectors around the current mean.
2. Every candidate plays the SAME deals (common random numbers) at 6-max
   tables against opponents drawn from the TRAIN pool only, with duplicate
   seat rotation. Score = learner bb/100.
3. The elite fraction updates the mean/std of the search distribution.
4. The final mean is the learned profile (no cherry-picking of a lucky
   candidate). It is then evaluated once on held-out seeds against
   (a) the TRAIN pool and (b) the UNSEEN pool, paired against the starting
   profile on identical deals and lineups.
5. "Improvement" is claimed only when the 95% CI of the paired difference
   (learned - start) on the UNSEEN pool is entirely above zero.
"""

from __future__ import annotations

import json
import math
import random
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent import ProfileAgent
from .arena import MeanCI, deal_seed
from .engine import play_hand
from .profiles import STUDENT, TRAIN_POOL, TUNABLE, UNSEEN_POOL, Profile, from_vector, to_vector

TABLE_SIZE = 6
BIG_BLIND = 2
STACK_BB = 100


@dataclass(frozen=True)
class LearnConfig:
    generations: int = 10
    population: int = 16
    elite_frac: float = 0.25
    deals_per_candidate: int = 60  # x6 seat rotations = hands per candidate
    test_deals: int = 600  # held-out deals for the final evaluation (x6 hands, per pool)
    seed: int = 7
    init_std_frac: float = 0.25  # initial std as a fraction of each parameter's range
    min_std_frac: float = 0.03
    smoothing: float = 0.7  # weight of the new elite statistics in the update


def _lineup(pool: Sequence[Profile], dseed: int) -> list[Profile]:
    rng = random.Random(dseed ^ 0x5EED)
    return [pool[rng.randrange(len(pool))] for _ in range(TABLE_SIZE - 1)]


def learner_blocks(
    learner: Profile,
    pool: Sequence[Profile],
    deals: int,
    base_seed: int,
    agent_cache: dict[str, ProfileAgent] | None = None,
) -> list[float]:
    """Learner's bb/hand per deal (averaged over the 6 seat rotations)."""
    cache = agent_cache if agent_cache is not None else {}
    me = ProfileAgent(learner)
    stack = STACK_BB * BIG_BLIND
    out: list[float] = []
    for d in range(deals):
        dseed = deal_seed(base_seed, d)
        opponents = _lineup(pool, dseed)
        opp_agents = [cache.setdefault(p.name, ProfileAgent(p)) for p in opponents]
        total = 0
        for r in range(TABLE_SIZE):
            seated = list(opp_agents)
            seated.insert(r, me)
            res = play_hand(seated, [stack] * TABLE_SIZE, button=d % TABLE_SIZE, small_blind=BIG_BLIND // 2,
                            big_blind=BIG_BLIND, deck_seed=dseed)
            total += res.deltas[r]
        out.append(total / BIG_BLIND / TABLE_SIZE)
    return out


def _bb100(blocks: Sequence[float]) -> MeanCI:
    ci = MeanCI.of(blocks)
    return MeanCI(ci.mean * 100, ci.sd * 100, ci.n, ci.lo * 100, ci.hi * 100)


def paired_comparison(start: Profile, learned: Profile, pool: Sequence[Profile], deals: int, base_seed: int) -> dict[str, Any]:
    cache: dict[str, ProfileAgent] = {}
    a = learner_blocks(start, pool, deals, base_seed, cache)
    b = learner_blocks(learned, pool, deals, base_seed, cache)
    diff = [y - x for x, y in zip(a, b)]
    d = _bb100(diff)
    return {
        "pool": [p.name for p in pool],
        "deals": deals,
        "hands_each": deals * TABLE_SIZE,
        "seed": base_seed,
        "start_bb100": _bb100(a).to_dict(),
        "learned_bb100": _bb100(b).to_dict(),
        "paired_diff_bb100": d.to_dict(),
        "improvement_supported": d.lo > 0,
        "regression_supported": d.hi < 0,
    }


def learn(
    config: LearnConfig,
    start: Profile = STUDENT,
    train_pool: Sequence[Profile] = TRAIN_POOL,
    unseen_pool: Sequence[Profile] = UNSEEN_POOL,
    out_root: Path | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    if {p.name for p in train_pool} & {p.name for p in unseen_pool}:
        raise ValueError("TRAIN and UNSEEN pools must be disjoint")
    t0 = time.perf_counter()
    rng = random.Random(config.seed)
    lows = [lo for _, lo, _ in TUNABLE]
    highs = [hi for _, _, hi in TUNABLE]
    spans = [h - lo for lo, h in zip(lows, highs)]
    mean = to_vector(start)
    std = [s * config.init_std_frac for s in spans]
    n_elite = max(2, int(round(config.population * config.elite_frac)))
    history: list[dict[str, Any]] = []
    cache: dict[str, ProfileAgent] = {}

    for g in range(config.generations):
        gen_seed = config.seed * 1_000 + g  # new deals every generation, shared by all candidates
        candidates: list[list[float]] = [list(mean)]  # always re-score the current mean
        while len(candidates) < config.population:
            candidates.append([min(h, max(lo, rng.gauss(m, s))) for m, s, lo, h in zip(mean, std, lows, highs)])
        scored = []
        for i, vec in enumerate(candidates):
            prof = from_vector(start, vec, f"cand-g{g}-{i}")
            blocks = learner_blocks(prof, train_pool, config.deals_per_candidate, gen_seed, cache)
            scored.append((statistics.fmean(blocks) * 100, vec))
        scored.sort(key=lambda t: t[0], reverse=True)
        elites = [v for _, v in scored[:n_elite]]
        new_mean = [statistics.fmean(col) for col in zip(*elites)]
        new_std = [statistics.pstdev(col) for col in zip(*elites)]
        a = config.smoothing
        mean = [a * nm + (1 - a) * m for nm, m in zip(new_mean, mean)]
        std = [max(config.min_std_frac * sp, a * ns + (1 - a) * s) for ns, s, sp in zip(new_std, std, spans)]
        entry = {
            "generation": g,
            "seed": gen_seed,
            "hands_per_candidate": config.deals_per_candidate * TABLE_SIZE,
            "mean_candidate_bb100": round(scored_mean(scored, candidates[0]), 2),
            "best_bb100": round(scored[0][0], 2),
            "elite_avg_bb100": round(statistics.fmean(s for s, _ in scored[:n_elite]), 2),
            "population_avg_bb100": round(statistics.fmean(s for s, _ in scored), 2),
            "new_mean": _named(mean),
            "new_std": _named(std),
        }
        history.append(entry)
        log(f"gen {g:>2}: best {entry['best_bb100']:>8.1f}  elite {entry['elite_avg_bb100']:>8.1f}  "
            f"pop {entry['population_avg_bb100']:>8.1f}  current-mean {entry['mean_candidate_bb100']:>8.1f} bb/100")

    learned = replace(from_vector(start, mean, "Learned"), description="Learned by botlab CEM vs the TRAIN pool", training_opponent=False)
    log("final evaluation on held-out seeds ...")
    test_seed_train = config.seed * 1_000 + 900_001
    test_seed_unseen = config.seed * 1_000 + 900_002
    vs_train = paired_comparison(start, learned, train_pool, config.test_deals, test_seed_train)
    vs_unseen = paired_comparison(start, learned, unseen_pool, config.test_deals, test_seed_unseen)
    verdict = _verdict(vs_unseen)
    log(verdict)
    total_hands = (config.generations * config.population * config.deals_per_candidate + 4 * config.test_deals) * TABLE_SIZE
    report: dict[str, Any] = {
        "kind": "botlab.learn.report",
        "created": datetime.now().isoformat(timespec="seconds"),
        "boundary": "local headless engine only; opponents are labelled training bots",
        "config": config.__dict__,
        "table": {"seats": TABLE_SIZE, "big_blind": BIG_BLIND, "stack_bb": STACK_BB, "duplicate_rotation": True},
        "train_pool": [p.name for p in train_pool],
        "unseen_pool": [p.name for p in unseen_pool],
        "start_profile": start.to_dict(),
        "learned_profile": learned.to_dict(),
        "generations": history,
        "eval_train_pool_heldout_seeds": vs_train,
        "eval_unseen_pool": vs_unseen,
        "improvement_supported_on_unseen": vs_unseen["improvement_supported"],
        "verdict": verdict,
        "total_hands_simulated": total_hands,
        "elapsed_sec": round(time.perf_counter() - t0, 1),
    }
    if out_root is not None:
        run_dir = out_root / datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        learned.save(run_dir / "learned_profile.json")
        report["run_dir"] = str(run_dir)
        log(f"wrote {run_dir / 'report.json'}")
    return report


def scored_mean(scored: list[tuple[float, list[float]]], mean_vec: list[float]) -> float:
    for s, v in scored:
        if v is mean_vec:
            return s
    return math.nan


def _named(vec: Sequence[float]) -> dict[str, float]:
    return {name: round(v, 4) for (name, _, _), v in zip(TUNABLE, vec)}


def _verdict(cmp: dict[str, Any]) -> str:
    d = cmp["paired_diff_bb100"]
    span = f"{d['mean']:+.1f} bb/100, 95% CI [{d['ci95_lo']:+.1f}, {d['ci95_hi']:+.1f}] over {cmp['hands_each']} hands"
    if cmp["improvement_supported"]:
        return f"IMPROVED on unseen opponents: learned - start = {span}"
    if cmp["regression_supported"]:
        return f"REGRESSED on unseen opponents: learned - start = {span}"
    return f"NOT PROVEN: difference on unseen opponents is within noise: {span}"
