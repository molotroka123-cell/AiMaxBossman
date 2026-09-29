"""Stub of the official `claude` / `codex` CLIs with the same interface the rave
connectors use (status command, headless run, prompt on stdin). No network, no
credential. Behaviour via env: STUB_LOGIN = subscription | none | apikey,
STUB_FAIL = 1 (headless run exits non-zero), STUB_SLEEP = seconds."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    tool, argv = sys.argv[1], sys.argv[2:]
    login = os.environ.get("STUB_LOGIN", "subscription")
    if tool == "claude" and argv[:2] == ["auth", "status"]:
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
    prompt = sys.stdin.read()
    time.sleep(float(os.environ.get("STUB_SLEEP", "0")))
    if os.environ.get("STUB_FAIL") == "1":
        print("boom", file=sys.stderr)
        return 3
    if tool == "claude" and "-p" in argv:
        if os.environ.get("STUB_TAMPER") == "1":      # a misbehaving agent edits its own .git/config
            with open(Path(".git") / "config", "a", encoding="utf-8") as fh:
                fh.write("\n[core]\n\tfsmonitor = calc.exe\n")
        Path("CLAUDE_STUB.md").write_text(f"claude stub saw: {prompt.strip()}\n", encoding="utf-8")
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                          "result": f"STUB-CLAUDE-OK: {prompt.strip()[:40]}", "session_id": "s"}))
        return 0
    if tool == "codex" and argv[:1] == ["exec"]:
        ws = Path(argv[argv.index("-C") + 1])
        (ws / "CODEX_STUB.md").write_text(f"codex stub saw: {prompt.strip()}\n", encoding="utf-8")
        Path(argv[argv.index("-o") + 1]).write_text(f"STUB-CODEX-OK: {prompt.strip()[:40]}", encoding="utf-8")
        print(json.dumps({"type": "turn.completed"}))
        return 0
    print(f"unexpected argv {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
