#!/usr/bin/env python3
"""Export Bossman runs of chosen worker models into the distillation dataset.

Reads the Command Center database READ-ONLY (sqlite `mode=ro`): each finished
run of the given model aliases becomes one RAW_CANDIDATE record through
tools/distill_recorder.py (same schema, same secret redaction). The run's
conversation comes from `task_runs.checkpoint.messages`; the verdict is the
run status (completed != verified — `verifier_verdict` stays UNVERIFIED unless
a verifier says otherwise). Already exported run ids are skipped.

    python tools/distill_export_bossman.py --alias nemotron-ultra-free
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from distill_recorder import Recorder  # noqa: E402

DEFAULT_DB = pathlib.Path(os.environ.get("BCC_DATA_DIR") or
                          pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter") / "bcc.db"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alias", action="append", required=True, help="model alias (repeatable)")
    ap.add_argument("--model-id", default="", help="provider model id to record (default: alias)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--name", default="bossman-runs")
    ns = ap.parse_args(argv)
    rec = Recorder(ns.name)
    done: set[int] = set()
    if rec.path.exists():
        for line in rec.path.read_text(encoding="utf-8").splitlines():
            rid = (json.loads(line).get("extra") or {}).get("bossman_run_id")
            if rid is not None:
                done.add(int(rid))
    con = sqlite3.connect(f"file:{pathlib.Path(ns.db).as_posix()}?mode=ro", uri=True)
    q = ("select r.id, r.task_id, r.status, r.model_alias, r.tokens_in, r.tokens_out, r.cost_usd, r.result, "
         "r.error, r.checkpoint, r.started_at, r.finished_at, t.title from task_runs r join tasks t on t.id = r.task_id "
         f"where r.model_alias in ({','.join('?' * len(ns.alias))}) and r.status in ('completed','failed','stopped')")
    n = 0
    for row in con.execute(q, ns.alias):
        rid, tid, status, alias, tin, tout, cost, result, error, cp, st, fin, title = row
        if rid in done:
            continue
        cp = json.loads(cp) if cp else {}
        msgs = list((cp or {}).get("messages") or [])
        if msgs and msgs[-1].get("role") == "assistant" and (msgs[-1].get("content") or "") == (result or ""):
            msgs = msgs[:-1]
        rec.record(model=ns.model_id or alias, task_class="bossman_task", messages=msgs, response_text=result or "",
                   usage={"prompt_tokens": tin, "completion_tokens": tout, "cost": cost}, error=error,
                   verdict="UNVERIFIED", verifier="", curator_note=f"bossman run status={status}",
                   extra={"bossman_run_id": rid, "bossman_task_id": tid, "title": title, "alias": alias,
                          "started_at": st, "finished_at": fin, "privacy_note":
                          "prompt may contain Bossman memory context sent to the provider"})
        n += 1
    print(f"exported {n} runs -> {rec.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
