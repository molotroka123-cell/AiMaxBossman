"""Batch of N river-spot variations solved with TexasSolver (open source, local) — Bossman's GTO-Wizard-style dataset.

    python tools/gto_batch.py --n 1000 --seed 14 --out gto_batch.jsonl [--accuracy 1.0 --iterations 120]

Each line: board, pot/stack (in BB), range pair name, the first player's mixed strategy per hand in the ranges
(action -> frequency), exploitability. Resumable: spots already in --out (by id) are skipped. Study/post-session
review only — never real-time assistance at a real table.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gto_solve import solve  # noqa: E402

RANKS, SUITS = "AKQJT98765432", "shdc"
# (name, IP range, OOP range): compact single-raised / 3-bet pot river ranges, ~30-45 hands each.
RANGES = {
    "BTNvsBB_srp": ("AA,KK,QQ,JJ,TT,99,88,AKs,AQs,AJs,ATs,KQs,KJs,QJs,JTs,T9s,98s,AKo,AQo,KQo,A5s,A4s,76s,87s",
                    "TT,99,88,77,66,55,AQs,AJs,ATs,KQs,KJs,QJs,JTs,T9s,98s,87s,76s,AQo,KQo,A5s,A4s,K9s,Q9s"),
    "COvsBB_srp": ("AA,KK,QQ,JJ,TT,99,88,77,AKs,AQs,AJs,ATs,KQs,KJs,QJs,JTs,T9s,AKo,AQo,KQo,A5s",
                   "99,88,77,66,55,44,AJs,ATs,A9s,KQs,KJs,QJs,JTs,T9s,98s,AJo,KQo,A5s,K9s"),
    "SBvsBB_srp": ("AA,KK,QQ,JJ,TT,99,88,77,66,AKs,AQs,AJs,ATs,A9s,KQs,KJs,QJs,JTs,AKo,AQo,AJo,KQo,A5s,A4s,K9s",
                   "TT,99,88,77,66,55,44,AQs,AJs,ATs,A9s,KQs,KJs,KTs,QJs,QTs,JTs,T9s,98s,AQo,AJo,KQo"),
    "3bet_pot": ("AA,KK,QQ,JJ,TT,AKs,AQs,AKo,A5s,A4s", "QQ,JJ,TT,99,AKs,AQs,AJs,KQs,AKo,AQo"),
    "4bet_pot": ("AA,KK,QQ,AKs,AKo", "AA,KK,QQ,JJ,AKs,AKo"),
    "limp_pot": ("AA,KK,QQ,JJ,TT,99,88,77,66,55,AKs,AQs,AJs,ATs,KQs,KJs,QJs,JTs,T9s,98s,87s,76s,AKo,AQo,KQo",
                 "99,88,77,66,55,44,33,22,A9s,A8s,A7s,K9s,Q9s,J9s,T9s,98s,87s,76s,65s,54s,AJo,KJo,QJo"),
}
POTS = (6, 12, 24, 40)          # BB
STACKS = (24, 48, 90)           # effective, BB
BETS = ([33, 75], [50], [33, 66, 125])


def random_board(rng: random.Random) -> str:
    deck = [r + s for r in RANKS for s in SUITS]
    return ",".join(rng.sample(deck, 5))


def make_spot(i: int, seed: int) -> dict:
    rng = random.Random(seed * 100003 + i)
    name = rng.choice(list(RANGES))
    ip, oop = RANGES[name]
    return {"id": i, "name": name, "board": random_board(rng), "pot": rng.choice(POTS), "stack": rng.choice(STACKS),
            "bets": rng.choice(BETS), "raise": [60], "ip": ip, "oop": oop}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=14)
    ap.add_argument("--out", default="gto_batch.jsonl")
    ap.add_argument("--accuracy", type=float, default=1.0, help="% of pot exploitability to stop at")
    ap.add_argument("--iterations", type=int, default=120)
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=120)
    a = ap.parse_args()
    out = Path(a.out)
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
    t0, solved, failed = time.time(), 0, 0
    with out.open("a", encoding="utf-8") as fh:
        for i in range(a.n):
            if i in done:
                continue
            spot = make_spot(i, a.seed)
            spot.update(accuracy=a.accuracy, iterations=a.iterations, threads=a.threads)
            t = time.time()
            try:
                res = solve(spot, timeout=a.timeout)
                row = {k: spot[k] for k in ("id", "name", "board", "pot", "stack", "bets")}
                row.update(actions=res["actions"], exploitability=res["exploitability"], seconds=round(time.time() - t, 1),
                           strategy=res["strategy"])
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                solved += 1
            except Exception as exc:  # noqa: BLE001 — a bad spot must not stop the batch
                failed += 1
                print(f"spot {i} failed: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            if (solved + failed) % 25 == 0:
                print(f"{solved + failed} spots, {solved} solved, {failed} failed, {time.time() - t0:.0f}s", flush=True)
    print(json.dumps({"solved": solved, "failed": failed, "seconds": round(time.time() - t0)}))


if __name__ == "__main__":
    main()
