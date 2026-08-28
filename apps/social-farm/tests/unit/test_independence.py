"""Приложение обязано остаться самостоятельным.

BOSSMAN — плоскость управления, и он говорит с этим сервисом по HTTP. Любой
импорт `bcc.*` превратил бы независимый сервис в связанный модуль, а закрытое
ядро V2.2 — в чужую зону ответственности. Проверяется механически, а не
дисциплиной.

Здесь же — запреты границы безопасности. Отсутствие обхода капчи и антидетекта
доказать тестом нельзя (`DIGEST_CORE` B4), но можно доказать, что в дереве нет
кода, который этим занимается. Это не полная гарантия, и в отчёте так и
сказано; но это тот барьер, который ловит появление такого кода завтра.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "social_farm"

FORBIDDEN_ROOTS = {"bcc", "bossman", "bossman_core", "command_center"}

# Библиотеки и приёмы, существующие ради обхода защиты. Их появление в дереве
# означает, что кто-то начал делать то, что запрещено границей безопасности.
FORBIDDEN_PACKAGES = {
    "undetected_chromedriver", "selenium_stealth", "playwright_stealth",
    "puppeteer_extra", "twocaptcha", "anticaptcha", "capmonster",
    "python_anticaptcha", "捕", "deathbycaptcha",
}
FORBIDDEN_PATTERNS = [
    (re.compile(r"(?i)\b(?:solve|bypass|defeat)[_ ]?captcha\b"), "обход капчи"),
    (re.compile(r"(?i)\bstealth[_ ]?(?:mode|plugin|patch)\b"), "антидетект"),
    (re.compile(r"(?i)\bspoof[_ ]?(?:fingerprint|canvas|webgl|useragent)\b"),
     "подмена отпечатка"),
    (re.compile(r"(?i)\bproxy[_ ]?rotat"), "ротация прокси ради обхода"),
    (re.compile(r"(?i)\bnavigator\.webdriver\s*="), "сокрытие автоматизации"),
]


def module_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_source_tree_is_non_empty():
    assert len(module_files()) >= 10


def test_no_control_plane_imports():
    offenders = {}
    for path in module_files():
        bad = imported_roots(path) & FORBIDDEN_ROOTS
        if bad:
            offenders[str(path)] = sorted(bad)
    assert not offenders, f"импорт плоскости управления: {offenders}"


def test_no_evasion_packages_are_imported():
    offenders = {}
    for path in module_files():
        bad = imported_roots(path) & FORBIDDEN_PACKAGES
        if bad:
            offenders[str(path)] = sorted(bad)
    assert not offenders, f"пакеты обхода защиты: {offenders}"


def test_no_evasion_code_patterns():
    """Скан по образцам. Он не доказывает отсутствие обхода — он ловит его
    появление. В отчёте этот пункт помечен как закрытый ревью, а не тестом."""
    offenders = []
    for path in module_files():
        # сам список образцов, естественно, им же и совпадает
        if path.name == "test_independence.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, reason in FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    offenders.append(f"{path.name}:{number} — {reason}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_every_module_parses_and_imports_cleanly():
    import importlib

    for path in module_files():
        relative = path.relative_to(SRC.parent)
        module = ".".join(relative.with_suffix("").parts)
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        importlib.import_module(module)
