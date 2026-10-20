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


def test_non_mapping_frontmatter_does_not_break_discovery(tmp_path):
    _write(tmp_path, "list-skill", "---\n- just\n- a list\n---\n# Body\n")
    _write(tmp_path, "clean-skill", CLEAN)
    lib = SkillLibrary([tmp_path], tmp_path)
    found = {s.id: s for s in lib.discover()}
    assert set(found) == {"list-skill", "clean-skill"}
    assert found["list-skill"].frontmatter == {}
    assert found["list-skill"].name == "list-skill"


# ---------------------------------------------------------------------- size

def test_oversized_skill_is_skipped_with_russian_error(tmp_path):
    from bcc.v2 import skill_library as sl
    big = CLEAN.replace("1. step", "x" * (sl.MAX_SKILL_BYTES + 10))
    _write(tmp_path, "huge-skill", big.replace("clean-skill", "huge-skill"))
    _write(tmp_path, "clean-skill", CLEAN)
    lib = SkillLibrary([tmp_path], tmp_path)
    assert [s.id for s in lib.discover()] == ["clean-skill"]
    assert [e["skill"] for e in lib.errors] == ["huge-skill"]
    msg = lib.errors[0]["message"]
    assert "huge-skill" in msg and "не больше" in msg
    assert str(tmp_path) not in msg          # no absolute paths for the owner UI


def test_create_refuses_oversized_content_before_writing(tmp_path):
    import pytest
    from bcc.v2 import skill_library as sl
    lib = SkillLibrary([tmp_path], tmp_path)
    with pytest.raises(ValueError, match="не больше"):
        lib.create("huge-skill", CLEAN + "x" * sl.MAX_SKILL_BYTES)
    assert not (tmp_path / "huge-skill").exists()
    assert lib.create("clean-skill", CLEAN).name == "clean-skill"


async def test_load_errors_endpoint(env, tmp_path):
    from bcc.v2 import skill_library as sl
    root = tmp_path / "ws" / ".agents" / "skills"
    _write(root, "huge-skill", "---\nname: huge-skill\n---\n" + "x" * (sl.MAX_SKILL_BYTES + 1))
    _write(root, "clean-skill", CLEAN)
    env.svc.skills = SkillLibrary([root], root)
    ids = [s["id"] for s in (await env.client.get("/api/skills")).json()]
    errs = (await env.client.get("/api/skill-load-errors")).json()
    assert ids == ["clean-skill"]
    assert [e["skill"] for e in errs["errors"]] == ["huge-skill"]


# --------------------------------------------------------------- containment

def _link_dir(link: Path, target: Path) -> None:
    import os

    import pytest
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import _winapi
        try:
            _winapi.CreateJunction(str(target), str(link))   # no admin/dev-mode needed
            return
        except OSError:
            pass
    pytest.skip("symlink/junction not supported here")


def test_skill_linked_from_outside_the_root_is_refused(tmp_path):
    root = tmp_path / "skills"
    outside = tmp_path / "elsewhere"
    _write(outside, "planted", "---\nname: planted\ndescription: чужой файл\n---\n# x\n")
    _write(root, "clean-skill", CLEAN)
    _link_dir(root / "planted", outside / "planted")
    lib = SkillLibrary([root], root)
    assert [s.id for s in lib.discover()] == ["clean-skill"]
    assert [e["skill"] for e in lib.errors] == ["planted"]
    assert "вне папки навыков" in lib.errors[0]["message"]
    assert str(outside) not in lib.errors[0]["message"]


def test_skill_link_inside_the_same_root_still_loads(tmp_path):
    root = tmp_path / "skills"
    _write(root / "_store", "real-skill", CLEAN.replace("clean-skill", "real-skill"))
    _link_dir(root / "real-skill", root / "_store" / "real-skill")
    lib = SkillLibrary([root], root)
    assert [s.id for s in lib.discover()] == ["real-skill"]
    assert lib.errors == []


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
