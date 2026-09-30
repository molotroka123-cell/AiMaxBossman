"""Freeze manifest: artifacts with sha256, linked to the journal head, tamper-evident."""
from __future__ import annotations

import hashlib
import json

import pytest

from bcc.autonomy.journal import Journal
from bcc.autonomy.manifest import build_manifest, verify_manifest, write_manifest


@pytest.fixture
def setup(tmp_path):
    j = Journal(tmp_path / "j")
    j.append("goal.created", {"goal_id": "JEFF-1"})
    a = tmp_path / "junit.xml"
    a.write_text("<testsuite/>")
    b = tmp_path / "staging.json"
    b.write_text("{}")
    return j, {"junit": a, "staging": b}


def test_manifest_lists_hashes_and_links_the_journal_head(setup):
    j, files = setup
    head = j.head()
    m = build_manifest(j, files, required=("junit", "staging"), label="rc", candidate_sha="d" * 40)
    assert m["complete"] and m["missing"] == []
    assert m["artifacts"]["junit"]["sha256"] == hashlib.sha256(b"<testsuite/>").hexdigest()
    assert m["journal"] == {"seq": 1, "head": head}
    last = j.entries()[-1]
    assert last["kind"] == "freeze.manifest" and last["payload"]["manifest_sha256"] == m["manifest_sha256"]
    assert verify_manifest(m, j) == []


def test_missing_required_artifact_is_named(setup, tmp_path):
    j, files = setup
    m = build_manifest(j, {**files, "voice": tmp_path / "nope.json"}, required=("junit", "voice", "report"))
    assert not m["complete"] and m["missing"] == ["report", "voice"]
    assert any("missing at freeze" in p for p in verify_manifest(m, j))


def test_changed_artifact_or_manifest_is_detected(setup, tmp_path):
    j, files = setup
    m = build_manifest(j, files, required=("junit",))
    files["junit"].write_text("<testsuite failures='1'/>")
    assert "junit: artifact changed" in verify_manifest(m, j)
    files["junit"].write_text("<testsuite/>")
    forged = json.loads(json.dumps(m))
    forged["complete"] = True
    forged["label"] = "other"
    assert any("manifest content changed" in p for p in verify_manifest(forged, j))
    p = write_manifest(m, tmp_path / "out" / "manifest.json")
    assert verify_manifest(json.loads(p.read_text()), j) == []


def test_manifest_against_a_foreign_or_broken_journal(setup, tmp_path):
    j, files = setup
    m = build_manifest(j, files)
    other = Journal(tmp_path / "other")
    other.append("x", {})
    problems = verify_manifest(m, other)
    assert any("not in the journal chain" in p for p in problems)
    assert any("no freeze.manifest entry" in p for p in problems)
    with open(j.path, "ab") as fh:
        fh.write(b"garbage\n")
    assert any("journal does not verify" in p for p in verify_manifest(m, j))
    with pytest.raises(ValueError):
        build_manifest(j, files)
