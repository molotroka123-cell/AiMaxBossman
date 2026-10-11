"""Owner's AGPL decision must survive package and archive creation."""
from pathlib import Path
import json
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_windows_bundle as bundle

PROJECTS = [ROOT, ROOT / "bossman-core", ROOT / "command-center",
            *[p.parent for p in sorted((ROOT / "apps").glob("*/pyproject.toml"))]]


@pytest.mark.parametrize("project", PROJECTS, ids=lambda p: p.name)
def test_owned_distribution_ships_agpl(project):
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["license"] == {"text": "AGPL-3.0-only"}
    assert (project / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    text = (project / "LICENSE").read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
    assert "19 November 2007" in text
    assert "13. Remote Network Interaction" in text
    assert len(text) > 30000


def test_node_helper_has_same_license():
    p = ROOT / "apps/token-launch-helper"
    assert json.loads((p / "package.json").read_text())["license"] == "AGPL-3.0-only"
    assert (p / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()


def test_windows_license_offer_is_bound_to_source_commit(tmp_path):
    sha = "abc01234" * 5
    bundle.write_licenses(tmp_path / "LICENSES", {"runtime": {}}, sha=sha)
    assert (tmp_path / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert (tmp_path / "LICENSING.md").exists()
    source = (tmp_path / "LICENSES/SOURCE.md").read_text(encoding="utf-8")
    assert f"/tree/{sha}" in source
    assert f"/archive/{sha}.zip" in source
    assert (tmp_path / "LICENSES/README.md").exists()


@pytest.mark.parametrize("sha", ["main", "a" * 39, "z" * 40, ""])
def test_windows_offer_rejects_unproven_revision(tmp_path, sha):
    with pytest.raises(ValueError):
        bundle.write_licenses(tmp_path / "LICENSES", {"runtime": {}}, sha=sha)
    assert not (tmp_path / "LICENSES").exists()
