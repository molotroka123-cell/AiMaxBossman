#!/usr/bin/env python3
"""Ling coding worker for Bossman 1.5.

The model call goes through bcc.economy_orchestrator -> Bossman provider and
governance layers. The worker can only read/write inside one explicit worktree
and run bounded local commands. It has no network/pip/install/checkout/push
authority. Codex/Aster verify and decide promotion.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))

from bcc.economy_orchestrator import BossmanOpenRouter, ROLE_PROMPTS  # noqa: E402

MAX_STEPS = 30
MAX_READ = 50_000

TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List files under the bounded worktree.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a UTF-8 file in the bounded worktree.",
        "parameters": {"type": "object", "required": ["path"],
                       "properties": {"path": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "write_file", "description": "Create/replace one UTF-8 file in the bounded worktree.",
        "parameters": {"type": "object", "required": ["path", "content"],
                       "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "run_tests", "description": "Run a bounded local test/compile command in the worktree.",
        "parameters": {"type": "object", "required": ["command"],
                       "properties": {"command": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "finish", "description": "Finish after verification.",
        "parameters": {"type": "object", "required": ["status", "summary"],
                       "properties": {
                           "status": {"type": "string", "enum": ["DONE", "BLOCKED"]},
                           "summary": {"type": "string"},
                       }},
    }},
]

FORBIDDEN = re.compile(
    r"(?i)(?:\bcurl\b|\bwget\b|\bInvoke-WebRequest\b|\bpip\s+install\b|\bnpm\s+install\b|"
    r"\bgit\s+(?:push|fetch|pull|clone|checkout|switch|reset|clean)\b|"
    r"\bshutdown\b|\bformat\b|\bdel\s+/[sq]\b|\brm\s+-rf\b)"
)


def safe(root: pathlib.Path, rel: str) -> pathlib.Path:
    p = (root / rel.replace("\\", "/")).resolve()
    base = root.resolve()
    if p != base and base not in p.parents:
        raise ValueError("path escapes worktree")
    return p


def execute(root: pathlib.Path, name: str, args: dict[str, Any]) -> str:
    try:
        if name == "list_files":
            rows = []
            for p in root.rglob("*"):
                if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts:
                    rows.append(str(p.relative_to(root)).replace("\\", "/"))
            return "\n".join(sorted(rows)[:5000])
        if name == "read_file":
            return safe(root, str(args["path"])).read_text(encoding="utf-8")[:MAX_READ]
        if name == "write_file":
            p = safe(root, str(args["path"]))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(args["content"]), encoding="utf-8", newline="\n")
            return "WROTE " + str(p.relative_to(root))
        if name == "run_tests":
            command = str(args["command"])
            if FORBIDDEN.search(command):
                return "REFUSED: command outside local coding/test authority"
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONPATH": str(root)}
            proc = subprocess.run(command, cwd=root, shell=True, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=180, env=env)
            return "exit_code=" + str(proc.returncode) + "\n" + (proc.stdout + proc.stderr)[-12000:]
        return "ERROR: unknown tool"
    except subprocess.TimeoutExpired:
        return "exit_code=timeout"
    except Exception as exc:
        return "ERROR: " + type(exc).__name__ + ": " + str(exc)


async def run_agent(worktree: pathlib.Path, task: str) -> dict[str, Any]:
    gateway = BossmanOpenRouter()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": ROLE_PROMPTS["ling_coder"]},
        {"role": "user", "content": task},
    ]
    claim = None
    summary = ""
    transcript = []
    successful_tests: list[str] = []
    for step in range(1, MAX_STEPS + 1):
        res = await gateway.chat("ling_coder", messages, tools=TOOLS)
        assistant: dict[str, Any] = {"role": "assistant", "content": res.text or ""}
        if res.tool_calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": c.raw_arguments or json.dumps(c.arguments)}}
                for c in res.tool_calls
            ]
        messages.append(assistant)
        transcript.append({"step": step, "text": res.text, "calls": [c.name for c in res.tool_calls]})
        if not res.tool_calls:
            messages.append({"role": "user", "content": "Continue with tools, or call finish."})
            continue
        finished = False
        for call in res.tool_calls:
            if call.name == "finish":
                claim = str(call.arguments.get("status") or "BLOCKED")
                summary = str(call.arguments.get("summary") or "")
                result = "ok"
                finished = True
            else:
                result = execute(worktree, call.name, call.arguments)
                if call.name == "run_tests" and result.startswith("exit_code=0"):
                    successful_tests.append(str(call.arguments.get("command") or "")[:500])
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        if finished:
            if claim == "DONE" and not successful_tests:
                messages.append({"role": "user", "content":
                                 "DONE is refused until at least one relevant run_tests call exits 0. "
                                 "Run an executable verifier, then finish again."})
                finished = False
                continue
            return {"claim": claim, "summary": summary, "steps": step,
                    "transcript": transcript, "successful_tests": successful_tests,
                    "cost": gateway.ledger.rows}
    return {"claim": "BLOCKED", "summary": "step limit reached", "steps": MAX_STEPS,
            "transcript": transcript, "successful_tests": successful_tests,
            "cost": gateway.ledger.rows}


async def main_async(ns) -> int:
    worktree = pathlib.Path(ns.worktree).resolve()
    if not worktree.is_dir():
        raise SystemExit("worktree not found")
    task = pathlib.Path(ns.task_file).read_text(encoding="utf-8") if ns.task_file else ns.task
    if not task.strip():
        raise SystemExit("task is empty")
    before = subprocess.run(["git", "status", "--short"], cwd=worktree, capture_output=True, text=True).stdout
    result = await run_agent(worktree, task)
    verify = None
    if ns.verify:
        verify = execute(worktree, "run_tests", {"command": ns.verify})
    after = subprocess.run(["git", "status", "--short"], cwd=worktree, capture_output=True, text=True).stdout
    result.update({"worktree": str(worktree), "before_status": before, "after_status": after,
                   "verification": verify, "model_done_is_pass": False})
    pathlib.Path(ns.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(ns.out)
    if verify is not None and not verify.startswith("exit_code=0"):
        return 1
    return 0 if result["claim"] == "DONE" else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", required=True)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--task")
    group.add_argument("--task-file")
    ap.add_argument("--verify", default="")
    ap.add_argument("--out", required=True)
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    raise SystemExit(main())
