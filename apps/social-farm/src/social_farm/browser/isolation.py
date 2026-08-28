"""Изоляция браузерных контекстов по аккаунтам.

Главный инвариант всего потока: **сессия аккаунта A не может оказаться в
контексте аккаунта B.** Не «не должна» — не может. Ошибка здесь означает
публикацию от чужого имени, и откатить её нельзя.

Изоляция стоит на трёх опорах, и ни одна из них не полагается на дисциплину
вызывающего кода:

1. **Каталог на аккаунт.** Путь выводится из идентификатора аккаунта функцией,
   у которой нет другого входа. Передать «свой» путь в обход невозможно.
2. **Маркер владельца в каталоге.** В каталоге лежит файл `.account` с
   идентификатором. Перед открытием контекста он читается и сверяется. Каталог,
   переименованный или подставленный руками, будет отвергнут.
3. **Права 0700.** Каталог с сессией, читаемый другими пользователями машины, —
   это чужой вход в аккаунт. Права проверяются, а не только выставляются.

Четвёртая опора — процессная — живёт в `worker.py`: воркер привязан к одному
аккаунту, и запрос с чужим идентификатором отвергается дважды, на отправке и
на приёме.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

MARKER_NAME = ".account"
_UNSAFE = re.compile(r"[^a-zA-Z0-9_.-]+")


class CrossAccountViolation(RuntimeError):
    """Действие одного аккаунта попыталось попасть в контекст другого.

    Отдельный тип, а не `ValueError`: это не ошибка ввода, а сработавшая
    граница безопасности, и обрабатывать её как обычную ошибку нельзя.
    """

    def __init__(self, expected: str, actual: str, detail: str = "") -> None:
        tail = f": {detail}" if detail else ""
        super().__init__(
            f"контекст аккаунта {actual!r} не может обслуживать аккаунт "
            f"{expected!r}{tail}")
        self.expected = expected
        self.actual = actual


def account_slug(account_id: str) -> str:
    """Имя каталога по идентификатору аккаунта.

    Читаемая часть — чтобы человек понимал, что он видит в файловой системе;
    хеш — чтобы два разных идентификатора, отличающиеся только запрещёнными в
    имени файла символами, не получили один каталог.
    """
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("идентификатор аккаунта пуст")
    readable = _UNSAFE.sub("-", account_id)[:40].strip("-") or "account"
    digest = hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:16]
    return f"{readable}.{digest}"


@dataclass(frozen=True, slots=True)
class AccountContextRoot:
    """Корень каталогов браузерных контекстов."""

    root: Path
    mode: int = 0o700

    def path_for(self, account_id: str) -> Path:
        """Путь к каталогу контекста. Единственный способ его получить."""
        return Path(self.root) / account_slug(account_id)

    def prepare(self, account_id: str) -> Path:
        """Создать каталог с правильными правами и поставить маркер владельца."""
        directory = self.path_for(account_id)
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, self.mode)
        parent = Path(self.root)
        # Корень тоже не должен быть открыт наружу: каталог 0755 с сессиями
        # внутри выдаёт как минимум список аккаунтов.
        os.chmod(parent, self.mode)
        marker = directory / MARKER_NAME
        if marker.exists():
            self.assert_owned(account_id, directory)
        else:
            marker.write_text(str(account_id), encoding="utf-8")
            os.chmod(marker, 0o600)
        return directory

    def owner_of(self, directory: Path) -> str | None:
        marker = Path(directory) / MARKER_NAME
        if not marker.exists():
            return None
        return marker.read_text(encoding="utf-8").strip()

    def assert_owned(self, account_id: str, directory: Path) -> Path:
        """Проверить, что каталог принадлежит именно этому аккаунту.

        Вызывается перед КАЖДЫМ открытием контекста, а не один раз при
        создании: каталог могли подменить между запусками.
        """
        directory = Path(directory)
        expected = self.path_for(account_id)
        if directory.resolve() != expected.resolve():
            raise CrossAccountViolation(
                str(account_id), self.owner_of(directory) or "неизвестен",
                f"путь {directory} не является каталогом аккаунта")
        owner = self.owner_of(directory)
        if owner is None:
            raise CrossAccountViolation(
                str(account_id), "неизвестен",
                f"в каталоге {directory} нет маркера владельца {MARKER_NAME}")
        if owner != str(account_id):
            raise CrossAccountViolation(str(account_id), owner,
                                        f"маркер каталога {directory}")
        return directory

    def assert_private(self, directory: Path) -> None:
        """Права каталога должны быть не шире объявленных."""
        directory = Path(directory)
        actual = stat.S_IMODE(directory.stat().st_mode)
        if actual & ~self.mode:
            raise PermissionError(
                f"каталог контекста {directory} имеет права {oct(actual)}, "
                f"допустимо не шире {oct(self.mode)}: сессия аккаунта не должна "
                f"быть доступна другим пользователям машины")

    def known_accounts(self) -> list[str]:
        """Кому принадлежат существующие каталоги. Только для диагностики."""
        root = Path(self.root)
        if not root.exists():
            return []
        found = []
        for child in sorted(root.iterdir()):
            if child.is_dir():
                owner = self.owner_of(child)
                if owner:
                    found.append(owner)
        return found


__all__ = ["MARKER_NAME", "AccountContextRoot", "CrossAccountViolation", "account_slug"]
