"""PHASE 7 — контракт переносимости на Windows, закреплённый тестами.

Эти тесты пишутся на Linux и обязаны падать на СЛОМАННОМ поведении на ЛЮБОЙ
платформе. Поэтому ни один из них не спрашивает `sys.platform`: там, где дефект
проявляется только на Windows, воспроизводится не платформа, а её ПРАВИЛО —
кодировка по умолчанию, отличная от utf-8 (дочерний интерпретатор с локалью C),
и запрет удалять файл, помеченный «только для чтения» (эмуляция WinError 5).

Что закреплено:

W-01  `toolkit/fileintel._resolve` сдерживал путь сравнением ПРЕФИКСА СТРОКИ.
      Сосед с общим началом имени (`…/coder-secrets` при workdir `…/coder`)
      считался «внутри» — на любой платформе. На Windows к этому добавлялось
      второе: сравнение путей там регистронезависимо, а `str.startswith` — нет.
      files.py этот же дефект уже лечил и описал; fileintel остался с копией.

W-02  Текстовые файлы открывались БЕЗ явной кодировки, то есть в кодировке
      локали. На Windows это ANSI-кодовая страница (cp1251/cp1252), а не utf-8:
      лог команды или сводка этапа с кириллицей либо роняли запись
      UnicodeEncodeError, либо ложились на диск в кодировке, которой читатель
      (`fs.read`, utf-8) прочитать уже не может. Тот же класс, что и ключ улик
      без O_BINARY: «поток по умолчанию» на Windows означает не то, что на POSIX.

W-03  `apprentice/teacher_sandbox` помечает acceptance-тесты `chmod 0o444`, а
      затем сносил каталог `shutil.rmtree(..., ignore_errors=True)`. На POSIX
      удаление разрешает каталог, и это работает. На Windows право на удаление
      проверяется у самого файла: 0o444 → FILE_ATTRIBUTE_READONLY → PermissionError,
      а `ignore_errors=True` его глотает. Гарантия модуля («каталог уничтожается
      после вызова») тихо переставала выполняться, и песочница с содержимым
      бандла оставалась в %TEMP%.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from bossman.toolkit import ToolContext, files, fileintel

CORE_ROOT = Path(fileintel.__file__).resolve().parents[2]


# --------------------------------------------------------------------- W-01

def _ctx(workdir: Path) -> ToolContext:
    return ToolContext(agent="test", workdir=workdir,
                       journal=workdir / "journal.md", notes_dir=workdir / "notes")


def test_fileintel_refuses_sibling_directory_with_a_shared_name_prefix(tmp_path: Path) -> None:
    """`…/coder-secrets` — НЕ «внутри» `…/coder`, хотя строка и начинается так же."""
    workdir = tmp_path / "coder"
    workdir.mkdir()
    sibling = tmp_path / "coder-secrets"
    sibling.mkdir()
    (sibling / "vault.txt").write_text("SECRET", encoding="utf-8")

    with pytest.raises(PermissionError):
        fileintel._resolve(_ctx(workdir), "../coder-secrets/vault.txt")


def test_fileintel_still_allows_legitimate_paths_inside_the_workdir(tmp_path: Path) -> None:
    """Сдерживание не должно превратиться в отказ всему подряд."""
    workdir = tmp_path / "coder"
    (workdir / "notes").mkdir(parents=True)
    (workdir / "notes" / "a.md").write_text("ok", encoding="utf-8")

    assert fileintel._resolve(_ctx(workdir), "notes/a.md") == (workdir / "notes" / "a.md").resolve()
    assert fileintel._resolve(_ctx(workdir), ".") == workdir.resolve()


def test_fileintel_uses_the_one_containment_helper_and_not_a_copy() -> None:
    """Копия расходится с оригиналом — этот дефект так и появился."""
    assert fileintel._contains is files._contains


# --------------------------------------------------------------------- W-02

# Модули, чей текст заведомо приходит от пользователя/команды и содержит
# кириллицу: здесь кодировка локали недопустима ни в одном вызове.
_AUDITED = (
    "bossman/toolkit/shell.py",
    "bossman/toolkit/analysis.py",
    "bossman/toolkit/files.py",
    "bossman/toolkit/fileintel.py",
    "bossman/projects/planner.py",
    "bossman/projects/runner.py",
    "bossman/projects/plan.py",
    "bossman/cli.py",
    "bossman/profiles/store.py",
    "bossman/resource_brain/probe.py",
    "bossman/apprentice/teacher_sandbox.py",
    "bossman/apprentice/live_workspace.py",
    "bossman_v3/memory/journal.py",
)

_TEXT_APIS = {"read_text", "write_text", "open"}


def _mode_of(call: ast.Call, *, method: bool) -> str | None:
    """Строка режима вызова open, либо None если её не видно статически."""
    idx = 0 if method else 1               # path.open(mode) против open(file, mode)
    if len(call.args) > idx:
        node = call.args[idx]
        return node.value if isinstance(node, ast.Constant) else None
    for kw in call.keywords:
        if kw.arg == "mode":
            return kw.value.value if isinstance(kw.value, ast.Constant) else None
    return "r"


def _locale_encoded_calls(source: str) -> list[tuple[int, str]]:
    """Вызовы текстового файлового API без явной кодировки (позиционной или именованной)."""
    bad: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name, method = func.id, False
        elif isinstance(func, ast.Attribute):
            name, method = func.attr, True
            # os.open — дескриптор, а не текстовый поток; у него своя болезнь
            # (O_BINARY), закрытая в bossman_shared/evidence.py.
            if isinstance(func.value, ast.Name) and func.value.id in {"os", "tarfile", "zipfile", "io"}:
                continue
        else:
            continue
        if name not in _TEXT_APIS:
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        if name == "open":
            mode = _mode_of(node, method=method)
            if mode is None or "b" in mode:
                continue
        elif name == "read_text" and node.args:
            continue                        # read_text("utf-8") — кодировка позиционная
        elif name == "write_text" and len(node.args) > 1:
            continue                        # write_text(data, "utf-8")
        bad.append((node.lineno, name))
    return bad


@pytest.mark.parametrize("rel", _AUDITED)
def test_audited_modules_never_open_text_with_the_locale_encoding(rel: str) -> None:
    """Явная кодировка — не стиль, а контракт: на Windows локаль не utf-8."""
    path = CORE_ROOT / rel
    found = _locale_encoded_calls(path.read_text(encoding="utf-8"))
    assert not found, (
        f"{rel}: текстовый файл открывается в кодировке локали в строках "
        f"{[line for line, _ in found]}; на Windows это ANSI-кодовая страница"
    )


# Реальный прогон боевого кода в дочернем интерпретаторе, чья кодировка по
# умолчанию НЕ utf-8: на Linux локаль C даёт ascii, на Windows — ANSI-страницу.
# Кириллица не влезает ни в ту, ни в другую, то есть правило Windows здесь
# воспроизводится, а не имитируется.
_CHILD = textwrap.dedent(
    """
    import asyncio, locale, sys
    from pathlib import Path

    assert locale.getpreferredencoding(False).lower().replace("-", "") not in ("utf8", "cp65001"), \\
        "дочерний интерпретатор всё ещё в utf-8: проверка ничего не проверяет"

    from bossman.toolkit import ToolContext, files, shell

    CYRILLIC = "Сводка этапа: тест пройден ✓"

    async def fake_exec(cmd, ctx, timeout=600):
        return 0, CYRILLIC

    shell._exec = fake_exec
    workdir = Path(sys.argv[1])
    ctx = ToolContext(agent="t", workdir=workdir, journal=workdir / "j.md", notes_dir=workdir / "n")

    asyncio.run(shell.run({"cmd": "echo"}, ctx))
    asyncio.run(shell.tests({"cmd": "pytest"}, ctx))

    logs = sorted((workdir / "assets" / "logs").glob("*.txt"))
    assert len(logs) == 2, logs
    for log in logs:
        # Читаем ровно так, как читает fs.read: utf-8.
        assert log.read_text(encoding="utf-8") == CYRILLIC, log
    print("OK")
    """
)


def test_shell_logs_round_trip_when_the_default_locale_is_not_utf8(tmp_path: Path) -> None:
    """W-02, боевой путь: run/tests пишут лог, fs.read читает его как utf-8."""
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    workdir = tmp_path / "work"
    workdir.mkdir()

    env = dict(os.environ)
    env.update(LC_ALL="C", LANG="C", LC_CTYPE="C",
               PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", PYTHONIOENCODING="utf-8")
    env.pop("PYTHONWARNDEFAULTENCODING", None)

    done = subprocess.run([sys.executable, str(script), str(workdir)],
                          capture_output=True, text=True, env=env, timeout=180)
    assert done.returncode == 0, f"stdout={done.stdout}\nstderr={done.stderr}"
    assert "OK" in done.stdout


# --------------------------------------------------------------------- W-03

def test_hermetic_workspace_is_destroyed_even_with_read_only_files(monkeypatch, tmp_path: Path) -> None:
    """Правило Windows: удаление проверяет права САМОГО файла, а не каталога.

    Эмулируем его на любой платформе: `os.unlink` отказывает файлу без бита
    записи владельца — ровно как Windows отказывает при FILE_ATTRIBUTE_READONLY.
    До исправления `ignore_errors=True` глотал этот отказ, и песочница
    оставалась на диске; после — флаг снимается и каталог сносится.
    """
    from bossman.apprentice import teacher_sandbox

    real_unlink = os.unlink
    refused: list[str] = []

    def windows_like_unlink(path, *, dir_fd=None):
        try:
            mode = os.stat(path, dir_fd=dir_fd, follow_symlinks=False).st_mode
        except OSError:
            mode = 0o600
        if not mode & 0o200:
            refused.append(str(path))
            raise PermissionError(13, "Access is denied", str(path))
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", windows_like_unlink)

    bundle = {"files": {"src/mod.py": "value = 1\n"},
              "constraints": ["не трогать тесты"],
              "failing_test": "tests/test_mod.py::test_value"}
    with teacher_sandbox.hermetic_workspace(
            bundle, acceptance_readonly={"tests/test_mod.py": "assert value == 2\n"}) as ws:
        root = Path(ws.path)
        assert (root / "tests" / "test_mod.py").is_file()
        assert not os.stat(root / "tests" / "test_mod.py").st_mode & 0o200, \
            "acceptance-тест обязан быть только для чтения — иначе проверять нечего"

    assert refused, "эмуляция Windows не сработала: отказа на удаление не было"
    assert not root.exists(), (
        f"песочница пережила выход из контекста: {root}; на Windows это молчаливая "
        f"утечка содержимого бандла в %TEMP%"
    )
