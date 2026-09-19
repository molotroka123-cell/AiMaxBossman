"""Рекурсивное удаление дома владельца и системного корня отвергается, а не спрашивается.

BL-100. `rm -rf /` был в списке «нельзя никогда» с самого начала, а
`rm -rf ~/` и `rm -rf /etc/passwd` — нет: они доходили до владельца как `ask`,
то есть одно нажатие отделяло от необратимой потери. Порядок букв во флагах
защитой тоже не был: старый шаблон знал ровно `-rf`, и `rm -fr` его обходил.

Рубежа два — инструмент (`features/tools_terminal`) и рантайм
(`v2/terminal_control`), — и распознаватель у них ОДИН: два списка «что нельзя
никогда» разъехались бы, и владелец узнал бы об этом ровно один раз.

Обратная сторона проверяется здесь же: обычная работа (`rm -rf build`,
`rm -rf ~/проект/dist`) остаётся разрешённой. Запрет, который блокирует
работу, — поломка другого рода.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest

from bcc.features import tools_terminal
from bcc.features.tools_terminal import SPECS, hard_deny_reason
from bcc.tools import decide_effect
from bcc.v2.terminal_control import (DANGEROUS, TerminalManager, TerminalPolicy,
                                     unrecoverable_delete_target)

SPEC = [s for s in SPECS if s.name == "terminal.run"][0]
GRANTED = {"id": "агент", "permissions": ["terminal.run"]}
MODES = ("sandbox", "project_host", "system_admin")

UNRECOVERABLE = [
    "rm -rf ~/",
    "rm -rf ~",
    "rm -fr ~/",                       # порядок флагов не защита
    "rm -r -f $HOME",
    "rm --recursive --force ${HOME}",
    "rm -rf %USERPROFILE%",
    "rm -rf /etc/passwd",
    "rm -rf /etc",
    "rm -rf /usr",
    "rm -rf /home",
    "rm -rf /boot/grub",
    "rm -rf /",
]
ORDINARY_WORK = [
    "rm -rf build",
    "rm -rf ./node_modules",
    "rm -rf ~/проект/dist",
    "rm -rf /home/владелец/проект/build",
    "rm -rf /work",
    "rm заметка.txt",
]


def _effect(command: str, mode: str) -> str:
    return decide_effect(SPEC, {"command": command, "mode": mode}, GRANTED)[0]


@pytest.mark.parametrize("command", UNRECOVERABLE)
def test_the_tool_boundary_refuses_outright_in_every_mode(command):
    for mode in MODES:
        assert _effect(command, mode) == "deny", f"{command!r} в режиме {mode}"
    assert hard_deny_reason(command), command


@pytest.mark.parametrize("command", UNRECOVERABLE)
def test_the_runtime_boundary_refuses_the_same_commands(command):
    for mode in MODES:
        policy = TerminalPolicy(allowed_roots=[], mode=mode)
        assert unrecoverable_delete_target(command), command
        # allowed_roots пуст — решение по cwd здесь не проверяется, проверяется
        # сама команда; отдельный тест ниже доказывает отказ ДО процесса.
        assert policy.decision(command, Path("/")) == "deny"


def test_approval_does_not_unlock_it_and_no_process_is_spawned(tmp_path):
    manager = TerminalManager()
    policy = TerminalPolicy(allowed_roots=[tmp_path], mode="system_admin")

    async def run():
        with pytest.raises(PermissionError):
            await manager.start("rm -rf ~/", tmp_path, policy, approved=True)
    asyncio.run(run())
    assert manager.sessions == {}, "процесс был порождён для отвергнутой команды"


@pytest.mark.parametrize("command", ORDINARY_WORK)
def test_ordinary_cleanup_is_not_blocked(command):
    assert not unrecoverable_delete_target(command), command
    assert hard_deny_reason(command) == "", command
    for mode in MODES:
        assert _effect(command, mode) != "deny", f"{command!r} в режиме {mode}"


def test_the_old_pattern_alone_would_have_missed_these():
    """Контроль: без новой проверки прежний список ловит только голый корень."""
    missed = [c for c in UNRECOVERABLE if not any(p.search(c) for p in DANGEROUS)]
    assert "rm -rf ~/" in missed and "rm -rf /etc/passwd" in missed
    assert [c for c in UNRECOVERABLE if any(p.search(c) for p in DANGEROUS)] == ["rm -rf /"]


def test_the_recognizer_reads_flags_not_their_spelling():
    assert unrecoverable_delete_target("rm -fr /etc") == "/etc"
    assert unrecoverable_delete_target("rm -v -r -f /usr") == "/usr"
    # Без рекурсии это не тот класс: `rm /etc/passwd` не сносит дерево.
    assert unrecoverable_delete_target("rm /etc/passwd") == ""
    assert unrecoverable_delete_target("rm -f /etc/passwd") == ""


def test_both_boundaries_share_one_recognizer():
    """Дублирующий список неизбежно разъехался бы — его здесь быть не должно."""
    text = inspect.getsource(tools_terminal)
    assert "unrecoverable_delete_target" in text, "верхний рубеж не зовёт распознаватель"
    assert not re.search(r"UNRECOVERABLE_TARGETS\s*=|UNRECOVERABLE_TREES\s*=", text), (
        "у верхнего рубежа завёлся свой список целей — рубежи разъедутся")
