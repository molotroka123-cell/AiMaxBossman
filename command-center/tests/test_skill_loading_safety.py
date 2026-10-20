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
