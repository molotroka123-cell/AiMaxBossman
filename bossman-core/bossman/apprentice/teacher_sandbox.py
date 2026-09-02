"""Hermetic teacher workspace (PASS 2): Claude Code runs in a throw-away directory
that contains ONLY the sanitized ProblemBundle files and a short contract.

Prompt text is not a security boundary, so the boundary is structural:
  * fresh temp dir, no .git, no .env, no repository, no owner data;
  * the bundle content travels inline in the prompt, so the teacher needs no
    filesystem tools at all — file/shell/web tools are denied on the CLI;
  * inherited environment is scrubbed (credentials, BOSSMAN_*, PYTHONPATH, git);
  * the directory is destroyed after the call; the candidate diff is applied by
    the verifier to ITS OWN worktree (LiveWorkspace), never by the teacher.
`isolation_level()` reports what the host can enforce: with bubblewrap the process
also loses the rest of the filesystem; without it, isolation is cwd + tool denial.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

CONTRACT_FILE = "TEACHER_CONTRACT.md"
DENIED_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Glob", "Grep", "LS",
                "WebFetch", "WebSearch", "Task", "Agent")
_SECRET_ENV = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|SESSION|AUTH|(^|_)KEYS?(_|$))", re.I)
_DROPPED_ENV_PREFIXES = ("BOSSMAN_", "BCC_", "GIT_", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_", "AWS_", "GOOGLE_", "AZURE_",
                         "DATABASE_URL", "PGPASSWORD", "OPENROUTER", "OPENAI")


class TeacherWorkspaceRefused(RuntimeError):
    pass


def isolation_level() -> str:
    return "bwrap" if shutil.which("bwrap") else "process-cwd+tool-denylist"


def scrubbed_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the teacher process: no credentials, no Bossman/repo context."""
    src = dict(os.environ if base is None else base)
    out: dict[str, str] = {}
    for key, value in src.items():
        if _SECRET_ENV.search(key) or key.startswith(_DROPPED_ENV_PREFIXES):
            continue
        out[key] = value
    return out


def _safe_relative(path: str) -> Path:
    raw = str(path).replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or ".." in raw.split("/") or "\x00" in raw:
        raise TeacherWorkspaceRefused(f"bundle path refused: {path!r}")
    return Path(raw)


@dataclass(slots=True)
class HermeticWorkspace:
    path: Path
    files: tuple[str, ...]
    level: str
    denied_tools: tuple[str, ...] = DENIED_TOOLS
    env: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "files": list(self.files), "level": self.level, "denied_tools": list(self.denied_tools),
                "env_keys": sorted(self.env)}


@contextmanager
def hermetic_workspace(bundle: Any, *, acceptance_readonly: dict[str, str] | None = None) -> Iterator[HermeticWorkspace]:
    """Materialise ONLY the bundle files (+ contract, + optional read-only acceptance
    tests) in a fresh temp dir; destroy it afterwards, whatever happens."""
    data = bundle.as_dict() if hasattr(bundle, "as_dict") else dict(bundle)
    files = data.get("files") or {}
    tmp = Path(tempfile.mkdtemp(prefix="bossman-teacher-"))
    written: list[str] = []
    try:
        for path, content in files.items():
            rel = _safe_relative(path)
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(str(content), encoding="utf-8")
            written.append(rel.as_posix())
        for path, content in (acceptance_readonly or {}).items():
            rel = _safe_relative(path)
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(str(content), encoding="utf-8")
            os.chmod(dest, 0o444)
            written.append(rel.as_posix())
        contract = ["# Teacher contract", "", "You are an untrusted patch proposer. This directory holds only the files of the",
                    "problem bundle. Return a unified diff for the allowed files only. Constraints:", ""]
        contract += [f"- {c}" for c in (data.get("constraints") or ())]
        contract += ["", "Failing test:", "", "```", str(data.get("failing_test") or "")[:4000], "```", ""]
        (tmp / CONTRACT_FILE).write_text("\n".join(contract), encoding="utf-8")
        written.append(CONTRACT_FILE)
        yield HermeticWorkspace(path=tmp, files=tuple(sorted(written)), level=isolation_level(), env=scrubbed_env())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def bwrap_prefix(workspace: Path) -> list[str]:
    """Optional OS sandbox (only used when bubblewrap is installed): read-only system,
    the hermetic dir bound at /work, no other filesystem, private pid namespace."""
    home = Path.home()
    prefix = ["bwrap", "--unshare-pid", "--die-with-parent", "--new-session", "--dev", "/dev", "--proc", "/proc",
              "--tmpfs", "/tmp", "--bind", str(workspace), "/work", "--chdir", "/work"]
    for ro in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/ssl", "/etc/resolv.conf", "/opt"):
        if Path(ro).exists():
            prefix += ["--ro-bind", ro, ro]
    claude_home = home / ".claude"
    if claude_home.exists():                       # Claude's own session config, read-only
        prefix += ["--ro-bind", str(claude_home), str(claude_home)]
    return prefix
