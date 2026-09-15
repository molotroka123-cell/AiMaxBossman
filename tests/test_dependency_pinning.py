"""Зависимости закреплены точно, а не «не ниже».

Найдено аудитом по эпохам интернета (2016–2021, цепочка поставок). В
`solana_volume_suite/requirements.txt` лежали десять зависимостей, ВСЕ через
`>=`, — в подсистеме, которая держит приватные ключи и подписывает транзакции
mainnet.

Прецедент не гипотетический. Ноябрь 2018, event-stream: в цепочку зависимостей
биткоин-кошелька Copay попал вредоносный релиз транзитивного пакета и выгружал
приватные ключи. Пользователи не меняли свои зависимости — менялся мир вокруг
незакреплённого `>=`.

Проверка устроена так, чтобы её нельзя было обойти добавлением НОВОГО файла:
сканируется всё дерево, а не список путей.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Каталоги сборки и чужие деревья: там лежат копии, а не источник правды.
SKIP_PARTS = {"build", "dist", "node_modules", ".git", "__pycache__",
              ".venv", "venv", "site-packages", "handoffs"}

# Строка требования без версии вида `pkg`, `pkg>=1.0`, `pkg~=1.0`, `pkg>1`.
UNPINNED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\s*(\[[^\]]*\])?\s*([<>~!]=?|$)")


def _requirement_files() -> list[Path]:
    out = []
    for path in ROOT.rglob("requirements*.txt"):
        if SKIP_PARTS & set(path.parts):
            continue
        out.append(path)
    return sorted(out)


def _requirement_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue                       # -r, --index-url и прочие директивы
        lines.append(line)
    return lines


def test_there_is_something_to_check():
    """Без этой проверки тест остался бы зелёным, перестав что-либо сканировать."""
    files = _requirement_files()
    assert files, "не найдено ни одного requirements-файла — сканер сломан"


@pytest.mark.parametrize("path", _requirement_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_requirement_is_pinned_to_an_exact_version(path: Path):
    unpinned = [line for line in _requirement_lines(path)
                if "==" not in line and UNPINNED.match(line)]
    assert not unpinned, (
        f"{path.relative_to(ROOT)}: незакреплённые зависимости {unpinned}. "
        "Плавающая версия означает, что состав кода определяет тот, кто "
        "последним опубликовал релиз (event-stream, ноябрь 2018). "
        "Закрепите через ==, сняв версию с индекса, а не по памяти.")
