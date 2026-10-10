"""SolverPolicy: answer a HU NLHE river input by SOLVING the spot exactly (the same CFR+ solver that labels the dataset), not by a model.

It plugs into the same interface as every other policy (``act(inp) -> {"action","size","probs","explanation"}``) and therefore into the same
validator and the same Bossman route (``pokervision.policy_route``). The answer is the equilibrium of the ABSTRACTED river game
(check / bet 50 % / bet 100 % / pot raise / all-in at low SPR) for the GIVEN ranges; ranges are assumptions supplied by the caller.

It refuses (raises ``NotSolvable``) instead of guessing when: the input history does not follow the abstraction's tree, the legal actions
do not match the tree node, or the hero's hand is not in the hero's given range (no equilibrium strategy exists for it)."""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from .policies import Policy, parse_range_text
from .ranges import cd, expand, strengths
from .solver import Solver, Spot, TreeConfig
from .spots import EXPLOIT_PCT_OF_POT

POS = ("OOP", "IP")


class NotSolvable(ValueError):
    pass


@lru_cache(maxsize=64)
def _solve(board: tuple, pot: float, stack: float, oop_text: str, ip_text: str, iters: int, max_iters: int):
    b = [cd.parse_card(c) for c in board]
    dead = set(b)
    oop = expand(parse_range_text(oop_text), dead)
    ip = expand(parse_range_text(ip_text), dead)
    if not oop or not ip:
        raise NotSolvable("a given range is empty on this board")
    s0 = np.array(strengths(oop, b), float); s1 = np.array(strengths(ip, b), float)
    w0 = np.array([w for _, w in oop]); w1 = np.array([w for _, w in ip])
    m0 = np.array([[h[0], h[1]] for h, _ in oop]); m1 = np.array([[h[0], h[1]] for h, _ in ip])
    compat = ((m0[:, None, :, None] != m1[None, :, None, :]).all((2, 3))).astype(float)
    cfg = TreeConfig(pot=pot, stack=stack)
    sv = Solver(Spot(cfg, s0, s1, w0, w1, compat))
    done, ex = 0, float("inf")
    while done < max_iters:                                  # same schedule as spots.solve_spec: stop at <= 0.5 % of the pot
        ex = sv.solve(iters); done += iters
        if ex <= EXPLOIT_PCT_OF_POT / 100.0 * cfg.pot:
            break
    return sv, (oop, ip), float(ex), done


def study_input(board: list, hole: list, position: str, pot: float, stack: float, hero_range: str, villain_range: str, history: list | None = None,
                iters: int = 400, max_iters: int = 1600) -> dict:
    """Owner's study tool: a model input for a river spot typed in by hand; the legal actions are taken from the solved tree at that point."""
    if position not in POS:
        raise NotSolvable("position must be OOP or IP")
    p = POS.index(position)
    texts = {position: hero_range, POS[1 - p]: villain_range}
    sv, _, _, _ = _solve(tuple(board), float(pot), float(stack), texts["OOP"], texts["IP"], iters, max_iters)
    nd = sv.nodes[0]
    for h in history or []:
        labels = [a for a, _ in nd.actions]
        if nd.kind != "decision" or h["action"] not in labels:
            raise NotSolvable(f"history step {h} is not in the abstracted tree")
        nd = sv.nodes[nd.children[labels.index(h["action"])]]
    if nd.kind != "decision" or nd.player != p:
        raise NotSolvable("it is not this player's decision after that history")
    return {"task": "hu_nlhe_river_action", "format": "HU NLHE river, abstracted bet sizes", "units": "chips",
            "hero": {"position": position, "hole": list(hole)}, "board": list(board), "pot_at_river_start": float(pot), "effective_stack": float(stack),
            "pot_now": round(pot + nd.put[0] + nd.put[1], 2), "hero_in_this_street": round(nd.put[p], 2), "to_call": round(nd.facing, 2),
            "history": [{"player": POS[x[0]], "action": x[1], "amount": round(x[2], 2)} for x in nd.hist],
            "ranges": {"hero": hero_range, "villain": villain_range}, "legal": [{"action": a, "amount": round(amt, 2)} for a, amt in nd.actions]}


class SolverPolicy(Policy):
    name = "river_solver"

    def __init__(self, iters: int = 400, max_iters: int = 1600):
        self.iters, self.max_iters = iters, max_iters

    def act(self, inp: dict) -> dict:
        hero_pos = inp["hero"]["position"]
        if hero_pos not in POS:
            raise NotSolvable("hero position must be OOP or IP")
        p = POS.index(hero_pos)
        texts = {hero_pos: inp["ranges"]["hero"], POS[1 - p]: inp["ranges"]["villain"]}
        sv, combos, exploit, n_it = _solve(tuple(inp["board"]), float(inp["pot_at_river_start"]), float(inp["effective_stack"]),
                                           texts["OOP"], texts["IP"], self.iters, self.max_iters)
        nd = sv.nodes[0]
        for h in inp["history"]:                                  # walk the tree along the observed river history
            labels = [a for a, _ in nd.actions]
            if nd.kind != "decision" or POS[nd.player] != h["player"] or h["action"] not in labels:
                raise NotSolvable(f"history step {h} is not in the abstracted tree")
            k = labels.index(h["action"])
            if abs(nd.actions[k][1] - float(h["amount"])) > 0.02 * max(1.0, nd.actions[k][1]):
                raise NotSolvable(f"history amount {h['amount']} differs from the tree's {nd.actions[k][1]}")
            nd = sv.nodes[nd.children[k]]
        if nd.kind != "decision" or nd.player != p:
            raise NotSolvable("it is not the hero's decision at this node")
        legal = {a["action"]: float(a["amount"]) for a in inp["legal"]}
        tree = {a: float(amt) for a, amt in nd.actions}
        if set(legal) != set(tree) or any(abs(legal[a] - tree[a]) > 0.02 * max(1.0, tree[a]) for a in tree):
            raise NotSolvable(f"legal actions {sorted(legal)} do not match the tree node {sorted(tree)}")
        hole = {cd.parse_card(c) for c in inp["hero"]["hole"]}
        idx = next((i for i, (hc, _) in enumerate(combos[p]) if set(hc) == hole), None)
        if idx is None:
            raise NotSolvable("the hero's hand is not in the hero's given range: no equilibrium strategy for it")
        sig = sv.average(nd)[idx]
        probs = {a: round(float(x), 4) for (a, _), x in zip(nd.actions, sig)}
        tot = sum(probs.values()) or 1.0
        probs = {a: v / tot for a, v in probs.items()}
        best = max(probs, key=probs.get)
        mix = ", ".join(f"{a} {v:.0%}" for a, v in sorted(probs.items(), key=lambda kv: -kv[1]) if v >= 0.01)
        return {"action": best, "size": legal[best], "probs": probs,
                "explanation": f"равновесие абстрактной игры реки (CFR+, {n_it} итераций, эксплуатируемость {exploit:.3g} фишек): {mix}"}
