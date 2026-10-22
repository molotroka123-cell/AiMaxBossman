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

def utf8_console() -> None:
    # Shipped runners start with `-I`, which ignores PYTHONUTF8/PYTHONIOENCODING:
    # without this the first Cyrillic line dies with cp1252 on Windows.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


utf8_console()



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

MAX_OUTPUT_CHARS = 12_000
DEFAULT_TEST_TIMEOUT_SEC = 60
MAX_TEST_TIMEOUT_SEC = 180

FORBIDDEN = re.compile(
    r"(?i)(?:\bcurl\b|\bwget\b|\bInvoke-WebRequest\b|\bpip\s+install\b|\bnpm\s+install\b|"
    r"\bgit\s+(?:push|fetch|pull|clone|checkout|switch|reset|clean)\b|"
    r"\bshutdown\b|\bformat\b|\bdel\s+/[sq]\b|\brm\s+-rf\b)"
)

SHELL_META_RE = re.compile(r"[;&|`$><\r\n]")

SAFE_SYSTEM_ENV_KEYS = frozenset({
    "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
    "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
    "LANG", "LC_ALL",
})

SECRET_ENV_KEY_RE = re.compile(
    r"(?i)(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|PRIVATE|MNEMONIC|SEED|DSN|COOKIE|SESSION|WEBHOOK)"
)

ALLOWED_PYTEST_VALUE_FLAGS = frozenset({"-k", "-m", "--maxfail", "--tb"})
ALLOWED_PYTEST_BOOL_FLAGS = frozenset({
    "-q", "-v", "-vv", "-s", "-x", "--collect-only", "--disable-warnings", "--no-header", "--no-summary",
})
FORBIDDEN_PYTEST_FLAG_PREFIXES = (
    "-c", "-o", "--override-ini", "--rootdir", "--import-mode", "-p", "--confcutdir", "--basetemp",
)


def safe(root: pathlib.Path, rel: str) -> pathlib.Path:
    if "\x00" in rel:
        raise ValueError("null byte in path")
    cleaned = rel.replace("\\", "/").strip()
    if pathlib.Path(cleaned).is_absolute() or re.match(r"^[A-Za-z]:", cleaned):
        raise ValueError("path escapes worktree")
    p = (root / cleaned).resolve()
    base = root.resolve()
    if p != base and base not in p.parents:
        raise ValueError("path escapes worktree")
    return p


def scrubbed_test_env(root: pathlib.Path) -> dict[str, str]:
    """Build a minimal environment stripped of all credentials and configured for offline test execution."""
    base = root.resolve()
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        if k.upper() in SAFE_SYSTEM_ENV_KEYS and not SECRET_ENV_KEY_RE.search(k):
            env[k] = v
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(base),
        "BOSSMAN_OFFLINE_MODE_ENABLED": "1",
        "BOSSMAN_NO_NETWORK": "1",
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
    })
    return env


def validate_test_command(command: str | list[str], root: pathlib.Path) -> list[str]:
    """Validate and normalize a test command into a strict argv list (no shell)."""
    if isinstance(command, str):
        raw = command.strip()
        if not raw:
            raise ValueError("empty command")
        if SHELL_META_RE.search(raw) or FORBIDDEN.search(raw):
            raise ValueError("shell metacharacters or forbidden command")
        try:
            tokens = shlex.split(raw, posix=True)
        except ValueError as exc:
            raise ValueError(f"invalid command quoting: {exc}") from exc
    elif isinstance(command, list):
        tokens = [str(t) for t in command if str(t)]
        for tok in tokens:
            if SHELL_META_RE.search(tok) or FORBIDDEN.search(tok):
                raise ValueError("shell metacharacters or forbidden token")
    else:
        raise ValueError("unsupported command type")

    if not tokens:
        raise ValueError("empty command tokens")

    head = pathlib.Path(tokens[0]).name.lower()
    if head in ("pytest", "pytest.exe"):
        runner_module = "pytest"
        rest = tokens[1:]
    elif head in ("python", "python3", "python.exe", pathlib.Path(sys.executable).name.lower()):
        if len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] in ("pytest", "unittest", "py_compile"):
            runner_module = tokens[2]
            rest = tokens[3:]
        else:
            raise ValueError("only 'python -m pytest|unittest|py_compile' is allowed")
    else:
        raise ValueError(f"executable '{tokens[0]}' is outside test allowlist")

    validated_args: list[str] = []
    skip_next = False
    for idx, arg in enumerate(rest):
        if skip_next:
            skip_next = False
            if SHELL_META_RE.search(arg):
                raise ValueError("unsafe flag value")
            validated_args.append(arg)
            continue

        for bad_prefix in FORBIDDEN_PYTEST_FLAG_PREFIXES:
            if arg == bad_prefix or arg.startswith(bad_prefix + "="):
                raise ValueError(f"forbidden test flag: {arg}")

        if arg.startswith("-"):
            flag_name = arg.split("=", 1)[0]
            if flag_name in ALLOWED_PYTEST_BOOL_FLAGS:
                validated_args.append(arg)
            elif flag_name in ALLOWED_PYTEST_VALUE_FLAGS:
                if "=" in arg:
                    validated_args.append(arg)
                else:
                    if idx + 1 >= len(rest):
                        raise ValueError(f"missing value for flag {arg}")
                    validated_args.append(arg)
                    skip_next = True
            else:
                raise ValueError(f"flag '{arg}' is not in test allowlist")
        else:
            path_part = arg.split("::", 1)[0]
            safe_target = safe(root, path_part)
            rel_target = str(safe_target.relative_to(root.resolve())).replace("\\", "/")
            if "::" in arg:
                node_suffix = arg.split("::", 1)[1]
                if not re.fullmatch(r"[A-Za-z0-9_.\[\]\-:]+", node_suffix):
                    raise ValueError("invalid pytest node selector")
                validated_args.append(f"{rel_target}::{node_suffix}")
            else:
                validated_args.append(rel_target)

    return [sys.executable, "-I", "-m", runner_module, *validated_args]


def execute(root: pathlib.Path, name: str, args: dict[str, Any]) -> str:
    try:
        base = root.resolve()
        if name == "list_files":
            rows = []
            for p in base.rglob("*"):
                if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts:
                    rows.append(str(p.relative_to(base)).replace("\\", "/"))
            return "\n".join(sorted(rows)[:5000])
        if name == "read_file":
            return safe(base, str(args["path"])).read_text(encoding="utf-8")[:MAX_READ]
        if name == "write_file":
            p = safe(base, str(args["path"]))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(args["content"]), encoding="utf-8", newline="\n")
            return "WROTE " + str(p.relative_to(base))
        if name == "run_tests":
            raw_cmd = args.get("command", "")
            try:
                argv = validate_test_command(raw_cmd, base)
            except ValueError as vex:
                return f"REFUSED: {vex}"
            req_timeout = int(args.get("timeout") or DEFAULT_TEST_TIMEOUT_SEC)
            timeout_sec = max(1, min(req_timeout, MAX_TEST_TIMEOUT_SEC))
            env = scrubbed_test_env(base)
            proc = subprocess.run(
                argv,
                cwd=str(base),
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                env=env,
            )
            combined = (proc.stdout or "") + (proc.stderr or "")
            if len(combined) > MAX_OUTPUT_CHARS:
                combined = combined[-MAX_OUTPUT_CHARS:]
            return f"exit_code={proc.returncode}\n{combined}"
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
