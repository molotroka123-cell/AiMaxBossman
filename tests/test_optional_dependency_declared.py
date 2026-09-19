"""Если продукт советует «pip install X» — X обязан быть объявлен в проекте.

Найдено ИЗМЕРЕНИЕМ, а не чтением: сравнение родного разбора файлов с MarkItDown
(раздел 24 требует измеренной выгоды) дало по PDF честный отказ

    ParseUnavailable: PDF-парсер недоступен: установите pypdf (pip install pypdf)

Отказ честный — но выполнить его через объявленный способ установки было
нельзя: `pypdf` не встречался ни в `dependencies`, ни в одном `extra`, ни в
одном requirements-файле дерева. То есть возможность «разбор PDF» существовала
в коде, объявлялась в `detect_kind`, имела запись в `_PARSERS` и тест — и при
этом ни один документированный `pip install` её не включал.

Тест той возможности был написан как «или отказ, или разбор», поэтому оставался
зелёным на машине, где PDF не работает вовсе. Тест, который проходит при обоих
исходах, не гейтит ничего; настоящий гейт — здесь, и он про ОБЪЯВЛЕНИЕ.

Тот же класс, что OBSERVER-DEPS-001 (наблюдение на Windows молча падало, потому
что pywinauto не был объявлен нигде), только в другом модуле. Поэтому проверка
написана обобщённо: она не знает слова «pypdf», а сканирует всё дерево и ловит
любую будущую подсказку того же вида.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_PARTS = {"build", "dist", "node_modules", ".git", "__pycache__",
              ".venv", "venv", "site-packages", "handoffs", "tests"}

# Подсказка владельцу: «pip install <имя>». Ровно то, что он скопирует в консоль.
HINT = re.compile(r"pip install\s+(?:-U\s+|--upgrade\s+)?([A-Za-z0-9][A-Za-z0-9._-]*)")

# Имена, которые в подсказке означают не пакет, а плейсхолдер или сам продукт.
NOT_A_THIRD_PARTY = {"bossman", "bossman-core", "bossman-shared", "bossman_core",
                     "bossman_shared", "pip", "wheel", "setuptools", "."}


def _sources() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.py")
                  if not (SKIP_PARTS & set(p.parts)) and not p.name.startswith("test_"))


def _declared() -> set[str]:
    """Всё, что проект объявляет как устанавливаемое: pyproject + requirements."""
    names: set[str] = set()

    def add(req: str) -> None:
        name = re.split(r"[\s<>=!~;\[]", req.strip(), maxsplit=1)[0]
        if name:
            names.add(name.lower().replace("_", "-"))

    for path in ROOT.rglob("pyproject.toml"):
        if SKIP_PARTS & set(path.parts):
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        project = data.get("project", {})
        for req in project.get("dependencies", []) or []:
            add(req)
        for group in (project.get("optional-dependencies", {}) or {}).values():
            for req in group or []:
                add(req)

    for path in ROOT.rglob("requirements*.txt"):
        if SKIP_PARTS & set(path.parts):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                add(line)
    return names


def _hints() -> list[tuple[Path, int, str]]:
    found = []
    for path in _sources():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "pip install" not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name in HINT.findall(line):
                key = name.lower().replace("_", "-")
                if key not in NOT_A_THIRD_PARTY:
                    found.append((path, lineno, key))
    return found


def test_every_install_hint_the_product_gives_is_declared_somewhere():
    """Подсказка, которую нельзя выполнить объявленным способом, — это не
    подсказка, а тупик: владелец делает ровно то, что написано, и возможность
    всё равно не появляется в собранном дистрибутиве."""
    declared = _declared()
    orphans = [(str(p.relative_to(ROOT)), n, name)
               for p, n, name in _hints() if name not in declared]
    assert not orphans, (
        "продукт советует установить пакет, который нигде не объявлен "
        f"(ни dependencies, ни extras, ни requirements): {orphans}; "
        f"объявлено сейчас: {sorted(declared)}")


def test_the_scan_actually_sees_the_hint_it_is_supposed_to_gate():
    """Обратный контроль. Без него тест выше проходил бы и на пустом списке —
    достаточно было бы сломать регулярное выражение или обход дерева, и «зелено»
    означало бы «ничего не найдено», а не «всё объявлено».
    """
    names = {name for _, _, name in _hints()}
    assert names, "ни одной подсказки не найдено — сканер сломан, а не дерево чисто"
    assert "pypdf" in names, (
        "исходная подсказка из file_intel._parse_pdf должна попадать в выборку; "
        f"найдено: {sorted(names)}")


def test_the_pdf_capability_has_a_named_install_path():
    """Именно та дыра, ради которой тест написан, названа явно: у разбора PDF
    есть extra, который его включает."""
    data = tomllib.loads((ROOT / "bossman-core" / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    carriers = [name for name, reqs in extras.items()
                if any(r.lower().startswith("pypdf") for r in reqs)]
    assert carriers, (
        "у bossman-core нет extra, который приносит pypdf; "
        f"есть только: {sorted(extras)}")
