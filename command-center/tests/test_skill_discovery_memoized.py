"""FABLE5 perf: discover() не пере-парсит SKILL.md, пока ФС не изменилась."""
from pathlib import Path

import bcc.v2.skill_library as sl
from bcc.v2.skill_library import SkillLibrary

SKILL = """---
id: demo-skill
title: Demo
description: d
---
body
"""


def _mk(root: Path, sid: str):
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(SKILL.replace("demo-skill", sid), encoding="utf-8")


def test_second_discover_skips_reparse(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    _mk(root, "alpha")
    lib = SkillLibrary([root], root)

    calls = {"n": 0}
    orig = sl.parse_skill
    monkeypatch.setattr(sl, "parse_skill", lambda p, r: (calls.__setitem__("n", calls["n"] + 1), orig(p, r))[1])

    a = lib.discover()
    assert len(a) == 1 and calls["n"] == 1
    b = lib.discover()                     # ФС не менялась → parse не вызывается
    assert len(b) == 1 and calls["n"] == 1, "неизменная ФС не должна пере-парситься"

    _mk(root, "beta")                      # добавили навык → ключ изменился
    c = lib.discover()
    assert len(c) == 2 and calls["n"] == 3, "изменение ФС обязано пере-сканироваться"


def test_cache_is_not_mutable_from_outside(tmp_path):
    root = tmp_path / "skills"
    _mk(root, "alpha")
    lib = SkillLibrary([root], root)
    first = lib.discover()
    first.clear()                          # мутируем возвращённый список
    assert len(lib.discover()) == 1, "внешняя мутация не должна портить кэш"
