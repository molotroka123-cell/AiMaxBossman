"""Trigger matching for imported-skill selection (Russian inflection).

Triggers are word STEMS ("исправ" hits "исправь"). A multi-word trigger was
matched literally, so the stemmed phrase "отдельн рабоч" (using-git-worktrees)
could never match "отдельную рабочую копию", and "пул-реквест" missed
"пул реквест". ё/е spelling also split matches.
"""
from __future__ import annotations

import pytest

from bcc.v2.skill_catalog import SkillCatalog, score_skill


@pytest.mark.parametrize("task, trigger, points", [
    ("Сделай отдельную рабочую копию репозитория", "отдельн рабоч", 2),
    ("Открой пул реквест на ревью", "пул-реквест", 1),
    ("открой пулл-реквест", "пул-реквест", 1),
    ("Ошибка в отчёте за месяц", "отчет", 1),
    ("Ошибка в отчете за месяц", "отчёт", 1),
])
def test_stemmed_and_spelling_variants_match(task, trigger, points):
    assert score_skill(task, [trigger]) == (points, [trigger])


@pytest.mark.parametrize("task, trigger", [
    ("светка пришла", "ветк"),                 # stem must start a word
    ("отдельно, а потом рабочую", "отдельн рабоч"),   # words must be adjacent
    ("план", "выполни план"),
])
def test_negative_controls_still_do_not_match(task, trigger):
    assert score_skill(task, [trigger]) == (0, [])


def test_real_catalog_selects_worktrees_for_russian_phrase():
    rows = SkillCatalog().select("Нужна отдельная рабочая копия в ворктри для эксперимента")
    assert "superpowers/using-git-worktrees" in {r["id"] for r in rows}
    hit = next(r for r in rows if r["id"] == "superpowers/using-git-worktrees")
    assert "отдельн рабоч" in hit["matched"]
