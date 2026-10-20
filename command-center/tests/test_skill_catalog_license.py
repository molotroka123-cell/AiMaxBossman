"""An imported skill without a permissive licence on disk is never delivered.

Provenance already pins repo/commit/sha256; the licence was only checked by
a CI test over the shipped catalog, not when a catalog is loaded. A skill
dropped into the catalog with a non-permissive or missing licence was
UNVERIFIED (selectable) and reached the student as context.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bcc.v2 import skill_catalog as sc
from bcc.v2.skill_catalog import QUARANTINED, UNVERIFIED, SkillCatalog

BODY = "---\nname: widget-care\ndescription: Use when frobnicating widgets.\n---\n# Widget care\n"


def _skill(root: Path, license_: dict | None, *, write_license_file: bool = True) -> None:
    d = root / "src" / "widget-care"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(BODY, encoding="utf-8")
    if write_license_file:
        (root / "src" / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    prov = {"schema": sc.PROVENANCE_SCHEMA, "id": "src/widget-care",
            "source": {"repo": "example/src", "commit": "0" * 40, "path": "skills/widget-care/SKILL.md"},
            "sha256": hashlib.sha256(BODY.encode()).hexdigest(), "status": "UNVERIFIED",
            "selection": {"triggers": ["widget"]}}
    if license_ is not None:
        prov["license"] = license_
    (d / "provenance.json").write_text(json.dumps(prov), encoding="utf-8")


def _entry(root: Path):
    (e,) = SkillCatalog(root).entries()
    return e


def test_permissive_licence_with_file_is_selectable(tmp_path):
    _skill(tmp_path, {"spdx": "MIT", "file": "../LICENSE"})
    assert _entry(tmp_path).status == UNVERIFIED


@pytest.mark.parametrize("license_, write_file, needle", [
    (None, True, "лиценз"),
    ({"spdx": "GPL-3.0-only", "file": "../LICENSE"}, True, "GPL-3.0-only"),
    ({"spdx": "LicenseRef-Proprietary", "file": "../LICENSE"}, True, "LicenseRef-Proprietary"),
    ({"spdx": "MIT", "file": "../LICENSE"}, False, "файл лицензии"),
    # a real MIT file, but outside the catalog: not shipped with the wheel
    ({"spdx": "MIT", "file": "../../../outside/LICENSE"}, True, "файл лицензии"),
])
def test_missing_or_non_permissive_licence_quarantines(tmp_path, license_, write_file, needle):
    root = tmp_path / "cat"
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    _skill(root, license_, write_license_file=write_file)
    e = _entry(root)
    assert e.status == QUARANTINED
    assert needle in e.status_reason
    assert SkillCatalog(root).select("fix the widget please") == []
