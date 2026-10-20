"""Same skill id in several roots: one card, the copy that actually runs,
and the shadowed copies named (root + version) instead of a silent double."""
from __future__ import annotations

from pathlib import Path

from bcc.v2.skill_library import SkillLibrary


def _skill(root: Path, sid: str, version: str, desc: str) -> None:
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {sid}\ndescription: {desc}\nmetadata:\n  version: \"{version}\"\n---\n# {sid}\n",
        encoding="utf-8")


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    owner, home = tmp_path / "ws" / ".agents" / "skills", tmp_path / "home" / ".claude" / "skills"
    _skill(owner, "dup-skill", "2.0", "копия владельца")
    _skill(home, "dup-skill", "1.0", "старая копия из дома")
    _skill(home, "only-home", "1.0", "только дома")
    return owner, home


def test_library_reports_shadowed_copies(tmp_path):
    owner, home = _roots(tmp_path)
    lib = SkillLibrary([owner, home], owner)
    assert lib.by_id()["dup-skill"].description == "копия владельца"
    sh = lib.shadowed()
    assert list(sh) == ["dup-skill"]
    assert [(c["root"], c["version"]) for c in sh["dup-skill"]] == [(".claude/skills", "1.0")]
    assert str(tmp_path) not in repr(sh)


async def test_api_lists_each_id_once_with_the_runnable_copy(env, tmp_path):
    owner, home = _roots(tmp_path)
    env.svc.skills = SkillLibrary([owner, home], owner)
    listing = (await env.client.get("/api/skills")).json()
    assert sorted(s["id"] for s in listing) == ["dup-skill", "only-home"]
    dup = next(s for s in listing if s["id"] == "dup-skill")
    assert dup["version"] == "2.0" and dup["description"] == "копия владельца"
    assert [c["version"] for c in dup["shadowed"]] == ["1.0"]
    only = next(s for s in listing if s["id"] == "only-home")
    assert only["shadowed"] == []
    # the card that is shown is the one that runs
    got = (await env.client.get("/api/skills/dup-skill")).json()
    assert got["version"] == dup["version"]
