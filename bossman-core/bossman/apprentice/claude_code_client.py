"""Real subprocess adapter for Claude Code, treated as an untrusted teacher."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ._bootstrap import trace
from .errors import BudgetExhausted, FallbackRefused


@dataclass(frozen=True, slots=True)
class ClaudeProcessPolicy:
    timeout_s: int = 120
    max_attempts: int = 2
    # Claude runs in plan mode: it proposes a diff; PatchVerifier owns apply.
    permission_mode: str = "plan"


class ClaudeCodeClient:
    """Invoke a locally installed ``claude`` binary without giving it authority.

    The command runner is injectable so integration tests use a real subprocess
    boundary with a stub executable and no credential.  Output is converted to
    visible typed facts; raw output and hidden reasoning are discarded.
    """
    def __init__(self, workspace: str | Path, *, command: tuple[str, ...] = ("claude",),
                 policy: ClaudeProcessPolicy = ClaudeProcessPolicy(),
                 runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        self.workspace, self.command, self.policy = Path(workspace).resolve(), command, policy
        self.runner, self.calls = runner, 0

    def _prompt(self, bundle: dict) -> str:
        # Deliberately no request for reasoning and no authority to run commands.
        return ("You are an untrusted patch proposer. Return JSON only with keys opened_files, symbols, root_cause, "
                "diff (a standard unified diff), test_results, attempt_errors. Do not run commands, push, deploy, "
                "read outside the supplied files, include secrets, or explain private reasoning.\n"
                + json.dumps(bundle, sort_keys=True, ensure_ascii=False))

    @staticmethod
    def _json_or_text(text: str) -> dict[str, Any]:
        try:
            value = json.loads(text)
            if isinstance(value, dict): return value
        except json.JSONDecodeError: pass
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if match:
            try:
                value = json.loads(match.group(1))
                if isinstance(value, dict): return value
            except json.JSONDecodeError: pass
        diff = re.search(r"(?:diff --git .*|--- a/.*)", text, re.S)
        return {"diff": diff.group(0) if diff else "", "attempt_errors": ["teacher output was not structured JSON"]}

    def run(self, bundle: dict) -> dict[str, Any]:
        if self.calls >= self.policy.max_attempts: raise BudgetExhausted("Claude Code max attempts reached")
        self.calls += 1
        prompt = self._prompt(bundle)
        # Keep inherited credentials out of the subprocess environment.  Claude
        # may obtain its own authenticated session, but it never receives ours.
        env = {k: v for k, v in os.environ.items() if not re.search(r"(TOKEN|SECRET|PASSWORD|API[_-]?KEY)", k, re.I)}
        argv = [*self.command, "-p", prompt, "--output-format", "json", "--permission-mode", self.policy.permission_mode]
        try:
            if self.runner is not None:
                result = self.runner(argv, cwd=str(self.workspace), env=env, timeout=self.policy.timeout_s,
                                     text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, check=False)
            else:
                result = subprocess.run(argv, cwd=self.workspace, env=env, timeout=self.policy.timeout_s, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, check=False)
        except FileNotFoundError as exc: raise FallbackRefused("Claude Code executable is not installed") from exc
        except subprocess.TimeoutExpired as exc: raise FallbackRefused(f"Claude Code timed out after {self.policy.timeout_s}s") from exc
        raw = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        parsed = self._json_or_text(result.stdout or "")
        for hidden in ("chain_of_thought", "hidden_reasoning", "thoughts", "scratchpad", "reasoning", "raw_prompt"):
            parsed.pop(hidden, None)
        sanitized = trace().redact_text(raw)[:8000]
        parsed["commands"] = []  # Teacher does not get executable command authority.
        parsed["log_text"] = sanitized
        parsed["artifacts"] = [f"claude-process:returncode={result.returncode}"]
        parsed["model_id"] = str(parsed.get("model_id") or "claude-code")
        parsed["model_version"] = str(parsed.get("model_version") or "unknown")
        if result.returncode and not parsed.get("attempt_errors"):
            parsed["attempt_errors"] = [f"claude exited {result.returncode}"]
        return parsed
