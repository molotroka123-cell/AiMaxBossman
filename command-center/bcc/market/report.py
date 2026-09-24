"""End-of-run report (spec §16) from the ledger — numbers are counted, never typed.

    python -m bcc.market.report --since 2026-09-24T10:31:37Z [--until ...] \
        --calibration <calibration-report.json> --impl-sha <sha> --out <md>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from .ledger import default_root


def window_stats(db: Path, since: str, until: str | None) -> dict[str, Any]:
    con = sqlite3.connect(db)
    try:
        q = "FROM observations WHERE captured_at_utc >= ?" + (" AND captured_at_utc <= ?" if until else "")
        args = (since, until) if until else (since,)
        by = dict(con.execute(f"SELECT status, COUNT(*) {q} GROUP BY status", args).fetchall())
        total = sum(by.values())
        live = con.execute(f"SELECT COUNT(*) {q} AND stream_state='LIVE'", args).fetchone()[0]
        oi = con.execute(f"SELECT COUNT(*) {q} AND status='VERIFIED' AND oi_value IS NOT NULL", args).fetchone()[0]
        cvd = con.execute(f"SELECT COUNT(*) {q} AND status='VERIFIED' AND cvd_value IS NOT NULL", args).fetchone()[0]
        price = con.execute(f"SELECT COUNT(*) {q} AND status='VERIFIED' AND price IS NOT NULL", args).fetchone()[0]
        first, last = con.execute(f"SELECT MIN(captured_at_utc), MAX(captured_at_utc) {q}", args).fetchone()
        dup_frames = con.execute(f"SELECT COUNT(*) FROM (SELECT frame_sha256 {q} AND frame_sha256 IS NOT NULL "
                                 "GROUP BY frame_sha256 HAVING COUNT(*) > 1)", args).fetchone()[0]
        extractors = dict(con.execute(f"SELECT extractor, COUNT(*) {q} GROUP BY extractor", args).fetchall())
        stale_with_numbers = con.execute(f"SELECT COUNT(*) {q} AND status IN ('STALE_FRAME','UNREADABLE') AND "
                                         "(oi_value IS NOT NULL OR cvd_value IS NOT NULL OR price IS NOT NULL)",
                                         args).fetchone()[0]
    finally:
        con.close()
    rate = lambda n, d: round(n / d, 4) if d else None       # noqa: E731
    return {"attempted": total, "by_status": by, "live_attempts": live, "first": first, "last": last,
            "verified": by.get("VERIFIED", 0), "verified_rate": rate(by.get("VERIFIED", 0), total),
            "oi_coverage_live": rate(oi, live), "cvd_coverage_live": rate(cvd, live),
            "price_coverage_live": rate(price, live), "duplicate_frames": dup_frames,
            "stale_or_unreadable_rows_with_numbers": stale_with_numbers, "extractors": extractors}


def render(stats: dict[str, Any], *, calibration: dict | None, meta: dict[str, Any]) -> str:
    b = stats["by_status"]
    cal = calibration or {}
    lines = [
        "# Twitch OI/CVD collector — owner run 2026-09-24",
        "",
        "Status: **DATA_COLLECTION** (not TRADING_MODEL_READY). No trade/order capability exists in the collector.",
        "",
        "| Field | Value |", "|---|---|",
        f"| Source URL (owner) | {meta.get('owner_url')} |",
        f"| Requested / resolved URL | {meta.get('requested_url')} / {meta.get('resolved_url')} |",
        f"| Implementation SHA | `{meta.get('impl_sha')}` (branch feat/jev-twitch-collector-20260924) |",
        f"| Bossman build SHA | {meta.get('bossman_build')} |",
        f"| Jev mode | {meta.get('jev_mode')} |",
        f"| Extractor | {', '.join(f'{k} ({v})' for k, v in stats['extractors'].items())} |",
        f"| Collection window (UTC) | {stats['first']} → {stats['last']} |",
        f"| Attempted | {stats['attempted']} |",
        f"| VERIFIED | {b.get('VERIFIED', 0)} ({stats['verified_rate']}) |",
        f"| LOW_CONFIDENCE | {b.get('LOW_CONFIDENCE', 0)} |",
        f"| UNREADABLE | {b.get('UNREADABLE', 0)} |",
        f"| STALE_FRAME | {b.get('STALE_FRAME', 0)} |",
        f"| PLAYER_ERROR / OFFLINE / LOGIN / AMBIGUOUS | {b.get('PLAYER_ERROR', 0)} / {b.get('STREAM_OFFLINE', 0)} / "
        f"{b.get('LOGIN_REQUIRED', 0)} / {b.get('AMBIGUOUS_SYMBOL', 0) + b.get('AMBIGUOUS_UNIT', 0)} |",
        f"| OI coverage (VERIFIED OI / live attempts) | {stats['oi_coverage_live']} |",
        f"| CVD coverage | {stats['cvd_coverage_live']} |",
        f"| Price coverage | {stats['price_coverage_live']} |",
        f"| Duplicate frames recorded | {stats['duplicate_frames']} |",
        f"| Stale/unreadable rows carrying a number | {stats['stale_or_unreadable_rows_with_numbers']} |",
        f"| Manual calibration | {cal.get('set')}: OI/CVD {cal.get('oi_cvd_accuracy_over_visible')} of visible, "
        f"all fields {cal.get('accuracy_over_all_visible')}, wrong recorded {cal.get('wrong_recorded')} |",
        f"| Restart / STOP | {meta.get('restart_stop')} |",
        f"| Storage | {meta.get('storage')} |",
        "",
        "## Blockers / limits", "",
        *[f"- {x}" for x in meta.get("blockers", [])],
        "", "## Next action", "", f"- {meta.get('next_action')}", "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bcc.market.report")
    ap.add_argument("--root", default=None)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=None)
    ap.add_argument("--calibration", default=None)
    ap.add_argument("--meta", required=True, help="JSON file with the non-counted fields")
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    root = Path(ns.root) if ns.root else default_root()
    stats = window_stats(root / "market-observations.sqlite", ns.since, ns.until)
    cal = json.loads(Path(ns.calibration).read_text(encoding="utf-8")) if ns.calibration else None
    meta = json.loads(Path(ns.meta).read_text(encoding="utf-8"))
    Path(ns.out).write_text(render(stats, calibration=cal, meta=meta), encoding="utf-8")
    Path(ns.out).with_suffix(".json").write_text(json.dumps({"stats": stats, "meta": meta}, ensure_ascii=False,
                                                            indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
