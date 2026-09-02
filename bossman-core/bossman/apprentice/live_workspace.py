"""Scoped real-filesystem workspace used by the untrusted teacher bridge."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable


class WorkspaceRefused(RuntimeError): pass


class LiveWorkspace:
    """A small, scoped adapter for :class:`PatchVerifier`.

    It never executes teacher-provided commands.  Diffs are applied by git only
    after paths and symlink boundaries are checked; tests use a fixed argv.
    """
    def __init__(self, root: str | Path, *, allowed_paths: Iterable[str], protected_paths: Iterable[str] = (),
                 test_command: tuple[str, ...] | None = None, timeout_s: int = 120) -> None:
        self.root = Path(root).resolve()
        self.allowed_paths = tuple(p.replace("\\", "/").strip("/") for p in allowed_paths)
        self.protected_paths = frozenset(p.replace("\\", "/").strip("/") for p in protected_paths)
        self.test_command = test_command or (sys.executable, "-m", "pytest", "-q")
        self.timeout_s = timeout_s
        if not self.root.is_dir() or not self.allowed_paths: raise WorkspaceRefused("existing root and allowed paths are required")

    def _relative(self, path: str) -> Path:
        raw = path.replace("\\", "/")
        if raw.startswith(("/", "\\")) or ".." in raw.split("/"): raise WorkspaceRefused(f"path traversal refused: {path!r}")
        relative = Path(raw)
        candidate = (self.root / relative).resolve(strict=False)
        try: candidate.relative_to(self.root)
        except ValueError as exc: raise WorkspaceRefused(f"path escapes workspace: {path!r}") from exc
        norm = relative.as_posix().lstrip("./")
        if not any(norm == p or norm.startswith(p + "/") for p in self.allowed_paths):
            raise WorkspaceRefused(f"path outside allowed scope: {path!r}")
        # Existing symlinks must resolve inside root; a symlinked leaf is never writable.
        probe = self.root
        for part in relative.parts:
            probe /= part
            if probe.is_symlink(): raise WorkspaceRefused(f"symlink path refused: {path!r}")
        return relative

    def _path(self, path: str) -> Path: return self.root / self._relative(path)

    def read(self, path: str) -> str:
        return self._path(path).read_text(encoding="utf-8")

    def write(self, path: str, text: str, *, restore: bool = False) -> None:
        rel = self._relative(path)
        # Defense in depth (PASS 2): protected paths (acceptance tests, security
        # policy) are immutable at the workspace layer too, not only in
        # PatchVerifier.  Only AcceptanceBinding.restore may rewrite them, and
        # only to their bound contents (restore=True).
        if rel.as_posix() in self.protected_paths and not restore:
            raise WorkspaceRefused(f"protected path is immutable: {path!r}")
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="")

    def snapshot(self) -> dict[str, bytes | None]:
        result: dict[str, bytes | None] = {}
        for prefix in self.allowed_paths:
            base = self.root / prefix
            if base.is_file(): result[prefix] = base.read_bytes()
            elif base.exists():
                for item in base.rglob("*"):
                    if item.is_file() and not item.is_symlink(): result[item.relative_to(self.root).as_posix()] = item.read_bytes()
        return result

    def restore(self, token: dict[str, bytes | None]) -> None:
        current = self.snapshot()
        for rel in current:
            if rel not in token: (self.root / rel).unlink(missing_ok=True)
        for rel, contents in token.items():
            if contents is not None:
                dest = self.root / rel; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(contents)

    @staticmethod
    def _diff_paths(diff: str) -> list[str]:
        paths: list[str] = []
        for line in diff.splitlines():
            if line.startswith("+++ "):
                value = line[4:].split("\t", 1)[0].strip()
                if value != "/dev/null": paths.append(value[2:] if value.startswith("b/") else value)
        return paths

    def apply(self, patch: dict[str, str] | str) -> None:
        if isinstance(patch, dict):
            for path, contents in patch.items(): self.write(path, contents)
            return
        paths = self._diff_paths(patch)
        if not paths: raise WorkspaceRefused("unified diff has no target paths")
        for path in paths:
            rel = self._relative(path)
            if rel.as_posix() in self.protected_paths: raise WorkspaceRefused(f"protected path is immutable: {path}")
        run = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], input=patch, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.root, timeout=self.timeout_s,
                             shell=False, check=False)
        if run.returncode: raise WorkspaceRefused(f"unified diff rejected: {run.stderr[-500:]}")

    def run_tests(self, ids: tuple[str, ...]) -> tuple[bool, list[str], str]:
        if not ids: return False, ["no test ids"], "independent verification needs test ids"
        run = subprocess.run([*self.test_command, *ids], cwd=self.root, text=True, shell=False,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout_s, check=False)
        output = run.stdout[-8000:]
        return run.returncode == 0, list(ids) if run.returncode else [], output
