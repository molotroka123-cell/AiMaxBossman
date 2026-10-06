"""Build training / evaluation examples from solved spots.

Input  : what a player can see (own cards, board, pot, stacks, street history, legal actions) + the GIVEN ranges as hand-class lists.
Answer : {"action", "size", "probs", "explanation"} — probs are the equilibrium frequencies of the abstracted game (mixed strategies are kept
         as probabilities; the 'action' is only the most frequent one). The explanation is a TEMPLATE filled from solver facts (equity vs the
         range, pot odds, EV per action), not model-written text.
Split  : by BOARD (all spots on one board go to the same split), never by individual example or frame."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import numpy as np

from .ranges import cd
from .spots import SolvedSpot, make_spec, ranges_text, solve_spec
from .validate import validate_input, validate_reference

POS = ("OOP", "IP")
MIN_REACH = 0.02


def split_of(board_key: str) -> str:
    h = int(hashlib.sha1(("split|" + board_key).encode()).hexdigest()[:8], 16) % 100
    return "train" if h < 70 else "val" if h < 80 else "test"


def _reaches(sv):
    """Reach vectors (own range weights times equilibrium action probabilities) at every decision node."""
    out = {0: (sv.spot.w0.astype(float), sv.spot.w1.astype(float))}
    for nd in sv.nodes:
        if nd.kind != "decision":
            continue
        r0, r1 = out[nd.nid]
        sig = sv.average(nd)
        for a, ch in enumerate(nd.children):
            out[ch] = (r0 * sig[:, a], r1) if nd.player == 0 else (r0, r1 * sig[:, a])
    return out


class NodeStats:
    """Per decision node: reach vectors, equilibrium strategy, per-hand EV per action and equity (all vs the villain's CURRENT range)."""
    def __init__(self, ss: SolvedSpot, nd, reach):
        sv = ss.solver; self.nd = nd; p = nd.player
        r0, r1 = reach[nd.nid]
        own, opp = (r0, r1) if p == 0 else (r1, r0)
        w_own = sv.spot.w0 if p == 0 else sv.spot.w1
        w_opp = sv.spot.w1 if p == 0 else sv.spot.w0
        C = sv.C if p == 0 else sv.C.T
        self.opp_mass = C @ opp
        self.opp_full = C @ w_opp
        self.frac = own / np.maximum(w_own, 1e-12)
        self.sig = sv.average(nd)
        vals = [sv._walk(ch, r0, r1, False, avg=True) for ch in nd.children]
        self.ev = np.stack([v[p] for v in vals], 1) / np.maximum(self.opp_mass[:, None], 1e-12)
        outcome = (sv.A if p == 0 else -sv.A.T)
        self.eq = (np.where(C > 0, (outcome + 1) / 2.0, 0.0) @ opp) / np.maximum(self.opp_mass, 1e-12)

    def reachable(self) -> np.ndarray:
        return np.where((self.frac >= MIN_REACH) & (self.opp_mass >= MIN_REACH * np.maximum(self.opp_full, 1e-12)))[0]


def spec_of(ss: SolvedSpot) -> dict:
    return {"board": [cd.card_str(c) for c in ss.board], "pot": ss.cfg.pot, "stack": ss.cfg.stack, "oop_pct": ss.pct[0], "ip_pct": ss.pct[1]}


def model_input(ss: SolvedSpot, nd, i: int) -> dict:
    """What the model may see for hero = hand ``i`` of the acting player at node ``nd``. No villain cards, no solver output."""
    p = nd.player
    texts = ranges_text(ss.pct)
    hole = [cd.card_str(c) for c in ss.combos[p][i][0]]
    pot_now = round(ss.cfg.pot + nd.put[0] + nd.put[1], 2)
    return {"task": "hu_nlhe_river_action", "format": "HU NLHE river, abstracted bet sizes", "units": "chips",
            "hero": {"position": POS[p], "hole": hole}, "board": [cd.card_str(c) for c in ss.board],
            "pot_at_river_start": ss.cfg.pot, "effective_stack": ss.cfg.stack, "pot_now": pot_now, "hero_in_this_street": round(nd.put[p], 2),
            "to_call": round(nd.facing, 2), "history": [{"player": POS[h[0]], "action": h[1], "amount": round(h[2], 2)} for h in nd.hist],
            "ranges": {"hero": texts[p], "villain": texts[1 - p]}, "legal": [{"action": a, "amount": round(amt, 2)} for a, amt in nd.actions]}


def reference_for(ss: SolvedSpot, st: NodeStats, i: int) -> dict:
    nd = st.nd
    pr = {a: round(float(st.sig[i, k]), 4) for k, (a, _) in enumerate(nd.actions)}
    tot = sum(pr.values()); pr = {a: round(v / tot, 4) for a, v in pr.items()}
    best = max(pr, key=pr.get)
    pot_now = ss.cfg.pot + nd.put[0] + nd.put[1]
    po = nd.facing / (pot_now + nd.facing) if nd.facing > 0 else 0.0
    mix = ", ".join(f"{a} {v:.0%}" for a, v in sorted(pr.items(), key=lambda kv: -kv[1]) if v >= 0.01)
    expl = (f"Equity against the villain's range here ≈ {st.eq[i]:.0%}" + (f", the call needs {po:.0%}" if nd.facing > 0 else "")
            + f". Equilibrium of the abstracted game mixes: {mix}.")
    return {"action": best, "size": round(dict(nd.actions)[best], 2), "probs": pr, "explanation": expl,
            "ev_by_action": {a: round(float(st.ev[i, k]), 3) for k, (a, _) in enumerate(nd.actions)}, "equity": round(float(st.eq[i]), 3)}


def examples_from_spot(ss: SolvedSpot, per_node_cap: int = 6, rng: random.Random | None = None) -> list[dict]:
    rng = rng or random.Random(ss.spot_id)
    reach = _reaches(ss.solver)
    out = []
    for nd in ss.solver.nodes:
        if nd.kind != "decision":
            continue
        st = NodeStats(ss, nd, reach)
        ok = list(st.reachable())
        if not ok:
            continue
        pick = ok if len(ok) <= per_node_cap else rng.sample(ok, per_node_cap)
        for i in pick:
            inp, ref = model_input(ss, nd, int(i)), reference_for(ss, st, int(i))
            if validate_input(inp) or validate_reference(ref, inp):
                continue
            out.append({"id": f"{ss.spot_id}:{nd.nid}:{i}", "spot_id": ss.spot_id, "board_key": ss.board_key, "node": nd.nid, "hand_idx": int(i),
                        "spec": spec_of(ss), "input": inp, "reference": ref})
    return out


def build(n_spots: int, seed: int, out_dir: Path, per_node_cap: int = 6, workers: int = 4) -> dict:
    """Solve spots, keep converged ones, write jsonl per split and a manifest with a content hash."""
    from concurrent.futures import ProcessPoolExecutor
    rng = random.Random(seed)
    specs, seen = [], set()
    from .spots import spot_id
    while len(specs) < n_spots:
        sp = make_spec(rng)
        if spot_id(sp) not in seen:
            seen.add(spot_id(sp)); specs.append(sp)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, rejected = [], []
    with ProcessPoolExecutor(workers) as ex:
        for sp, res in zip(specs, ex.map(_work, [(s, per_node_cap) for s in specs], chunksize=2)):
            if res is None:
                rejected.append({"spot": spot_id(sp), "why": "range too small"}); continue
            kind, payload = res
            (rows if kind == "ok" else rejected).extend(payload if kind == "ok" else [payload])
    splits = {"train": [], "val": [], "test": []}
    for r in rows:
        splits[split_of(r["board_key"])].append(r)
    h = hashlib.sha256()
    for name, lst in splits.items():
        with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for r in sorted(lst, key=lambda r: r["id"]):
                line = json.dumps(r, ensure_ascii=False, sort_keys=True); f.write(line + "\n"); h.update(line.encode())
    (out_dir / "rejected_spots.json").write_text(json.dumps(rejected, indent=1), encoding="utf-8")
    boards = {k: sorted({r["board_key"] for r in v}) for k, v in splits.items()}
    leak = set(boards["train"]) & (set(boards["val"]) | set(boards["test"])) or set(boards["val"]) & set(boards["test"])
    manifest = {"seed": seed, "spots_requested": n_spots, "spots_kept": len({r["spot_id"] for r in rows}), "spots_rejected": len(rejected),
                "examples": {k: len(v) for k, v in splits.items()}, "boards": {k: len(v) for k, v in boards.items()}, "board_leak": sorted(leak),
                "sha256": h.hexdigest(), "format": "HU NLHE river, ranges given, abstracted bet sizes (solver.TreeConfig defaults)"}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def _work(arg):
    spec, cap = arg
    ss = solve_spec(spec)
    if ss is None:
        return None
    from .spots import EXPLOIT_PCT_OF_POT
    if ss.exploit > EXPLOIT_PCT_OF_POT / 100.0 * ss.cfg.pot:
        return ("rej", {"spot": ss.spot_id, "why": f"not converged: exploitability {ss.exploit:.3f} chips"})
    return ("ok", examples_from_spot(ss, cap))


def load(dir_: Path, split: str) -> list[dict]:
    p = Path(dir_) / f"{split}.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
