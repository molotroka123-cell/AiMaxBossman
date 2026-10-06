"""Policies that map a model INPUT (dict) to an answer {"action","size","probs"?}. Baselines and a numpy imitation stand-in live here; a
fine-tuned LLM/LoRA plugs in through openai_policy.OpenAICompatPolicy with the same interface and the same validator."""
from __future__ import annotations

import random
from functools import lru_cache

import numpy as np

from .ranges import cd, ev
from .validate import validate_output

ACTIONS = ("check", "bet50", "bet100", "allin", "fold", "call", "raise")


def parse_range_text(text: str) -> list:
    return [(t.split(":")[0], float(t.split(":")[1]) if ":" in t else 1.0) for t in text.split(",") if t]


@lru_cache(maxsize=20000)
def _equity(hole: tuple, board: tuple, villain_text: str) -> float:
    from .ranges import expand
    dead = {cd.parse_card(c) for c in hole + board}
    combos = expand(parse_range_text(villain_text), dead)
    b = [cd.parse_card(c) for c in board]
    h = [cd.parse_card(c) for c in hole]
    hs = ev.evaluate(h + b)
    num = den = 0.0
    for (c1, c2), w in combos:
        s = ev.evaluate([c1, c2] + b)
        num += w * (1.0 if hs > s else 0.5 if hs == s else 0.0); den += w
    return num / den if den else 0.5


def equity_vs_range(inp: dict) -> float:
    return _equity(tuple(inp["hero"]["hole"]), tuple(inp["board"]), inp["ranges"]["villain"])


def pot_odds(inp: dict) -> float:
    return inp["to_call"] / (inp["pot_now"] + inp["to_call"]) if inp["to_call"] > 0 else 0.0


def _legal(inp):
    return {a["action"]: a["amount"] for a in inp["legal"]}


def _pure(inp, action):
    return {"action": action, "size": _legal(inp)[action], "explanation": ""}


class Policy:
    name = "policy"
    def act(self, inp: dict) -> dict: raise NotImplementedError


class RandomLegal(Policy):
    name = "random_legal"
    def __init__(self, seed=0): self.rng = random.Random(seed)
    def act(self, inp): return _pure(inp, self.rng.choice(list(_legal(inp))))


class PassiveCheckCall(Policy):
    name = "always_check_call"
    def act(self, inp):
        L = _legal(inp)
        return _pure(inp, "check" if "check" in L else "call")


class EquityRule(Policy):
    """Transparent rule on equity against the GIVEN range and pot odds (the coach's logic transplanted to river spots)."""
    name = "equity_rule"
    def act(self, inp):
        L = _legal(inp); eq = equity_vs_range(inp); po = pot_odds(inp)
        if inp["to_call"] > 0:
            if eq > 0.88:
                for a in ("raise", "allin"):
                    if a in L: return _pure(inp, a)
            return _pure(inp, "call" if eq >= po + 0.03 else "fold")
        if eq > 0.8:
            for a in ("bet50", "bet100", "allin"):
                if a in L: return _pure(inp, a)
        return _pure(inp, "check")


class Reference(Policy):
    """The solver's own answer (upper bound; not a candidate)."""
    name = "solver_reference"
    def __init__(self, table): self.table = table
    def act(self, inp):
        r = self.table[_key(inp)]
        return {"action": r["action"], "size": r["size"], "probs": r["probs"]}


def _key(inp):
    return (tuple(inp["hero"]["hole"]), tuple(inp["board"]), inp["pot_now"], inp["to_call"], tuple(h["action"] for h in inp["history"]), inp["hero"]["position"], inp["effective_stack"], inp["ranges"]["villain"], inp["ranges"]["hero"])


# ---------------------------------------------------------------- imitation stand-in (NOT the LoRA)
def features(inp: dict) -> np.ndarray:
    L = _legal(inp)
    eq = equity_vs_range(inp); po = pot_odds(inp)
    pot = inp["pot_now"]; spr = (inp["effective_stack"] - inp["hero_in_this_street"]) / max(inp["pot_at_river_start"], 1e-9)
    f = [1.0, eq, eq * eq, po, inp["to_call"] / pot, min(spr, 8.0) / 8.0, 1.0 if inp["hero"]["position"] == "IP" else 0.0, 1.0 if inp["to_call"] > 0 else 0.0,
         len(inp["history"]) / 4.0, eq * po, eq * (1.0 if inp["to_call"] > 0 else 0.0)]
    f += [1.0 if a in L else 0.0 for a in ACTIONS]
    return np.array(f, float)


class Imitation(Policy):
    """Small MLP trained on solver labels. A stand-in used to prove the train -> held-out -> compare -> promote pipeline without a GPU; it is
    NOT the LoRA adapter and says nothing about LLM fine-tuning."""
    name = "imitation_mlp"

    def __init__(self, hidden=48, seed=0):
        self.h = hidden; self.rng = np.random.default_rng(seed); self.W1 = self.b1 = self.W2 = self.b2 = None

    def _forward(self, X, M):
        Z = np.tanh(X @ self.W1 + self.b1)
        logits = Z @ self.W2 + self.b2
        logits = np.where(M > 0, logits, -1e9)
        e = np.exp(logits - logits.max(1, keepdims=True))
        return Z, e / e.sum(1, keepdims=True)

    @staticmethod
    def _xy(examples):
        X = np.stack([features(e["input"]) for e in examples])
        M = np.stack([[1.0 if a in _legal(e["input"]) else 0.0 for a in ACTIONS] for e in examples])
        Y = np.stack([[e["reference"]["probs"].get(a, 0.0) for a in ACTIONS] for e in examples])
        return X, M, Y

    def fit(self, train, val=None, epochs=400, lr=0.05, l2=1e-4):
        X, M, Y = self._xy(train)
        mu, sd = X.mean(0), X.std(0) + 1e-6; mu[0], sd[0] = 0.0, 1.0
        self.mu, self.sd = mu, sd
        Xn = (X - mu) / sd
        d = Xn.shape[1]
        self.W1 = self.rng.normal(0, 0.3, (d, self.h)); self.b1 = np.zeros(self.h)
        self.W2 = self.rng.normal(0, 0.3, (self.h, len(ACTIONS))); self.b2 = np.zeros(len(ACTIONS))
        best, best_state = 1e9, None
        for ep in range(epochs):
            Z, P = self._forward(Xn, M)
            G = (P - Y) / len(Xn)
            gW2 = Z.T @ G + l2 * self.W2; gb2 = G.sum(0)
            dZ = (G @ self.W2.T) * (1 - Z * Z)
            gW1 = Xn.T @ dZ + l2 * self.W1; gb1 = dZ.sum(0)
            self.W1 -= lr * gW1 * 5; self.b1 -= lr * gb1 * 5; self.W2 -= lr * gW2 * 5; self.b2 -= lr * gb2 * 5
            if val is not None and ep % 20 == 0:
                v = self._loss(val)
                if v < best:
                    best, best_state = v, (self.W1.copy(), self.b1.copy(), self.W2.copy(), self.b2.copy())
        if best_state:
            self.W1, self.b1, self.W2, self.b2 = best_state
        return self

    def _loss(self, examples):
        X, M, Y = self._xy(examples)
        _, P = self._forward((X - self.mu) / self.sd, M)
        return float(-(Y * np.log(P + 1e-9)).sum(1).mean())

    def act(self, inp):
        X = ((features(inp) - self.mu) / self.sd)[None, :]
        M = np.array([[1.0 if a in _legal(inp) else 0.0 for a in ACTIONS]])
        _, P = self._forward(X, M)
        probs = {a: round(float(P[0, k]), 4) for k, a in enumerate(ACTIONS) if M[0, k] > 0}
        tot = sum(probs.values()); probs = {a: v / tot for a, v in probs.items()}
        best = max(probs, key=probs.get)
        return {"action": best, "size": _legal(inp)[best], "probs": probs}


def safe_distribution(out, inp: dict) -> tuple[dict, bool]:
    """Distribution actually used in play: the model's own if its answer validates; otherwise the safest legal action (check, else fold)
    and the answer counts as INVALID."""
    ok, _, dist = validate_output(out, inp)
    if ok:
        return dist, True
    L = _legal(inp)
    fb = "check" if "check" in L else "fold"
    return {a: (1.0 if a == fb else 0.0) for a in L}, False
