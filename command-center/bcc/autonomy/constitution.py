"""The user constitution and the owner pin (docs/constitution/BOSSMAN_CONSTITUTION.md, "Enforcement").

``verify()`` hashes the constitution and compares it with the owner's pin,
``%LOCALAPPDATA%\\Bossman\\autonomy\\constitution.sha256`` (outside the
repository). Missing file, missing pin, unreadable pin or a mismatch -> the
status is BLOCKED with the reason; the autonomy loop must not run.

``pin()`` writes the pin, and only for a human: stdin and stdout must be a TTY,
no agent-session marker may be set in the environment, the pin must live
outside the repository, and the person must type the first 12 hex characters
of the SHA they are pinning. There is no API and no flag that skips this.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, TextIO

from .journal import atomic_write_bytes

#: Environment variables that mark an agent / automation session. Any of them
#: set (non-empty) makes ``pin`` refuse, even on a TTY.
AGENT_ENV_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT", "CODEX_SANDBOX",
                     "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_THREAD_ID", "BOSSMAN_AGENT_SESSION",
                     "BOSSMAN_AUTONOMY_WORKER", "BOSSMAN_RAVE_AGENT", "CI", "GITHUB_ACTIONS")
CONFIRM_CHARS = 12
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def repo_root() -> Path:
    # <repo>/command-center/bcc/autonomy/constitution.py
    return Path(__file__).resolve().parents[3]


def default_constitution_path() -> Path:
    return repo_root() / "docs" / "constitution" / "BOSSMAN_CONSTITUTION.md"


def default_pin_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("LOCALAPPDATA") or (str(Path.home() / "AppData" / "Local") if os.name == "nt"
                                       else env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share"))
    return Path(base) / "Bossman" / "autonomy" / "constitution.sha256"


@dataclass(frozen=True)
class ConstitutionStatus:
    ok: bool
    sha: str
    pinned_sha: str
    reason: str
    path: str = ""
    pin_path: str = ""

    @property
    def status(self) -> str:
        return "OK" if self.ok else "BLOCKED"

    def as_dict(self) -> dict:
        return {"status": self.status, "ok": self.ok, "sha": self.sha, "pinned_sha": self.pinned_sha,
                "reason": self.reason, "path": self.path, "pin_path": self.pin_path}


@dataclass(frozen=True)
class PinResult:
    ok: bool
    sha: str
    reason: str


def load(path: str | os.PathLike | None = None) -> bytes:
    return Path(path or default_constitution_path()).read_bytes()


def sha256(path: str | os.PathLike | None = None) -> str:
    return hashlib.sha256(load(path)).hexdigest()


def read_pin(pin_path: str | os.PathLike | None = None) -> str | None:
    """The pinned SHA, or None when absent. A malformed pin raises ValueError."""
    p = Path(pin_path or default_pin_path())
    if not p.exists():
        return None
    text = p.read_text(encoding="ascii", errors="replace").strip().lower()
    if not _HEX64.match(text):
        raise ValueError("pin file does not contain one SHA-256")
    return text


def verify(path: str | os.PathLike | None = None, pin_path: str | os.PathLike | None = None) -> ConstitutionStatus:
    cpath = Path(path or default_constitution_path())
    ppath = Path(pin_path or default_pin_path())
    common = {"path": str(cpath), "pin_path": str(ppath)}
    try:
        sha = hashlib.sha256(cpath.read_bytes()).hexdigest()
    except OSError as exc:
        return ConstitutionStatus(False, "", "", f"constitution unreadable: {type(exc).__name__}", **common)
    try:
        pinned = read_pin(ppath)
    except (OSError, ValueError) as exc:
        return ConstitutionStatus(False, sha, "", f"pin unreadable: {exc}", **common)
    if pinned is None:
        return ConstitutionStatus(False, sha, "", "constitution is not pinned by the user: run "
                                                  "`bossman autonomy constitution pin` in an interactive terminal",
                                  **common)
    if pinned != sha:
        return ConstitutionStatus(False, sha, pinned, "constitution changed since the user pinned it "
                                                      "(sha mismatch): the user must review and re-pin", **common)
    return ConstitutionStatus(True, sha, pinned, "pinned by the user", **common)


def agent_markers(env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    return [k for k in AGENT_ENV_MARKERS if str(env.get(k, "")).strip()]


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def pin(path: str | os.PathLike | None = None, pin_path: str | os.PathLike | None = None, *,
        stdin: TextIO | None = None, stdout: TextIO | None = None, env: Mapping[str, str] | None = None,
        read_line: Callable[[], str] | None = None, journal=None) -> PinResult:
    """Interactive owner pin. Refuses without a TTY or inside an agent session."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    cpath = Path(path or default_constitution_path())
    ppath = Path(pin_path or default_pin_path(env))

    def refuse(reason: str, sha: str = "") -> PinResult:
        if journal is not None:
            journal.append("constitution.pin_refused", {"reason": reason, "sha": sha})
        return PinResult(False, sha, reason)

    markers = agent_markers(env)
    if markers:
        return refuse(f"refused: agent/automation session ({', '.join(markers)}); only the user pins")
    try:
        interactive = bool(stdin.isatty()) and bool(stdout.isatty())
    except (AttributeError, ValueError, OSError):
        interactive = False
    if not interactive:
        return refuse("refused: not an interactive terminal (stdin and stdout must be a TTY)")
    if _inside(ppath, repo_root()) or _inside(ppath, cpath.parent):
        return refuse("refused: the pin must be stored outside the repository")
    try:
        sha = hashlib.sha256(cpath.read_bytes()).hexdigest()
    except OSError as exc:
        return refuse(f"constitution unreadable: {type(exc).__name__}")
    try:
        current = read_pin(ppath)
    except (OSError, ValueError):
        current = None
    stdout.write(f"Constitution: {cpath}\nSHA-256:      {sha}\n"
                 f"Current pin:  {current or '(none)'}\n"
                 f"Type the first {CONFIRM_CHARS} characters of the SHA-256 to pin it (anything else cancels): ")
    stdout.flush()
    answer = (read_line or stdin.readline)().strip().lower()
    if answer != sha[:CONFIRM_CHARS]:
        return refuse("cancelled: confirmation did not match", sha)
    atomic_write_bytes(ppath, (sha + "\n").encode("ascii"))
    if journal is not None:
        journal.append("constitution.pinned", {"sha": sha, "previous": current or ""})
    stdout.write("Pinned.\n")
    return PinResult(True, sha, "pinned")


__all__ = ["AGENT_ENV_MARKERS", "ConstitutionStatus", "PinResult", "agent_markers", "default_constitution_path",
           "default_pin_path", "load", "pin", "read_pin", "sha256", "verify"]
