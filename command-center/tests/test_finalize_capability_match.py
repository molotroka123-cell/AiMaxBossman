"""Кто может доказать чей эффект (bcc/finalize._effect_problem).

Таблица `known_kinds` существует ради одного: успех ЧУЖОЙ способности не должен
засчитываться за обязательство — клик в браузере не доказывает, что процесс жив.
Она НЕ список тех, кому разрешено завершаться.

Два реальных тупика, найденных прогоном golden-миссий, закрыты здесь навсегда:
`memory.write` пишет заметку в файл, поэтому файловое ожидание над этой заметкой —
ровно та проверка, которая нужна; а неизвестная способность (mcp/plugin/openclaw)
не есть доказанное несовпадение. В обоих случаях эффект НАСТУПАЛ, `verify_all` мог
прочитать его из мира, но финализатор отказывал — и `finalize_override` отказывал
снова, так что решение владельца не могло разблокировать задачу никогда.
"""
from __future__ import annotations

import pytest

from bcc.finalize import _effect_problem
from bcc.v2.verification import parse_expected


def _row(tool: str, source: str, *, command: str = "", **kw):
    # `args` — именно вложенный словарь: `_effectful` читает команду оттуда,
    # и плоский ключ верхнего уровня сделал бы строку «не меняющей мир», то
    # есть тест проходил бы, не проверив ничего.
    row = {"tool": tool, "source": source, "status": "executed",
           "args": {"command": command} if command else {},
           "args_hash": f"h-{tool}", "result_preview": "", "error": None}
    row.update(kw)
    return row


def _file_expectation(target="/tmp/proof.txt"):
    return parse_expected([{"kind": "file", "target": target, "expect": {"exists": True}}])


def test_memory_write_can_be_proved_by_the_note_it_actually_writes():
    """Регрессия: memory.write + файловое ожидание над созданной заметкой.

    Раньше memory отображалась в {memory, db}, пересечения с {file} не было, и
    задача уходила в waiting_approval с «no matching post-state verifier», хотя
    файл лежал на диске и был проверяем."""
    assert _effect_problem([_row("memory.write", "memory")], _file_expectation()) == ""


@pytest.mark.parametrize("tool,source", [("mcp:echo:write_note", "mcp"),
                                         ("plugin:telegram.send", "plugin"),
                                         ("openclaw.send", "openclaw")])
def test_unknown_capability_is_not_a_known_mismatch(tool, source):
    """Инструмент за MCP/плагином/OpenClaw умеет то, что умеет сервер за ним.
    Отсутствие знания — не доказательство несовпадения: решает `verify_all`,
    читая мир, а не таблица, которая про этот источник ничего не знает."""
    assert _effect_problem([_row(tool, source)], _file_expectation()) == ""


def test_a_capability_that_cannot_have_done_it_is_still_refused():
    """Обратная сторона: терминальная правка НЕ доказывает, что процесс жив.
    Это и есть смысл таблицы — она не ослаблена."""
    expected = parse_expected([{"kind": "process", "target": "1", "expect": {"running": True}}])
    problem = _effect_problem([_row("terminal.run", "terminal", command="python mutate.py",
                                    result_preview="exit_code=0")], expected)
    assert "no matching post-state verifier" in problem


def test_browser_download_can_be_proved_by_the_file_it_leaves():
    assert _effect_problem([_row("browser.click", "browser")], _file_expectation()) == ""


def test_without_a_declared_obligation_the_finalizer_claims_nothing():
    """Финализатор проверяет объявленное, а не выдумывает обязательства:
    слой нулевой/неудачной попытки — action_contract._gate, не этот файл."""
    assert _effect_problem([_row("memory.write", "memory")], []) == ""
