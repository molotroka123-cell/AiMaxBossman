"""Skill run input is validated against the skill's FULL JSON Schema.

The old hand-written checker looked only at ``required`` and ``type``: the
bundled ``mimik`` skill declares ``oneOf``/``additionalProperties: false``/
``maxLength`` and ``open-news`` declares ``enum`` — none of it was enforced.
"""
from __future__ import annotations

from pathlib import Path

from bcc.features.skills import _validate_input
from bcc.v2.skill_library import SkillLibrary, skill_contract

ROOT = Path(__file__).resolve().parents[2]

MIMIK_LIKE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"markdown": {"type": "string", "maxLength": 10},
                   "snapshot": {"type": "object"},
                   "mode": {"type": "string", "enum": ["search", "process"]}},
    "oneOf": [{"required": ["markdown"]}, {"required": ["snapshot"]}],
}


def test_valid_input_has_no_errors():
    assert _validate_input(MIMIK_LIKE, {"markdown": "# ok"}) == []
    assert _validate_input({}, {"anything": 1}) == []          # no schema → no rules


def test_additional_properties_are_refused_in_russian():
    errs = _validate_input(MIMIK_LIKE, {"markdown": "# ok", "settings": {"token": "x"}})
    assert errs and any("лишн" in e and "settings" in e for e in errs), errs


def test_one_of_is_enforced():
    assert _validate_input(MIMIK_LIKE, {})                      # neither variant
    assert _validate_input(MIMIK_LIKE, {"markdown": "a", "snapshot": {}})   # both


def test_max_length_and_enum_are_enforced():
    errs = _validate_input(MIMIK_LIKE, {"markdown": "x" * 11})
    assert any("markdown" in e and "10" in e for e in errs), errs
    errs = _validate_input(MIMIK_LIKE, {"markdown": "a", "mode": "delete"})
    assert any("mode" in e and "search" in e for e in errs), errs


def test_required_and_type_messages_kept():
    schema = {"required": ["website_url"], "properties": {"website_url": {"type": "string"},
                                                          "depth": {"type": "integer"}}}
    assert _validate_input(schema, {}) == ["нет обязательного поля website_url"]
    assert _validate_input(schema, {"website_url": "u", "depth": True}) == \
        ["depth должно быть типа integer"]


def test_broken_skill_schema_is_reported_not_ignored():
    errs = _validate_input({"type": "strnig"}, {"a": 1})
    assert errs and "схема входа навыка" in errs[0]


def test_real_mimik_skill_refuses_extra_fields():
    con = skill_contract(SkillLibrary([ROOT / ".agents/skills"], ROOT / ".agents/skills")
                         .by_id()["mimik"])
    assert _validate_input(con.input_schema, {"markdown": "# Guide"}) == []
    assert _validate_input(con.input_schema, {"markdown": "# Guide", "browser_profile": "x"})
