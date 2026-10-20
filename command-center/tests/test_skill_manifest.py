"""SKILL.md manifest validation: required fields, types, schemas, licence.

Errors block create/import (nothing is written); warnings are shown only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.v2.skill_library import SkillLibrary, parse_skill, validate_manifest

GOOD = """---
name: good-skill
description: Хороший навык.
metadata:
  version: "1.2"
required_tools: [skilltest.fetch]
permissions: [browser.read]
input_schema:
  type: object
  properties:
    url: {type: string}
  required: [url]
---
# Good
1. step
"""

# the dashboard's "Новый навык" template: no name/description yet
UI_TEMPLATE = """---
metadata:
  version: "1.0"
permissions: []
required_tools: []
input_schema:
  type: object
  properties:
    topic:
      type: string
  required: [topic]
---

# Название скилла
"""


def _sk(tmp_path: Path, sid: str, content: str):
    d = tmp_path / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content, encoding="utf-8")
    return parse_skill(d / "SKILL.md", tmp_path)


def test_good_manifest_is_clean(tmp_path):
    assert validate_manifest(_sk(tmp_path, "good-skill", GOOD)) == {"errors": [], "warnings": []}


@pytest.mark.parametrize("patch, needle", [
    (("required_tools: [skilltest.fetch]", "required_tools: {a: 1}"), "required_tools"),
    (("permissions: [browser.read]", "permissions: [[nested]]"), "permissions"),
    (("    url: {type: string}", "    url: {type: strnig}"), "input_schema"),
    (("name: good-skill", "name: Мой навык"), "name"),
    (("description: Хороший навык.", "description: \"\""), "description"),
    (("  version: \"1.2\"", "  version: \"1.2\"\n  upstream: example/skills"), "лиценз"),
])
def test_broken_manifest_has_russian_error(tmp_path, patch, needle):
    errs = validate_manifest(_sk(tmp_path, "good-skill", GOOD.replace(*patch)))["errors"]
    assert errs and any(needle in e for e in errs), errs


def test_upstream_with_license_is_fine(tmp_path):
    content = GOOD.replace('  version: "1.2"', '  version: "1.2"\n  upstream: example/skills\n'
                                                '  license: MIT')
    assert validate_manifest(_sk(tmp_path, "good-skill", content))["errors"] == []
    top = GOOD.replace("description:", "license: Apache-2.0\ndescription:").replace(
        '  version: "1.2"', '  version: "1.2"\n  upstream: example/skills')
    assert validate_manifest(_sk(tmp_path, "good-skill", top))["errors"] == []


def test_name_mismatch_and_missing_fields_are_warnings(tmp_path):
    res = validate_manifest(_sk(tmp_path, "other-dir", GOOD))
    assert res["errors"] == [] and any("other-dir" in w for w in res["warnings"])
    res = validate_manifest(_sk(tmp_path, "my-skill", UI_TEMPLATE))
    assert res["errors"] == []
    assert any("name" in w for w in res["warnings"])
    assert any("description" in w for w in res["warnings"])


def test_create_refuses_broken_manifest_and_writes_nothing(tmp_path):
    lib = SkillLibrary([tmp_path], tmp_path)
    with pytest.raises(ValueError, match="input_schema"):
        lib.create("broken-skill", GOOD.replace("{type: string}", "{type: strnig}"))
    assert not (tmp_path / "broken-skill").exists()
    assert lib.create("my-skill", UI_TEMPLATE).id == "my-skill"      # template still works


async def test_api_create_refuses_and_list_shows_manifest(env, tmp_path):
    ws = tmp_path / "ws" / ".agents" / "skills"
    env.svc.skills = SkillLibrary([ws], ws)
    r = await env.client.post("/api/skills", json={
        "id": "broken-skill", "content": GOOD.replace("{type: string}", "{type: strnig}")})
    assert r.status_code == 422, r.text
    assert "input_schema" in r.text
    r = await env.client.post("/api/skills", json={"id": "my-skill", "content": UI_TEMPLATE})
    assert r.status_code == 200, r.text
    listing = (await env.client.get("/api/skills")).json()
    assert [s["id"] for s in listing] == ["my-skill"]
    assert listing[0]["manifest"]["errors"] == []
    assert listing[0]["manifest"]["warnings"]
