"""Multi-horizon market statistics from verified compatible ledger rows."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import analyzer


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def summarize(latest: dict[str, Any], history: list[dict[str, Any]],
              horizons_minutes: tuple[int, ...] = (15, 30, 60)) -> dict[str, Any]:
    """Compare latest against the nearest verified row at/before each horizon."""
    out: dict[str, Any] = {}
    end = _ts(latest["captured_at_utc"])
    compatible = [r for r in history if analyzer._complete(r) and analyzer._identity(r) == analyzer._identity(latest)
                  and _ts(r["captured_at_utc"]) < end]
    for minutes in horizons_minutes:
        target = end - minutes * 60
        candidates = [r for r in compatible if _ts(r["captured_at_utc"]) <= target]
        if not candidates:
            out[f"{minutes}m"] = {"status": "INSUFFICIENT_HISTORY"}
            continue
        previous = max(candidates, key=lambda r: _ts(r["captured_at_utc"]))
        analysis = analyzer.analyze_pair(previous, latest)
        out[f"{minutes}m"] = {
            "status": "VERIFIED",
            "from": previous["captured_at_utc"],
            "to": latest["captured_at_utc"],
            "delta_price": analysis["delta_price"],
            "delta_cvd": analysis["delta_cvd"],
            "delta_oi": analysis["delta_oi"],
            "matrix": analysis["matrix"],
            "regime": analysis["regime"],
        }
    return out
