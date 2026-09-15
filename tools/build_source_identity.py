"""Build identity from HEAD objects and observed files, never index flags."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess


def _git(repository: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repository), *args],
                            capture_output=True, timeout=30, check=True)
    return result.stdout


def source_identity(repository: Path) -> dict:
    """Unknown Git identity is unmeasured; any extra packaged input is dirty.

    Setuptools outputs live outside these input directories. Only generated
    Python bytecode is excluded; Git ignore/exclude rules cannot hide source.
    """
    repository = repository.resolve()
    try:
        sha = _git(repository, "rev-parse", "HEAD").decode().strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise ValueError("unmeasured source SHA")
        tracked = set()
        dirty = bool(_git(repository, "status", "--porcelain", "--untracked-files=normal"))
        for entry in _git(repository, "ls-tree", "-r", "-z", sha).split(b"\0"):
            if not entry:
                continue
            header, encoded_path = entry.split(b"\t", 1)
            mode, kind, expected = header.split()
            relative = os.fsdecode(encoded_path)
            tracked.add(relative)
            path = repository / relative
            if kind != b"blob":
                dirty = True  # submodule content is not attested here
                continue
            try:
                data = os.fsencode(os.readlink(path)) if mode == b"120000" else path.read_bytes()
                actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data,
                                      usedforsecurity=False).hexdigest()
                dirty |= (actual != expected.decode() or (mode == b"120000") != path.is_symlink())
                if os.name != "nt" and mode in {b"100644", b"100755"}:
                    dirty |= bool(path.stat().st_mode & 0o111) != (mode == b"100755")
            except OSError:
                dirty = True
        inputs = [repository / name for name in
                  ("bossman_shared", "learning", "schemas", "bossman-core/bossman",
                   "command-center/bcc", "command-center/ui")]
        inputs.extend((repository / "apps").glob("*/src"))
        inputs.extend((repository / "apps").glob("*/profiles"))
        for pattern in ("*/app.manifest.yaml", "*/pyproject.toml"):
            for path in (repository / "apps").glob(pattern):
                if path.relative_to(repository).as_posix() not in tracked:
                    dirty = True
        for root in inputs:
            for path in root.rglob("*"):
                if not (path.is_file() or path.is_symlink()):
                    continue
                if path.suffix in {".pyc", ".pyo"} and "__pycache__" in path.parts:
                    continue
                if path.relative_to(repository).as_posix() not in tracked:
                    dirty = True
        return {"source_sha": sha, "source_dirty": bool(dirty)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"source_sha": None, "source_dirty": None}
