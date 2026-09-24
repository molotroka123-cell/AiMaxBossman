"""Bossman 1.5 runtime-failure -> self-repair inbox.

This feature does not patch stable code. It captures code/harness-shaped failures
from ordinary tasks as redacted, deduplicated evidence for Bossman's own
self-improvement runner. Network, approvals, CAPTCHA and owner-input waits are
not code bugs and are deliberately excluded.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, Request

from ..plugin_security import redact_text
from . import Feature

router = APIRouter(prefix="/v15/self-repair", tags=["v1.5"])

CODE_RX = re.compile(
    r"(Traceback|AssertionError|TypeError|AttributeError|KeyError|ImportError|ModuleNotFoundError|"
    r"FileNotFoundError|OSError|SyntaxError|pytest|FAILED\s+[^\n]+::|internal error)",
    re.I,
)
EXCLUDE_RX = re.compile(
    r"(network|rate.?limit|captcha|approval|owner.?input|waiting.?for.?owner|timeout|"
    r"quota|credential|login required|stopped|cancelled)",
    re.I,
)
PATH_RX = re.compile(r"(?<![\w.-])((?:command-center|bossman-core|tools|learning|tests)/[\w./-]+\.py)")
TEST_RX = re.compile(r"((?:command-center/|bossman-core/)?tests/[\w./-]+\.py(?:::[\w\[\].-]+)?)")


def _path(svc) -> Path:
    p = Path(svc.settings.data_dir) / "v1.5" / "self-repair" / "inbox.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def classify(error: str) -> dict | None:
    clean = redact_text(str(error or ""))[:6000]
    if not clean or EXCLUDE_RX.search(clean) or not CODE_RX.search(clean):
        return None
    paths = sorted(set(PATH_RX.findall(clean)))[:20]
    tests = sorted(set(TEST_RX.findall(clean)))[:20]
    signature = hashlib.sha256(re.sub(r"\d+", "#", clean).encode()).hexdigest()[:16]
    return {"signature": signature, "error": clean, "paths": paths, "tests": tests}


def _recent(path: Path, limit: int = 100) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, limit):]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


async def _setup(svc):
    async def capture(task, run_id, error):
        finding = classify(error)
        if finding is None:
            return
        path = _path(svc)
        recent = _recent(path, 200)
        # Same signature is one repair candidate; occurrences remain observable.
        previous = next((r for r in reversed(recent) if r.get("signature") == finding["signature"]), None)
        row = {
            "schema": "bossman.v1.5.self-repair/1",
            "id": finding["signature"],
            "signature": finding["signature"],
            "status": "QUEUED",
            "task_id": task.get("id"),
            "run_id": run_id,
            "at": time.time(),
            "error": finding["error"],
            "paths": finding["paths"],
            "tests": finding["tests"],
            "occurrence": int((previous or {}).get("occurrence") or 0) + 1,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        await svc.bus.emit("v15.self_repair.queued", repair_id=row["id"],
                           task_id=task.get("id"), run_id=run_id,
                           paths=row["paths"], tests=row["tests"])
    svc.engine.add_hook("on_failure", capture, critical=False)


@router.get("/inbox")
async def inbox(request: Request, limit: int = 50):
    rows = _recent(_path(request.app.state.svc), min(max(int(limit), 1), 200))
    # Collapse repeats for the owner view.
    latest = {}
    for row in rows:
        latest[row.get("signature") or row.get("id")] = row
    return {"items": list(latest.values())[-limit:]}


FEATURE = Feature(name="v15_self_repair", router=router, setup=_setup)
