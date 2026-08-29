"""Stage 10 — изолированная рабочая копия репозитория.

Прод-дерево НЕ является writable-областью (non-negotiable #6): фабрика работает
в одноразовой копии. Секреты и .git-хуки в копию не переносятся — вредоносный
hook из чужого репозитория не должен исполниться на хосте.
Патч считается diff'ом копии против исходного состояния, без git-операций в
проде.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from .models import Patch

# Никогда не копируем в рабочую область: секреты, ключи, git-хуки, окружения.
EXCLUDE = shutil.ignore_patterns(
    ".env", ".env.*", "*.pem", "*.key", "id_rsa*", ".ssh", ".git",
    "__pycache__", "*.pyc", ".venv", "venv", "node_modules",
)


class WorkspaceManager:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def prepare(self, job_id: str, repo_path: str | Path) -> Path:
        """Сделать одноразовую копию и снимок-эталон для последующего diff."""
        src = Path(repo_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"repo path does not exist: {src}")
        base = self.root / job_id
        work = base / "work"
        pristine = base / "pristine"
        for d in (work, pristine):
            if d.exists():
                shutil.rmtree(d)
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, d, symlinks=False, ignore=EXCLUDE)
        return work

    def diff(self, job_id: str) -> Patch:
        """Патч = отличия рабочей копии от эталона. Только текст, без git push."""
        base = self.root / job_id
        work, pristine = base / "work", base / "pristine"
        if not work.exists() or not pristine.exists():
            return Patch(diff="", files=(), sha256="")
        proc = subprocess.run(       # argv-массив, без shell
            ["diff", "-ruN", "--", str(pristine), str(work)],
            capture_output=True, text=True)
        text = proc.stdout
        files = tuple(sorted({
            line.split("\t")[0].split(" ")[1].replace(str(pristine) + "/", "")
            for line in text.splitlines()
            if line.startswith("+++ ") and "/dev/null" not in line
        }))
        return Patch(diff=text, files=files,
                     sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())

    def cleanup(self, job_id: str) -> None:
        base = self.root / job_id
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
