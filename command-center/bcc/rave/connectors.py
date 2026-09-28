"""Rave connectors: what actually runs as one agent, inside its isolated workspace.

One interface for all of them (`Connector.run(ctx) -> Outcome`):

* mock   — deterministic scripted agent (tests/demos; never presented as a model);
* local  — Bossman's own local tool loop (bossman.apprentice.local_sidecar) as a
           child process against an OpenAI-compatible endpoint (Ollama default);
* claude — the official Claude Code CLI, headless `claude -p`;
* codex  — the official Codex CLI, `codex exec`.

Both CLIs run under the owner's SUBSCRIPTION login (claude.ai / ChatGPT plan),
done by the owner in the official flow; the paid API-key path is off by default.

Connectors never touch credentials: the CLI connectors run each tool's own
status command and keep only logged-in / auth method / plan. The prompt goes on
stdin, never through a shell command line.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .spec import AgentSpec, SpecError, float_param, int_param

DEFAULT_LOCAL_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_LOCAL_MODEL = "bossman-fast-qwen36-35b-a3b-q5:latest"
CLAUDE_TOOLS = "Read,Edit,Write,Glob,Grep"
CLAUDE_DENIED = "Bash,WebFetch,WebSearch"
_SECRETISH = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|CREDENTIAL)", re.I)


class Blocked(Exception):
    """The agent can not run now (missing opt-in, runtime, login…); others continue."""

    def __init__(self, reason: str, **extra: Any):
        super().__init__(reason)
        self.reason = reason
        self.extra = extra


@dataclass
class Outcome:
    answer: str
    meta: dict = field(default_factory=dict)


@dataclass
class ProcResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool


class Ctx(Protocol):
    name: str
    prompt: str
    workspace: Path
    agent_dir: Path
    allow: list[str]

    async def checkpoint(self) -> None: ...
    def journal_state(self, step: int) -> str | None: ...
    async def begin_step(self, step: int, total: int, label: str) -> None: ...
    async def end_step(self, step: int, note: str = "") -> None: ...
    def exec_log(self, line: str) -> None: ...
    async def run_process(self, argv: list[str], *, stdin: bytes, timeout: float,
                          env: dict[str, str] | None = None) -> ProcResult: ...


def child_env(keep_prefixes: tuple[str, ...] = ()) -> dict[str, str]:
    """The owner's environment minus Bossman/other secrets. Variables the tool
    itself reads for its own login (keep_prefixes) stay: that is the owner's
    choice of how the tool authenticates, not ours."""
    env = {}
    for k, v in os.environ.items():
        if k.upper().startswith("BCC_") or k.upper().startswith("BOSSMAN_"):
            continue
        if _SECRETISH.search(k) and not k.upper().startswith(keep_prefixes):
            continue
        env[k] = v
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class Connector:
    kind = "base"
    idempotent = False           # can an interrupted step be re-checked instead of re-run?
    optin: str | None = None     # needs a one-time owner opt-in approval

    def __init__(self, spec: AgentSpec):
        self.spec = spec

    def describe(self) -> dict:
        return {"provider": self.kind, "model": self.spec.model}

    async def preflight(self, ctx: Ctx) -> None:
        return None

    async def run(self, ctx: Ctx) -> Outcome:
        raise NotImplementedError


# ------------------------------------------------------------------ mock


class MockConnector(Connector):
    """Scripted agent. Params: steps (4), delay seconds per step (1.5),
    file (rave/<name>.md), crash_at (step number that raises)."""

    kind = "mock"
    idempotent = True

    def __init__(self, spec: AgentSpec):
        super().__init__(spec)
        self.steps = int_param(spec, "steps", 4, 1, 50)
        self.delay = float_param(spec, "delay", 1.5, 0.0, 60.0)
        self.file = (spec.params.get("file") or f"rave/{spec.name}.md").replace("\\", "/").lstrip("/")
        if ".." in self.file.split("/") or self.file.split("/")[0] == ".git" or re.match(r"^[A-Za-z]:", self.file):
            raise SpecError(f"{spec.name}: file должен быть путём внутри проекта")
        self.crash_at = int_param(spec, "crash_at", 0, 0, 50)

    def describe(self) -> dict:
        return {"provider": "mock (scripted, not a model)", "model": "-", "auth": "none"}

    def content(self, prompt: str, upto: int) -> str:
        lines = [f"# {self.spec.name}", f"prompt: {prompt.strip()[:200]}", ""]
        lines += [f"- step {i}/{self.steps} by {self.spec.name}" for i in range(1, upto + 1)]
        return "\n".join(lines) + "\n"

    async def run(self, ctx: Ctx) -> Outcome:
        target = ctx.workspace / self.file
        for k in range(1, self.steps + 1):
            await ctx.checkpoint()
            state = ctx.journal_state(k)
            if state == "done":
                continue
            await ctx.begin_step(k, self.steps, f"mock step {k}")
            if self.crash_at == k:
                raise RuntimeError(f"mock agent {self.spec.name} crashed at step {k} (crash_at={k})")
            want = self.content(ctx.prompt, k)
            if state == "started" and target.is_file() and target.read_text(encoding="utf-8") == want:
                ctx.exec_log(f"step {k} reconciled (effect already present, not re-executed)")
            else:
                await asyncio.sleep(self.delay)          # "thinking"; STOP cancels here
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(want, encoding="utf-8", newline="\n")
                ctx.exec_log(f"step {k} executed")
            await ctx.end_step(k)
        return Outcome(f"mock agent {self.spec.name}: {self.steps} steps done, wrote {self.file}",
                       {"file": self.file})


# ------------------------------------------------------------------ local sidecar


def _core_path() -> str | None:
    spec = importlib.util.find_spec("bossman")
    if spec is None or not spec.submodule_search_locations:
        return None
    return str(Path(list(spec.submodule_search_locations)[0]).parent)


class LocalConnector(Connector):
    """Bossman's local coding loop in a child process. Params: endpoint,
    max_steps (20), timeout seconds (900)."""

    kind = "local"

    def __init__(self, spec: AgentSpec, *, endpoint: str | None = None, model: str | None = None):
        super().__init__(spec)
        self.endpoint = spec.params.get("endpoint") or endpoint or os.environ.get(
            "BOSSMAN_RAVE_LOCAL_ENDPOINT") or DEFAULT_LOCAL_ENDPOINT
        self.model = spec.model or model or DEFAULT_LOCAL_MODEL
        self.max_steps = int_param(spec, "max_steps", 20, 1, 60)
        self.timeout = int_param(spec, "timeout", 900, 30, 7200)

    def describe(self) -> dict:
        return {"provider": f"local sidecar · {self.endpoint}", "model": self.model, "auth": "local (no login)"}

    async def preflight(self, ctx: Ctx) -> None:
        if _core_path() is None:
            raise Blocked("рантайм local_sidecar (bossman-core) не найден рядом с Command Center")
        from ..providers import is_local_url
        if not is_local_url(self.endpoint):
            raise Blocked(f"local-агент ходит только в локальный endpoint, а не {self.endpoint}")

    async def run(self, ctx: Ctx) -> Outcome:
        await ctx.checkpoint()
        await ctx.begin_step(1, 1, f"local sidecar · {self.model}")
        env = child_env()
        core = _core_path() or ""
        env["PYTHONPATH"] = os.pathsep.join(p for p in (core, env.get("PYTHONPATH", "")) if p)
        argv = [sys.executable, "-m", "bossman.apprentice.local_sidecar", "--endpoint", self.endpoint,
                "--model", self.model, "--max-steps", str(self.max_steps)]
        req = {"schema": "bossman.openhands.v1", "instruction": ctx.prompt, "workspace": str(ctx.workspace),
               "allowed_paths": list(ctx.allow), "protected_paths": [], "timeout_seconds": self.timeout}
        res = await ctx.run_process(argv, stdin=json.dumps(req).encode("utf-8"),
                                    timeout=self.timeout + 60, env=env)
        ctx.exec_log(f"step 1 executed (sidecar exit={res.returncode}, timed_out={res.timed_out})")
        if res.timed_out:
            raise RuntimeError(f"local sidecar timed out after {self.timeout}s")
        line = (res.stdout.decode("utf-8", "replace").strip().splitlines() or [""])[-1]
        try:
            data = json.loads(line)
        except ValueError:
            tail = res.stderr.decode("utf-8", "replace").strip()[-400:]
            raise RuntimeError(f"local sidecar returned no JSON (exit {res.returncode}): {tail}") from None
        meta = {k: data.get(k) for k in ("status", "stop_reason", "steps", "tool_calls_total", "tests",
                                         "elapsed_seconds", "model", "model_kind", "error_type")}
        await ctx.end_step(1, str(data.get("status")))
        if data.get("status") != "completed":
            raise RuntimeError(f"local sidecar: {data.get('status')} "
                               f"({data.get('stop_reason') or data.get('error_type') or 'no reason'}): "
                               f"{str(data.get('summary') or '')[:300]}")
        return Outcome(str(data.get("summary") or "(no summary)"), meta)


# ------------------------------------------------------------------ official CLIs


def resolve_cli(name: str) -> list[str] | None:
    """argv prefix of an official CLI. BOSSMAN_RAVE_<NAME>_CMD (JSON list) overrides
    it — used by tests with a stub that has the same interface."""
    override = os.environ.get(f"BOSSMAN_RAVE_{name.upper()}_CMD")
    if override:
        try:
            argv = json.loads(override)
            if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv):
                return argv
        except ValueError:
            pass
        return None
    found = shutil.which(name)
    if not found:
        return None
    path = Path(found)
    if path.suffix.lower() in (".cmd", ".bat"):
        # npm shim: run the real binary, never a batch file (cmd.exe argument parsing).
        exe = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if name == "claude" and exe.is_file():
            return [str(exe)]
        return None
    return [str(path)]


#: API-key auth for the CLI connectors is OFF by default (owner rule: the rave
#: drives the CLIs through the owner's SUBSCRIPTION login). Only an explicit
#: server setting plus the agent param `auth=api_key` turns it on, and the
#: status then says "API key (paid per token)".
API_KEY_OPT_ENV = "BOSSMAN_RAVE_ALLOW_API_KEY"
_CLAUDE_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_CODEX_KEY_VARS = ("OPENAI_API_KEY", "CODEX_API_KEY")
CLAUDE_LOGIN_STEP = ("владелец входит сам в обычном терминале: `claude auth login --claudeai` (браузер, аккаунт "
                     "Claude с подпиской Pro/Max); проверка: `claude auth status --text`")
CODEX_LOGIN_STEP = ("владелец входит сам в обычном терминале: `codex login` → «Sign in with ChatGPT» "
                    "(браузер, план Plus/Pro/Business); проверка: `codex login status`")
CLAUDE_SOURCES = ("https://code.claude.com/docs/en/legal-and-compliance",
                  "https://code.claude.com/docs/en/headless")
CODEX_SOURCES = ("https://learn.chatgpt.com/docs/pricing", "https://learn.chatgpt.com/docs/non-interactive-mode",
                 "https://learn.chatgpt.com/docs/auth")


def api_key_allowed(spec: AgentSpec) -> bool:
    return spec.params.get("auth") == "api_key" and os.environ.get(API_KEY_OPT_ENV, "").strip() in ("1", "true", "yes")


def cli_env(keep: tuple[str, ...], key_vars: tuple[str, ...], *, api_key: bool) -> dict[str, str]:
    """Child env for an official CLI: its own login variables stay, and the
    paid API-key variables are removed unless the API-key path is enabled —
    so a stray ANTHROPIC_API_KEY/OPENAI_API_KEY never silently switches the
    agent from the subscription to per-token billing."""
    env = child_env(keep)
    if not api_key:
        for k in key_vars:
            env.pop(k, None)
    return env


async def _status_proc(argv: list[str], env: dict[str, str], timeout: float = 45) -> tuple[int | None, str]:
    from bossman.apprentice.proc_tree import run_tree  # noqa: WPS433 (bossman-core)
    import subprocess
    res = await asyncio.to_thread(run_tree, argv, timeout=timeout, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    return res.returncode, (res.stdout or b"").decode("utf-8", "replace")


async def claude_login(*, api_key: bool = False) -> dict:
    """`claude auth status --json` (the CLI's own status command), reduced to
    non-identifying fields: never e-mail, org or token data."""
    cmd = resolve_cli("claude")
    if not cmd:
        return {"installed": False, "logged_in": False, "reason": "claude CLI не найден",
                "login_step": "установите Claude Code: https://code.claude.com/docs/en/setup"}
    code, text = await _status_proc([*cmd, "auth", "status", "--json"],
                                    cli_env(("CLAUDE", "ANTHROPIC"), _CLAUDE_KEY_VARS, api_key=api_key))
    try:
        data = json.loads(text[text.find("{"):]) if "{" in text else {}
    except ValueError:
        data = {}
    method = data.get("authMethod")
    subscription = method == "claude.ai"
    return {"installed": True, "logged_in": bool(data.get("loggedIn")), "auth_method": method,
            "subscription": subscription, "plan": data.get("subscriptionType"),
            "auth": "subscription (claude login)" if subscription else
                    ("API key (paid per token)" if data.get("loggedIn") else "not logged in"),
            "login_step": CLAUDE_LOGIN_STEP, "exit_code": code}


async def codex_login(*, api_key: bool = False) -> dict:
    cmd = resolve_cli("codex")
    if not cmd:
        return {"installed": False, "logged_in": False, "reason": "codex CLI не найден",
                "login_step": "установите Codex CLI: https://learn.chatgpt.com/docs/codex/cli"}
    code, text = await _status_proc([*cmd, "login", "status"],
                                    cli_env(("CODEX", "OPENAI"), _CODEX_KEY_VARS, api_key=api_key))
    low = text.lower()
    method = "chatgpt" if "chatgpt" in low else "api_key" if "api key" in low else None
    return {"installed": True, "logged_in": code == 0 and method is not None, "auth_method": method,
            "subscription": method == "chatgpt",
            "auth": "subscription (codex login)" if method == "chatgpt" else
                    ("API key (paid per token)" if method == "api_key" else "not logged in"),
            "login_step": CODEX_LOGIN_STEP, "exit_code": code}


def _check_login(tool: str, login: dict, api_key: bool) -> None:
    if not login.get("installed"):
        raise Blocked(f"{tool} CLI не найден. {login.get('login_step')}", login=login)
    if not login.get("logged_in"):
        raise Blocked(f"{tool}: не выполнен вход. Bossman вход не запускает — {login['login_step']}; "
                      f"затем `bossman rave resume <id> --agent <имя>`", login=login)
    if not login.get("subscription") and not api_key:
        raise Blocked(f"{tool}: вход не по подписке ({login.get('auth')}). Рейв работает через ПОДПИСКУ — "
                      f"{login['login_step']}. Путь по API-ключу выключен по умолчанию "
                      f"({API_KEY_OPT_ENV}=1 и параметр агента auth=api_key).", login=login)


class ClaudeConnector(Connector):
    """`claude -p` in the agent workspace under the owner's Claude subscription:
    edits only (no Bash, no web), JSON result."""

    kind = "claude"
    optin = "claude"

    def __init__(self, spec: AgentSpec):
        super().__init__(spec)
        self.timeout = int_param(spec, "timeout", 900, 30, 7200)
        self.api_key = api_key_allowed(spec)

    def describe(self) -> dict:
        return {"provider": "Claude Code CLI", "model": self.spec.model or "default",
                "auth": "API key (paid per token)" if self.api_key else "subscription (claude login)"}

    async def preflight(self, ctx: Ctx) -> None:
        login = await claude_login(api_key=self.api_key)
        _check_login("claude", login, self.api_key)
        ctx.exec_log(f"claude login ok: {login.get('auth')} plan={login.get('plan')}")

    async def run(self, ctx: Ctx) -> Outcome:
        await ctx.checkpoint()
        await ctx.begin_step(1, 1, "claude -p")
        argv = [*(resolve_cli("claude") or []), "-p", "--output-format", "json",
                "--permission-mode", "acceptEdits", "--allowedTools", CLAUDE_TOOLS,
                "--disallowedTools", CLAUDE_DENIED, "--setting-sources", "project",
                "--no-session-persistence", "--permission-prompts", "none"]
        if self.spec.model:
            argv += ["--model", self.spec.model]
        res = await ctx.run_process(argv, stdin=ctx.prompt.encode("utf-8"), timeout=self.timeout,
                                    env=cli_env(("CLAUDE", "ANTHROPIC"), _CLAUDE_KEY_VARS, api_key=self.api_key))
        ctx.exec_log(f"step 1 executed (claude exit={res.returncode}, timed_out={res.timed_out})")
        if res.timed_out:
            raise RuntimeError(f"claude -p timed out after {self.timeout}s")
        text = res.stdout.decode("utf-8", "replace").strip()
        try:
            data = json.loads(text[text.find("{"):]) if "{" in text else {}
        except ValueError:
            data = {}
        await ctx.end_step(1)
        if not data or data.get("is_error") or res.returncode:
            raise RuntimeError(f"claude -p failed (exit {res.returncode}): "
                               f"{str(data.get('result') or text or res.stderr.decode('utf-8', 'replace'))[:400]}")
        meta = {k: data.get(k) for k in ("subtype", "num_turns", "duration_ms", "stop_reason")}
        meta["permission_denials"] = len(data.get("permission_denials") or [])
        return Outcome(str(data.get("result") or ""), meta)


class CodexConnector(Connector):
    """`codex exec` in the agent workspace (workspace-write sandbox) under the
    owner's ChatGPT subscription login."""

    kind = "codex"
    optin = "codex"

    def __init__(self, spec: AgentSpec):
        super().__init__(spec)
        self.timeout = int_param(spec, "timeout", 900, 30, 7200)
        self.api_key = api_key_allowed(spec)

    def describe(self) -> dict:
        return {"provider": "Codex CLI", "model": self.spec.model or "default",
                "auth": "API key (paid per token)" if self.api_key else "subscription (codex login)"}

    async def preflight(self, ctx: Ctx) -> None:
        login = await codex_login(api_key=self.api_key)
        _check_login("codex", login, self.api_key)
        ctx.exec_log(f"codex login ok: {login.get('auth')}")

    async def run(self, ctx: Ctx) -> Outcome:
        await ctx.checkpoint()
        await ctx.begin_step(1, 1, "codex exec")
        last = ctx.agent_dir / "codex-last-message.txt"
        argv = [*(resolve_cli("codex") or []), "exec", "--sandbox", "workspace-write", "--skip-git-repo-check",
                "--ephemeral", "--json", "-o", str(last), "-C", str(ctx.workspace)]
        if self.spec.model:
            argv += ["-m", self.spec.model]
        argv.append("-")
        res = await ctx.run_process(argv, stdin=ctx.prompt.encode("utf-8"), timeout=self.timeout,
                                    env=cli_env(("CODEX", "OPENAI"), _CODEX_KEY_VARS, api_key=self.api_key))
        ctx.exec_log(f"step 1 executed (codex exit={res.returncode}, timed_out={res.timed_out})")
        if res.timed_out:
            raise RuntimeError(f"codex exec timed out after {self.timeout}s")
        await ctx.end_step(1)
        answer = last.read_text(encoding="utf-8", errors="replace").strip() if last.is_file() else ""
        if res.returncode:
            tail = (res.stderr or res.stdout).decode("utf-8", "replace").strip()[-400:]
            raise RuntimeError(f"codex exec failed (exit {res.returncode}): {tail}")
        return Outcome(answer or "(no final message)", {"events": len(res.stdout.splitlines())})


def build(spec: AgentSpec) -> Connector:
    return {"mock": MockConnector, "local": LocalConnector, "claude": ClaudeConnector,
            "codex": CodexConnector}[spec.connector](spec)
