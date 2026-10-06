"""Evaluation on HELD-OUT spots (split by board). Metrics:
 * validity: parseable JSON, legal action, size equals the action's amount, probabilities a distribution over legal actions;
 * local EV loss vs the equilibrium of the abstracted game, per example (chips and % of the pot) — counterfactual EV of the model's mix versus the
   solver's mix at the same information (exact for this game); invalid answers are reported separately and also scored at the worst legal action;
 * agreement with the solver's most frequent action and total-variation distance between mixes;
 * profile level (policies that can answer at every hand/node): exact EV against the equilibrium and exploitability of the policy as a
   strategy, and exact EV against FIXED opponents (station / nit / maniac) — no sampling noise, so no seeds are needed;
 * latency per answer and peak memory are measured by the caller (they depend on the model server).
All confidence intervals are cluster bootstraps over SPOTS (answers within a spot are not independent)."""
from __future__ import annotations

import random
import time
from collections import defaultdict

import numpy as np

from .dataset import NodeStats, _reaches, model_input
from .policies import ACTIONS, safe_distribution
from .ranges import cd
from .spots import solve_spec
from .validate import parse_model_output, validate_output


def cluster_ci(values_by_spot: dict, n_boot: int = 1000, seed: int = 11) -> dict:
    keys = list(values_by_spot)
    if not keys:
        return {"mean": None, "lo": None, "hi": None, "n_spots": 0}
    per = [(np.sum(values_by_spot[k]), len(values_by_spot[k])) for k in keys]
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        s = [per[rng.randrange(len(per))] for _ in per]
        tot_n = sum(n for _, n in s)
        means.append(sum(v for v, _ in s) / max(tot_n, 1))
    means.sort()
    tot = sum(v for v, _ in per) / max(sum(n for _, n in per), 1)
    return {"mean": round(float(tot), 5), "lo": round(float(means[int(0.025 * n_boot)]), 5), "hi": round(float(means[int(0.975 * n_boot)]), 5), "n_spots": len(keys)}


def paired_diff_ci(a_by_spot: dict, b_by_spot: dict, n_boot: int = 2000, seed: int = 13) -> dict:
    """mean(a) - mean(b) over the SAME spots with a cluster bootstrap. For losses: a = baseline, b = candidate; lo > 0 means the candidate is better."""
    keys = sorted(set(a_by_spot) & set(b_by_spot))
    if not keys:
        return {"mean": None, "lo": None, "hi": None, "n_spots": 0}
    d = [(float(np.sum(a_by_spot[k]) - np.sum(b_by_spot[k])), len(a_by_spot[k])) for k in keys]
    rng = random.Random(seed); out = []
    for _ in range(n_boot):
        s = [d[rng.randrange(len(d))] for _ in d]
        out.append(sum(v for v, _ in s) / max(sum(n for _, n in s), 1))
    out.sort()
    return {"mean": round(sum(v for v, _ in d) / max(sum(n for _, n in d), 1), 5), "lo": round(out[int(0.025 * n_boot)], 5), "hi": round(out[int(0.975 * n_boot)], 5), "n_spots": len(keys)}


def example_metrics(policy, examples: list[dict]) -> dict:
    byspot = defaultdict(lambda: defaultdict(list))
    invalid_reasons = defaultdict(int)
    lat = []
    for ex in examples:
        inp, ref = ex["input"], ex["reference"]
        t0 = time.perf_counter()
        try:
            out = policy.act(inp)
        except Exception as exc:  # noqa: BLE001 - a crashing policy is an invalid answer, not a crash of the evaluation
            out = {"_error": type(exc).__name__}
        lat.append((time.perf_counter() - t0) * 1000)
        out = parse_model_output(out)
        ok, reasons, dist = validate_output(out, inp)
        s = byspot[ex["spot_id"]]
        s["valid"].append(1.0 if ok else 0.0)
        pot = inp["pot_now"]
        evs = ref["ev_by_action"]; eqm = sum(ref["probs"][a] * evs[a] for a in evs)
        worst = eqm - min(evs.values())
        if ok:
            loss = max(0.0, eqm - sum(dist[a] * evs[a] for a in evs))
            s["ev_loss_valid"].append(loss / pot * 100)
            s["ev_loss_all"].append(loss / pot * 100)
            s["top1"].append(1.0 if max(dist, key=dist.get) == ref["action"] else 0.0)
            s["tv"].append(0.5 * sum(abs(dist.get(a, 0.0) - ref["probs"].get(a, 0.0)) for a in set(dist) | set(ref["probs"])))
            act = max(dist, key=dist.get)
            if act.startswith("bet") or act in ("raise", "allin"):
                s["size_ok"].append(1.0 if out.get("size") is not None else 0.0)
        else:
            s["ev_loss_all"].append(worst / pot * 100)
            for r in reasons: invalid_reasons[r.split(" ")[0] + " " + " ".join(r.split(" ")[1:3])] += 1
    res = {"n_examples": len(examples), "latency_ms_p50": round(float(np.percentile(lat, 50)), 3) if lat else None,
           "latency_ms_p95": round(float(np.percentile(lat, 95)), 3) if lat else None, "invalid_reasons": dict(invalid_reasons)}
    for k in ("valid", "ev_loss_valid", "ev_loss_all", "top1", "tv", "size_ok"):
        res[k] = cluster_ci({sp: v[k] for sp, v in byspot.items() if v[k]})
    res["_by_spot"] = {k: {sp: list(v[k]) for sp, v in byspot.items() if v[k]} for k in ("ev_loss_all", "valid")}
    return res


# ---------------------------------------------------------------- profile-level evaluation
def _rank_in_range(s: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Strength percentile of each hand inside its own range (1 = strongest)."""
    order = np.argsort(s); cw = np.cumsum(w[order]); r = np.empty(len(s)); r[order] = (cw - w[order] / 2) / cw[-1]
    return r


def fixed_opponent_profile(ss, player: int, kind: str) -> dict:
    sv = ss.solver
    s = sv.spot.s0 if player == 0 else sv.spot.s1; w = sv.spot.w0 if player == 0 else sv.spot.w1
    r = _rank_in_range(s, w)
    prof = {}
    for nd in sv.nodes:
        if nd.kind != "decision" or nd.player != player:
            continue
        labs = [a for a, _ in nd.actions]
        M = np.zeros((len(s), len(labs)))
        def pick(opts):
            for o in opts:
                if o in labs: return labs.index(o)
            return None
        for i in range(len(s)):
            if nd.facing > 0:
                if kind == "station":
                    a = pick(["call"])
                elif kind == "nit":
                    a = pick(["raise", "allin"]) if r[i] >= 0.9 else (pick(["call"]) if r[i] >= 0.6 else pick(["fold"]))
                else:
                    a = pick(["raise", "allin", "call"])
            else:
                if kind == "station":
                    a = pick(["check"])
                elif kind == "nit":
                    a = pick(["bet50", "bet100", "allin"]) if r[i] >= 0.8 else pick(["check"])
                else:
                    a = pick(["bet100", "allin", "bet50", "check"])
            M[i, a if a is not None else 0] = 1.0
        prof[nd.nid] = M
    return prof


def policy_profile(policy, ss, player: int) -> tuple[dict, int, int]:
    """The policy answers for EVERY hand at EVERY node of ``player``; invalid answers fall back to check/fold and are counted."""
    sv = ss.solver; prof = {}; n_bad = n_all = 0
    for nd in sv.nodes:
        if nd.kind != "decision" or nd.player != player:
            continue
        labs = [a for a, _ in nd.actions]
        M = np.zeros((sv.n[player], len(labs)))
        for i in range(sv.n[player]):
            inp = model_input(ss, nd, i)
            try:
                out = parse_model_output(policy.act(inp))
            except Exception:  # noqa: BLE001
                out = None
            dist, ok = safe_distribution(out, inp)
            n_all += 1; n_bad += 0 if ok else 1
            for k, a in enumerate(labs):
                M[i, k] = dist.get(a, 0.0)
        prof[nd.nid] = M
    return prof, n_bad, n_all


def profile_metrics(policy, specs: list[dict], opponents=("station", "nit", "maniac")) -> dict:
    """Per spot and per seat: EV loss vs equilibrium, exploitability, and EV against fixed opponents (all in % of the pot, exact)."""
    loss, expl, bad = defaultdict(list), defaultdict(list), [0, 0]
    vs = {o: defaultdict(list) for o in opponents}
    for sp in specs:
        ss = solve_spec({"board": [cd.parse_card(c) for c in sp["board"]], "pot": sp["pot"], "stack": sp["stack"], "oop_pct": sp["oop_pct"], "ip_pct": sp["ip_pct"]})
        if ss is None:
            continue
        sv = ss.solver; pot = ss.cfg.pot; key = ss.spot_id
        v_eq = sv.values()
        for p in (0, 1):
            prof, nb, na = policy_profile(policy, ss, p); bad[0] += nb; bad[1] += na
            hv = sv.values(prof)[p]
            loss[key].append((v_eq[p] - hv) / pot * 100)
            expl[key].append((sv.br_value(1 - p, prof) - v_eq[1 - p]) / pot * 100)
            for o in opponents:
                oprof = fixed_opponent_profile(ss, 1 - p, o)
                vs[o][key].append(sv.values({**prof, **oprof})[p] / pot * 100)
    out = {"invalid_share_in_play": round(bad[0] / max(bad[1], 1), 4), "ev_loss_vs_equilibrium_pct_pot": cluster_ci(loss),
           "exploitability_pct_pot": cluster_ci(expl)}
    for o in opponents:
        out[f"ev_vs_{o}_pct_pot"] = cluster_ci(vs[o])
    return out
