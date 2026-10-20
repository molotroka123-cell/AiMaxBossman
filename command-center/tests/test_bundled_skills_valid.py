"""Every skill shipped in the repository parses and has a valid manifest.

Covers the owner skills in ``.agents/skills`` and the imported text-only
catalog in ``bcc/skills_catalog``. A new skill with a broken frontmatter, a
foreign upstream without a licence, or an oversized file fails here, in CI,
instead of silently disappearing from the owner's skill list.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.v2.skill_catalog import CATALOG_ROOT
from bcc.v2.skill_library import MAX_SKILL_BYTES, SkillLibrary, validate_manifest

REPO = Path(__file__).resolve().parents[2]
AGENT_SKILLS = REPO / ".agents" / "skills"
CATALOG_SOURCES = sorted(p for p in CATALOG_ROOT.iterdir() if p.is_dir())


def _all_skill_files() -> list[Path]:
    files = sorted(AGENT_SKILLS.glob("*/SKILL.md"))
    for src in CATALOG_SOURCES:
        files += sorted(src.glob("*/SKILL.md"))
    return files


def test_inventory_is_not_empty():
    assert len(list(AGENT_SKILLS.glob("*/SKILL.md"))) >= 40
    assert sum(len(list(s.glob("*/SKILL.md"))) for s in CATALOG_SOURCES) >= 8


@pytest.mark.parametrize("root", [AGENT_SKILLS, *CATALOG_SOURCES], ids=lambda p: p.name)
def test_every_skill_in_root_loads_without_errors(root):
    lib = SkillLibrary([root], root)
    found = lib.discover()
    assert lib.errors == []
    assert len(found) == len(list(root.glob("*/SKILL.md")))


@pytest.mark.parametrize("path", _all_skill_files(),
                         ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}")
def test_bundled_skill_manifest_is_valid(path):
    assert path.stat().st_size <= MAX_SKILL_BYTES
    lib = SkillLibrary([path.parent.parent], path.parent.parent)
    sk = lib.by_id()[path.parent.name]
    res = validate_manifest(sk)
    assert res["errors"] == [], res["errors"]
    assert sk.frontmatter.get("name") == path.parent.name
    assert sk.description.strip(), "description is how the skill is found"
