"""Strategy quality, measured SEPARATELY from perception: fixed opponents, fixed seeds, duplicate deals, 95% CIs.

Candidates: BotLab STUDENT (baseline), BotLab learned profile (existing evidence), and the vision-side equity policy
(strategy.decide, which only receives visible information). bb/100 here is against *training opponents* in the local
engine: it is not exploitability and not a profit claim. Exploitability is NOT computed (NLHE is not tractable)."""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

BOTLAB = Path(__file__).resolve().parents[3] / "poker-botlab"
sys.path.insert(0, str(BOTLAB))


def run(hands_per_opponent: int, seed: int, learned_path: Path | None) -> dict:
    from botlab.agent import ProfileAgent
    from botlab.arena import MeanCI, deal_seed
    from botlab.engine import Action, play_hand
    from botlab.profiles import STUDENT, UNSEEN_POOL, Profile
    from pokervision.strategy import VisibleInfo, decide

    class VisionAgent:
        name = "vision-equity-policy"
        def act(self, obs, rng):
            from botlab.cards import card_str
            info = VisibleInfo(tuple(card_str(c) for c in obs.hole), tuple(card_str(c) for c in obs.board), float(obs.pot), float(obs.to_call),
                               float(obs.stack), max(1, min(obs.n_active_opponents, 5)),
                               tuple(k for k, ok in (("FOLD", obs.legal.can_fold), ("CHECK", obs.legal.can_check), ("CALL", obs.legal.can_call), ("RAISE", obs.legal.can_raise or obs.legal.can_bet)) if ok))
            ch = decide(info, rng, sims=120)
            L = obs.legal
            if ch.action == "RAISE" and (L.can_raise or L.can_bet):
                to = max(L.min_to, min(L.max_to, int(max(obs.current_bet, obs.big_blind) * 2.5)))
                return Action("raise" if L.can_raise else "bet", to)
            if ch.action in ("CALL",) and L.can_call:
                return Action("call")
            if ch.action == "FOLD" and L.can_fold:
                return Action("fold")
            return Action("check") if L.can_check else (Action("call") if L.can_call else Action("fold"))

    cands = {"baseline_student": ProfileAgent(STUDENT), "vision_equity_policy": VisionAgent()}
    if learned_path and learned_path.exists():
        cands["botlab_learned"] = ProfileAgent(Profile.from_dict(json.loads(learned_path.read_text(encoding="utf-8"))))
    big_blind, stack = 2, 200
    out = {"seed": seed, "hands_per_opponent": hands_per_opponent, "mode": "heads-up, duplicate deals (both seat assignments)", "opponents": [p.name for p in UNSEEN_POOL], "candidates": {}}
    per_cand_blocks: dict[str, list[float]] = {k: [] for k in cands}
    for name, agent in cands.items():
        per_opp = {}
        for opp in UNSEEN_POOL:
            opp_agent = ProfileAgent(opp)
            blocks = []
            for d in range(hands_per_opponent // 2):
                dseed = deal_seed(seed, d)
                tot = 0
                for r in range(2):
                    seats = [agent, opp_agent] if r == 0 else [opp_agent, agent]
                    me = 0 if r == 0 else 1
                    res = play_hand(seats, [stack] * 2, button=d % 2, small_blind=1, big_blind=big_blind, deck_seed=dseed, hand_id=2 * d + r)
                    tot += res.deltas[me]
                blocks.append(tot / big_blind / 2 * 100)        # bb/100 of this duplicate block (per hand * 100)
            ci = MeanCI.of(blocks)
            per_opp[opp.name] = ci.to_dict()
            per_cand_blocks[name].extend(blocks)
        out["candidates"][name] = {"per_opponent_bb100": per_opp, "pooled_bb100": MeanCI.of(per_cand_blocks[name]).to_dict()}
    # paired difference to baseline on identical deals (same order of blocks)
    base = per_cand_blocks["baseline_student"]
    out["paired_vs_baseline"] = {}
    for name, blocks in per_cand_blocks.items():
        if name == "baseline_student":
            continue
        diff = [a - b for a, b in zip(blocks, base)]
        ci = MeanCI.of(diff)
        out["paired_vs_baseline"][name] = {**ci.to_dict(), "supported_gain": ci.lo > 0, "supported_loss": ci.hi < 0}
    out["exploitability"] = "NOT_COMPUTED: no-limit hold'em is not tractable for exact best response; bb/100 vs fixed training opponents is not exploitability"
    out["equity_note"] = "equity-vs-random is a heuristic input, not optimal strategy or a profit guarantee"
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hands", type=int, default=600); ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--learned", default=str(BOTLAB / "evidence" / "learn-20261005-102859" / "learned_profile.json"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = run(a.hands, a.seed, Path(a.learned))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: {n: v["pooled_bb100"] for n, v in res["candidates"].items()} if k == "candidates" else v for k, v in res.items() if k in ("candidates", "paired_vs_baseline")}, indent=1)[:1500])
