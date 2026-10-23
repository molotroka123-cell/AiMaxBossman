"""Live proof on the owner's PC: owner writes a task to Gemma in the chat -> Gemma hands it to Claude Code / Codex via
`cli.delegate` -> the CLI edits a candidate worktree -> the report comes back into the chat (owner 23.10).

    python tools/prove_cli_delegate.py --repo C:/Users/asd/Bossman/wt-delegate-proof [--cli claude] [--agent 41]

The task is synthetic and verifiable: create docs/DELEGATE_PROOF.md holding a random code. Checks:
  1. the chat turn completes;  2. the file exists in the worktree with the exact code;
  3. a cli.delegate receipt (<data>/cli-delegations/*.json) was written after the start with rc 0 and this cli;
  4. the chat answer names the CLI's result (the code or the file). Receipt JSON under docs/audits/.
The worktree must be a candidate (never the live checkout) inside the owner's terminal roots.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from prove_chat_attachments import api, code


def _data_dir() -> Path:
    return Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--cli", default="claude", choices=("claude", "codex"))
    ap.add_argument("--agent", type=int, default=41)
    ap.add_argument("--wait", type=int, default=1800)
    ap.add_argument("--out", default="docs/audits/2026-10-23-cli-delegate")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    target = repo / "docs" / "DELEGATE_PROOF.md"
    target.unlink(missing_ok=True)
    c = code()
    started = time.time()
    tid = api("POST", "/api/chat/threads", {"title": f"PROOF cli.delegate {a.cli}"})["id"]
    text = (f"Передай задачу кодеру {a.cli} через инструмент cli.delegate (cli={a.cli}, mode=auto, repo={repo}). "
            f"Задача для него: создай файл docs/DELEGATE_PROOF.md ровно с одной строкой «delegate code {c}» и больше "
            f"ничего не меняй. Когда он закончит — коротко перескажи мне его отчёт.")
    sent = api("POST", f"/api/chat/threads/{tid}/send", {"text": text, "agent_id": a.agent}, timeout=900)
    task_id = sent.get("task_id") or (sent.get("task") or {}).get("id") or (sent.get("turn") or {}).get("task_id")
    answer, status = "", "?"
    while time.time() - started < a.wait:
        turn = next((x for x in api("GET", f"/api/chat/threads/{tid}").get("turns") or []
                     if int(x.get("task_id") or 0) == int(task_id or 0)), {})
        status = turn.get("status")
        if status in ("completed", "failed", "stopped", "cancelled"):
            answer = str(turn.get("result") or turn.get("error") or "")
            break
        time.sleep(5)
    content = target.read_text(encoding="utf-8").strip() if target.is_file() else ""
    receipts = []
    for f in sorted((_data_dir() / "cli-delegations").glob("*.json")):
        if f.stat().st_mtime >= started:
            try:
                receipts.append(json.loads(f.read_text(encoding="utf-8")))
            except ValueError:
                pass
    rec = next((r for r in receipts if r.get("cli") == a.cli), {})
    checks = {
        "turn_completed": status == "completed",
        "file_written_with_code": c in content,
        "delegation_receipt_rc0": bool(rec) and rec.get("rc") == 0 and Path(rec.get("cwd") or "") == repo,
        "answer_reports_result": c in answer or "DELEGATE_PROOF" in answer,
    }
    ok = all(checks.values())
    receipt = {"verdict": "PASS" if ok else "FAIL", "cli": a.cli, "agent_id": a.agent, "thread_id": tid,
               "task_id": task_id, "turn_status": status, "seconds": round(time.time() - started, 1),
               "code": c, "file_content": content[:200], "delegation": {k: rec.get(k) for k in
               ("id", "mode", "rc", "cost_usd", "seconds", "diff_stat")}, "answer_chars": len(answer),
               "checks": checks, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"cli-delegate-{a.cli}-{time.strftime('%Y%m%d-%H%M')}.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    for k, v in checks.items():
        print("PASS" if v else "FAIL", k)
    print("VERDICT", receipt["verdict"], "| task", task_id, status, "|", receipt["seconds"], "s")
    if not ok:
        print("answer:", answer[:800])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
