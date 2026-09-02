"""Deterministic context compiler helpers (intelligence plan step 2, ideas F5.1/F5.10/F2.8).

No model involved. Two operations:
  map   — repo map (files + top-level symbols via ast) cached per HEAD sha under
          .bossman-cache/repo_map-<sha>.json; discovery paid once per commit.
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
import subprocess
import sys
from pathlib import Path

CACHE_DIR = ".bossman-cache"
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", CACHE_DIR}


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)          # грубая оценка, детерминированная


def head_sha(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "nogit"


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
    """Карта репозитория, кэшированная по sha: второй вызов на том же sha читает файл."""
    root = Path(root).resolve()
    sha = sha or head_sha(root)
    cdir = Path(cache_dir) if cache_dir else root / CACHE_DIR
    cache = cdir / f"repo_map-{sha}.json"
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
    data = {"sha": sha, "root": str(root), "files": files,
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
        data = repo_map(Path(ns.root), ns.sha)
        print(json.dumps({"sha": data["sha"], "files": len(data["files"]), "total_tokens": data["total_tokens"], "cache": data["cache"]}))
    else:
        data = failing_test_slice(Path(ns.root), Path(ns.test), depth=ns.depth)
        print(json.dumps(data, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
