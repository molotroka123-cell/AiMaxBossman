"""Read-only market analysis. The ledger is the sole source of live values."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import schema
from .ledger import Ledger

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("trader_apprentice", REPO / "learning" / "trader_apprentice.py")
assert _spec and _spec.loader
import sys
ta = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ta
_spec.loader.exec_module(ta)


def _complete(rec: dict[str, Any]) -> bool:
    m, q, ev, ins = rec["metrics"], rec["quality"], rec["evidence"], rec["instrument"]
    return (not schema.validate(rec) and q["status"] == schema.VERIFIED and q["fresh_frame"]
            and "triple-read-unanimous/v3" in (ev.get("extractor") or "")
            and all(m.get(k) is not None for k in ("price", "cvd_value", "cvd_unit", "oi_value", "oi_unit"))
            and all(q.get("per_metric", {}).get(k, {}).get("status") == schema.VERIFIED
                    for k in ("price", "cvd", "oi"))
            and all(ins.get(k) for k in ("symbol", "exchange", "timeframe"))
            and m.get("cvd_type") == "aggregated")


def _identity(rec: dict[str, Any]) -> tuple[str, ...]:
    ins = rec["instrument"]
    return (rec["source"]["channel"], ins["symbol"], ins["exchange"], ins["timeframe"],
            rec["metrics"]["cvd_type"], rec["evidence"]["extractor"])


def _snapshot(rec: dict[str, Any]) -> ta.Snapshot:
    ins, m = rec["instrument"], rec["metrics"]
    common = dict(provider="Coinwise/k1m6a", instrument=ins["symbol"], market=ins["exchange"],
                  aggregation=str(ins["timeframe"]), normalization="displayed badge absolute units",
                  version=rec["evidence"]["extractor"])
    return ta.Snapshot(price=m["price"], cvd=m["cvd_value"] * schema.UNITS[m["cvd_unit"]],
                       open_interest=m["oi_value"] * schema.UNITS[m["oi_unit"]],
                       source="twitch:k1m6a", instrument=f"{ins['symbol']}:{ins['exchange']}",
                       timestamp=rec["captured_at_utc"], cvd_series=ta.SeriesId(**common),
                       oi_series=ta.SeriesId(**common))


def _matches(matrix: str, regime: str) -> list[dict[str, str]]:
    matches = []
    path = REPO / "data" / "trading" / "btc_casebook_2026_09.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        if case.get("state", "").upper() == matrix.upper() or case.get("regime") == regime:
            matches.append({"case_id": case["case_id"], "date": case.get("date", ""),
                            "lesson": case.get("lesson", "")})
    for path in sorted((REPO / "data" / "trading" / "canonical_cases").glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        for event in case.get("timeline", []):
            if event.get("state", "").upper() == matrix.upper():
                matches.append({"case_id": case["canonical_case_id"],
                                "date": event.get("date", ""),
                                "lesson": event.get("lesson", "") or event.get("summary", "")})
                break
    return matches[:3]


def analyze_pair(previous: dict[str, Any], latest: dict[str, Any], *, levels: dict[str, float] | None = None) -> dict[str, Any]:
    if not (_complete(previous) and _complete(latest)):
        raise ValueError("analysis requires two complete verified calibrated observations")
    if _identity(previous) != _identity(latest):
        raise ValueError("incompatible metric series")
    p, c = _snapshot(previous), _snapshot(latest)
    level_map = ta.LevelMap(**levels) if levels else None
    result = ta.analyze(p, c, level_map)
    if result.regime == ta.Regime.UNKNOWN:
        raise ValueError("Trader Apprentice refused comparison")
    matrix = f"Price {result.price_direction.value} + CVD {result.cvd_direction.value} + OI {result.oi_direction.value}"
    nearest = result.to_dict()
    return {"timestamp": latest["captured_at_utc"], "instrument": latest["instrument"],
            "price": c.price, "cvd": c.cvd, "oi": c.open_interest,
            "delta_price": c.price - p.price, "delta_cvd": c.cvd - p.cvd,
            "delta_oi": c.open_interest - p.open_interest, "matrix": matrix,
            "regime": result.regime.value, "levels": levels or {},
            "long_scenario": {"trigger": "reclaim confirmed support" if levels else "UNKNOWN",
                              "confirmation": "hold/retest with non-deteriorating flow" if levels else "UNKNOWN",
                              "invalidation": "loss of reclaimed support" if levels else "UNKNOWN"},
            "bear_scenario": {"trigger": "loss of confirmed support" if levels else "UNKNOWN",
                              "confirmation": "failed reclaim with bearish flow" if levels else "UNKNOWN",
                              "invalidation": "reclaim and acceptance above support" if levels else "UNKNOWN"},
            "missing_data": [] if levels else ["levels"],
            "classification_confidence": result.confidence,
            "case_matches": _matches(matrix, result.regime.value),
            "evidence_refs": [{"timestamp": r["captured_at_utc"],
                               "frame_sha256": r["evidence"]["frame_sha256"],
                               "crop_sha256": r["evidence"]["crop_sha256"]} for r in (previous, latest)],
            "classification_detail": nearest}


def latest_analysis(ledger: Ledger) -> dict[str, Any] | None:
    """Find latest complete row and its preceding complete row in the same series."""
    latest = None
    for (raw,) in ledger.db.execute("SELECT raw FROM observations ORDER BY id DESC"):
        rec = json.loads(raw)
        if not _complete(rec):
            continue
        if latest is None:
            latest = rec
        elif _identity(rec) == _identity(latest):
            return analyze_pair(rec, latest)
    return None
