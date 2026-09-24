"""Owner-triggered read-only deep market analysis.

Builds a deterministic evidence bundle from the durable market ledger, then
optionally asks the local text model for a synthesis. It never creates an order.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from . import analyzer, horizons
from .deep_request import DeepAnalysisRequest
from .ledger import Ledger
from .notify import explain_local


def _complete_rows(ledger: Ledger) -> list[dict[str, Any]]:
    rows = []
    for (raw,) in ledger.db.execute("SELECT raw FROM observations ORDER BY id"):
        rec = json.loads(raw)
        if analyzer._complete(rec):
            rows.append(rec)
    return rows


def build_deep_bundle(ledger: Ledger, request: DeepAnalysisRequest) -> dict[str, Any]:
    errors = request.validate()
    if errors:
        raise ValueError(f"invalid deep-analysis request: {errors}")
    rows = _complete_rows(ledger)
    if len(rows) < 2:
        return {"status": "INSUFFICIENT_DATA", "symbol": request.symbol, "missing": ["verified_history"]}
    latest = rows[-1]
    compatible = [r for r in rows if analyzer._identity(r) == analyzer._identity(latest)]
    if len(compatible) < 2:
        return {"status": "INSUFFICIENT_DATA", "symbol": request.symbol, "missing": ["compatible_history"]}
    immediate = analyzer.analyze_pair(compatible[-2], latest)
    multi = horizons.summarize(latest, compatible[:-1], (15, 30, 60))
    return {
        "status": "VERIFIED",
        "symbol": request.symbol,
        "timestamp": latest["captured_at_utc"],
        "immediate": immediate,
        "horizons": multi,
        "context_hours": request.context_hours,
        "read_only": True,
        "evidence_refs": immediate["evidence_refs"],
    }


def format_deep(bundle: dict[str, Any], prose: str | None = None) -> str:
    if bundle["status"] != "VERIFIED":
        return "BTC — ГЛУБОКИЙ АНАЛИЗ\n\nНедостаточно свежей совместимой VERIFIED истории."
    a = bundle["immediate"]
    def n(v, scale=1, suffix=""):
        return "UNKNOWN" if v is None else f"{v/scale:,.2f}{suffix}"
    lines = [
        "BTC — ГЛУБОКИЙ АНАЛИЗ СЕЙЧАС",
        f"Время данных: {bundle['timestamp']}",
        "",
        f"Цена: {n(a['price'])}",
        f"CVD: {n(a['cvd'], 1e9, 'B')}",
        f"OI: {n(a['oi'], 1e9, 'B')}",
        f"Сейчас: {a['matrix']} → {a['regime']}",
        "",
        "ГОРИЗОНТЫ",
    ]
    for key in ("15m", "30m", "60m"):
        h = bundle["horizons"].get(key, {})
        if h.get("status") == "VERIFIED":
            lines.append(f"{key}: {h['matrix']} → {h['regime']}")
        else:
            lines.append(f"{key}: недостаточно истории")
    if a.get("levels"):
        lines += ["", f"УРОВНИ: {a['levels']}"]
    if a.get("case_matches"):
        lines += ["", "ПОХОЖИЕ CASE"]
        lines += [f"{c['case_id']}: {c['lesson']}" for c in a["case_matches"][:3]]
    if prose:
        lines += ["", "ВЫВОД", prose]
    lines += ["", "ДАННЫЕ: VERIFIED • READ-ONLY"]
    return "\n".join(lines)


async def run_deep(ledger: Ledger, request: DeepAnalysisRequest) -> str:
    bundle = build_deep_bundle(ledger, request)
    if bundle["status"] != "VERIFIED":
        return format_deep(bundle)
    # Reuse the constrained local explanation path; raw values remain deterministic.
    try:
        prose = await asyncio.to_thread(explain_local, bundle["immediate"])
    except Exception:
        prose = None
    return format_deep(bundle, prose)
