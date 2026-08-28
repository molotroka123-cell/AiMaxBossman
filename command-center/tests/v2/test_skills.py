from pathlib import Path

from bcc.v2.skill_library import SkillLibrary

def test_skill_discovery_and_import(tmp_path: Path):
    source = tmp_path/"source"
    canonical = tmp_path/"repo"/".agents"/"skills"
    d = source/"my-skill"
    d.mkdir(parents=True)
    (d/"SKILL.md").write_text("""---
name: my-skill
description: Example
---
# Hello
""", encoding="utf-8")
    lib = SkillLibrary([source], canonical)
    skills = lib.discover()
    assert len(skills) == 1
    imported = lib.import_skill(skills[0])
    assert imported.id == "my-skill"
    assert (canonical/"my-skill"/"SKILL.md").exists()
