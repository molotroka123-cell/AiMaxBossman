"""A Windows checkout/unzip turns SKILL.md into CRLF; the pinned skill must stay usable."""
import shutil
from pathlib import Path

from bcc.v2.skill_catalog import SkillCatalog

REAL = Path(__file__).resolve().parents[1] / "bcc" / "skills_catalog"


def _catalog(tmp_path: Path, *, crlf: bool, tamper: bool = False):
    root = tmp_path / "cat"
    shutil.copytree(REAL / "superpowers", root / "superpowers")
    skill = root / "superpowers" / "systematic-debugging" / "SKILL.md"
    data = skill.read_bytes().replace(b"\r\n", b"\n")
    if tamper:
        data += b"\nextra line\n"
    skill.write_bytes(data.replace(b"\n", b"\r\n") if crlf else data)
    return {s.id: s for s in SkillCatalog(root).entries()}


def test_crlf_line_ends_do_not_quarantine_a_pinned_skill(tmp_path):
    skills = _catalog(tmp_path, crlf=True)
    assert skills["superpowers/systematic-debugging"].status == "UNVERIFIED"


def test_a_real_edit_under_crlf_is_still_quarantined(tmp_path):
    skills = _catalog(tmp_path, crlf=True, tamper=True)
    assert skills["superpowers/systematic-debugging"].status == "QUARANTINED"
