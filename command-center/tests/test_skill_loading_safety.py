"""Skill loading safety: encoding, size, YAML, path containment, manifest.

Each rule is a pair: a hostile/broken fixture is refused or reported, and a
clean fixture next to it still loads untouched.
"""
from __future__ import annotations

from pathlib import Path

from bcc.v2.skill_library import SkillLibrary, parse_skill

CLEAN = """---
name: clean-skill
description: Чистый навык для проверки загрузчика.
---
# Clean
1. step
"""


def _write(root: Path, sid: str, content: str | bytes) -> Path:
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


# ------------------------------------------------------------------ encoding

def test_utf8_bom_does_not_hide_frontmatter(tmp_path):
    content = ("---\nname: bom-skill\ndescription: Сохранён Блокнотом\n"
               "required_tools: [skilltest.fetch]\n---\n# Body\n")
    p = _write(tmp_path, "bom-skill", b"\xef\xbb\xbf" + content.encode("utf-8"))
    sk = parse_skill(p, tmp_path)
    assert sk.name == "bom-skill"
    assert sk.description == "Сохранён Блокнотом"
    assert sk.frontmatter["required_tools"] == ["skilltest.fetch"]
    assert sk.body.startswith("# Body")
    # negative control: the same text without BOM parses identically
    q = _write(tmp_path, "plain-skill", content)
    assert parse_skill(q, tmp_path).frontmatter == sk.frontmatter


# ---------------------------------------------------------------------- YAML

BOMB = """---
name: bomb-skill
description: alias bomb
a: &a ["x","x","x","x","x","x","x","x","x"]
b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]
c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]
d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]
e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]
f: &f [*e,*e,*e,*e,*e,*e,*e,*e,*e]
g: &g [*f,*f,*f,*f,*f,*f,*f,*f,*f]
---
# Body
"""


def test_yaml_alias_bomb_is_not_expanded(tmp_path):
    """/api/skills/{id} serialises the frontmatter to JSON: a shared-alias tree
    of 9**7 leaves would turn one small file into gigabytes of response."""
    sk = parse_skill(_write(tmp_path, "bomb-skill", BOMB), tmp_path)
    import json
    assert not isinstance(sk.frontmatter.get("g"), list)
    assert len(json.dumps(sk.frontmatter, ensure_ascii=False)) < 2000
    # plain scalars still survive (the lenient fallback keeps name/description)
    assert sk.name == "bomb-skill" and sk.description == "alias bomb"
    # negative control: an ordinary nested frontmatter keeps its structure
    ok = parse_skill(_write(tmp_path, "nested-skill",
                            "---\nname: nested-skill\ndescription: d\nmetadata:\n"
                            "  version: \"2.0\"\nrequired_tools: [a.b]\n---\n# B\n"), tmp_path)
    assert ok.frontmatter["metadata"] == {"version": "2.0"}
    assert ok.frontmatter["required_tools"] == ["a.b"]


def test_yaml_python_tags_never_execute(tmp_path, monkeypatch):
    import os
    calls = []
    monkeypatch.setattr(os, "system", lambda *a: calls.append(a) or 0)
    evil = ("---\nname: evil-skill\ndescription: tag\n"
            "x: !!python/object/apply:os.system [\"echo pwned\"]\n---\n# Body\n")
    sk = parse_skill(_write(tmp_path, "evil-skill", evil), tmp_path)
    assert calls == []
    assert sk.name == "evil-skill"
    assert not isinstance(sk.frontmatter.get("x"), int)
