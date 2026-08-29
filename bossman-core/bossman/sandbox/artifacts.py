"""Stage 8 — Artifact Gate.

Ненадёжные выходы песочницы не переписывают продовое состояние напрямую: они
проходят gate — нормализация пути, containment-проверка, защита от symlink- и
archive-traversal, лимит размера, хеш, карантин исполняемых, hook сканера
секретов. Всё, что не прошло, помечается quarantined с причинами.
"""
from __future__ import annotations

import hashlib
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Callable

from .. import errors
from .models import Artifact

# Исполняемые/опасные расширения — карантин (не запрещаем импорт, но метим).
EXECUTABLE_EXTS = frozenset({
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".vbs", ".js",
    ".jar", ".app", ".dmg", ".so", ".dll", ".dylib", ".bin", ".run", ".elf",
})

DEFAULT_MAX_BYTES = 200 * 1024 * 1024


class ArtifactGate:
    def __init__(
        self,
        sandbox_root: str | os.PathLike,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        secret_scanner: Callable[[bytes], list[str]] | None = None,
    ) -> None:
        # Реальный физический корень песочницы; всё должно лежать ВНУТРИ него.
        self.root = Path(sandbox_root).resolve()
        self.max_bytes = max_bytes
        self.secret_scanner = secret_scanner

    # ---- проверка одного пути ----

    def _contained_real(self, candidate: Path) -> Path:
        """Резолвит путь (включая symlink) и требует, чтобы он был ВНУТРИ root.
        Бросает ArtifactRejected при выходе за пределы (symlink escape / ..)."""
        real = candidate.resolve()
        try:
            real.relative_to(self.root)
        except ValueError as exc:
            raise errors.ArtifactRejected(
                f"path escapes sandbox root: {candidate}", extra={"resolved": str(real)}) from exc
        return real

    def inspect(self, rel_path: str) -> Artifact:
        """Проверить один файл-кандидат внутри root и вернуть Artifact с вердиктом."""
        rel = _normalize_rel(rel_path)
        candidate = self.root / rel
        # symlink escape: и сам путь, и его родитель обязаны оставаться внутри.
        real = self._contained_real(candidate)
        if candidate.is_symlink():
            # symlink разрешён только если цель внутри root (уже проверено _contained_real),
            # но помечаем карантином — это подозрительный вектор.
            reasons_sym = ("symlink",)
        else:
            reasons_sym = ()
        if not real.exists() or not real.is_file():
            raise errors.ArtifactRejected(f"not a regular file: {rel}")

        st = real.stat()
        # ХАРДЛИНК наружу (red-team): у жёсткой ссылки нет пути-цели, поэтому
        # resolve() её не ловит — файл выглядит обычным и лежит внутри root, а
        # содержимое принадлежит чужому файлу хоста. Настоящий артефакт песочницы
        # всегда имеет ровно одну ссылку.
        if st.st_nlink > 1:
            raise errors.ArtifactRejected(
                f"hard link is not a sandbox artifact (nlink={st.st_nlink}): {rel}",
                extra={"rel": rel, "nlink": st.st_nlink})
        size = st.st_size
        reasons: list[str] = list(reasons_sym)
        quarantined = bool(reasons_sym)

        if size > self.max_bytes:
            raise errors.ArtifactRejected(
                f"artifact too large: {size} > {self.max_bytes}", extra={"rel": rel})

        data = real.read_bytes()
        sha = hashlib.sha256(data).hexdigest()

        if Path(rel).suffix.lower() in EXECUTABLE_EXTS or (st.st_mode & 0o111):
            quarantined = True
            reasons.append("executable")

        if self.secret_scanner is not None:
            hits = self.secret_scanner(data)
            if hits:
                quarantined = True
                reasons.append("secret:" + ",".join(sorted(set(hits))[:5]))

        return Artifact(rel_path=rel, size=size, sha256=sha,
                        quarantined=quarantined, reasons=tuple(reasons))

    # ---- проверка архива перед распаковкой (traversal defense) ----

    def safe_archive_members(self, archive_path: str | os.PathLike) -> list[str]:
        """Вернуть список безопасных членов архива; бросить при traversal/absolute/
        symlink-члене. Ничего не распаковывает — только валидирует."""
        p = Path(archive_path)
        names: list[str]
        is_evil: Callable[[str], bool] = lambda n: n.startswith("/") or ".." in Path(n).parts

        if zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as zf:
                names = zf.namelist()
                for n in names:
                    if is_evil(n):
                        raise errors.ArtifactRejected(f"zip traversal member: {n}")
        elif tarfile.is_tarfile(p):
            with tarfile.open(p) as tf:
                names = []
                for m in tf.getmembers():
                    if is_evil(m.name):
                        raise errors.ArtifactRejected(f"tar traversal member: {m.name}")
                    if m.issym() or m.islnk():
                        # символические/жёсткие ссылки в архиве — вектор escape.
                        target = m.linkname
                        if target.startswith("/") or ".." in Path(target).parts:
                            raise errors.ArtifactRejected(f"tar link escape: {m.name} -> {target}")
                    names.append(m.name)
        else:
            raise errors.ArtifactRejected("unsupported or invalid archive")
        return names


def _normalize_rel(rel_path: str) -> str:
    """Нормализовать относительный путь; запретить абсолютные и '..'-выходы РАНО."""
    if not rel_path or rel_path.strip() == "":
        raise errors.ArtifactRejected("empty artifact path")
    p = Path(rel_path)
    if p.is_absolute():
        raise errors.ArtifactRejected(f"absolute artifact path not allowed: {rel_path}")
    parts = p.parts
    if ".." in parts:
        raise errors.ArtifactRejected(f"parent traversal in artifact path: {rel_path}")
    return str(Path(*parts)) if parts else rel_path
