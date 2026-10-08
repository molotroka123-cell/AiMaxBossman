#!/usr/bin/env python3
"""Local completeness manifest: what exists on THIS machine that the cloud cannot see.

The cloud integrator sees only pushed refs. Before anyone may say "nothing is lost",
the owner's machine has to answer, per git working copy: which HEAD, which branch,
how far ahead/behind its upstream, which files are modified or untracked, which
local commits are on no remote, which stashes and worktrees exist; plus which
Bossman builds are installed and the SHA each one was built from.

Read-only. It never prints or stores file CONTENTS. Untracked/modified files are
listed by path, size and sha256 ONLY when they are not secret-like, not model
weights and not caches; those are counted by category and never opened.

    python tools/local_completeness_manifest.py --out manifest.json
    python tools/local_completeness_manifest.py --root C:/Users/<you>/Bossman --root D:/work --out m.json

Verdict: LOCAL_COMPLETENESS=COMPLETE only when every working copy is clean, has
no unpushed commits and no stashes; otherwise GAPS with the list. A repository
that could not be read is UNVERIFIED, never COMPLETE. Exit code 0 = COMPLETE,
1 = GAPS, 2 = UNVERIFIED.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "bossman.local_completeness_manifest.v1"

#: Never opened, never hashed: a hash of a short secret is guessable, and the owner rule
#: (07.10) is that keys, vault and credentials never leave the machine in any form.
SECRET_GLOBS = ("*.env", ".env*", "*provider-keys*", "*.dpapi", "vault.*", "*.vault", "*credentials*",
                "*secret*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "*token*",
                "*.kdbx", "*cookies*", "*session*.json")
WEIGHT_GLOBS = ("*.safetensors", "*.gguf", "*.ckpt", "*.pt", "*.pth", "*.onnx", "*.bin", "*.npz", "*.lora")
CACHE_PARTS = {"__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", ".mypy_cache", ".ruff_cache",
               ".cache", "dist", "build", ".tox"}
MAX_HASH_BYTES = 50 * 1024 * 1024
SKIP_WALK = CACHE_PARTS | {".git", "models", "media-runtime", "ComfyUI"}


def _git(repo: Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
        return cp.returncode, cp.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}"


def classify(path: str) -> str:
    """secret / weights / cache / file — decided by name only, before anything is opened."""
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1].lower()
    if any(fnmatch.fnmatch(name, g) for g in SECRET_GLOBS) or "/keys/" in f"/{p.lower()}":
        return "secret"
    if any(fnmatch.fnmatch(name, g) for g in WEIGHT_GLOBS):
        return "weights"
    if CACHE_PARTS & set(p.split("/")):
        return "cache"
    return "file"


def _file_row(repo: Path, rel: str, status: str) -> dict[str, Any]:
    kind = classify(rel)
    row: dict[str, Any] = {"path": rel, "status": status, "kind": kind}
    if kind != "file":
        return row                                   # counted, never opened
    full = repo / rel
    try:
        if full.is_file():
            size = full.stat().st_size
            row["size"] = size
            if size <= MAX_HASH_BYTES:
                h = hashlib.sha256()
                with full.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                row["sha256"] = h.hexdigest()
            else:
                row["sha256"] = "NOT_HASHED_TOO_LARGE"
        elif full.is_dir():
            row["kind"] = "directory"
    except OSError as exc:
        row["error"] = type(exc).__name__
    return row


def inspect_repo(repo: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(repo)}
    code, head = _git(repo, "rev-parse", "HEAD")
    if code != 0:
        out["state"] = "UNVERIFIED"
        out["error"] = "git rev-parse HEAD failed"
        return out
    out["head"] = head.strip()
    out["branch"] = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")[1].strip()
    code, up = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    out["upstream"] = up.strip() if code == 0 else None
    if out["upstream"]:
        code, counts = _git(repo, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        if code == 0 and counts.split():
            behind, ahead = counts.split()[:2]
            out["behind"], out["ahead"] = int(behind), int(ahead)
    code, remotes = _git(repo, "remote", "-v")
    out["remotes"] = sorted({line.split()[1] for line in remotes.splitlines() if line.split()[1:]})
    # Commits on local branches that no remote-tracking ref contains (the remote state is as of the
    # last fetch; run `git fetch --all` first for a current answer — recorded in `fetched_note`).
    code, unpushed = _git(repo, "log", "--branches", "--not", "--remotes", "--format=%H%x09%cs%x09%s")
    out["unpushed_commits"] = [dict(zip(("sha", "date", "subject"), line.split("\t", 2)))
                               for line in unpushed.splitlines() if line.strip()] if code == 0 else "UNVERIFIED"
    code, branches = _git(repo, "for-each-ref", "--format=%(refname:short)%09%(objectname)%09%(upstream:short)",
                          "refs/heads")
    out["local_branches"] = [dict(zip(("name", "sha", "upstream"), line.split("\t")))
                             for line in branches.splitlines() if line.strip()]
    code, stash = _git(repo, "stash", "list", "--format=%gd%x09%cs%x09%s")
    out["stashes"] = [line for line in stash.splitlines() if line.strip()] if code == 0 else "UNVERIFIED"
    code, wts = _git(repo, "worktree", "list", "--porcelain")
    out["worktrees"] = [line.split(" ", 1)[1] for line in wts.splitlines() if line.startswith("worktree ")]
    code, status = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", timeout=300)
    files: list[dict[str, Any]] = []
    if code == 0:
        entries = status.split("\0")
        i = 0
        while i < len(entries):
            item = entries[i]
            i += 1
            if len(item) < 4:
                continue
            xy, rel = item[:2], item[3:]
            if xy[0] in "RC":                         # rename/copy carries the old path next
                i += 1
            files.append(_file_row(repo, rel, xy.strip() or xy))
    else:
        out["state"] = "UNVERIFIED"
        out["error"] = "git status failed"
        return out
    out["dirty_files"] = files
    out["dirty_by_kind"] = {k: sum(1 for f in files if f["kind"] == k)
                            for k in sorted({f["kind"] for f in files})}
    gaps = []
    if files:
        gaps.append(f"{len(files)} modified/untracked")
    if isinstance(out["unpushed_commits"], list) and out["unpushed_commits"]:
        gaps.append(f"{len(out['unpushed_commits'])} unpushed commits")
    if isinstance(out["stashes"], list) and out["stashes"]:
        gaps.append(f"{len(out['stashes'])} stashes")
    if not out["remotes"]:
        gaps.append("no remote at all")
    out["gaps"] = gaps
    out["state"] = "GAPS" if gaps else "CLEAN"
    return out


def find_repos(roots: list[Path], max_depth: int = 4) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, _ in os.walk(root):
            here = Path(dirpath)
            if (here / ".git").exists():
                found.append(here)
            depth = len(here.relative_to(root).parts)
            dirnames[:] = [d for d in dirnames if d not in SKIP_WALK and depth < max_depth]
    return sorted(set(found))


def installed_builds(roots: list[Path]) -> list[dict[str, Any]]:
    """Every installed bundle that carries bcc/_build.json (the identity the backend reports)."""
    builds = []
    for root in roots:
        app = root / "app"
        if not app.is_dir():
            continue
        for bundle in sorted(app.glob("BOSSMAN-Windows-x64-*")):
            row: dict[str, Any] = {"path": str(bundle), "folder": bundle.name}
            ids = list(bundle.glob("runtime/Lib/site-packages/bcc/_build.json"))
            if ids:
                try:
                    data = json.loads(ids[0].read_text(encoding="utf-8"))
                    row["build_identity"] = {k: data.get(k) for k in ("sha", "source_sha", "built_at", "branch")
                                             if k in data} or {"keys": sorted(data)[:20]}
                except (OSError, ValueError) as exc:
                    row["build_identity"] = f"UNREADABLE: {type(exc).__name__}"
            else:
                row["build_identity"] = "NOT_FOUND"
            builds.append(row)
    return builds


def build_manifest(roots: list[Path], repos: list[Path] | None = None) -> dict[str, Any]:
    repos = repos if repos is not None else find_repos(roots)
    rows = [inspect_repo(r) for r in repos]
    states = {r["state"] for r in rows}
    if not rows or "UNVERIFIED" in states:
        verdict = "UNVERIFIED"
    elif states == {"CLEAN"}:
        verdict = "COMPLETE"
    else:
        verdict = "GAPS"
    return {"schema": SCHEMA, "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": os.environ.get("COMPUTERNAME") or (os.uname().nodename if hasattr(os, "uname") else ""),
            "roots": [str(r) for r in roots], "LOCAL_COMPLETENESS": verdict,
            "fetched_note": "unpushed = not contained in remote-tracking refs as of the last fetch; "
                            "run `git fetch --all --prune` in each repo first",
            "excluded_never_opened": {"secret_globs": list(SECRET_GLOBS), "weight_globs": list(WEIGHT_GLOBS),
                                      "cache_dirs": sorted(CACHE_PARTS)},
            "repositories": rows, "installed_builds": installed_builds(roots)}


def _default_roots() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    roots = [home / "Bossman"]
    here = Path(__file__).resolve().parents[1]
    if here not in roots:
        roots.append(here)
    return roots


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", action="append", type=Path, help="folder to scan for git working copies (repeatable)")
    ap.add_argument("--out", type=Path, help="write the JSON manifest here")
    args = ap.parse_args(argv)
    roots = args.root or _default_roots()
    manifest = build_manifest(roots)
    text = json.dumps(manifest, ensure_ascii=False, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    summary = {"LOCAL_COMPLETENESS": manifest["LOCAL_COMPLETENESS"],
               "repositories": [{"path": r["path"], "state": r["state"], "head": r.get("head", "")[:12],
                                 "gaps": r.get("gaps", [r.get("error", "")])} for r in manifest["repositories"]],
               "installed_builds": [{"folder": b["folder"], "build_identity": b["build_identity"]}
                                    for b in manifest["installed_builds"]]}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return {"COMPLETE": 0, "GAPS": 1}.get(manifest["LOCAL_COMPLETENESS"], 2)


if __name__ == "__main__":
    sys.exit(main())
