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

# Каталог приложений, которые владелец МОЖЕТ разрешить конкретной задаче
# (2026-10-10, задача 83). Ключи спецификации:
#   windows   — исполняемые файлы для запуска (резолв только в системных
#               каталогах; для упакованных приложений — ещё и алиас
#               %LOCALAPPDATA%\Microsoft\WindowsApps);
#   process   — имена образов процессов, чьи окна принимают ввод;
#   package   — для упакованного приложения: префикс каталога пакета в
#               Program Files\WindowsApps; окно принимается, только если exe
#               процесса лежит именно там (подложенный mspaint.exe — нет);
#   hosted    — окно рамкой держит ApplicationFrameHost (Калькулятор Win11).
# Расширять каталог — отдельным осознанным решением владельца и ревью кода:
# каталог — граница того, что вообще можно разрешить; разрешение на задачу —
# выбор владельца внутри этой границы. Оболочки, браузеры, мессенджеры,
# менеджеры паролей и сам Bossman сюда не попадают никогда (см. HARD_DENIED).
APP_CATALOG: dict[str, dict] = {
    "notepad": {"windows": ("notepad.exe",), "process": ("notepad.exe",), "label": "Блокнот"},
    "calculator": {"windows": ("calc.exe",), "process": ("calc.exe",), "hosted": True,
                   "label": "Калькулятор"},
    "charmap": {"windows": ("charmap.exe",), "process": ("charmap.exe",), "label": "Таблица символов"},
    "paint": {"windows": ("mspaint.exe",), "process": ("mspaint.exe",), "package": "microsoft.paint_",
              "label": "Paint"},
}

# Без явного разрешения владельца на задачу действует прежний узкий набор
# (первый сквозной путь Stage 13): Блокнот и Калькулятор.
DEFAULT_APPS: tuple[str, ...] = ("notepad", "calculator")
APP_ALLOWLIST: dict[str, dict] = {k: APP_CATALOG[k] for k in DEFAULT_APPS}

# Синонимы, которыми модель реально называет то же приложение (в т.ч. по-русски).
ALIASES: dict[str, str] = {
    "notepad.exe": "notepad", "блокнот": "notepad", "notepad++": "notepad",
    "calc": "calculator", "calc.exe": "calculator", "калькулятор": "calculator",
    "charmap.exe": "charmap", "таблица символов": "charmap", "character map": "charmap",
    "mspaint": "paint", "mspaint.exe": "paint", "паинт": "paint",
}

# Процессы, в окна которых Computer Use не шлёт ввод и которые нельзя
# разрешить ни одной задаче: оболочки и терминалы (произвольные команды),
# браузеры и мессенджеры (платежи, вход, отправка наружу — для браузера есть
# отдельный toolkit со своей политикой), UAC/учётные данные/Windows Security,
# системные консоли и сам Bossman (самоодобрение через собственный UI).
HARD_DENIED_PROCESSES = frozenset({
    "cmd.exe", "powershell.exe", "pwsh.exe", "powershell_ise.exe", "windowsterminal.exe", "wt.exe",
    "openconsole.exe", "conhost.exe", "bash.exe", "wsl.exe", "wslhost.exe", "mintty.exe",
    "git-bash.exe", "python.exe", "pythonw.exe", "py.exe", "node.exe", "mshta.exe", "wscript.exe",
    "cscript.exe", "rundll32.exe", "regedit.exe", "regedt32.exe", "mmc.exe", "taskmgr.exe",
    "msiexec.exe", "control.exe", "systemsettings.exe", "explorer.exe", "consent.exe",
    "credentialuibroker.exe", "lsass.exe", "logonui.exe", "securityhealthsystray.exe",
    "securityhealthhost.exe", "sechealthui.exe", "smartscreen.exe",
    "msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "iexplore.exe", "vivaldi.exe",
    "telegram.exe", "whatsapp.exe", "discord.exe", "slack.exe", "outlook.exe", "olk.exe",
    "thunderbird.exe", "teams.exe", "ms-teams.exe", "chatgpt.exe", "claude.exe",
    "code.exe", "cursor.exe", "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe",
})
HARD_DENIED_PREFIXES = ("bossman", "bcc")


def hard_denied_process(name: str | None) -> bool:
    """Процесс, который не может быть целью ввода ни при каком разрешении."""
    n = str(name or "").strip().lower()
    return bool(n) and (n in HARD_DENIED_PROCESSES or n.startswith(HARD_DENIED_PREFIXES))


def normalize_grant(apps) -> tuple[str, ...]:
    """Список приложений владельца для задачи -> канонические имена каталога.

    ValueError с понятной причиной на всё, что вне каталога: пути, аргументы,
    неизвестные имена, оболочки. Пустой список — тоже ошибка: «ничего не
    разрешено» выражается снятием разрешения, а не пустым грантом.
    """
    if isinstance(apps, str):
        apps = [apps]
    if not isinstance(apps, (list, tuple)) or not apps:
        raise ValueError("apps: непустой список логических имён из каталога")
    out: list[str] = []
    for raw in apps:
        name = canonical_app(raw, allowed=APP_CATALOG)
        if name is None:
            raise ValueError(f"«{str(raw)[:80]}» нет в каталоге Computer Use "
                             f"(доступны: {', '.join(sorted(APP_CATALOG))})")
        if name not in out:
            out.append(name)
    return tuple(out)


def grant_specs(apps) -> dict[str, dict]:
    """Спецификации каталога для набора разрешённых имён (None -> DEFAULT_APPS)."""
    names = DEFAULT_APPS if apps is None else tuple(apps)
    return {n: APP_CATALOG[n] for n in names if n in APP_CATALOG}

# Ни разделителей пути, ни разделителей аргументов, ни подстановок оболочки.
FORBIDDEN_CHARS = set('/\\:;&|<>"\'`$%*?!\n\r\t\0')
MAX_TARGET_LEN = 64


def canonical_app(target: str | None, allowed: dict | None = None) -> str | None:
    """Логическое имя из allowlist, либо None (отказ).

    None означает «не разрешено» для ЛЮБОЙ причины: пустое, слишком длинное,
    похожее на путь/аргументы, отсутствующее в таблице. `allowed` — набор,
    действующий для задачи (разрешение владельца); по умолчанию — APP_ALLOWLIST.
    """
    if not target:
        return None
    name = str(target).strip().lower()
    if not name or len(name) > MAX_TARGET_LEN:
        return None
    if any(ch in FORBIDDEN_CHARS for ch in name):
        return None
    name = ALIASES.get(name, name)
    table = APP_ALLOWLIST if allowed is None else allowed
    return name if name in table else None


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
    spec = APP_CATALOG.get(app)
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
        if spec.get("package"):
            # Упакованное приложение (Paint Win11) запускается через алиас
            # выполнения приложения. Подлинность окна потом проверяется по
            # каталогу пакета в WindowsApps, а не по этому пути.
            alias = Path(os.environ.get("LOCALAPPDATA") or "") / "Microsoft" / "WindowsApps" / exe
            if os.environ.get("LOCALAPPDATA") and os.path.lexists(alias):
                return alias
    return None


def packaged_exe_ok(exe_path: str | None, package_prefix: str) -> bool:
    """exe процесса лежит в каталоге пакета Program Files\\WindowsApps\\<prefix>*."""
    if not exe_path:
        return False
    p = Path(str(exe_path))
    parts = [x.lower() for x in p.parts]
    try:
        i = parts.index("windowsapps")
    except ValueError:
        return False
    return (i + 1 < len(parts) and parts[i + 1].startswith(package_prefix.lower())
            and i >= 1 and parts[i - 1] in ("program files", "program files (x86)"))
