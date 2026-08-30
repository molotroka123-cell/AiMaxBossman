"""Stage 10 — изолированная рабочая копия репозитория.

Прод-дерево НЕ является writable-областью (non-negotiable #6): фабрика работает
в одноразовой копии. Секреты и .git-хуки в копию не переносятся — вредоносный
hook из чужого репозитория не должен исполниться на хосте.
Патч считается diff'ом копии против исходного состояния, без git-операций в
проде.
"""
from __future__ import annotations

import difflib
import hashlib
import shutil
from pathlib import Path

from .models import Patch

# Никогда не копируем в рабочую область: секреты, ключи, git-хуки, окружения.
EXCLUDE = shutil.ignore_patterns(
    ".env", ".env.*", "*.pem", "*.key", "id_rsa*", ".ssh", ".git",
    "__pycache__", "*.pyc", ".venv", "venv", "node_modules",
)


def _files_under(root: Path) -> dict[str, Path]:
    """Относительные posix-пути всех файлов дерева (стабильный порядок)."""
    return {p.relative_to(root).as_posix(): p
            for p in sorted(root.rglob("*")) if p.is_file()}


def _read_lines(path: Path | None) -> tuple[list[str], bool]:
    """Строки файла и признак «не текст». Отсутствующий файл — пустой список.

    Бинарность определяется NUL-байтом, как это делает GNU diff: \\x00\\x01\\x02
    декодируется в UTF-8 без ошибки, но текстом не является.
    """
    if path is None:
        return [], False
    try:
        raw = path.read_bytes()
    except OSError:
        return [], True
    if b"\0" in raw:
        return [], True
    try:
        return raw.decode("utf-8").splitlines(keepends=True), False
    except UnicodeDecodeError:
        return [], True


def _file_chunk(rel: str, a: Path | None, b: Path | None,
                pristine: Path, work: Path) -> str:
    """Unified-фрагмент для одного файла; пустая строка — изменений нет."""
    a_lines, a_bin = _read_lines(a)
    b_lines, b_bin = _read_lines(b)
    # `diff -ruN` показывает обе стороны реальными путями, а не /dev/null.
    from_file, to_file = f"{pristine.as_posix()}/{rel}", f"{work.as_posix()}/{rel}"
    header = f"diff -ruN -- {from_file} {to_file}\n"
    if a_bin or b_bin:
        if a is not None and b is not None and a.read_bytes() == b.read_bytes():
            return ""
        # GNU diff печатает для бинарных только эту строку, без unified-заголовка.
        return f"Binary files {from_file} and {to_file} differ\n"
    if a_lines == b_lines:
        return ""
    body = "".join(difflib.unified_diff(a_lines, b_lines,
                                        fromfile=from_file, tofile=to_file))
    if not body:
        return ""
    if not body.endswith("\n"):        # файл без завершающего перевода строки
        body += "\n"
    return header + body


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
        """Патч = отличия рабочей копии от эталона. Только текст, без git push.

        Считается на difflib, а не внешним `diff -ruN`: на чистой Windows GNU
        diff нет, и фабрика падала бы без Git Bash/WSL. Формат unified-заголовков
        сохранён, поэтому ревью/хранилище/маршруты читают патч как раньше.
        """
        base = self.root / job_id
        work, pristine = base / "work", base / "pristine"
        if not work.exists() or not pristine.exists():
            return Patch(diff="", files=(), sha256="")
        old, new = _files_under(pristine), _files_under(work)
        chunks: list[str] = []
        files: list[str] = []
        for rel in sorted(set(old) | set(new)):
            a, b = old.get(rel), new.get(rel)
            chunk = _file_chunk(rel, a, b, pristine, work)
            if not chunk:
                continue
            chunks.append(chunk)
            # Удалённые файлы в списке затронутых не числились и раньше.
            if b is not None:
                files.append(rel)
        text = "".join(chunks)
        return Patch(diff=text, files=tuple(files),
                     sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())

    def cleanup(self, job_id: str) -> None:
        base = self.root / job_id
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
