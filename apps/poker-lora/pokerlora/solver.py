"""Exact river subgame solver (vectorised CFR+) for heads-up no-limit hold'em with GIVEN ranges and a small bet-size abstraction.

Own implementation (numpy only; no third-party solver code). Verified on a toy game with a known equilibrium (tests) and by exploitability.
Utilities are chips won/lost relative to the start of the river, each player having put P0/2 into the dead money.

Tree: OOP acts first. No bet facing: check | bet(sizes) | all-in. Facing a bet: fold | call | raise (one pot-size raise) | all-in; after a raise only
fold | call. Check-check = showdown. The abstraction is part of the label: strategies are equilibria OF THIS ABSTRACTED GAME."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Node:
    nid: int
    kind: str                          # "decision" | "fold" | "showdown"
    player: int = -1                   # actor (decision) or folder (fold)
    actions: list = field(default_factory=list)       # [(label, amount_to_put_in_total_this_action)]
    children: list = field(default_factory=list)
    put: tuple = (0.0, 0.0)            # chips each player has put in on the river so far
    facing: float = 0.0                # amount hero must add to call (decision nodes)
    path: tuple = ()                   # action labels from the root, for history text
    hist: tuple = ()                   # ((player, label, amount_added), ...) from the root
    n_raises: int = 0


@dataclass
class TreeConfig:
    pot: float                         # dead money before the river action (both players' contributions so far)
    stack: float                       # effective stack behind
    bet_fracs: tuple = (0.5, 1.0)
    raise_frac: float = 1.0            # pot-size raise
    allow_raise: bool = True
    allow_allin: bool = True
    allin_max_spr: float = 3.0         # all-in is offered as an extra option only when stack <= this * pot


def build_tree(cfg: TreeConfig) -> list[Node]:
    nodes: list[Node] = []

    def new(**kw) -> Node:
        n = Node(nid=len(nodes), **kw); nodes.append(n); return n

    def pot_of(put): return cfg.pot + put[0] + put[1]

    def amounts_open(put, p):
        rem = cfg.stack - put[p]
        out = []
        for f in cfg.bet_fracs:
            a = round(f * pot_of(put), 2)
            if 0 < a < rem:
                out.append((f"bet{int(round(f * 100))}", a))
        if cfg.allow_allin and rem > 0 and (cfg.stack <= cfg.allin_max_spr * cfg.pot or not out):
            out.append(("allin", rem))
        elif rem > 0 and not out:
            out.append(("allin", rem))
        return out

    def rec(player, put, path, facing, n_raises, checks, hist=()):
        node = new(kind="decision", player=player, put=put, facing=facing, path=path, n_raises=n_raises, hist=hist)
        opp = 1 - player
        acts, kids = [], []

        def fold_child():
            n = new(kind="fold", player=player, put=put, path=path + ("fold",)); return n.nid

        def showdown_child(p2):
            n = new(kind="showdown", put=p2, path=path + ("call",)); return n.nid

        if facing <= 0:
            acts.append(("check", 0.0))
            if checks == 1:
                n = new(kind="showdown", put=put, path=path + ("check",)); kids.append(n.nid)
            else:
                kids.append(rec(opp, put, path + ("check",), 0.0, n_raises, 1, hist + ((player, "check", 0.0),)))
            for lab, amt in amounts_open(put, player):
                p2 = list(put); p2[player] += amt
                acts.append((lab, amt)); kids.append(rec(opp, tuple(p2), path + (lab,), amt, n_raises, 0, hist + ((player, lab, amt),)))
        else:
            acts.append(("fold", 0.0)); kids.append(fold_child())
            call_amt = min(facing, cfg.stack - put[player])
            p2 = list(put); p2[player] += call_amt
            acts.append(("call", call_amt)); kids.append(showdown_child(tuple(p2)))
            rem_after = cfg.stack - p2[player]
            if rem_after > 0 and n_raises == 0 and cfg.allow_raise:
                pot_after_call = pot_of(tuple(p2))
                raise_extra = round(cfg.raise_frac * pot_after_call, 2)
                opts = []
                if 0 < raise_extra < rem_after:
                    opts.append(("raise", raise_extra))
                if cfg.allow_allin and rem_after > 0 and (not opts or cfg.stack <= cfg.allin_max_spr * cfg.pot):
                    opts.append(("allin", rem_after))
                for lab, extra in opts:
                    p3 = list(p2); p3[player] += extra
                    acts.append((lab, call_amt + extra))
                    kids.append(rec(opp, tuple(p3), path + (lab,), extra, 1, 0, hist + ((player, lab, call_amt + extra),)))
        node.actions, node.children = acts, kids
        return node.nid

    rec(0, (0.0, 0.0), (), 0.0, 0, 0)
    # the root must be nodes[0]
    return nodes


@dataclass
class Spot:
    cfg: TreeConfig
    s0: np.ndarray                     # hand strengths, player 0 (OOP)
    s1: np.ndarray
    w0: np.ndarray                     # range weights
    w1: np.ndarray
    compat: np.ndarray                 # [n0, n1] 1 if the two hands do not share a card


class Solver:
    def __init__(self, spot: Spot):
        self.spot = spot
        self.nodes = build_tree(spot.cfg)
        n0, n1 = len(spot.s0), len(spot.s1)
        self.n = (n0, n1)
        sgn = np.sign(spot.s0[:, None] - spot.s1[None, :]).astype(np.float64)
        self.A = spot.compat * sgn                      # player-0 showdown outcome matrix
        self.C = spot.compat.astype(np.float64)
        self.regret = {}; self.ssum = {}
        for nd in self.nodes:
            if nd.kind == "decision":
                self.regret[nd.nid] = np.zeros((self.n[nd.player], len(nd.actions)))
                self.ssum[nd.nid] = np.zeros((self.n[nd.player], len(nd.actions)))
        self.t = 0
        self.half = spot.cfg.pot / 2.0

    # ------------------------------------------------------------ strategy
    def _sigma(self, nd: Node) -> np.ndarray:
        r = np.maximum(self.regret[nd.nid], 0.0)
        s = r.sum(1, keepdims=True)
        k = r.shape[1]
        return np.where(s > 0, r / np.maximum(s, 1e-12), 1.0 / k)

    def average(self, nd: Node) -> np.ndarray:
        s = self.ssum[nd.nid]; tot = s.sum(1, keepdims=True)
        return np.where(tot > 0, s / np.maximum(tot, 1e-12), 1.0 / s.shape[1])

    # ------------------------------------------------------------ traversal
    def _terminal(self, nd: Node, r0, r1):
        if nd.kind == "showdown":
            c = self.half + nd.put[0]                   # equal contributions at showdown
            return c * (self.A @ r1), -c * (self.A.T @ r0)
        f = nd.player; m = self.half + nd.put[f]
        if f == 1:                                      # player 1 folds, player 0 wins m
            return m * (self.C @ r1), -m * (self.C.T @ r0)
        return -m * (self.C @ r1), m * (self.C.T @ r0)

    def _walk(self, nid: int, r0, r1, update: bool, avg: bool = False, br_player: int | None = None, profile=None):
        nd = self.nodes[nid]
        if nd.kind != "decision":
            return self._terminal(nd, r0, r1)
        p = nd.player
        if profile is not None and nid in profile:
            sig = profile[nid]
        elif br_player is not None and p == br_player:
            sig = None
        else:
            sig = self.average(nd) if avg else self._sigma(nd)
        vals = []
        for a, ch in enumerate(nd.children):
            if sig is None:
                vals.append(self._walk(ch, r0, r1, update, avg, br_player, profile))
            elif p == 0:
                vals.append(self._walk(ch, r0 * sig[:, a], r1, update, avg, br_player, profile))
            else:
                vals.append(self._walk(ch, r0, r1 * sig[:, a], update, avg, br_player, profile))
        up = np.stack([v[p] for v in vals], 1)                         # [n_p, n_a]: the actor's counterfactual value per hand and action
        if sig is None:                                                # best response: each hand takes its best action
            zeros = np.zeros(self.n[1 - p])
            best = up.max(1)
            return (best, zeros) if p == 0 else (zeros, best)
        uo = sum(v[1 - p] for v in vals)                                # the other player's value: sum over actions (reach already includes sigma)
        node_u = (sig * up).sum(1)
        if update:
            self.regret[nd.nid] = np.maximum(self.regret[nd.nid] + (up - node_u[:, None]), 0.0)   # CFR+
            own = r0 if p == 0 else r1
            self.ssum[nd.nid] += self.t * own[:, None] * sig                                         # linear averaging
        return (node_u, uo) if p == 0 else (uo, node_u)

    # ------------------------------------------------------------ public
    def solve(self, iters: int = 400) -> float:
        for _ in range(iters):
            self.t += 1
            self._walk(0, self.spot.w0.astype(np.float64), self.spot.w1.astype(np.float64), True)
        return self.exploitability()

    def _norm(self) -> float:
        return float(self.spot.w0 @ self.C @ self.spot.w1)

    def values(self, profile=None) -> tuple[float, float]:
        """Expected value (chips) of each player when both play ``profile`` where given, else the average strategy."""
        u0, u1 = self._walk(0, self.spot.w0.astype(float), self.spot.w1.astype(float), False, avg=True, profile=profile)
        z = self._norm()
        return float(self.spot.w0 @ u0) / z, float(self.spot.w1 @ u1) / z

    def br_value(self, player: int, profile=None) -> float:
        """Value of ``player`` best-responding to the other player's strategy (default: average). ``profile`` fixes strategies of nodes."""
        u0, u1 = self._walk(0, self.spot.w0.astype(float), self.spot.w1.astype(float), False, avg=True, br_player=player, profile=profile)
        w = self.spot.w0 if player == 0 else self.spot.w1
        return float(w @ (u0 if player == 0 else u1)) / self._norm()

    def exploitability(self, profile=None) -> float:
        """(BR0 + BR1) / 2 in chips; 0 at equilibrium (zero-sum, the two players' values sum to 0)."""
        return (self.br_value(0, profile) + self.br_value(1, profile)) / 2.0
