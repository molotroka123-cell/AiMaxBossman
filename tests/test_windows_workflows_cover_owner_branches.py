"""BL-089: ветка, из которой собирается продукт владельца, обязана попадать
под фильтр ОБОИХ заданий Windows.

Задание, которое не запускается, строже не становится — оно молчит. Молчание
неотличимо от «проверки прошли» в любом отчёте, который смотрит на красное и
зелёное, потому что отсутствие прогона не окрашено никак.

Проверка идёт настоящей семантикой шаблонов веток GitHub (`*` не переходит
через `/`, `**` переходит), а не поиском подстроки: строка `release/**`,
случайно оставшаяся в комментарии, фильтр не меняет, а подстрокой находится.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "tools" / "owner_facing_branches.json").read_text(encoding="utf-8"))


def branch_pattern_matches(pattern: str, branch: str) -> bool:
    """Семантика шаблонов веток GitHub Actions.

    Документация GitHub: `*` совпадает с любыми символами, КРОМЕ `/`;
    `**` совпадает с любыми символами, включая `/`.
    """
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


def push_branches(workflow: str) -> list[str]:
    data = yaml.safe_load((ROOT / workflow).read_text(encoding="utf-8"))
    # `on:` в YAML 1.1 разбирается как булево True — отсюда оба ключа.
    on = data.get("on", data.get(True))
    assert isinstance(on, dict), f"{workflow}: у задания нет разобранного блока on:"
    push = on.get("push")
    assert isinstance(push, dict), f"{workflow}: задание не слушает push"
    branches = push.get("branches")
    assert branches, f"{workflow}: у push нет списка веток — фильтр не ограничивает ничего"
    return list(branches)


def covers(workflow: str, branch: str) -> bool:
    return any(branch_pattern_matches(p, branch) for p in push_branches(workflow))


# --- контроль самого сопоставителя ------------------------------------------
# Без него все проверки ниже проверяли бы мою функцию, а не задания.

@pytest.mark.parametrize("pattern,branch,expected", [
    ("claude/**", "claude/foo", True),
    ("claude/**", "claude/foo/bar", True),          # ** переходит через /
    ("claude/*", "claude/foo/bar", False),          # * не переходит
    ("release/**", "release/bossman-owner", True),
    ("release/**", "releases/bossman-owner", False),  # префикс не шаблон
    ("release/**", "main", False),
    ("**", "что-угодно/вообще", True),
])
def test_the_matcher_implements_github_semantics(pattern, branch, expected):
    assert branch_pattern_matches(pattern, branch) is expected


# --- собственно контракт -----------------------------------------------------

@pytest.mark.parametrize("workflow", CONTRACT["workflows"])
@pytest.mark.parametrize("branch", CONTRACT["must_be_covered"])
def test_every_owner_facing_branch_is_covered(workflow, branch):
    assert covers(workflow, branch), (
        f"ветка {branch} не попадает под фильтр {workflow}: "
        f"{push_branches(workflow)}. Продукт из неё не собирается ВООБЩЕ, "
        f"и это выглядит как отсутствие замечаний."
    )


@pytest.mark.parametrize("workflow", CONTRACT["workflows"])
@pytest.mark.parametrize("branch", CONTRACT["must_not_be_covered"])
def test_the_filter_still_refuses_branches_it_should(workflow, branch):
    """Пара к проверке выше.

    Без неё требование «покрыто» чинилось бы заменой списка на `**`, и тогда
    каждый черновик занимал бы очередь тридцатипятиминутной сборкой.
    """
    assert not covers(workflow, branch), (
        f"ветка {branch} покрыта фильтром {workflow} — фильтр перестал фильтровать"
    )


def test_the_canonical_branch_is_itself_declared_owner_facing():
    assert CONTRACT["canonical"] in CONTRACT["must_be_covered"], (
        "канонической объявлена ветка, покрытия которой никто не требует"
    )


@pytest.mark.parametrize("workflow", CONTRACT["windows_pair"])
def test_both_windows_workflows_cover_the_same_branches(workflow):
    """Сборка архива и владельческий прогон обязаны смотреть на один набор.

    Расхождение означало бы архив, который собран, но на котором владельческие
    задачи не прогонялись, — ровно тот разрыв, ради которого оба задания и
    существуют парой.

    Требование только к этой паре. oss-integrations живёт своим списком: он
    обязан покрывать владельческие ветки, но не обязан совпадать с Windows.
    """
    reference = sorted(push_branches(CONTRACT["windows_pair"][0]))
    assert sorted(push_branches(workflow)) == reference


def test_the_windows_pair_is_a_subset_of_the_declared_workflows():
    """Иначе пару можно было бы вывести из-под проверки покрытия, оставив
    совпадение списков между собой."""
    assert set(CONTRACT["windows_pair"]) <= set(CONTRACT["workflows"])


# --- BL-088 и его класс: скрипт, который задание исполняет, обязан быть в
# --- его же фильтре путей ----------------------------------------------------

SCRIPT_CALL = re.compile(r"(?:python3?|py)\s+((?:tools|scripts)/[\w./-]+\.py)")


def path_filter_matches(pattern: str, path: str) -> tuple[bool, bool]:
    """Шаблон путей GitHub. Возвращает (совпало, это_отрицание).

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
    verdict = False
    for pattern in patterns:
        matched, negated = path_filter_matches(pattern, path)
        if matched:
            verdict = not negated
    return verdict


def push_paths(workflow: str) -> list[str]:
    data = yaml.safe_load((ROOT / workflow).read_text(encoding="utf-8"))
    on = data.get("on", data.get(True))
    return list(on["push"].get("paths") or [])


def executed_scripts(workflow: str) -> list[str]:
    """Скрипты, которые задание вызывает ПРЯМО, вида `python tools/x.py`.

    Умышленно не ловит вызовы через `-m`, переменную или из распакованного
    архива: сторож, который делает вид, что видит больше, чем видит, вреднее
    отсутствующего. Прямая форма покрывает все нынешние вызовы обоих заданий.
    """
    text = (ROOT / workflow).read_text(encoding="utf-8")
    return sorted(set(SCRIPT_CALL.findall(text)))


@pytest.mark.parametrize("workflow", CONTRACT["workflows"])
def test_every_script_the_job_runs_is_in_its_own_path_filter(workflow):
    patterns = push_paths(workflow)
    scripts = executed_scripts(workflow)
    # Пустой список законен: задание, которое зовёт только pytest, скриптов не
    # исполняет, и покрывать нечего. Что сам разбор не онемел, стережёт
    # отдельная проверка ниже — на задании, где вызовы заведомо есть.
    # Отсутствующий `paths:` означает «поднимать на ЛЮБОЙ файл», то есть
    # покрыто всё. Это законный проход, а не повод пропустить проверку:
    # пропуск здесь отключал бы сторожа тем самым изменением, которое он и
    # должен ловить.
    if not patterns:
        return
    missing = [s for s in scripts if not path_is_in_filter(patterns, s)]
    assert not missing, (
        f"{workflow} исполняет эти скрипты, но их правка задание НЕ поднимает: "
        f"{missing}. Проверка уедет в архив непроверенной."
    )


@pytest.mark.parametrize("pattern,path,expected", [
    ("tools/metrics_smoke.py", "tools/metrics_smoke.py", True),
    ("command-center/**", "command-center/tests/a.py", True),
    ("command-center/*", "command-center/tests/a.py", False),
    ("tools/*.py", "tools/a.py", True),
    ("tools/*.py", "tools/sub/a.py", False),
])
def test_the_path_matcher_implements_github_semantics(pattern, path, expected):
    matched, _ = path_filter_matches(pattern, path)
    assert matched is expected


def test_a_negation_wins_over_an_earlier_match():
    """Контроль порядка: без него исключения читались бы как совпадения."""
    assert path_is_in_filter(["command-center/**"], "command-center/tests/a.py")
    assert not path_is_in_filter(
        ["command-center/**", "!command-center/tests/**"], "command-center/tests/a.py")
    assert path_is_in_filter(
        ["command-center/**", "!command-center/tests/**", "command-center/tests/keep.py"],
        "command-center/tests/keep.py")


def test_the_script_scanner_has_not_gone_blind():
    """Канарейка на сам разбор.

    Общая проверка выше проходит и при пустом списке — это правильно для
    задания без вызовов, но означает, что сломавшийся разбор был бы зелёным.
    windows-bundle вызывает скрипты заведомо, и здесь это закреплено числом.
    """
    found = executed_scripts(".github/workflows/windows-bundle.yml")
    assert len(found) >= 8, f"разбор нашёл только {found} — похоже, он перестал видеть вызовы"
    assert "tools/build_windows_bundle.py" in found
