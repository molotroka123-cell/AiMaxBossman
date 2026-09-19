"""Семантика фильтров GitHub Actions — ОДНА на все проверки.

Здесь лежит ровно то, чем GitHub решает, поднимется задание или нет. Модуль
заведён потому, что второй экземпляр такого сопоставителя — это второй ответ
на вопрос «поднимется ли задание», и разъехаться они могут молча: одна
проверка скажет «покрыто», другая промолчит, а владелец узнает об этом на
своей машине. Тот же довод, что у единого распознавателя необратимого
удаления в BL-100: два списка «что нельзя» разъезжаются, один — нет.

Документация GitHub, которую модуль воспроизводит:

* в шаблоне ВЕТКИ `*` совпадает с любыми символами, КРОМЕ `/`, а `**` — с
  любыми, включая `/`;
* отсутствие `branches` у события означает «поднимать на любой ветке», а не
  «не поднимать»;
* `branches-ignore` — отрицание: совпадение запрещает прогон.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["branch_pattern_matches", "push_trigger", "reachable_on_push",
           "path_filter_matches", "path_is_in_filter"]


def branch_pattern_matches(pattern: str, branch: str) -> bool:
    """Совпадает ли шаблон ветки GitHub Actions с именем ветки."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(out), branch) is not None


def push_trigger(workflow: dict[str, Any]) -> dict[str, Any] | None:
    """Блок `on.push` задания или None, если задание push не слушает.

    `on:` в YAML 1.1 разбирается как булево True — отсюда оба ключа. Короткая
    форма (`on: [push, pull_request]`) даёт пустой блок: событие слушается,
    фильтров нет.
    """
    on = workflow.get("on", workflow.get(True))
    if isinstance(on, list):
        return {} if "push" in on else None
    if not isinstance(on, dict) or "push" not in on:
        return None
    push = on["push"]
    return push if isinstance(push, dict) else {}


def reachable_on_push(workflow: dict[str, Any], branch: str) -> bool:
    """Поднимется ли задание пушем в эту ветку — по ВЕТКЕ, без учёта путей.

    Фильтр путей здесь намеренно не смотрится: он отвечает на другой вопрос
    («этот ли коммит»), а этот — на вопрос «эта ли ветка вообще». Смешивать их
    нельзя: задание, недостижимое по ветке, не поднимет ни один коммит.
    """
    push = push_trigger(workflow)
    if push is None:
        return False
    ignored = push.get("branches-ignore")
    if ignored and any(branch_pattern_matches(p, branch) for p in ignored):
        return False
    branches = push.get("branches")
    if branches is None:
        return True
    return any(branch_pattern_matches(p, branch) for p in branches)


def path_filter_matches(pattern: str, path: str) -> tuple[bool, bool]:
    """Шаблон ПУТЕЙ GitHub. Возвращает (совпало, это_отрицание).

    В фильтрах путей `*` тоже не переходит через `/`, а ведущий `!` задаёт
    исключение; выигрывает ПОСЛЕДНИЙ совпавший шаблон.
    """
    negated = pattern.startswith("!")
    body = pattern[1:] if negated else pattern
    out: list[str] = []
    i = 0
    while i < len(body):
        if body.startswith("**", i):
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    return re.fullmatch("".join(out), path) is not None, negated


def path_is_in_filter(patterns: list[str], path: str) -> bool:
    """Поднимет ли правка этого файла задание. Выигрывает ПОСЛЕДНИЙ совпавший."""
    verdict = False
    for pattern in patterns:
        matched, negated = path_filter_matches(pattern, path)
        if matched:
            verdict = not negated
    return verdict
