"""Learning from owner decisions and real outcomes. Nothing here changes gates or weights by itself: it produces calibration
evidence, a weight PROPOSAL (needs owner approval) and a redacted dataset for evaluating/fine-tuning Bossman's local models."""
from __future__ import annotations

import json

from .ai import redact
from .scoring import WEIGHTS

MIN_N_CALIBRATION = 20
MIN_N_PROPOSAL = 30


def record_decision(store, tender_key: str, decision: str, reasons: str, snapshot: dict) -> int:
    """snapshot = what the system predicted at decision time: tier_if_cleared, fit_score, components, gate statuses."""
    assert decision in ("go", "no_go", "watch")
    return store.add_doc("decisions", tender_key, {"decision": decision, "reasons": reasons, "predicted": snapshot})


def record_outcome(store, tender_key: str, result: str, actual_margin_pct: float | None, cpv: list[str], note: str) -> int:
    assert result in ("won", "lost", "withdrawn", "not_submitted")
    return store.add_doc("outcomes", tender_key, {"result": result, "actual_margin_pct": actual_margin_pct, "cpv": cpv, "note": note})


def calibration(store) -> dict:
    decisions = {d["tender_key"]: d for d in store.docs("decisions")}
    rows: dict[str, dict] = {}
    for o in store.docs("outcomes"):
        if o["result"] not in ("won", "lost"):
            continue
        d = decisions.get(o["tender_key"])
        tier = (d or {}).get("predicted", {}).get("tier_if_cleared") or "UNKNOWN"
        r = rows.setdefault(tier, {"n": 0, "won": 0})
        r["n"] += 1
        r["won"] += o["result"] == "won"
    out = {}
    for tier, r in rows.items():
        out[tier] = {**r, "win_rate_smoothed": round((r["won"] + 1) / (r["n"] + 2), 3), "enough_data": r["n"] >= MIN_N_CALIBRATION}
    total = sum(r["n"] for r in rows.values())
    return {"total_closed_outcomes": total, "by_predicted_tier": out,
            "note": f"выводы допустимы при n>={MIN_N_CALIBRATION} на класс; сейчас {'достаточно' if total >= MIN_N_CALIBRATION else 'недостаточно'} данных",
            "owner_vs_model": _owner_vs_model(store)}


def _owner_vs_model(store) -> dict:
    agree = disagree = 0
    for d in store.docs("decisions"):
        t = d.get("predicted", {}).get("tier_if_cleared")
        if not t:
            continue
        model_go = t in ("EASY", "MEDIUM")
        if (d["decision"] == "go") == model_go:
            agree += 1
        else:
            disagree += 1
    return {"agree": agree, "disagree": disagree}


def propose_weights(store) -> dict:
    """Compare mean component values of won vs lost tenders; move each weight by at most 5 points; sum stays 100. Proposal only."""
    decisions = {d["tender_key"]: d for d in store.docs("decisions")}
    won, lost = [], []
    for o in store.docs("outcomes"):
        comps = (decisions.get(o["tender_key"]) or {}).get("predicted", {}).get("components")
        if comps and o["result"] in ("won", "lost"):
            (won if o["result"] == "won" else lost).append(comps)
    n = len(won) + len(lost)
    if n < MIN_N_PROPOSAL or not won or not lost:
        return {"status": "INSUFFICIENT_DATA", "n": n, "needed": MIN_N_PROPOSAL, "current_weights": dict(WEIGHTS)}

    def mean(rows, k):
        vals = [r[k]["value"] for r in rows if r.get(k) and r[k]["value"] is not None]
        return sum(vals) / len(vals) if vals else None

    new, evidence = dict(WEIGHTS), {}
    for k in WEIGHTS:
        mw, ml = mean(won, k), mean(lost, k)
        if mw is None or ml is None:
            continue
        delta = max(-5.0, min(5.0, (mw - ml) * 10))
        new[k] = max(2.0, WEIGHTS[k] + delta)
        evidence[k] = {"mean_won": round(mw, 3), "mean_lost": round(ml, 3), "delta": round(delta, 2)}
    scale = 100 / sum(new.values())
    return {"status": "PROPOSAL", "n": n, "weights": {k: round(v * scale, 1) for k, v in new.items()}, "evidence": evidence,
            "requires": "owner approval (action learning.weights.apply)"}


def export_dataset(store) -> str:
    """JSONL, one line per owner decision with its reasons: for evaluating or fine-tuning Bossman local models. Redacted, no company data."""
    lines = []
    for d in store.docs("decisions"):
        t = store.get_tender(d["tender_key"]) or {}
        facts = {k: t.get(k) for k in ("title", "category", "country", "estimated_value", "currency", "deadline", "cpv")}
        lines.append(json.dumps({"input": json.loads(redact(json.dumps(facts, ensure_ascii=False))), "predicted": d.get("predicted", {}).get("tier_if_cleared"),
                                 "owner_decision": d["decision"], "owner_reasons": redact(d.get("reasons", "")), "demo": bool(t.get("demo"))}, ensure_ascii=False))
    return "\n".join(lines)
