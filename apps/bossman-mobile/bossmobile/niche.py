"""Read-only status of the 28-day niche research run (``python -m niche_research``) for the phone.

The run keeps its state in ``<NICHE_RESEARCH_DIR>/niche_research.sqlite3`` (default
``%LOCALAPPDATA%\\Bossman\\niche-research``). Bossman has no endpoint for it, so the gateway opens that file strictly
read-only (SQLite URI ``mode=ro``: no write, no schema creation, no journal) and returns counters only: run day,
queue counts, spend, error count, stopped reason. Task payloads, results and error texts never leave the PC through
here. Decision record: docs/audits/2026-10-24-mobile-parity/decision-1.json (three-model vote, option A).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

RUN_DAYS = 28                      # niche_research.RUN_DAYS
DB_NAME = "niche_research.sqlite3"
PAID_DAILY_HARD_CAP = 2.0          # niche_research.config.PAID_DAILY_HARD_CAP


def _meta(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def _paid_limit(data_dir: Path) -> float:
    try:
        raw = json.loads((data_dir / "config.json").read_text(encoding="utf-8")).get("paid_usd_limit") or 0.0
        return max(0.0, min(float(raw), PAID_DAILY_HARD_CAP))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def status(data_dir: Path, now: float | None = None) -> dict:
    """Counters of the run; ``present: False`` when the run was never started on this PC."""
    now = time.time() if now is None else now
    db = Path(data_dir) / DB_NAME
    if not db.is_file():
        return {"present": False, "started": False, "reason": "исследование ниш на этом ПК не запускалось"}
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error as exc:
        return {"present": True, "readable": False, "reason": type(exc).__name__}
    try:
        start = _meta(conn, "start_ts")
        stopped = _meta(conn, "stopped")
        queue = {"pending": 0, "running": 0, "done": 0, "failed": 0}
        for st, n in conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status"):
            queue[str(st)] = int(n)
        day0 = now - (now % 86400)
        paid_total = float(conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM spend_log WHERE paid=1").fetchone()[0])
        paid_today = float(conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM spend_log WHERE paid=1 AND ts>=?",
                                        (day0,)).fetchone()[0])
        total = float(conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM spend_log").fetchone()[0])
        errors = int(conn.execute("SELECT COUNT(*) FROM error_log").fetchone()[0])
        last = conn.execute("SELECT MAX(finished_at) FROM tasks WHERE status='done'").fetchone()[0]
    except sqlite3.Error as exc:
        return {"present": True, "readable": False, "reason": type(exc).__name__}
    finally:
        conn.close()
    out = {"present": True, "readable": True, "started": isinstance(start, (int, float)), "queue": queue,
           "paid_spend_usd": round(paid_total, 6), "paid_today_usd": round(paid_today, 6),
           "total_spend_usd": round(total, 6), "paid_daily_limit_usd": _paid_limit(Path(data_dir)), "errors": errors,
           "last_done_ts": last, "run_days": RUN_DAYS,
           "stopped": ({"reason": str(stopped.get("reason", ""))[:80], "ts": stopped.get("ts")}
                       if isinstance(stopped, dict) else (bool(stopped) or None))}
    if out["started"]:
        deadline = start + RUN_DAYS * 86400
        out.update({"start_ts": start, "deadline_ts": deadline,
                    "day": 0 if now < start else min(int((now - start) // 86400) + 1, RUN_DAYS + 1),
                    "days_left": max(0.0, round((deadline - now) / 86400, 2))})
    return out
