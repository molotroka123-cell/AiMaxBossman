"""Guarded OpenHands sidecar client for Bossman's Apprentice/Teacher boundary.

OpenHands runs outside the Bossman interpreter (Python 3.12+ sidecar).  The
sidecar is treated as untrusted: Bossman derives the Git delta itself, rejects
pre-existing dirt, refuses repositories with remotes, and enforces explicit
allowed/protected paths after execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shlex
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


_SYSTEM_ENV_KEYS = (
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "HOME", "USERPROFILE", "TMP", "TEMP", "LANG", "LC_ALL",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
)
_DRIVE = re.compile(r"^[A-Za-z]:")


def _minimal_process_env() -> dict[str, str]:
    """Keep only OS/runtime variables. Provider credentials are explicit input."""
    return {key: os.environ[key] for key in _SYSTEM_ENV_KEYS if os.environ.get(key)}


def _git(workspace: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(workspace), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise OpenHandsError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _untracked_files(workspace: Path) -> tuple[str, ...]:
    return tuple(sorted(
        line.strip().replace("\\", "/")
        for line in _git(workspace, "ls-files", "--others", "--exclude-standard").splitlines()
        if line.strip()))


def _changed_files(workspace: Path) -> tuple[str, ...]:
    names: set[str] = set()
    for args in (("diff", "--name-only", "HEAD"), ("ls-files", "--others", "--exclude-standard")):
        names.update(line.strip().replace("\\", "/") for line in _git(workspace, *args).splitlines() if line.strip())
    return tuple(sorted(names))


def _evidence_diff(workspace: Path) -> str:
    """The diff a reviewer actually reads, INCLUDING files the agent created.

    `git diff HEAD` shows tracked changes only, so a run whose entire output was
    new files produced `changed_files=('NOTES.md',)` next to an empty diff — the
    file list said work happened and the evidence showed none. For a coding
    worker whose main product is new files that is the evidence gap that
    matters most.

    `--intent-to-add` registers the new paths in the sandbox INDEX so they
    appear in the diff. It touches neither the working tree nor HEAD, and the
    caller re-checks both afterwards; the worktree is disposable by
    construction."""
    untracked = _untracked_files(workspace)
    if untracked:
        _git(workspace, "add", "--intent-to-add", "--", *untracked)
    return _git(workspace, "diff", "--binary", "HEAD")


def _normalize_repo_path(path: str) -> str:
    raw = str(path).replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw or raw.startswith("/") or _DRIVE.match(raw) or "\x00" in raw or ".." in raw.split("/"):
        raise OpenHandsError(f"unsafe repository path: {path!r}")
    return raw.rstrip("/")


def _in_scope(path: str, prefixes: Sequence[str]) -> bool:
    clean = _normalize_repo_path(path)
    for prefix in prefixes:
        p = _normalize_repo_path(prefix)
        if clean == p or clean.startswith(p + "/"):
            return True
    return False


def _validate_scope(changed: Sequence[str], allowed: Sequence[str], protected: Sequence[str]) -> None:
    if not allowed:
        raise OpenHandsError("allowed_paths must be non-empty (fail closed)")
    for path in (*allowed, *protected):
        _normalize_repo_path(path)
    violations = [p for p in changed if not _in_scope(p, allowed) or _in_scope(p, protected)]
    if violations:
        raise OpenHandsError("OpenHands changed protected/out-of-scope paths: " + ", ".join(violations))


class OpenHandsClient:
    """Run a JSON-over-stdio OpenHands sidecar without granting repository authority."""

    def __init__(self, command: Sequence[str] | None = None, env: Mapping[str, str] | None = None):
        configured = os.environ.get("BOSSMAN_OPENHANDS_COMMAND", "")
        raw = tuple(command or ())
        if not raw and configured.strip():
            raw = tuple(shlex.split(configured, posix=os.name != "nt"))
        if not raw:
            raise OpenHandsError("OpenHands disabled: set BOSSMAN_OPENHANDS_COMMAND or pass command=")
        self.command = raw
        self.env = {str(k): str(v) for k, v in dict(env or {}).items()}

    def run(self, request: OpenHandsRequest) -> OpenHandsResult:
        workspace = request.workspace.resolve()
        if not (workspace / ".git").exists():
            raise OpenHandsError(f"workspace is not a git checkout: {workspace}")
        _validate_scope((), request.allowed_paths, request.protected_paths)
        dirty_before = _changed_files(workspace)
        if dirty_before:
            raise OpenHandsError("workspace must be clean before OpenHands run: " + ", ".join(dirty_before))
        remotes = tuple(line.strip() for line in _git(workspace, "remote").splitlines() if line.strip())
        if remotes:
            raise OpenHandsError("OpenHands workspace must not have git remotes")
        head_before = _git(workspace, "rev-parse", "HEAD").strip()
        config_before = (workspace / ".git" / "config").read_bytes()

        payload = {
            "schema": "bossman.openhands.v1",
            "instruction": request.instruction,
            "workspace": str(workspace),
            "allowed_paths": list(request.allowed_paths),
            "protected_paths": list(request.protected_paths),
            "model": request.model,
            "metadata": dict(request.metadata),
        }
        env = _minimal_process_env()
        env.update(self.env)
        try:
            proc = subprocess.run(
                list(self.command),
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=request.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OpenHandsError("OpenHands sidecar command is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise OpenHandsError(f"OpenHands sidecar timed out after {request.timeout_seconds}s") from exc

        try:
            response = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise OpenHandsError("OpenHands sidecar returned invalid JSON") from exc
        if response.get("schema") != "bossman.openhands.v1" or response.get("status") not in {"completed", "failed"}:
            raise OpenHandsError("OpenHands sidecar returned an invalid response contract")

        head_after = _git(workspace, "rev-parse", "HEAD").strip()
        if head_after != head_before:
            raise OpenHandsError("OpenHands may not commit/reset/rewrite sandbox HEAD")
        if (workspace / ".git" / "config").read_bytes() != config_before:
            raise OpenHandsError("OpenHands may not modify sandbox git configuration")
        if tuple(line.strip() for line in _git(workspace, "remote").splitlines() if line.strip()):
            raise OpenHandsError("OpenHands may not add git remotes")
        changed = _changed_files(workspace)
        _validate_scope(changed, request.allowed_paths, request.protected_paths)
        diff = _evidence_diff(workspace)
        if proc.returncode and response.get("status") != "failed":
            raise OpenHandsError(f"OpenHands sidecar exited {proc.returncode} without failed status")
        return OpenHandsResult(str(response["status"]), changed, diff, response)
