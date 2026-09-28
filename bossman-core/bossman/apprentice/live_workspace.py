"""Scoped real-filesystem workspace used by the untrusted teacher bridge."""
from __future__ import annotations

import codecs
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .proc_tree import run_tree


class WorkspaceRefused(RuntimeError): pass


#: File modes a reviewed patch may not create: a symlink (120000) points anywhere,
#: a gitlink (160000) is another repository.
_FORBIDDEN_MODES = re.compile(r"^(?:new file mode|new mode|deleted file mode|old mode)\s+(?:120000|160000)\b", re.M)


def _unquote(value: str) -> str:
    """git C-quotes a path with special characters: ``"a/\\320\\230.py"``."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        raw = codecs.escape_decode(value[1:-1].encode("latin-1"))[0]
        return raw.decode("utf-8", "replace")
    return value


def _strip_side(value: str) -> str:
    value = _unquote(value.split("\t", 1)[0])
    return value[2:] if value.startswith(("a/", "b/")) else value


def diff_target_paths(diff: str) -> list[str]:
    """EVERY path a unified/git diff touches: new AND old sides.

    Reading only ``+++`` lines missed deletions (``+++ /dev/null``), renames and
    copies (the source side) and binary patches (no ``---``/``+++`` at all) —
    each of which ``git apply`` carries out. Order is first appearance."""
    paths: list[str] = []

    def add(value: str) -> None:
        if value and value != "/dev/null" and value not in paths:
            paths.append(value)

    for line in diff.splitlines():
        if line.startswith(("+++ ", "--- ")):
            raw = line[4:].split("\t", 1)[0].strip()
            if raw != "/dev/null":
                add(_strip_side(raw))
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            add(_unquote(line.split(" ", 2)[2]))
        elif line.startswith("diff --git "):
            rest = line[len("diff --git "):].strip()
            if rest.startswith('"'):
                end = rest.find('"', 1)
                while end > 0 and rest[end - 1] == "\\":
                    end = rest.find('"', end + 1)
                if end > 0:
                    add(_strip_side(rest[:end + 1]))
                    add(_strip_side(rest[end + 1:].strip()))
            elif rest.startswith("a/") and len(rest) % 2 == 1:
                half = (len(rest) - 1) // 2                     # "a/P b/P": both sides equal
                left, right = rest[:half], rest[half + 1:]
                if right.startswith("b/") and left[2:] == right[2:]:
                    add(left[2:])
    return paths


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
        self._touched: dict[str, bytes | None] = {}

    def _relative(self, path: str) -> Path:
        raw = path.replace("\\", "/")
        if raw.startswith(("/", "\\")) or ".." in raw.split("/"): raise WorkspaceRefused(f"path traversal refused: {path!r}")
        # Windows aliases of one file: "x.py." and "x.py " open x.py, "x.py:s" is an
        # alternate stream of it. A name the filesystem rewrites cannot be compared.
        if any(part != part.rstrip(". ") or ":" in part for part in raw.split("/") if part not in ("", ".")):
            raise WorkspaceRefused(f"ambiguous path name refused: {path!r}")
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

    def _is_protected(self, rel: Path) -> bool:
        """Case-insensitive AND by real location: on Windows "tests/TEST_calc.py" is
        the protected "tests/test_calc.py"."""
        folded = {p.casefold() for p in self.protected_paths}
        if rel.as_posix().casefold() in folded:
            return True
        try:
            real = (self.root / rel).resolve(strict=False).relative_to(self.root).as_posix().casefold()
        except (OSError, ValueError):
            return True
        return real in folded

    def _remember(self, rel: Path) -> None:
        """Pre-change bytes of every path a change touches (None = did not exist), so
        restore() also undoes changes the allowed-path snapshot does not cover."""
        key = rel.as_posix()
        if key not in self._touched:
            dest = self.root / rel
            self._touched[key] = dest.read_bytes() if dest.is_file() and not dest.is_symlink() else None

    def read(self, path: str) -> str:
        return self._path(path).read_text(encoding="utf-8")

    def write(self, path: str, text: str, *, restore: bool = False) -> None:
        rel = self._relative(path)
        # Defense in depth (PASS 2): protected paths (acceptance tests, security
        # policy) are immutable at the workspace layer too, not only in
        # PatchVerifier.  Only AcceptanceBinding.restore may rewrite them, and
        # only to their bound contents (restore=True).
        if self._is_protected(rel) and not restore:
            raise WorkspaceRefused(f"protected path is immutable: {path!r}")
        if not restore:
            self._remember(rel)
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="")

    def snapshot(self) -> dict[str, bytes | None]:
        self._touched = {}                      # a new restore point starts a new change journal
        return self._scan()

    def _scan(self) -> dict[str, bytes | None]:
        result: dict[str, bytes | None] = {}
        for prefix in self.allowed_paths:
            base = self.root / prefix
            if base.is_file(): result[prefix] = base.read_bytes()
            elif base.exists():
                for item in base.rglob("*"):
                    if item.is_file() and not item.is_symlink(): result[item.relative_to(self.root).as_posix()] = item.read_bytes()
        return result

    def restore(self, token: dict[str, bytes | None]) -> None:
        current = self._scan()
        for rel in current:
            if rel not in token: (self.root / rel).unlink(missing_ok=True)
        for rel, contents in token.items():
            if contents is not None:
                dest = self.root / rel; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(contents)
        # Every path a change touched since snapshot(), including ones the allowed
        # snapshot does not cover.
        for rel, contents in self._touched.items():
            if rel in token:
                continue
            dest = self.root / rel
            if contents is None:
                dest.unlink(missing_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(contents)
        self._touched = {}

    @staticmethod
    def _diff_paths(diff: str) -> list[str]:
        return diff_target_paths(diff)

    def _git_paths(self, diff: bytes) -> list[str]:
        """The paths git itself will write, as git parses the patch (quoting,
        traditional diffs). The old side of a rename is not listed: the header parse adds it."""
        run = subprocess.run(["git", "apply", "--numstat", "-z", "-"], input=diff, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=self.root, timeout=self.timeout_s, shell=False, check=False)
        if run.returncode:
            raise WorkspaceRefused(f"unified diff rejected: {run.stderr.decode('utf-8', 'replace')[-500:]}")
        out: list[str] = []
        fields = run.stdout.decode("utf-8", "replace").split("\0")
        i = 0
        while i < len(fields):
            parts = fields[i].split("\t", 2)
            if len(parts) == 3 and parts[2]:
                out.append(parts[2]); i += 1
            elif len(parts) == 3:                # rename form: added\tdeleted\t\0old\0new
                out += [x for x in fields[i + 1:i + 3] if x]; i += 3
            else:
                i += 1
        return out

    def apply(self, patch: dict[str, str] | str) -> None:
        if isinstance(patch, dict):
            for path, contents in patch.items(): self.write(path, contents)
            return
        paths = self._diff_paths(patch)
        if not paths: raise WorkspaceRefused("unified diff has no target paths")
        if _FORBIDDEN_MODES.search(patch):
            raise WorkspaceRefused("unified diff creates a symlink or gitlink")
        # bytes: a text-mode stdin on Windows rewrites "\n" to "\r\n" and breaks the patch
        data = patch.encode("utf-8")
        unlisted = [p for p in self._git_paths(data) if p not in paths]
        if unlisted:
            raise WorkspaceRefused(f"unified diff touches paths its headers do not name: {unlisted}")
        rels = []
        for path in paths:                       # old AND new side of every file: delete/rename/copy/binary
            rel = self._relative(path)
            if self._is_protected(rel): raise WorkspaceRefused(f"protected path is immutable: {path}")
            rels.append(rel)
        for rel in rels:
            self._remember(rel)
        run = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], input=data,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.root, timeout=self.timeout_s,
                             shell=False, check=False)
        if run.returncode: raise WorkspaceRefused(f"unified diff rejected: {run.stderr.decode('utf-8', 'replace')[-500:]}")

    def run_tests(self, ids: tuple[str, ...]) -> tuple[bool, list[str], str]:
        if not ids: return False, ["no test ids"], "independent verification needs test ids"
        # run_tree: the timeout reaps the whole tree. subprocess.run(timeout=) killed only
        # pytest, and a grandchild still holding the pipe kept communicate() blocked.
        # UTF-8/replace: the host locale (cp1251) cannot decode every byte a test prints.
        run = run_tree([*self.test_command, *ids], cwd=self.root, text=True, encoding="utf-8", errors="replace",
                       shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout_s)
        output = (run.stdout or "")[-8000:]
        if run.timed_out:
            return False, list(ids), f"timed out after {self.timeout_s}s\n{output}"[-8000:]
        return run.returncode == 0, list(ids) if run.returncode else [], output
