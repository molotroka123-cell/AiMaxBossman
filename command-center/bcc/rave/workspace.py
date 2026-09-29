"""Per-agent git isolation for the rave (blocking helpers; callers use to_thread).

Every agent works in its own `git clone` of the project at a pinned base commit,
with no remote. A clone (not a shared-ref worktree) keeps an agent's own `git`
calls away from the owner's branches. Bossman — not the agent — commits the
workspace when the agent ends, so its work becomes a durable commit that is
never deleted automatically.

The agent can write inside `<ws>/.git` too, and `.git/config` can name
commands git would run (fsmonitor, hooks, filters). So Bossman fingerprints
`.git/config` + hooks right after the clone and refuses to run git in a
workspace whose fingerprint changed; then it only copies files (no git), and
the agent is reported as having tampered with its repository.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

BOSSMAN_ID = ("-c", "user.name=BOSSMAN", "-c", "user.email=bossman@local")
_SAFE_PART = re.compile(r"[^A-Za-z0-9._-]+")


class WorkspaceError(RuntimeError):
    pass


def _empty_hooks_dir() -> str:
    d = Path(tempfile.gettempdir()) / "bossman-rave-nohooks"
    d.mkdir(exist_ok=True)
    return str(d)


def _hardening() -> tuple[str, ...]:
    # Defence in depth against an agent-edited .git/config (the fingerprint is the gate).
    return ("-c", f"core.hooksPath={_empty_hooks_dir()}", "-c", "core.fsmonitor=false",
            "-c", "core.quotepath=off", "-c", "commit.gpgsign=false")


def git(repo: str | Path, *args: str, check: bool = True, data: bytes | None = None,
        timeout: float = 180) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
    proc = subprocess.run(["git", *_hardening(), "-C", str(repo), *args], input=data,
                          capture_output=True, timeout=timeout, check=False, env=env)
    if check and proc.returncode:
        raise WorkspaceError(f"git {' '.join(args[:3])}: "
                             + proc.stderr.decode("utf-8", "replace").strip()[:400])
    return proc


def out(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout.decode("utf-8", "replace").strip()


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def head_commit(repo: Path) -> str:
    return out(git(repo, "rev-parse", "--verify", "HEAD"))


def create_scratch_repo(path: Path, prompt: str) -> str:
    """A small project for a rave without --repo: README + initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    (path / "README.md").write_text(
        "# Rave scratch project\n\nCreated by Bossman for one Agentic Rave.\n\n"
        f"Prompt:\n\n> {prompt.strip()[:2000]}\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, *BOSSMAN_ID, "commit", "-q", "-m", "rave: scratch base")
    return head_commit(path)


def fingerprint(ws: Path) -> str:
    h = hashlib.sha256()
    cfg = ws / ".git" / "config"
    h.update(cfg.read_bytes() if cfg.is_file() else b"<no config>")
    hooks = ws / ".git" / "hooks"
    if hooks.is_dir():
        for p in sorted(hooks.iterdir()):
            if not p.name.endswith(".sample"):
                h.update(p.name.encode() + b"\0" + (p.read_bytes() if p.is_file() else b""))
    return "sha256:" + h.hexdigest()


def create_workspace(source: Path, base: str, ws: Path, branch: str) -> str:
    """Clone `source` into `ws`, drop the remote, check out `branch` at `base`.
    Returns the fingerprint of the fresh `.git` (config + hooks)."""
    if ws.exists() and any(ws.iterdir()):
        raise WorkspaceError(f"workspace already exists: {ws}")
    ws.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", *_hardening(), "clone", "--quiet", "--no-checkout", str(source), str(ws)],
                          capture_output=True, timeout=600, check=False,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if proc.returncode:
        raise WorkspaceError("git clone: " + proc.stderr.decode("utf-8", "replace").strip()[:400])
    git(ws, "remote", "remove", "origin")
    git(ws, "checkout", "-q", "-b", branch, base)
    # hooks copied from templates are samples only; a clone brings no hooks of the source
    return fingerprint(ws)


def tampered(ws: Path, expected: str | None) -> bool:
    return bool(expected) and fingerprint(ws) != expected


def snapshot(ws: Path, message: str, expected_fp: str | None) -> dict:
    """Commit everything in the workspace as BOSSMAN. Returns {commit, committed, tampered}."""
    if tampered(ws, expected_fp):
        return {"commit": None, "committed": False, "tampered": True}
    git(ws, "add", "-A")
    staged = git(ws, "diff", "--cached", "--quiet", check=False)
    committed = False
    if staged.returncode == 1:
        git(ws, *BOSSMAN_ID, "commit", "-q", "--no-verify", "-m", message)
        committed = True
    return {"commit": head_commit(ws), "committed": committed, "tampered": False}


def copy_files(ws: Path, dest: Path) -> int:
    """Fallback preservation without git (tampered workspace): copy the tree minus .git."""
    count = 0
    for src in ws.rglob("*"):
        rel = src.relative_to(ws)
        if rel.parts and rel.parts[0] == ".git":
            continue
        target = dest / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif src.is_file() and not src.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            count += 1
    return count


def changed(ws: Path, base: str, head: str) -> list[dict]:
    raw = out(git(ws, "diff", "--no-ext-diff", "--no-renames", "--name-status", base, head))
    items = []
    for line in raw.splitlines():
        status, _, path = line.partition("\t")
        if path:
            items.append({"status": status.strip()[:1], "path": path.strip()})
    return items


def diff(ws: Path, base: str, head: str, *, max_bytes: int = 400_000) -> dict:
    stat = out(git(ws, "diff", "--no-ext-diff", "--stat", base, head))
    patch = git(ws, "diff", "--no-ext-diff", "--no-renames", base, head).stdout.decode("utf-8", "replace")
    return {"stat": stat, "patch": patch[:max_bytes], "truncated": len(patch) > max_bytes,
            "files": changed(ws, base, head)}


def blob(ws: Path, commit: str, path: str) -> bytes | None:
    proc = git(ws, "cat-file", "blob", f"{commit}:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else None


def merge_file(base: bytes, ours: bytes, theirs: bytes, labels: tuple[str, str, str]) -> tuple[bytes, int]:
    """3-way text merge with diff3 markers (git merge-file). Returns (merged, conflict hunks)."""
    with tempfile.TemporaryDirectory(prefix="bossman-rave-merge-") as tmp:
        t = Path(tmp)
        (t / "a").write_bytes(ours)
        (t / "base").write_bytes(base)
        (t / "b").write_bytes(theirs)
        proc = subprocess.run(["git", "merge-file", "-p", "--diff3", "-L", labels[0], "-L", labels[1],
                               "-L", labels[2], str(t / "a"), str(t / "base"), str(t / "b")],
                              capture_output=True, timeout=60, check=False)
    if proc.returncode < 0:
        raise WorkspaceError("git merge-file failed")
    return proc.stdout, max(0, proc.returncode)


def safe_part(text: str) -> str:
    return (_SAFE_PART.sub("_", text).strip("._") or "file")[:80]


@dataclass
class AgentResult:
    name: str
    workspace: Path
    head: str


def detect_conflicts(base: str, agents: list[AgentResult], out_dir: Path) -> list[dict]:
    """Files changed by 2+ agents with different content. Every version is written
    to `out_dir/<n>/` (base, each agent, merged-with-markers): nothing is lost."""
    per_agent: dict[str, dict[str, str]] = {}
    for a in agents:
        per_agent[a.name] = {c["path"]: c["status"] for c in changed(a.workspace, base, a.head)}
    found: list[dict] = []
    for i, a in enumerate(agents):
        for b in agents[i + 1:]:
            for path in sorted(set(per_agent[a.name]) & set(per_agent[b.name])):
                va, vb = blob(a.workspace, a.head, path), blob(b.workspace, b.head, path)
                if va == vb:
                    continue                      # same result: no conflict
                vbase = blob(a.workspace, base, path)
                merged, hunks = merge_file(vbase or b"", va or b"", vb or b"", (a.name, "base", b.name))
                slot = out_dir / f"{safe_part(a.name)}__{safe_part(b.name)}__{safe_part(path)}"
                slot.mkdir(parents=True, exist_ok=True)
                name = safe_part(Path(path).name)
                if vbase is not None:
                    (slot / f"base__{name}").write_bytes(vbase)
                (slot / f"{safe_part(a.name)}__{name}").write_bytes(va if va is not None else b"")
                (slot / f"{safe_part(b.name)}__{name}").write_bytes(vb if vb is not None else b"")
                (slot / f"merged__{name}").write_bytes(merged)
                found.append({"file": path, "agents": [a.name, b.name],
                              "kind": "both_modified" if vbase is not None else "both_added",
                              "deleted_by": [n for n, v in ((a.name, va), (b.name, vb)) if v is None],
                              "auto_mergeable": hunks == 0, "conflict_hunks": hunks,
                              "artifacts": str(slot)})
    return found


def apply_to_project(project: Path, ws: Path, base: str, head: str, *, dry_run: bool = False) -> dict:
    """Copy an agent's result into the project's WORKING TREE (no commit).

    Preflight first, nothing written on refusal: every touched file in the
    project must still equal its base version (or be absent when the agent
    created it). A file changed since (e.g. another agent's version applied
    first) is a CONFLICT — the project and both agents' versions stay intact."""
    items = changed(ws, base, head)
    root = project.resolve()
    for c in items:
        target = (root / c["path"]).resolve()
        if root not in target.parents or c["path"].split("/")[0] == ".git":
            raise WorkspaceError(f"path escapes the project: {c['path']}")
    conflicts = []
    for c in items:
        current = project / c["path"]
        want_before = blob(ws, base, c["path"])
        have = current.read_bytes() if current.is_file() else None
        if have != want_before and not (have is not None and want_before is not None
                                         and have.replace(b"\r\n", b"\n") == want_before.replace(b"\r\n", b"\n")):
            conflicts.append(c["path"])
    if conflicts or dry_run:
        return {"applied": False, "conflicts": conflicts, "files": [c["path"] for c in items]}
    written = []
    for c in items:
        target = project / c["path"]
        data = blob(ws, head, c["path"])
        if data is None:
            if target.is_file():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        written.append(c["path"])
    return {"applied": True, "conflicts": [], "files": written}
