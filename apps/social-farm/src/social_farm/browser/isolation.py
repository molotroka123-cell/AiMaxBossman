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
   На Windows биты режима ничего не значат (`os.chmod` переключает только
   «только чтение», `st_mode` каталога всегда 0o777), поэтому там та же опора
   стоит на ACL: owner-only DACL ставит и читает icacls (OS-105).

Четвёртая опора — процессная — живёт в `worker.py`: воркер привязан к одному
аккаунту, и запрос с чужим идентификатором отвергается дважды, на отправке и
на приёме.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path

MARKER_NAME = ".account"
_UNSAFE = re.compile(r"[^a-zA-Z0-9_.-]+")


# ------------------------------------------------------------------ Windows ACL
# Близнец bossman_shared/evidence.py::restrict_to_owner и bcc/auth.py (тот же
# argv icacls). Повторён намеренно: у social-farm `dependencies = []`, и импорт
# пакета BOSSMAN превратил бы самостоятельный сервис в связанный модуль
# (tests/unit/test_independence.py). Отличия от близнеца: у каталога ACE
# наследуемый — (OI)(CI) — чтобы маркер, профиль браузера и загрузки внутри
# получили тот же owner-only доступ; и ACL не только ставится, но и читается.

def _on_windows() -> bool:
    return os.name == "nt"


def _owner_principal() -> str | None:
    """`ДОМЕН\\пользователь` текущего процесса или None, если его не узнать."""
    user = (os.environ.get("USERNAME") or "").strip()
    domain = (os.environ.get("USERDOMAIN") or "").strip()
    if not user:
        return None
    return f"{domain}\\{user}" if domain else user


def _console_encoding() -> str:
    # icacls печатает в пайп в OEM-кодировке консоли (cp866 у русской Windows):
    # имя пользователя кириллицей, прочитанное как UTF-8, не совпало бы с
    # владельцем, и приватный каталог был бы отвергнут.
    try:
        import ctypes  # noqa: PLC0415
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — не Windows или урезанный образ
        return "utf-8"


def _run_icacls(argv: list[str]) -> tuple[int, str]:
    """Запустить icacls. Шов для тестов: на Linux подменяется исполнителем."""
    proc = subprocess.run(  # noqa: S603 — argv, без строки для оболочки
        argv, capture_output=True, timeout=30, check=False)
    raw = proc.stdout or b""
    if proc.returncode != 0 and proc.stderr:
        raw = raw + b"\n" + proc.stderr
    return proc.returncode, raw.decode(_console_encoding(), errors="replace")


def _restrict_dir_to_owner(path: Path) -> None:
    """Owner-only наследуемый DACL на каталог. Best-effort: провал — предупреждение.

    Молча не проходит: `assert_private` потом прочитает ACL и откажет.
    """
    principal = _owner_principal()
    if principal is None:
        warnings.warn(f"не удалось определить владельца для ACL {path}: нет "
                      "USERNAME; каталог аккаунта остаётся с унаследованным ACL",
                      UserWarning, stacklevel=3)
        return
    argv = ["icacls", str(path), "/inheritance:r", "/grant:r",
            f"{principal}:(OI)(CI)F"]
    try:
        code, out = _run_icacls(argv)
    except Exception as exc:  # noqa: BLE001 — нет icacls, урезанный образ, таймаут
        warnings.warn(f"не удалось запустить icacls для {path}: {exc!r}; каталог "
                      "аккаунта остаётся доступным по унаследованному ACL",
                      UserWarning, stacklevel=3)
        return
    if code != 0:
        warnings.warn(f"icacls не сузил права {path} (код {code}): "
                      f"{out.strip()[:200]}", UserWarning, stacklevel=3)


def _parse_icacls(text: str, path: str) -> list[tuple[str, str]]:
    """Разобрать вывод `icacls <path>` в пары (субъект, права).

    Первая строка начинается с пути, продолжения выровнены пробелами; блок
    кончается пустой строкой, за ней — локализованная итоговая строка.
    """
    aces: list[tuple[str, str]] = []
    for index, line in enumerate(text.splitlines()):
        if not line.strip():
            break
        body = line[len(path):] if index == 0 and line.startswith(path) else line
        body = body.strip()
        principal, sep, rights = body.partition(":(")
        if not sep:
            continue
        aces.append((principal.strip(), "(" + rights))
    return aces


def _acl_problems(aces: list[tuple[str, str]], owner: str) -> list[str]:
    """Кто кроме владельца получает доступ. Пусто — ACL приватен.

    Разрешающая запись для любого другого субъекта (включая унаследованную
    BUILTIN\\Users) — утечка; запрещающая (DENY) доступ не расширяет.
    """
    if not aces:
        return ["ACL не прочитан: ни одной записи"]
    owner_l = owner.casefold()
    short = owner_l.rsplit("\\", 1)[-1]
    problems = []
    for principal, rights in aces:
        if "(DENY)" in rights.upper():
            continue
        p = principal.casefold()
        if p == owner_l or p == short:
            continue
        problems.append(f"{principal}:{rights}")
    return problems


def _windows_acl_problems(directory: Path) -> list[str]:
    owner = _owner_principal()
    if owner is None:
        return ["владелец процесса неизвестен (нет USERNAME): ACL не проверить"]
    try:
        code, out = _run_icacls(["icacls", str(directory)])
    except Exception as exc:  # noqa: BLE001
        return [f"icacls не запустился: {exc!r}"]
    if code != 0:
        return [f"icacls вернул код {code}: {out.strip()[:200]}"]
    return _acl_problems(_parse_icacls(out, str(directory)), owner)


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
        if _on_windows():
            # chmod выше на NTFS ACL не трогает. Закрываем ДО записи маркера,
            # чтобы маркер унаследовал owner-only запись.
            _restrict_dir_to_owner(parent)
            _restrict_dir_to_owner(directory)
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
        """Права каталога должны быть не шире объявленных.

        На Windows сверяется ACL: доступ кроме владельца — отказ; ACL, который
        не удалось прочитать, — тоже отказ (не доказано — не приватно).
        """
        directory = Path(directory)
        if _on_windows():
            problems = _windows_acl_problems(directory)
            if problems:
                raise PermissionError(
                    f"каталог контекста {directory} не закрыт ACL владельца: "
                    f"{'; '.join(problems)[:400]}. Сессия аккаунта не должна "
                    f"быть доступна другим пользователям машины")
            return
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
