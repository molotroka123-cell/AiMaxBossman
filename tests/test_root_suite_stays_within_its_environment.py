"""Корневой набор обязан идти в том окружении, которое ему ставят.

Как этот сторож появился. Я положил в `tests/` сценарий, который поднимает
настоящую базу через `bcc.db`. Локально всё зелено — в контейнере установлено
всё. В CI корневое задание ставит РОВНО `pytest pytest-timeout psutil httpx
pyyaml` и намеренно остаётся без сервисов и секретов, поэтому там вышло три
отказа: два «async def не поддерживается» и `ModuleNotFoundError: sqlalchemy`.
Тест переехал в набор Command Center, где эти зависимости есть.

Сторож проверяет не мой частный случай, а его класс: ни один модуль в `tests/`
не имеет права тянуть рантайм Command Center — ни сам, ни через соседний
скрипт, который он загружает. Второе и было моей ошибкой: в самом тесте
`sqlalchemy` не упоминался ни разу.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

# Чего нет в корневом задании CI (.github/workflows/root-ci.yml).
ABSENT = {"sqlalchemy", "aiosqlite", "fastapi", "uvicorn", "starlette",
          "bcc", "playwright", "pytest_asyncio"}

# Скрипт, который тест загружает по пути: ровно так дефект и приехал.
LOADS = re.compile(r"spec_from_file_location\(\s*[^,]+,\s*(.+?)\)", re.S)
SCRIPT_PATH = re.compile(r'"([^"]+\.py)"')


def _module_level_imports(source: str, where: str) -> set[str]:
    """Только верхний уровень: импорт внутри функции окружению не мешает."""
    found: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover — синтаксис ловит другой гейт
        return found
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _scripts_loaded_by(source: str) -> list[Path]:
    """Пути вида `REPO / "scripts" / "имя.py"`, которые тест исполняет сам."""
    paths: list[Path] = []
    for call in LOADS.findall(source):
        parts = SCRIPT_PATH.findall(call)
        if not parts:
            continue
        candidate = REPO.joinpath(*parts) if len(parts) > 1 else REPO / parts[0]
        for root in (REPO, REPO / "scripts", REPO / "tools"):
            guess = root / Path(*parts) if len(parts) > 1 else root / parts[0]
            if guess.is_file():
                candidate = guess
                break
        if candidate.is_file():
            paths.append(candidate)
    return paths


def test_no_root_test_needs_the_command_center_runtime():
    offenders: list[str] = []
    for module in sorted(TESTS.glob("test_*.py")):
        source = module.read_text(encoding="utf-8")
        direct = _module_level_imports(source, module.name) & ABSENT
        if direct:
            offenders.append(f"{module.name}: импортирует {', '.join(sorted(direct))}")
        for script in _scripts_loaded_by(source):
            indirect = _module_level_imports(
                script.read_text(encoding="utf-8"), script.name) & ABSENT
            if indirect:
                offenders.append(
                    f"{module.name}: загружает {script.relative_to(REPO)}, "
                    f"а тот импортирует {', '.join(sorted(indirect))}")
    assert not offenders, (
        "эти модули не пройдут в корневом задании CI — им место в "
        "command-center/tests:\n  " + "\n  ".join(offenders))


def test_the_guard_actually_catches_a_module_that_would_fail(tmp_path):
    """Негативный контроль: сторож, который нечему поймать, ничего не сторожит."""
    script = tmp_path / "needs_the_runtime.py"
    script.write_text("import sqlalchemy as sa\n", encoding="utf-8")
    assert _module_level_imports(script.read_text(encoding="utf-8"), "x") & ABSENT

    inside_a_function = "def go():\n    import sqlalchemy as sa\n    return sa\n"
    assert not (_module_level_imports(inside_a_function, "x") & ABSENT), \
        "импорт внутри функции окружению не мешает и ловиться не должен"


def test_the_guard_knows_what_root_ci_installs():
    """Список ABSENT должен опираться на то, что CI реально ставит."""
    workflow = (REPO / ".github" / "workflows" / "root-ci.yml").read_text(encoding="utf-8")
    install = next(line for line in workflow.splitlines()
                   if "pip install" in line and "pytest" in line)
    for present in ("pytest", "pytest-timeout", "psutil", "httpx", "pyyaml"):
        assert present in install, (present, install)
    for absent in ("sqlalchemy", "pytest-asyncio", "playwright"):
        assert absent not in install, (
            f"{absent} появился в корневом задании — обновите ABSENT")
