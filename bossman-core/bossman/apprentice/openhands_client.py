"""OpenHands execution backend for Bossman's Apprentice/Teacher boundary.

The backend is deliberately sidecar-based: Bossman may stay on Python 3.11
while OpenHands runs in a separately managed Python 3.12+ environment.
OpenHands is an untrusted code executor; Bossman derives the real git diff
and enforces path scope after the run instead of trusting agent self-report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
from typing import Mapping, Sequence


class OpenHandsError(RuntimeError):
    """Raised when the OpenHands sidecar cannot produce admissible evidence."""


@dataclass(frozen=True)
class OpenHandsRequest:
    instruction: str
    workspace: Path
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...] = ()
    model: str | None = None
    timeout_seconds: int = 900
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenHandsResult:
    status: str
    changed_files: tuple[str, ...]
    diff: str
    sidecar: Mapping[str, object]


def _git(workspace: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(workspace), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise OpenHandsError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _changed_files(workspace: Path) -> tuple[str, ...]:
    # Include staged, unstaged and untracked files. Never trust the model's list.
    names: set[str] = set()
    for args in (("diff", "--name-only", "HEAD"), ("ls-files", "--others", "--exclude-standard")):
        names.update(line.strip().replace("\\", "/") for line in _git(workspace, *args).splitlines() if line.strip())
    return tuple(sorted(names))


def _in_scope(path: str, prefixes: Sequence[str]) -> bool:
    clean = path.lstrip("./")
    return any(clean == p.rstrip("/") or clean.startswith(p.rstrip("/") + "/") for p in prefixes)


def _validate_scope(changed: Sequence[str], allowed: Sequence[str], protected: Sequence[str]) -> None:
    if not allowed:
        raise OpenHandsError("allowed_paths must be non-empty (fail closed)")
    violations = [p for p in changed if not _in_scope(p, allowed) or _in_scope(p, protected)]
    if violations:
        raise OpenHandsError("OpenHands changed protected/out-of-scope paths: " + ", ".join(violations))


class OpenHandsClient:
    """Run an OpenHands sidecar using a JSON-over-stdio contract."""

    def __init__(self, command: Sequence[str] | None = None, env: Mapping[str, str] | None = None):
        raw = command or (os.environ.get("BOSSMAN_OPENHANDS_COMMAND", "").split() or None)
        if not raw:
            raise OpenHandsError("OpenHands disabled: set BOSSMAN_OPENHANDS_COMMAND or pass command=")
        self.command = tuple(raw)
        self.env = dict(env or {})

    def run(self, request: OpenHandsRequest) -> OpenHandsResult:
        workspace = request.workspace.resolve()
        if not (workspace / ".git").exists():
            raise OpenHandsError(f"workspace is not a git checkout: {workspace}")
        payload = {
            "schema": "bossman.openhands.v1",
            "instruction": request.instruction,
            "workspace": str(workspace),
            "allowed_paths": list(request.allowed_paths),
            "protected_paths": list(request.protected_paths),
            "model": request.model,
            "metadata": dict(request.metadata),
        }
        env = os.environ.copy()
        env.update(self.env)
        proc = subprocess.run(
            list(self.command),
            input=json.dumps(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=request.timeout_seconds,
            check=False,
        )
        if proc.returncode:
            raise OpenHandsError(f"OpenHands sidecar exited {proc.returncode}: {proc.stderr[-2000:].strip()}")
        try:
            response = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise OpenHandsError("OpenHands sidecar returned invalid JSON") from exc
        if response.get("schema") != "bossman.openhands.v1" or response.get("status") not in {"completed", "failed"}:
            raise OpenHandsError("OpenHands sidecar returned an invalid response contract")
        changed = _changed_files(workspace)
        _validate_scope(changed, request.allowed_paths, request.protected_paths)
        diff = _git(workspace, "diff", "--binary", "HEAD")
        # Untracked files are named in evidence even though git diff HEAD cannot render them.
        return OpenHandsResult(str(response["status"]), changed, diff, response)
