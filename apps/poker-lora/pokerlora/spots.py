"""Spot generation: random river boards, pot, stack depth and ranges; solved exactly; only converged spots are kept."""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

import numpy as np

from .ranges import cd, expand, range_classes, range_text, strengths
from .solver import Solver, Spot, TreeConfig

POTS = (6.0, 12.0, 24.0, 40.0)
SPRS = (0.8, 1.5, 3.0, 6.0)
OOP_PCT = (12, 20, 30)
IP_PCT = (12, 20, 30)
EXPLOIT_PCT_OF_POT = 0.5               # a spot enters the dataset only if exploitability <= 0.5 % of the pot


@dataclass
class SolvedSpot:
    spot_id: str
    board_key: str
    board: list
    cfg: TreeConfig
    pct: tuple
    combos: tuple                       # (oop combos [(hole, w)], ip combos)
    solver: Solver
    exploit: float
    iters: int


def board_key(board) -> str:
    return " ".join(sorted(cd.card_str(c) for c in board))


def make_spec(rng: random.Random) -> dict:
    board = rng.sample(range(52), 5)
    pot = rng.choice(POTS)
    return {"board": board, "pot": pot, "stack": round(pot * rng.choice(SPRS), 2), "oop_pct": rng.choice(OOP_PCT), "ip_pct": rng.choice(IP_PCT)}


def spot_id(spec: dict) -> str:
    raw = f"{board_key(spec['board'])}|{spec['pot']}|{spec['stack']}|{spec['oop_pct']}|{spec['ip_pct']}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def solve_spec(spec: dict, iters: int = 400, max_iters: int = 1600) -> SolvedSpot | None:
    board = list(spec["board"])
    dead = set(board)
    oop = expand(range_classes(spec["oop_pct"]), dead)
    ip = expand(range_classes(spec["ip_pct"]), dead)
    if len(oop) < 20 or len(ip) < 20:
        return None
    s0 = np.array(strengths(oop, board), float); s1 = np.array(strengths(ip, board), float)
    w0 = np.array([w for _, w in oop]); w1 = np.array([w for _, w in ip])
    m0 = np.array([[h[0], h[1]] for h, _ in oop]); m1 = np.array([[h[0], h[1]] for h, _ in ip])
    compat = ((m0[:, None, :, None] != m1[None, :, None, :]).all((2, 3))).astype(float)
    cfg = TreeConfig(pot=spec["pot"], stack=spec["stack"])
    sv = Solver(Spot(cfg, s0, s1, w0, w1, compat))
    done = 0
    ex = float("inf")
    while done < max_iters:
        ex = sv.solve(iters); done += iters
        if ex <= EXPLOIT_PCT_OF_POT / 100.0 * cfg.pot:
            break
    return SolvedSpot(spot_id(spec), board_key(board), board, cfg, (spec["oop_pct"], spec["ip_pct"]), (oop, ip), sv, float(ex), done)


def ranges_text(pct) -> tuple[str, str]:
    return range_text(pct[0]), range_text(pct[1])
