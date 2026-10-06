"""Stub of the official `claude` / `codex` CLIs with the same interface the rave
connectors use (status command, headless run, prompt on stdin). No network, no
credential. Behaviour via env: STUB_LOGIN = subscription | none | apikey,
STUB_FAIL = 1 (headless run exits non-zero), STUB_SLEEP = seconds,
STUB_VERSION = the version `claude --version` prints (default 2.1.284; `garbage` = no version).

Like the real CLIs it REJECTS an unknown flag or option (exit 1, `error: unknown option`), a missing
option value and a value outside the option's choices, so a test would catch a wrong flag in the
connector's argv. `--permission-prompts` is unknown below Claude Code 2.1.259, as in the real CLI.

Account profiles (the rave account pool): the profile directory is CLAUDE_CONFIG_DIR / CODEX_HOME.
`<profile>/stub-login` (subscription | none | apikey) overrides STUB_LOGIN for that profile;
`<profile>/stub-limit` makes the headless run fail like an exhausted usage limit; every headless
run is appended to `<profile>/stub-runs.log` so a test can see which account ran."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

CLAUDE_VERSION_DEFAULT = "2.1.284"
# flag -> (takes a value, allowed values or None)
CLAUDE_PRINT_FLAGS = {
    "-p": (False, None), "--print": (False, None),
    "--output-format": (True, ("text", "json", "stream-json")),
    "--permission-mode": (True, ("acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")),
    "--allowedTools": (True, None), "--allowed-tools": (True, None),
    "--disallowedTools": (True, None), "--disallowed-tools": (True, None),
    "--setting-sources": (True, None), "--no-session-persistence": (False, None),
    "--permission-prompts": (True, ("host", "none")), "--model": (True, None),
}
CLAUDE_AUTH_STATUS_FLAGS = {"--json": (False, None), "--text": (False, None)}
CODEX_EXEC_FLAGS = {
    "--sandbox": (True, ("read-only", "workspace-write", "danger-full-access")),
    "-s": (True, ("read-only", "workspace-write", "danger-full-access")),
    "--skip-git-repo-check": (False, None), "--ephemeral": (False, None), "--json": (False, None),
    "-o": (True, None), "--output-last-message": (True, None), "-C": (True, None), "--cd": (True, None),
    "-m": (True, None), "--model": (True, None),
}


def parse_version(text: str) -> tuple[int, ...] | None:
    parts = text.strip().split(".")
    try:
        return tuple(int(p) for p in parts[:3]) if len(parts) >= 3 else None
    except ValueError:
        return None


def die(message: str, code: int = 1) -> int:
    print(message, file=sys.stderr)
    return code


def check_flags(argv: list[str], known: dict, *, positional_ok: tuple[str, ...] = ()) -> str | None:
    """None when every token is a known flag / value / allowed positional, else the CLI-style error."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in positional_ok:
            i += 1
            continue
        if not tok.startswith("-"):
            return f"error: unexpected argument '{tok}'"
        if tok not in known:
            return f"error: unknown option '{tok}'"
        takes, choices = known[tok]
        if takes:
            if i + 1 >= len(argv) or (argv[i + 1].startswith("-") and argv[i + 1] != "-"):
                return f"error: option '{tok}' argument missing"
            value = argv[i + 1]
            if choices and value not in choices:
                return f"error: option '{tok}' argument '{value}' is invalid. Allowed choices are {', '.join(choices)}."
            i += 2
        else:
            i += 1
    return None


def profile_dir(tool: str) -> Path | None:
    raw = os.environ.get("CLAUDE_CONFIG_DIR" if tool == "claude" else "CODEX_HOME")
    return Path(raw) if raw else None


def login_state(tool: str) -> str:
    prof = profile_dir(tool)
    if prof is not None and (prof / "stub-login").is_file():
        return (prof / "stub-login").read_text(encoding="utf-8").strip()
    return os.environ.get("STUB_LOGIN", "subscription")


def limited(tool: str) -> bool:
    prof = profile_dir(tool)
    return os.environ.get("STUB_LIMIT") == "1" or (prof is not None and (prof / "stub-limit").is_file())


def note_run(tool: str, prompt: str) -> None:
    prof = profile_dir(tool)
    if prof is not None and prof.is_dir():
        with open(prof / "stub-runs.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.time():.3f} {prompt.strip()[:60]}\n")


def main() -> int:
    tool, argv = sys.argv[1], sys.argv[2:]
    login = login_state(tool)
    if argv[:1] in (["--version"], ["-v"], ["-V"]):
        if tool == "claude":
            version = os.environ.get("STUB_VERSION", CLAUDE_VERSION_DEFAULT)
            print("no version here" if version == "garbage" else f"{version} (Claude Code)")
        else:
            print("codex-cli 0.157.1")
        return 0
    if tool == "claude" and argv[:2] == ["auth", "status"]:
        bad = check_flags(argv[2:], CLAUDE_AUTH_STATUS_FLAGS)
        if bad:
            return die(bad)
        print(json.dumps({"loggedIn": login != "none",
                          "authMethod": {"subscription": "claude.ai", "apikey": "apiKey"}.get(login, "none"),
                          "email": "owner@example.invalid", "orgId": "x", "subscriptionType": "max"}))
        return 0
    if tool == "codex" and argv[:2] == ["login", "status"]:
        if login == "none":
            print("Not logged in")
            return 1
        print("Logged in using ChatGPT" if login == "subscription" else "Logged in using an API key - sk-***")
        return 0
    if tool == "claude" and "-p" in argv:
        known = dict(CLAUDE_PRINT_FLAGS)
        version = parse_version(os.environ.get("STUB_VERSION", CLAUDE_VERSION_DEFAULT))
        if version is not None and version < (2, 1, 259):
            known.pop("--permission-prompts")
        bad = check_flags(argv, known)
        if bad:
            return die(bad)
    elif tool == "codex" and argv[:1] == ["exec"]:
        bad = check_flags(argv[1:], CODEX_EXEC_FLAGS, positional_ok=("-",))
        if bad:
            return die(bad)
    prompt = sys.stdin.read()
    time.sleep(float(os.environ.get("STUB_SLEEP", "0")))
    if os.environ.get("STUB_FAIL") == "1":
        print("boom", file=sys.stderr)
        return 3
    if tool == "claude" and "-p" in argv:
        note_run(tool, prompt)
        if limited(tool):
            print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "num_turns": 0,
                              "result": f"Claude AI usage limit reached|{int(time.time()) + 3600}",
                              "session_id": "s"}))
            return 1
        if os.environ.get("STUB_TAMPER") == "1":      # a misbehaving agent edits its own .git/config
            with open(Path(".git") / "config", "a", encoding="utf-8") as fh:
                fh.write("\n[core]\n\tfsmonitor = calc.exe\n")
        Path("CLAUDE_STUB.md").write_text(f"claude stub saw: {prompt.strip()}\n", encoding="utf-8")
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                          "result": f"STUB-CLAUDE-OK: {prompt.strip()[:40]}", "session_id": "s"}))
        return 0
    if tool == "codex" and argv[:1] == ["exec"]:
        note_run(tool, prompt)
        if limited(tool):
            print("You've hit your usage limit. Try again later.", file=sys.stderr)
            return 1
        ws = Path(argv[argv.index("-C") + 1])
        (ws / "CODEX_STUB.md").write_text(f"codex stub saw: {prompt.strip()}\n", encoding="utf-8")
        Path(argv[argv.index("-o") + 1]).write_text(f"STUB-CODEX-OK: {prompt.strip()[:40]}", encoding="utf-8")
        print(json.dumps({"type": "turn.completed"}))
        return 0
    print(f"unexpected argv {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
