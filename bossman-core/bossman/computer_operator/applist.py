"""Allowlist приложений, которые Computer Operator имеет право запускать.

Deny-by-default. Планировщик (модель) присылает ЛОГИЧЕСКОЕ имя — "notepad", —
а не путь и не командную строку. Конкретный исполняемый файл выбирает эта
таблица, поэтому произвольный путь, аргументы и подстановки оболочки из вывода
модели до процесса не доходят: APP_LAUNCH не может стать remote shell.

Путь резолвится ТОЛЬКО внутри системных каталогов Windows — подложенный в PATH
или в текущем каталоге notepad.exe не подменяет системный.
"""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

# Логическое имя -> исполняемые файлы по платформам. Список намеренно узкий:
# это первый сквозной путь Stage 13, а не каталог приложений. Расширять —
# отдельным осознанным решением владельца, а не «за компанию».
APP_ALLOWLIST: dict[str, dict[str, tuple[str, ...]]] = {
    "notepad": {"windows": ("notepad.exe",)},
    "calculator": {"windows": ("calc.exe",)},
}

# Синонимы, которыми модель реально называет то же приложение (в т.ч. по-русски).
ALIASES: dict[str, str] = {
    "notepad.exe": "notepad", "блокнот": "notepad", "notepad++": "notepad",
    "calc": "calculator", "calc.exe": "calculator", "калькулятор": "calculator",
}

# Ни разделителей пути, ни разделителей аргументов, ни подстановок оболочки.
FORBIDDEN_CHARS = set('/\\:;&|<>"\'`$%*?!\n\r\t\0')
MAX_TARGET_LEN = 64


def canonical_app(target: str | None) -> str | None:
    """Логическое имя из allowlist, либо None (отказ).

    None означает «не разрешено» для ЛЮБОЙ причины: пустое, слишком длинное,
    похожее на путь/аргументы, отсутствующее в таблице.
    """
    if not target:
        return None
    name = str(target).strip().lower()
    if not name or len(name) > MAX_TARGET_LEN:
        return None
    if any(ch in FORBIDDEN_CHARS for ch in name):
        return None
    name = ALIASES.get(name, name)
    return name if name in APP_ALLOWLIST else None


def _system_roots() -> tuple[Path, ...]:
    root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    return (root / "System32", root / "SysWOW64", root)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_executable(app: str, *, system: str | None = None) -> Path | None:
    """Абсолютный путь к разрешённому приложению или None, если его тут нет.

    Резолв ограничен системными каталогами: PATH-hijack («свой» notepad.exe в
    рабочем каталоге) не проходит.
    """
    spec = APP_ALLOWLIST.get(app)
    if not spec:
        return None
    exes = spec.get((system or platform.system()).lower(), ())
    roots = _system_roots()
    for exe in exes:
        for root in roots:
            candidate = root / exe
            if candidate.is_file():
                return candidate
        found = shutil.which(exe)
        if found:
            resolved = Path(found).resolve()
            if any(_within(resolved, root) for root in roots):
                return resolved
    return None
