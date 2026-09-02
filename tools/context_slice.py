"""Deterministic context compiler helpers (intelligence plan step 2, ideas F5.1/F5.10/F2.8).

No model involved. Two operations:
  map   — repo map (files + top-level symbols via ast) cached per WORKTREE
          FINGERPRINT under .bossman-cache/repo_map-<fingerprint>.json. The
          fingerprint covers HEAD sha + `git status --porcelain=v1 -z` + the
          content hash of every modified/added/untracked file + symlink targets,
          so a dirty worktree never returns a stale map (keying by sha alone
          did). `--sha` is an explicit override key, validated, never passed
          through to git.
  slice — failing-test-first slice: the test file plus the repository modules it
          imports transitively (depth-bounded), as a manifest of file@sha256 with
          token estimates. Stable ordering (policies/invariants would be prepended
          by the caller as the cache-stable prefix; slices are the volatile tail).

Usage:
  python tools/context_slice.py map <app_root> [--sha <sha>]
  python tools/context_slice.py slice <app_root> <test_file> [--depth 2]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CACHE_DIR = ".bossman-cache"
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", CACHE_DIR}
# Explicit cache key: a git sha (7..40 hex) or a plain label (letters/digits/_/-, ≤64).
# Anything else (paths, spaces, option-looking strings) is rejected — the key names a
# file under the cache dir and must never reach a subprocess as an argument.
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$|^[A-Za-z0-9_-]{1,64}$")
NOGIT = "nogit"


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)          # грубая оценка, детерминированная


def validate_sha(value: str) -> str:
    """Cache-key validation: 7..40 hex git sha or a short label; ValueError otherwise."""
    if not isinstance(value, str) or not SHA_RE.match(value):
        raise ValueError(f"invalid sha/cache key: {value!r} "
                         f"(expected 7-40 hex chars or [A-Za-z0-9_-]{{1,64}})")
    return value


def _git(root: Path, *args: str) -> bytes | None:
    """Run git in `root`; None when git is missing or `root` is not a repository."""
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True,
                              timeout=60).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def head_sha(root: Path) -> str:
    """HEAD sha validated to 40 hex chars; `nogit` when not a repository (or no commits)."""
    out = _git(Path(root), "rev-parse", "--verify", "HEAD")
    sha = (out or b"").decode("utf-8", "replace").strip()
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else NOGIT


def _skipped(rel: str) -> bool:
    return any(part in SKIP_DIRS for part in rel.split("/"))


def _status_entries(root: Path) -> list[tuple[str, str]] | None:
    """Parsed `git status --porcelain=v1 -z --untracked-files=all` for the subtree
    `root`, as (XY, path-relative-to-root). None when `root` is not a git repo.
    Rename/copy entries carry the *new* path; the old path is folded into XY so the
    rename itself is part of the fingerprint."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    toplevel = Path(top.decode("utf-8", "replace").strip()).resolve()
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".")
    if out is None:
        return None
    tokens = out.split(b"\0")
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if len(tok) < 4:
            continue
        xy = tok[:2].decode("utf-8", "replace")
        path = tok[3:].decode("utf-8", "replace")
        if xy[0] in "RC" or xy[1] in "RC":         # `R  new\0old\0`
            old = tokens[i].decode("utf-8", "replace") if i < len(tokens) else ""
            i += 1
            xy = f"{xy}<{old}"
        # porcelain paths are relative to the repository toplevel
        try:
            rel = (toplevel / path).resolve().relative_to(root).as_posix()
        except ValueError:
            try:
                rel = (toplevel / path).relative_to(root).as_posix()
            except ValueError:
                continue                             # outside the requested subtree
        if _skipped(rel):
            continue                                 # cache dir/pycache must not self-invalidate
        entries.append((xy, rel))
    return sorted(entries, key=lambda e: e[1])


def _hash_path(h: "hashlib._Hash", root: Path, rel: str) -> None:
    full = root / rel
    if full.is_symlink():
        h.update(b"link\0" + rel.encode("utf-8") + b"\0" + os.readlink(full).encode("utf-8") + b"\0")
    elif full.is_file():
        h.update(b"file\0" + rel.encode("utf-8") + b"\0" + hashlib.sha256(full.read_bytes()).digest())
    else:
        h.update(b"gone\0" + rel.encode("utf-8") + b"\0")   # deleted (or directory)


def worktree_fingerprint(root: Path) -> str:
    """Deterministic key for the *current* worktree state under `root`.

    sha256 over: HEAD sha; the parsed `git status --porcelain=v1 -z` entries
    (XY + path, renames with the old path); for every modified/added/untracked
    tracked-or-not file the content sha256 (path + bytes), symlinks by target,
    deletions by path. Same HEAD + any worktree change ⇒ different fingerprint.
    Outside git: HEAD is `nogit` and every non-skipped file under `root` is hashed.
    """
    root = Path(root).resolve()
    h = hashlib.sha256()
    head = head_sha(root)
    h.update(b"head\0" + head.encode("utf-8") + b"\0")
    entries = _status_entries(root)
    if entries is None:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if _skipped(rel) or p.is_dir() and not p.is_symlink():
                continue
            _hash_path(h, root, rel)
        return f"{NOGIT}-{h.hexdigest()[:32]}"
    for xy, rel in entries:
        h.update(b"status\0" + xy.encode("utf-8") + b"\0" + rel.encode("utf-8") + b"\0")
        _hash_path(h, root, rel)
    return h.hexdigest()[:40]


def _py_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def _symbols(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
    return out


def repo_map(root: Path, sha: str | None = None, *, cache_dir: Path | None = None) -> dict:
    """Карта репозитория, кэшированная по отпечатку worktree (HEAD + dirty-состояние):
    повторный вызов без изменений читает файл; любая правка/добавление/удаление/
    переименование/символическая ссылка под root даёт новый отпечаток и пересборку.
    `sha` — только явный ключ-переопределение (валидируется, в git не передаётся)."""
    root = Path(root).resolve()
    key = validate_sha(sha) if sha is not None else worktree_fingerprint(root)
    cdir = Path(cache_dir) if cache_dir else root / CACHE_DIR
    cache = cdir / f"repo_map-{key}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        data["cache"] = "hit"
        return data
    files = {}
    for p in _py_files(root):
        rel = p.relative_to(root).as_posix()
        text = p.read_text(encoding="utf-8", errors="replace")
        files[rel] = {"symbols": _symbols(p), "tokens": _tokens(text),
                      "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}
    data = {"sha": head_sha(root), "fingerprint": key, "root": str(root), "files": files,
            "total_tokens": sum(f["tokens"] for f in files.values()), "cache": "miss"}
    cdir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return data


def _imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.append(node.module)
            mods += [f"{node.module}.{a.name}" for a in node.names]
    return mods


def _resolve(root: Path, module: str) -> Path | None:
    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        cand = root.joinpath(*parts[:i])
        if cand.with_suffix(".py").is_file():
            return cand.with_suffix(".py")
        if (cand / "__init__.py").is_file():
            return cand / "__init__.py"
    return None


def failing_test_slice(root: Path, test_file: Path, *, depth: int = 2) -> dict:
    """Тест + модули репозитория, которые он импортирует (транзитивно до depth)."""
    root = Path(root).resolve()
    test_file = Path(test_file).resolve()
    seen: dict[Path, int] = {test_file: 0}
    frontier = [test_file]
    for level in range(1, depth + 1):
        nxt = []
        for f in frontier:
            for mod in _imports(f):
                p = _resolve(root, mod)
                if p is not None and p not in seen:
                    seen[p] = level
                    nxt.append(p)
        frontier = nxt
    manifest = []
    for p in sorted(seen, key=lambda x: (seen[x], x.as_posix())):
        text = p.read_text(encoding="utf-8", errors="replace")
        manifest.append({"path": p.relative_to(root).as_posix() if p.is_relative_to(root) else str(p),
                         "level": seen[p], "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
                         "tokens": _tokens(text), "reason": "failing-test" if seen[p] == 0 else f"import-depth-{seen[p]}"})
    return {"root": str(root), "test": test_file.relative_to(root).as_posix() if test_file.is_relative_to(root) else str(test_file),
            "depth": depth, "files": manifest, "total_tokens": sum(m["tokens"] for m in manifest)}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="context_slice")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map"); m.add_argument("root"); m.add_argument("--sha")
    s = sub.add_parser("slice"); s.add_argument("root"); s.add_argument("test"); s.add_argument("--depth", type=int, default=2)
    ns = ap.parse_args(argv)
    if ns.cmd == "map":
        try:
            data = repo_map(Path(ns.root), ns.sha)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps({"sha": data["sha"], "fingerprint": data["fingerprint"], "files": len(data["files"]),
                          "total_tokens": data["total_tokens"], "cache": data["cache"]}))
    else:
        data = failing_test_slice(Path(ns.root), Path(ns.test), depth=ns.depth)
        print(json.dumps(data, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
