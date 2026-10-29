"""Speed of an agent's runs from NUMBERS ONLY (owner 29.10: «замеряй скорость но не читай что мы пишем»).

    python tools/agent_speed.py --agent 51 [--since-hours 6]

Reads bcc.db read-only and selects only numeric/time columns (run id, task id, status, timestamps, tokens, cost,
model alias) — never prompts, results, checkpoints or chat text. Prints per-run seconds, output tokens per second and
cost, plus totals.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agent", type=int, default=51)
    ap.add_argument("--since-hours", type=float, default=6.0)
    a = ap.parse_args(argv)
    data = Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")
    db = sqlite3.connect(f"file:{data / 'bcc.db'}?mode=ro", uri=True)
    since = (datetime.now(timezone.utc) - timedelta(hours=a.since_hours)).replace(tzinfo=None).isoformat(sep=" ")
    rows = db.execute(
        "SELECT r.id, r.task_id, r.status, r.started_at, r.finished_at, r.tokens_in, r.tokens_out, r.cost_usd, r.model_alias "
        "FROM task_runs r JOIN tasks t ON t.id = r.task_id WHERE t.agent_id = ? AND r.started_at >= ? ORDER BY r.id",
        (a.agent, since)).fetchall()
    db.close()
    out, tot_s, tot_out, tot_cost = [], 0.0, 0, 0.0
    for rid, tid, st, s0, s1, tin, tout, cost, alias in rows:
        d0, d1 = _dt(s0), _dt(s1)
        secs = (d1 - d0).total_seconds() if d0 and d1 else None
        out.append({"run": rid, "task": tid, "status": st, "seconds": round(secs, 1) if secs else None,
                    "tokens_in": tin, "tokens_out": tout, "out_tok_per_s": round((tout or 0) / secs, 1) if secs else None,
                    "cost_usd": round(cost or 0, 6), "model": alias})
        if secs:
            tot_s += secs
        tot_out += tout or 0
        tot_cost += cost or 0
    done = [r for r in out if r["seconds"]]
    summary = {"agent": a.agent, "runs": len(out), "median_seconds": sorted(r["seconds"] for r in done)[len(done) // 2] if done else None,
               "avg_out_tok_per_s": round(tot_out / tot_s, 1) if tot_s else None, "total_cost_usd": round(tot_cost, 6)}
    print(json.dumps({"summary": summary, "runs": out}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
