"""Training report: local hand history OR local simulation -> report.json + REPORT.md with the limits explained.

Two sources, both strictly local (no network, no third-party site, no real-money action):

* ``simulation`` - the headless engine (seeded, reproducible; optional cash rake).
* ``history``    - a hand-history file the owner already has:
    - a Poker Train session export (JSON with ``handSummaries`` / ``records``), or
    - JSON Lines, one hand per line: ``{"position": "BTN", "preflop": "raise", "net": -12, "bb": 10}``
      (``net`` in chips, ``bb`` = big blind in chips; ``mistake`` / ``ev_lost`` optional).

The report never promises winnings. Every number that is an estimate comes with its interval and the
assumption behind it; sample-size and rake status are always stated.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import statistics
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Z95 = 1.959964
NON_VPIP = {"fold", "folds", "check", "checks", "bb_walk", "", None}
PFR_ACTIONS = {"raise", "raises", "bet", "bets"}

# Rule-of-thumb 6-max cash ranges used ONLY to flag "worth a look" - heuristics, not solver output.
HEURISTIC_RANGES = {"vpip_pct": (18.0, 28.0), "pfr_pct": (14.0, 24.0), "max_vpip_pfr_gap": 10.0}


@dataclass
class HandRec:
    position: str | None
    preflop: str | None  # hero's first preflop action
    net_chips: float
    big_blind: float | None
    mistake: str | None = None
    ev_lost: float = 0.0


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    z2 = Z95 * Z95
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _pct(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"value": round(100 * k / n, 1) if n else None, "n": n, "ci95": [round(100 * lo, 1), round(100 * hi, 1)]}


def _first(items: Iterable[dict], stage: str) -> dict | None:
    for a in items or []:
        if a.get("stage") == stage:
            return a
    return None


def load_history(path: Path, default_bb: float | None = None) -> tuple[list[HandRec], dict[str, Any]]:
    raw = path.read_bytes()
    meta: dict[str, Any] = {"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    text = raw.decode("utf-8")
    hands: list[HandRec] = []
    stripped = text.lstrip()
    if stripped.startswith("{") and '"handSummaries"' in text:
        data = json.loads(text)
        meta["format"] = "poker-train session export"
        meta["session_id"] = data.get("sessionId")
        bb_by_hand: dict[Any, float] = {}
        for r in data.get("records", []) or []:
            bl = r.get("blinds") or {}
            if isinstance(bl, dict) and bl.get("bb"):
                bb_by_hand.setdefault(r.get("handNumber"), float(bl["bb"]))
        meta["rake_recorded"] = data.get("rake") if "rake" in data else "unknown (export has no rake field)"
        for h in data["handSummaries"]:
            pf = _first(h.get("actions") or [], "preflop")
            hands.append(HandRec(position=h.get("position"), preflop=(pf or {}).get("action"),
                                 net_chips=float(h.get("netProfit") or 0.0),
                                 big_blind=bb_by_hand.get(h.get("hand"), default_bb),
                                 mistake=h.get("mistake"), ev_lost=float(h.get("evLost") or 0.0)))
    else:
        meta["format"] = "jsonl hands"
        for i, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            h = json.loads(line)
            if "net" not in h and "net_chips" not in h and "delta" not in h:
                continue  # non-hand event lines (e.g. decisions) are skipped, not guessed at
            net = h.get("net", h.get("net_chips", h.get("delta")))
            hands.append(HandRec(position=h.get("position"), preflop=h.get("preflop"), net_chips=float(net),
                                 big_blind=h.get("bb", default_bb), mistake=h.get("mistake"),
                                 ev_lost=float(h.get("ev_lost") or 0.0)))
    meta["hands_parsed"] = len(hands)
    return hands, meta


def _winrate(net_bb: Sequence[float]) -> dict[str, Any]:
    n = len(net_bb)
    if n == 0:
        return {"n": 0}
    mean = statistics.fmean(net_bb)
    sd = statistics.stdev(net_bb) if n > 1 else 0.0
    half = Z95 * sd / math.sqrt(n) if n > 1 else float("inf")
    need = math.ceil((Z95 * sd * 100 / 5.0) ** 2) if sd > 0 else None  # hands for a +-5 bb/100 interval
    return {
        "hands": n,
        "bb_per_100": round(mean * 100, 2),
        "sd_bb_per_100_hands": round(sd * 10, 1),  # sd per 100 hands = sd_hand * sqrt(100)
        "ci95_bb_per_100": [round((mean - half) * 100, 2), round((mean + half) * 100, 2)] if n > 1 else None,
        "hands_needed_for_plus_minus_5bb100": need,
    }


def report_from_hands(hands: Sequence[HandRec], meta: dict[str, Any], default_bb: float | None) -> dict[str, Any]:
    missing_bb = [h for h in hands if not (h.big_blind or default_bb)]
    usable = [h for h in hands if (h.big_blind or default_bb)]
    net_bb = [h.net_chips / (h.big_blind or default_bb) for h in usable]  # type: ignore[operator]
    pf = [h for h in hands if h.preflop is not None]
    vpip_k = sum(1 for h in pf if h.preflop not in NON_VPIP)
    pfr_k = sum(1 for h in pf if h.preflop in PFR_ACTIONS)
    by_pos: dict[str, dict[str, Any]] = {}
    for h in hands:
        d = by_pos.setdefault(h.position or "?", {"hands": 0, "net_bb": 0.0, "vpip": 0, "pfr": 0, "pf_hands": 0})
        d["hands"] += 1
        if h.big_blind or default_bb:
            d["net_bb"] += h.net_chips / (h.big_blind or default_bb)  # type: ignore[operator]
        if h.preflop is not None:
            d["pf_hands"] += 1
            d["vpip"] += h.preflop not in NON_VPIP
            d["pfr"] += h.preflop in PFR_ACTIONS
    positions = {k: {"hands": v["hands"], "net_bb": round(v["net_bb"], 1),
                     "vpip_pct": round(100 * v["vpip"] / v["pf_hands"], 1) if v["pf_hands"] else None,
                     "pfr_pct": round(100 * v["pfr"] / v["pf_hands"], 1) if v["pf_hands"] else None}
                 for k, v in sorted(by_pos.items())}
    mistakes: dict[str, int] = {}
    for h in hands:
        if h.mistake:
            mistakes[h.mistake] = mistakes.get(h.mistake, 0) + 1
    return {
        "winrate": _winrate(net_bb),
        "vpip": _pct(vpip_k, len(pf)),
        "pfr": _pct(pfr_k, len(pf)),
        "by_position": positions,
        "mistakes": {"count": sum(mistakes.values()), "by_type": mistakes,
                     "ev_lost_chips_sum": round(sum(h.ev_lost for h in hands), 1)},
        "data_quality": {"hands": len(hands), "hands_without_big_blind": len(missing_bb),
                         "hands_without_preflop_action": len(hands) - len(pf)},
    }


def report_from_simulation(hero: str, opponents: Sequence[str], hands: int, seed: int, big_blind: int, rake_pct: float,
                           rake_cap_bb: float, load_profile) -> dict[str, Any]:
    from .arena import run_arena
    from .engine import Rake

    profiles = [load_profile(hero)] + [load_profile(o) for o in opponents]
    rake = Rake(pct=rake_pct, cap=int(round(rake_cap_bb * big_blind))) if rake_pct > 0 else None
    res = run_arena(profiles, hands=hands, seed=seed, big_blind=big_blind, duplicate=True, rake=rake)
    b = res.bots[0]
    d = b.to_dict()
    n = b.hands
    return {
        "winrate": {"hands": n, **{k: d["bb_per_100"][k] for k in ("mean", "sd", "ci95_lo", "ci95_hi")}},
        "vpip": _pct(b.vpip, n),
        "pfr": _pct(b.pfr, n),
        "af": d["af"],
        "wtsd_pct": d["wtsd_pct"],
        "showdown_win_pct": d["showdown_win_pct"],
        "table": res.to_dict(),
    }


def _flags(rep: dict[str, Any]) -> list[str]:
    out = []
    v, p = rep.get("vpip", {}), rep.get("pfr", {})
    lo_v, hi_v = HEURISTIC_RANGES["vpip_pct"]
    lo_p, hi_p = HEURISTIC_RANGES["pfr_pct"]
    if v.get("value") is not None:
        if v["ci95"][0] > hi_v:
            out.append(f"VPIP {v['value']}% is above the {lo_v:.0f}-{hi_v:.0f}% heuristic band (even the interval's low end).")
        elif v["ci95"][1] < lo_v:
            out.append(f"VPIP {v['value']}% is below the {lo_v:.0f}-{hi_v:.0f}% heuristic band.")
    if p.get("value") is not None:
        if p["ci95"][0] > hi_p:
            out.append(f"PFR {p['value']}% is above the {lo_p:.0f}-{hi_p:.0f}% heuristic band.")
        elif p["ci95"][1] < lo_p:
            out.append(f"PFR {p['value']}% is below the {lo_p:.0f}-{hi_p:.0f}% heuristic band.")
    if v.get("value") is not None and p.get("value") is not None and v["value"] - p["value"] > HEURISTIC_RANGES["max_vpip_pfr_gap"]:
        out.append(f"VPIP-PFR gap {v['value'] - p['value']:.1f} points: many calls relative to raises (passive/limp-heavy).")
    return out


def _limits(rep: dict[str, Any], source: str) -> list[str]:
    w = rep["winrate"]
    n = w.get("hands", 0)
    lim = [
        "This is a training report, not advice to wager and not a promise of winnings; poker results vary widely.",
        f"Sample: {n} hands. Win-rate intervals below ~10,000 hands are wide; a positive point estimate is not proof of an edge.",
    ]
    need = w.get("hands_needed_for_plus_minus_5bb100")
    if need:
        lim.append(f"With the observed variance about {need:,} hands are needed for a +-5 bb/100 95% interval.")
    if source == "simulation":
        lim += [
            "Opponents are scripted training profiles, not people; results measure style vs those profiles only.",
            "Hero equity inside the policy is computed against RANDOM hands, not realistic ranges.",
            "Seeded and duplicate-dealt: the same seed and settings reproduce the same numbers exactly.",
        ]
    else:
        lim += [
            "Chips are normalised to big blinds per hand; tournament chips are not money and blinds/ICM change value.",
            "Positions, VPIP and PFR are taken from the file as recorded; hands without a preflop action are excluded from them.",
            "Mistake labels / EV-lost come from the training app's own heuristic evaluator, not a solver.",
        ]
    return lim


def build_report(source: str, body: dict[str, Any], meta: dict[str, Any], rake: dict[str, Any] | None, started: float) -> dict[str, Any]:
    rep = {
        "kind": "poker_training_report",
        "source": source,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input": meta,
        "rake": rake if rake else "none applied" if source == "simulation" else meta.get("rake_recorded", "unknown"),
        **body,
        "elapsed_sec": round(time.time() - started, 2),
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
    }
    rep["flags"] = _flags(rep)
    rep["limits"] = _limits(rep, source)
    rep["status"] = {"technically_executed": True, "accepted_by_customer": "NOT PROVEN", "paid": "NOT PROVEN"}
    return rep


def render_markdown(rep: dict[str, Any]) -> str:
    w = rep["winrate"]
    lines = ["# Poker training report", "", f"- source: **{rep['source']}**   generated: {rep['generated_utc']}",
             f"- rake: {rep['rake']}", f"- elapsed: {rep['elapsed_sec']} s", ""]
    lines += ["## Result", ""]
    if "bb_per_100" in w:
        lo, hi = (w.get("ci95_bb_per_100") or [None, None])
        lines.append(f"- win-rate: **{w['bb_per_100']} bb/100** over {w['hands']} hands, 95% CI [{lo}, {hi}]")
    else:
        lines.append(f"- win-rate: **{w['mean']} bb/100** over {w['hands']} hands, 95% CI [{w['ci95_lo']}, {w['ci95_hi']}]")
    for k, label in (("vpip", "VPIP"), ("pfr", "PFR")):
        d = rep[k]
        lines.append(f"- {label}: {d['value']}% (n={d['n']}, 95% CI {d['ci95'][0]}-{d['ci95'][1]}%)")
    if rep.get("af") is not None:
        lines.append(f"- aggression factor: {rep['af']}, WTSD: {rep.get('wtsd_pct')}%, W$SD: {rep.get('showdown_win_pct')}%")
    if "by_position" in rep:
        lines += ["", "## By position", "", "| pos | hands | net bb | VPIP % | PFR % |", "|---|---|---|---|---|"]
        for pos, d in rep["by_position"].items():
            lines.append(f"| {pos} | {d['hands']} | {d['net_bb']} | {d['vpip_pct']} | {d['pfr_pct']} |")
        m = rep["mistakes"]
        lines += ["", f"Recorded mistakes: {m['count']} (by type: {m['by_type']}), EV lost sum: {m['ev_lost_chips_sum']} chips (app heuristic)."]
    if "table" in rep:
        t = rep["table"]
        lines += ["", "## Table", "", "| bot | hands | bb/100 | 95% CI |", "|---|---|---|---|"]
        for b in t["bots"]:
            c = b["bb_per_100"]
            lines.append(f"| {b['label']} | {b['hands']} | {c['mean']} | [{c['ci95_lo']}, {c['ci95_hi']}] |")
        lines.append(f"\nrake total: {t['rake_total_chips']} chips ({t['rake_bb_per_100_hands']} bb per 100 hands, whole table); "
                     f"chip conservation: {t['chip_conservation']}; seed={t['seed']}")
    lines += ["", "## Worth a look (heuristic)", ""] + [f"- {f}" for f in (rep["flags"] or ["nothing outside the heuristic bands"])]
    lines += ["", "## Limits", ""] + [f"- {x}" for x in rep["limits"]]
    lines += ["", "## Status", "",
              f"- technically executed: {rep['status']['technically_executed']}",
              f"- accepted by a real customer: {rep['status']['accepted_by_customer']}",
              f"- payment confirmed: {rep['status']['paid']}", ""]
    return "\n".join(lines)


def write_report(rep: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    j, m = out_dir / "report.json", out_dir / "REPORT.md"
    j.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    m.write_text(render_markdown(rep), encoding="utf-8")
    return j, m
