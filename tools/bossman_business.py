"""Reports / Operations MANUAL import tools, one entry point (Reports-Import.cmd in the Windows bundle).

  exchange     exchange_volume.cli      run --export result.json|export.html --period business --out DIR
  report       exchange_volume.report   --result DIR/volume_result.json --deals DIR/deals.csv --out DIR
  ops          ops_intake.cli           requests -> deduplicated table
  ops-report   ops_intake.report        table -> daily report

These are MANUAL imports: a file you hand over is read once and the results are written to the folder you name
(report.md, disputed.csv, deals.csv, volume_result.json). Nothing is imported from Telegram automatically, nothing
is sent anywhere, no network call is made, and a business-month figure is never called profit.

Works from a checkout (packages beside this file) and from the installed bundle (packages in ``business_tools/``).
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = {"exchange": "exchange_volume.cli", "report": "exchange_volume.report",
         "ops": "ops_intake.cli", "ops-report": "ops_intake.report"}


def utf8_console() -> None:
    """The launcher runs with ``-I`` (which implies ``-E``): PYTHONUTF8 / PYTHONIOENCODING are ignored and a Windows
    console gets the locale code page, so the first Cyrillic report line would raise UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def compute_unit_economics(
    *,
    runtime_seconds: float,
    pc_power_watts: float = 350.0,
    kwh_price_usd: float = 0.15,
    cloud_cost_usd: float = 0.0,
    report_price_usd: float = 49.0,
) -> dict:
    """Deterministic unit-economics calculation from measured run metrics."""
    if runtime_seconds < 0 or pc_power_watts < 0 or kwh_price_usd < 0 or cloud_cost_usd < 0:
        raise ValueError("costs and runtime cannot be negative")
    energy_kwh = (runtime_seconds / 3600.0) * (pc_power_watts / 1000.0)
    energy_cost_usd = round(energy_kwh * kwh_price_usd, 8)
    total_cost_usd = round(energy_cost_usd + cloud_cost_usd, 8)
    net_margin_usd = round(report_price_usd - total_cost_usd, 6)
    margin_ratio = round(net_margin_usd / report_price_usd, 6) if report_price_usd > 0 else 0.0
    return {
        "runtime_seconds": round(runtime_seconds, 4),
        "energy_cost_usd": energy_cost_usd,
        "cloud_cost_usd": round(cloud_cost_usd, 6),
        "total_cost_usd": total_cost_usd,
        "report_price_usd": round(report_price_usd, 2),
        "net_margin_usd": net_margin_usd,
        "margin_ratio": margin_ratio,
        "positive_margin": bool(net_margin_usd > 0),
    }


def funnel_summary(rows: list[dict] | None = None) -> dict:
    """Honest commercial funnel ledger: never fabricates demo clients or paid orders."""
    items = list(rows or [])
    paid = [
        r for r in items
        if r.get("stage") == "paid"
        and not r.get("is_demo", False)
        and bool(str(r.get("payment_proof_ref") or "").strip())
        and float(r.get("amount_usd") or 0.0) > 0
    ]
    return {
        "leads": sum(1 for r in items if not r.get("is_demo", False)),
        "orders": sum(1 for r in items if r.get("stage") in ("order", "paid") and not r.get("is_demo", False)),
        "paid_clients": len(paid),
        "confirmed_revenue_usd": round(sum(float(r["amount_usd"]) for r in paid), 2),
        "status": "PASS" if paid else "NOT_RUN",
    }


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in TOOLS:
        print(__doc__)
        return 2
    for folder in (HERE / "business_tools", HERE):          # installed bundle first, then a checkout's tools/
        if (folder / "exchange_volume").is_dir():
            sys.path.insert(0, str(folder))
            break
    module = TOOLS[argv[0]]
    sys.argv = [module, *argv[1:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
