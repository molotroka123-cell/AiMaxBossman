"""`cli.delegate` — Gemma hands a prompt to an external coding CLI (Claude Code / Codex) and reports back.

Owner 23.10: «пишу задачу Gemma, он передаёт её Claude, тот пишет и отчёты в чат; в том числе auto и
bypass permissions для улучшения Bossman». Thin adapter, no new orchestrator:
  * argv only, never a shell string; env from rave.connectors.child_env (Bossman secrets stripped, the
    CLI's own login vars kept); cwd = a git worktree under the owner's terminal roots and NEVER the
    checkout the live backend runs from (installed stable is edited only through candidate → tests → promote);
  * modes: "auto"   = claude --permission-mode acceptEdits / codex --sandbox workspace-write,
           "bypass" = claude --permission-mode bypassPermissions / codex --dangerously-bypass-approvals-and-sandbox;
    bypass needs the explicit agent permission `cli.bypass` (effect_hook → ASK otherwise, hook is a floor);
  * owner STOP (computer/STOP) is honoured before start and polled during the run → process tree killed;
  * money: claude -p reports total_cost_usd; a daily ledger caps spend (CLI_DELEGATE_DAILY_USD, default 5);
  * receipt per call in <data>/cli-delegations/<id>.json; the chat gets the CLI's answer + `git diff --stat`.
Antigravity has no CLI on this PC (GUI only) → not a target.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from ..permissions import agent_allowed
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature
from .coding_tasks import _stop_active
from .tools_code import _within, allowed_roots

TOOL_NAME = "cli.delegate"
MAX_PROMPT_CHARS = 12000
MAX_REPORT_CHARS = 4000
DEFAULT_MINUTES = 20
DAILY_USD_ENV = "CLI_DELEGATE_DAILY_USD"
CLIS = {
    "claude": {"exe": "claude", "login_prefixes": ("ANTHROPIC_", "CLAUDE_")},
    "codex": {"exe": "codex", "login_prefixes": ("OPENAI_", "CODEX_")},
}


def _argv(cli: str, exe: str, prompt: str, mode: str, model: str | None) -> list[str]:
    if cli == "claude":
        argv = [exe, "-p", prompt, "--output-format", "json",
                "--permission-mode", "bypassPermissions" if mode == "bypass" else "acceptEdits"]
        if model:
            argv += ["--model", model]
        return argv
    argv = [exe, "exec", "--json"]
    argv += ["--dangerously-bypass-approvals-and-sandbox"] if mode == "bypass" else ["--sandbox", "workspace-write"]
    if model:
        argv += ["-m", model]
    return argv + [prompt]


def _live_checkout() -> Path:
    """The checkout the running backend imports bcc from: never a delegation target."""
    return Path(__file__).resolve().parents[2]


def _ledger_dir(svc) -> Path:
    base = Path(getattr(getattr(svc, "settings", None), "data_dir", "") or os.environ.get("BCC_DATA_DIR") or ".")
    d = base / "cli-delegations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _spent_today(d: Path) -> float:
    p = d / f"ledger-{time.strftime('%Y-%m-%d')}.jsonl"
    total = 0.0
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                total += float(json.loads(line).get("cost_usd") or 0.0)
            except (ValueError, TypeError):
                continue
    return total


def _parse_claude(stdout: str) -> tuple[str, float | None]:
    try:
        doc = json.loads(stdout)
    except ValueError:
        return stdout, None
    if isinstance(doc, dict):
        return str(doc.get("result") or ""), doc.get("total_cost_usd")
    return stdout, None


def _parse_codex(stdout: str) -> tuple[str, float | None]:
    last = ""
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("msg") if isinstance(ev, dict) else None
        if isinstance(msg, dict) and msg.get("type") in ("agent_message", "task_complete"):
            last = str(msg.get("message") or msg.get("last_agent_message") or last)
    return last or stdout, None


def run_cli(cli: str, prompt: str, cwd: Path, *, mode: str = "auto", minutes: int = DEFAULT_MINUTES,
            model: str | None = None, stop=lambda: False, exe: str | None = None,
            env: dict[str, str] | None = None) -> dict:
    """Pure runner (also used by tests with a fake exe): returns rc, answer, cost, seconds, stdout_tail."""
    spec = CLIS[cli]
    exe = exe or shutil.which(spec["exe"])
    if not exe:
        return {"rc": 127, "answer": f"{spec['exe']} not installed", "cost_usd": None, "seconds": 0.0,
                "stdout_tail": "", "stderr_tail": "", "stopped": False}
    if env is None:
        from ..rave.connectors import child_env
        env = child_env(keep_prefixes=spec["login_prefixes"])
    argv = _argv(cli, exe, prompt, mode, model)
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    except OSError as exc:  # binary vanished or not executable: a clean 127, never a traceback into the chat
        return {"rc": 127, "answer": f"{exe}: {type(exc).__name__}", "cost_usd": None, "seconds": 0.0,
                "stdout_tail": "", "stderr_tail": "", "stopped": False}
    deadline = t0 + minutes * 60
    stopped = False
    while True:
        try:
            out, err = proc.communicate(timeout=2)
            break
        except subprocess.TimeoutExpired:
            if stop() or time.monotonic() > deadline:
                stopped = True
                _kill_tree(proc.pid)
                out, err = proc.communicate(timeout=10)
                break
    seconds = round(time.monotonic() - t0, 1)
    answer, cost = (_parse_claude if cli == "claude" else _parse_codex)(out or "")
    if stopped:
        answer = ("STOP владельца" if stop() else f"таймаут {minutes} мин") + " — процесс остановлен. " + answer
    return {"rc": proc.returncode, "answer": answer, "cost_usd": cost, "seconds": seconds,
            "stdout_tail": (out or "")[-1500:], "stderr_tail": (err or "")[-800:], "stopped": stopped}


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:
        try:
            os.killpg(pid, 9)
        except OSError:
            pass


def _diff_stat(cwd: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(cwd), "diff", "--stat"], capture_output=True, text=True,
                              timeout=30, check=False).stdout[-1200:]
    except (OSError, subprocess.SubprocessError):
        return ""


def _effect_hook(args: dict):
    """bypass is never AUTO by default: the explicit `cli.bypass` grant (checked in the handler) decides."""
    if str(args.get("mode") or "auto") == "bypass":
        return ("ask", "cli.delegate: режим bypass — нужно право cli.bypass или одобрение владельца")
    return None


async def _t_delegate(args: dict, ctx: ToolContext) -> ToolResult:
    cli = str(args.get("cli") or "claude").lower()
    if cli not in CLIS:
        return ToolResult(content=f"{TOOL_NAME}: cli должен быть claude или codex", error=True)
    prompt = str(args.get("prompt") or "").strip()
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        return ToolResult(content=f"{TOOL_NAME}: prompt пустой или длиннее {MAX_PROMPT_CHARS}", error=True)
    mode = "bypass" if str(args.get("mode") or "auto") == "bypass" else "auto"
    if mode == "bypass" and not agent_allowed(ctx.agent, "cli.bypass") and ctx.approval_id is None:
        return ToolResult(content=f"{TOOL_NAME}: bypass без права cli.bypass и без одобрения — отказ", error=True)
    svc = ctx.svc
    try:
        cwd = Path(str(args.get("repo") or ctx.workspace or "")).expanduser().resolve(strict=True)
    except (OSError, ValueError):
        return ToolResult(content=f"{TOOL_NAME}: repo не найден", error=True)
    roots = await allowed_roots(svc)
    if not any(_within(cwd, [r]) for r in roots) or not (cwd / ".git").exists():
        return ToolResult(content=f"{TOOL_NAME}: repo вне разрешённых корней или не git: {cwd}", error=True)
    live = _live_checkout()
    if cwd == live or _within(cwd, [live]) or _within(live, [cwd]):
        return ToolResult(content=f"{TOOL_NAME}: это живой чекаут Bossman — правки только в candidate-worktree", error=True)
    data_dir = getattr(getattr(svc, "settings", None), "data_dir", None)
    if _stop_active(data_dir, cwd):
        return ToolResult(content=f"{TOOL_NAME}: активен STOP владельца", error=True)
    ledger = _ledger_dir(svc)
    cap = float(os.environ.get(DAILY_USD_ENV, "5") or 5)
    spent = _spent_today(ledger)
    if spent >= cap:
        return ToolResult(content=f"{TOOL_NAME}: дневной лимит ${cap:.2f} исчерпан (${spent:.2f})", error=True)
    minutes = max(1, min(120, int(args.get("minutes") or DEFAULT_MINUTES)))
    res = run_cli(cli, prompt, cwd, mode=mode, minutes=minutes, model=args.get("model") or None,
                  stop=lambda: _stop_active(data_dir, cwd))
    rec_id = uuid.uuid4().hex[:12]
    diff = _diff_stat(cwd)
    receipt = {"id": rec_id, "cli": cli, "mode": mode, "cwd": str(cwd),
               "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "prompt_chars": len(prompt),
               "rc": res["rc"], "cost_usd": res["cost_usd"], "seconds": res["seconds"],
               "stopped": res.get("stopped", False), "diff_stat": diff, "stdout_tail": res["stdout_tail"],
               "stderr_tail": res.get("stderr_tail", ""), "agent_id": (ctx.agent or {}).get("id"),
               "task_id": (ctx.task or {}).get("id"), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (ledger / f"{rec_id}.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    with (ledger / f"ledger-{time.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": rec_id, "cli": cli, "cost_usd": res["cost_usd"], "at": receipt["at"]}) + "\n")
    head = f"🛠 {cli} ({mode}) в {cwd.name}: rc={res['rc']}, {res['seconds']} с" + (f", ${res['cost_usd']:.3f}" if res["cost_usd"] else "")
    body = (res["answer"] or "")[:MAX_REPORT_CHARS]
    content = f"{head}\n{body}" + (f"\n\nИзменения (git diff --stat):\n{diff}" if diff.strip() else "\n\nИзменений в файлах нет.")
    return ToolResult(content=content, one_line=head, error=res["rc"] not in (0, None), external=True,
                      data={"receipt_id": rec_id, "cost_usd": res["cost_usd"], "rc": res["rc"]})


SPECS = [
    ToolSpec(
        name=TOOL_NAME,
        description=("Передать задачу внешнему кодеру-CLI на этом ПК (claude = Claude Code, codex = OpenAI Codex) "
                     "и вернуть его ответ в чат. prompt — самодостаточное задание по улучшению Bossman; repo — "
                     "candidate-worktree (не живой чекаут); mode auto = правки файлов без вопросов, bypass = без "
                     "ограничений (нужно право cli.bypass). STOP владельца останавливает процесс. "
                     "Hand a task to an external coding CLI (Claude Code / Codex) and report its answer."),
        handler=_t_delegate,
        input_schema={"cli": {"type": "string", "enum": ["claude", "codex"]},
                      "prompt": {"type": "string", "description": f"задание, до {MAX_PROMPT_CHARS} знаков"},
                      "repo": {"type": "string", "description": "путь к candidate-worktree (git)"},
                      "mode": {"type": "string", "enum": ["auto", "bypass"]},
                      "minutes": {"type": "integer", "description": "лимит времени, 1..120"},
                      "model": {"type": "string", "description": "необязательно: модель CLI"}},
        required=["prompt", "repo"],
        category="exec", permission="terminal.run", source="builtin", default_effect="auto",
        timeout_seconds=7200.0, idempotent=False, external_output=True, effect_hook=_effect_hook, hook_is_floor=True,
    ),
]


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_cli", router=None, setup=_setup)
