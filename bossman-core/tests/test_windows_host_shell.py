"""W6: режим SANDBOX_MODE=local на Windows звал несуществующий `sh`.

`_build_command` жёстко возвращал `["sh", "-c", cmd]`. На стоковой Windows
никакого `sh` в PATH нет, поэтому `create_subprocess_exec` падал
FileNotFoundError — то есть КАЖДЫЙ вызов `run`/`tests` у владельца, а не
«иногда». Правильная развилка уже есть в
command-center/bcc/v2/terminal_control.py::host_shell: Git-Bash `sh -lc`, если
он в PATH (он приходит с Git for Windows), иначе `%COMSPEC% /c`.

Здесь проверяется ровно выбор интерпретатора и то, что этот выбор НЕ трогает
проверки изоляции: неизвестный SANDBOX_MODE и отсутствие
BOSSMAN_UNSAFE_LOCAL_EXEC остаются отказом и под видом Windows.

Windows симулируется подменой `os.name`/`shutil.which`. Реального запуска
cmd.exe на Linux не бывает — тут доказуемо только то, какое argv собирается.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from bossman import errors
from bossman.config import settings
from bossman.toolkit import shell


class _Ctx:
    def __init__(self, wd):
        self.workdir = Path(wd)


@pytest.fixture
def local_mode(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "local", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", True, raising=False)


def _build_as_windows(cmd, ctx, *, sh_path):
    """Собрать argv так, как это сделал бы Windows-хост."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "name", "nt")
        mp.setattr(shutil, "which", lambda name: sh_path if name == "sh" else None)
        mp.setenv("COMSPEC", r"C:\Windows\system32\cmd.exe")
        return shell._build_command(cmd, ctx)


def test_posix_local_shell_unchanged(tmp_path, local_mode):
    """Linux-поведение не двигается ни на шаг."""
    assert shell._build_command("pytest -q", _Ctx(tmp_path)) == ["sh", "-c", "pytest -q"]


def test_windows_uses_git_bash_when_present(tmp_path, local_mode):
    argv = _build_as_windows("pytest -q", _Ctx(tmp_path),
                             sh_path=r"C:\Program Files\Git\usr\bin\sh.exe")
    assert argv == [r"C:\Program Files\Git\usr\bin\sh.exe", "-lc", "pytest -q"]


def test_windows_falls_back_to_comspec_without_sh(tmp_path, local_mode):
    argv = _build_as_windows("pytest -q", _Ctx(tmp_path), sh_path=None)
    assert argv == [r"C:\Windows\system32\cmd.exe", "/c", "pytest -q"]
    # именно отказ от `sh` — то, ради чего всё затевалось
    assert argv[0] != "sh"


def test_windows_command_stays_one_argv_element(tmp_path, local_mode):
    """Команда — ОДИН элемент argv и на Windows: второго парсера не появляется."""
    for sh_path in (r"C:\Program Files\Git\usr\bin\sh.exe", None):
        argv = _build_as_windows("pytest -q; rm -rf /", _Ctx(tmp_path), sh_path=sh_path)
        assert argv[-1] == "pytest -q; rm -rf /"
        assert len(argv) == 3


def test_windows_does_not_weaken_unsafe_local_gate(tmp_path, monkeypatch):
    """Изоляция не обменивается на переносимость: opt-in обязателен и на Windows."""
    monkeypatch.setattr(settings, "sandbox_mode", "local", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", False, raising=False)
    with pytest.raises(errors.PolicyDenied):
        _build_as_windows("id", _Ctx(tmp_path), sh_path=None)


def test_windows_unknown_mode_still_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "windows", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", True, raising=False)
    with pytest.raises(errors.PolicyDenied):
        _build_as_windows("id", _Ctx(tmp_path), sh_path=None)


def test_windows_docker_mode_untouched(tmp_path, monkeypatch):
    """Контейнерный путь не зависит от хостового шелла: внутри всегда `sh -lc`."""
    monkeypatch.setattr(settings, "sandbox_mode", "docker", raising=False)
    argv = _build_as_windows("pytest -q", _Ctx(tmp_path), sh_path=None)
    assert argv[0] == "docker" and argv[-2] == "-lc"
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
