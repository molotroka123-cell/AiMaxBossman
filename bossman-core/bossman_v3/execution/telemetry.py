"""Automatic real-workload telemetry derived from the durable TaskJournal.

This is deliberately observational: benchmark recording must never change execution
truth or turn a failed benchmark write into a failed user task.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def _ts(value: str) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def journal_record(journal: Any, *, completed: bool, context: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = dict(context or {})
    steps = list(journal.steps)
    updated = [_ts(s.updated_at) for s in steps]
    updated = [x for x in updated if x is not None]
    started_at = _ts(journal.created_at)
    ended_at = max(updated) if updated else time.time()
    verified = bool(completed and steps and all(s.finished and s.signature_valid(journal.task_id) for s in steps))
    failed = any(getattr(s, "status", "") == "FAILED" for s in steps)
    status = "passed" if verified else ("failed" if failed else "blocked")
    duration = max(0.0, ended_at - started_at) if started_at is not None else 0.0
    return {
        "task_id": journal.task_id,
        "status": status,
        "verified": verified,
        "duration_s": round(duration, 6),
        "human_interventions": int(ctx.get("human_interventions", 0) or 0),
        "retries": int(ctx.get("retries", 0) or 0),
        "concurrency": max(1, int(ctx.get("concurrency", 1) or 1)),
        "peak_memory_gb": float(ctx.get("peak_memory_gb", 0.0) or 0.0),
        "oom": bool(ctx.get("oom", False)),
        "started_at": started_at,
        "ended_at": ended_at,
        "source": "task_journal",
        "plan_digest": getattr(journal, "plan_digest", ""),
    }


def append_record(journal: Any, *, completed: bool, context: dict[str, Any] | None = None) -> Path | None:
    """Append one terminal task sample. Best-effort and de-duplicated by task/plan/status."""
    ctx = dict(context or {})
    if ctx.get("disable_real_workload_telemetry"):
        return None
    root = Path(ctx.get("real_workload_telemetry_root") or os.getenv("BOSSMAN_REAL_WORKLOAD_ROOT", ".bossman-state/benchmarks"))
    path = root / "real_workloads.jsonl"
    record = journal_record(journal, completed=completed, context=ctx)
    key = (record["task_id"], record["plan_digest"], record["status"])
    try:
        root.mkdir(parents=True, exist_ok=True)
        existing: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                existing.append(item)
                if (item.get("task_id"), item.get("plan_digest"), item.get("status")) == key:
                    return path
        existing.append(record)
        fd, tmp = tempfile.mkstemp(prefix=".real-workload-", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                for item in existing:
                    stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return path
    except OSError:
        return None
