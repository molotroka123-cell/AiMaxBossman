"""README обещает владельцу команды — каждая должна указывать на живой файл.

Слияние 19.09 дало пример цены: README раздела «Проверка» велел запустить
`python scripts/update_readme_scorecard.py --check`, а маркеры, без которых эта
команда падает, из того же README пропали. Документ обещал проверку, которая
гарантированно не проходила.

Здесь проверяется более слабое, зато механическое свойство: путь, который
README называет владельцу, существует в репозитории. Проверка НЕ утверждает,
что команда отработает успешно, и прямо об этом говорит — обещать больше, чем
проверяешь, и есть разбираемая тут беда.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

# `python tools/x.py`, `python -m pytest path/to/test.py`, `python scripts/y.py --flag`
_COMMAND = re.compile(
    r"^\s*python3?\s+(?:-m\s+pytest\s+|-m\s+\S+\s+|)([A-Za-z0-9_][\w./-]*\.(?:py|json|txt|toml))",
    re.MULTILINE,
)


def _fenced_blocks(text: str) -> list[str]:
    """Содержимое блоков кода.

    Забор ищется ПОСТРОЧНО. Первая версия этой функции искала
    ```` ```(?:bash)?\n(.*?)``` ```` через DOTALL, и закрывающий забор, за
    которым шёл перевод строки, сходил за открывающий: разбор выдавал прозу
    между блоками и ноль команд. Поймала это канарейка ниже, а не случай.
    """
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def referenced_paths() -> list[str]:
    text = README.read_text(encoding="utf-8")
    found: list[str] = []
    for block in _fenced_blocks(text):
        found.extend(_COMMAND.findall(block))
    return sorted(set(found))


def test_the_scanner_actually_finds_commands():
    """Канарейка: онемевший разбор сделал бы проверку ниже зелёной впустую."""
    paths = referenced_paths()
    assert len(paths) >= 5, f"разбор нашёл только {paths} — похоже, он перестал видеть команды"


@pytest.mark.parametrize("relative", referenced_paths())
def test_every_path_the_readme_tells_the_owner_to_run_exists(relative):
    assert (ROOT / relative).exists(), (
        f"README велит владельцу запустить {relative}, а файла нет. "
        f"Владелец узнает об этом, выполнив команду из README."
    )


def test_the_live_scorecard_block_is_present_and_singular():
    """Прямой сторож на то, что пропало при слиянии.

    Отдельно от test_readme_scorecard.py: тот проверяет СОДЕРЖИМОЕ блока против
    источника, а этот — что блок вообще есть. Пропажу маркеров они ловят с
    разных сторон, и пропажа обоих разом невозможна незаметно.
    """
    text = README.read_text(encoding="utf-8")
    assert text.count("<!-- BOSSMAN_LIVE_SCORECARD_START -->") == 1
    assert text.count("<!-- BOSSMAN_LIVE_SCORECARD_END -->") == 1


def test_owner_launch_and_release_checks_survive_readme_cleanup():
    """Owner-first prose must not erase the existing release revalidation path."""
    text = README.read_text(encoding="utf-8")
    assert {"scripts/update_readme_scorecard.py", "tools/exact_sha_certify.py",
            "tests/test_readme_commands_are_real.py", "tests/test_readme_scorecard.py",
            "tests/test_owner_acceptance_ps1_contract.py"}.issubset(referenced_paths())
    for entrypoint in ("Start-Bossman.cmd", "Evening-Test.cmd", "OWNER_ACCEPTANCE.md"):
        assert entrypoint in text
