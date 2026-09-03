"""Ярлык BOSSMAN на рабочем столе: один двойной клик — и Command Center открыт.

Ставит ярлык (Windows: .lnk на рабочем столе и в меню «Пуск»; Linux: .desktop;
macOS: небольшой .command) на существующий лаунчер ``python -m bcc.desktop``.
Нового рантайма, Electron и установщика здесь нет: это тонкий слой запуска
поверх уже проверенного окна bcc.desktop.

Всё генерируется как текст (тестируемо на любой ОС), запись и вызов оболочки —
только argv, без shell-строк.
"""
from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

APP_NAME = "BOSSMAN"
APP_COMMENT = "BOSSMAN AI Command Center — локальный центр управления"
ENTRY_FILE = "bossman"  # имя файла ярлыка без расширения

ICON_DIR = Path(__file__).resolve().parent.parent / "ui" / "icons"
ICON_WINDOWS = ICON_DIR / "bossman.ico"
ICON_PNG = ICON_DIR / "icon-512.png"


@dataclass(frozen=True)
class LauncherSpec:
    """Чем и откуда запускать окно BOSSMAN."""

    executable: str
    args: tuple[str, ...] = ()
    workdir: str = ""
    icon: str = ""
    name: str = APP_NAME
    comment: str = APP_COMMENT
    extra_env: dict[str, str] = field(default_factory=dict)
    console: bool = True

    @property
    def argv(self) -> list[str]:
        return [self.executable, *self.args]


def _windowless_python(executable: str | None = None, *, windows: bool | None = None) -> str:
    """pythonw.exe: приложение без окна консоли (режим --no-console).

    ``windows`` задаётся явно в тестах: подменять глобальный ``os.name`` нельзя —
    от него зависит выбор класса в ``pathlib``.
    """
    exe = executable or sys.executable
    if not (os.name == "nt" if windows is None else windows):
        return exe
    p = Path(exe)
    if p.name.lower() == "python.exe":
        cand = p.with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return exe


def _console_python(executable: str | None = None, *, windows: bool | None = None) -> str:
    """python.exe: рядом с окном приложения открывается консоль.

    Владельцу нужен токен доступа при первом входе, а pythonw консоли не даёт,
    поэтому под ярлыком по умолчанию идёт консольный интерпретатор.
    """
    exe = executable or sys.executable
    if not (os.name == "nt" if windows is None else windows):
        return exe
    p = Path(exe)
    if p.name.lower() == "pythonw.exe":
        cand = p.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return exe


def build_spec(*, executable: str | None = None, workdir: str | Path | None = None,
               host: str | None = None, port: int | None = None,
               icon: str | Path | None = None, console: bool = True) -> LauncherSpec:
    """Спецификация ярлыка: тот же вход, что у `bcc-desktop`, без секретов в аргументах.

    ``console=True`` (по умолчанию): вместе с окном приложения открывается
    консоль, в которой напечатан токен доступа. Секрета в самом ярлыке нет —
    он печатается процессом при запуске.
    """
    args: list[str] = ["-m", "bcc.desktop"]
    if host:
        args += ["--host", str(host)]
    if port:
        args += ["--port", str(port)]
    if icon is None:
        icon = ICON_WINDOWS if os.name == "nt" else ICON_PNG
    if not console:
        args.append("--no-show-token")
    return LauncherSpec(
        executable=(_console_python if console else _windowless_python)(executable),
        args=tuple(args),
        workdir=str(Path(workdir) if workdir else Path.cwd()),
        icon=str(icon),
        console=console,
    )


# ---------------------------------------------------------------- Windows

def _ps_quote(value: str) -> str:
    """PowerShell single-quoted string: удваиваем одинарные кавычки."""
    return "'" + str(value).replace("'", "''") + "'"


def powershell_shortcut_script(spec: LauncherSpec, lnk_paths: Sequence[str | Path]) -> str:
    """Скрипт, создающий .lnk через WScript.Shell (аргументы уже экранированы)."""
    arguments = " ".join(f'"{a}"' if " " in a else a for a in spec.args)
    lines = ["$ws = New-Object -ComObject WScript.Shell"]
    for i, lnk in enumerate(lnk_paths):
        v = f"$s{i}"
        lines += [
            f"{v} = $ws.CreateShortcut({_ps_quote(lnk)})",
            f"{v}.TargetPath = {_ps_quote(spec.executable)}",
            f"{v}.Arguments = {_ps_quote(arguments)}",
            f"{v}.WorkingDirectory = {_ps_quote(spec.workdir)}",
            f"{v}.Description = {_ps_quote(spec.comment)}",
            f"{v}.WindowStyle = 1",
        ]
        if spec.icon:
            lines.append(f"{v}.IconLocation = {_ps_quote(spec.icon)}")
        lines.append(f"{v}.Save()")
    return "\n".join(lines)


def windows_targets(home: Path) -> list[Path]:
    """Рабочий стол + меню «Пуск» владельца (без записи в систему целиком)."""
    desktop = Path(os.environ.get("USERPROFILE", home)) / "Desktop"
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    start = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return [desktop / f"{APP_NAME}.lnk", start / f"{APP_NAME}.lnk"]


# ---------------------------------------------------------------- Linux

def desktop_entry(spec: LauncherSpec) -> str:
    """Содержимое .desktop (freedesktop): Exec с полным путём, свой значок."""
    exec_line = " ".join(f'"{a}"' if " " in a else a for a in spec.argv)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={spec.name}\n"
        f"Comment={spec.comment}\n"
        f"Exec={exec_line}\n"
        f"Path={spec.workdir}\n"
        f"Icon={spec.icon}\n"
        f"Terminal={'true' if spec.console else 'false'}\n"
        "Categories=Development;Utility;\n"
        "StartupNotify=true\n"
        f"StartupWMClass={APP_NAME}\n"
    )


def linux_targets(home: Path) -> list[Path]:
    return [
        home / ".local" / "share" / "applications" / f"{ENTRY_FILE}.desktop",
        home / "Desktop" / f"{ENTRY_FILE}.desktop",
    ]


# ---------------------------------------------------------------- macOS

def macos_command(spec: LauncherSpec) -> str:
    quoted = " ".join(f'"{a}"' for a in spec.argv)
    return f'#!/bin/sh\ncd "{spec.workdir}" || exit 1\nexec {quoted}\n'


def macos_targets(home: Path) -> list[Path]:
    return [home / "Desktop" / f"{APP_NAME}.command"]


# ---------------------------------------------------------------- установка

def _write(path: Path, text: str, *, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def install(spec: LauncherSpec | None = None, *, home: Path | None = None,
            system: str | None = None,
            runner: Callable[[list[str]], int] | None = None) -> list[Path]:
    """Создаёт ярлыки. Возвращает пути. runner/home/system инъектируются в тестах."""
    spec = spec or build_spec()
    home = Path(home or Path.home())
    system = (system or platform.system()).lower()

    if system.startswith("win"):
        targets = windows_targets(home)
        for t in targets:
            t.parent.mkdir(parents=True, exist_ok=True)
        script = powershell_shortcut_script(spec, targets)
        run = runner or (lambda argv: subprocess.run(argv, check=False).returncode)  # noqa: S603
        code = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
        if code != 0:
            raise RuntimeError(f"не удалось создать ярлык (powershell код {code})")
        return targets

    if system == "darwin":
        return [_write(t, macos_command(spec), executable=True) for t in macos_targets(home)]

    # Linux и совместимые: значок кладём в тему пользователя, чтобы его нашли меню
    icon_target = home / ".local" / "share" / "icons" / "hicolor" / "512x512" / "apps" / f"{ENTRY_FILE}.png"
    if ICON_PNG.exists():
        icon_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ICON_PNG, icon_target)
        spec = LauncherSpec(spec.executable, spec.args, spec.workdir, str(icon_target), spec.name, spec.comment)
    entry = desktop_entry(spec)
    out: list[Path] = []
    for t in linux_targets(home):
        if t.parent.exists() or t.parent.name == "applications":
            out.append(_write(t, entry, executable=True))
    return out


def uninstall(*, home: Path | None = None, system: str | None = None) -> list[Path]:
    """Убирает созданные ярлыки (значок в теме пользователя тоже)."""
    home = Path(home or Path.home())
    system = (system or platform.system()).lower()
    if system.startswith("win"):
        targets = windows_targets(home)
    elif system == "darwin":
        targets = macos_targets(home)
    else:
        targets = linux_targets(home) + [
            home / ".local" / "share" / "icons" / "hicolor" / "512x512" / "apps" / f"{ENTRY_FILE}.png"]
    removed = []
    for t in targets:
        if t.exists():
            t.unlink()
            removed.append(t)
    return removed
