"""Constitution load / sha / owner pin (docs/constitution/BOSSMAN_CONSTITUTION.md "Enforcement")."""
from __future__ import annotations

import hashlib
import io

import pytest

from bcc.autonomy import constitution as c
from bcc.autonomy.journal import Journal


class Tty(io.StringIO):
    def __init__(self, text: str = "", tty: bool = True):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def files(tmp_path):
    doc = tmp_path / "repo" / "BOSSMAN_CONSTITUTION.md"
    doc.parent.mkdir()
    doc.write_text("# rules\n1. never spend money\n", encoding="utf-8")
    pin = tmp_path / "local" / "Bossman" / "autonomy" / "constitution.sha256"
    return doc, pin, hashlib.sha256(doc.read_bytes()).hexdigest()


CLEAN_ENV: dict[str, str] = {}


def test_real_constitution_exists_and_hashes():
    assert c.default_constitution_path().is_file()
    assert len(c.sha256()) == 64
    assert b"Enforcement" in c.load()


def test_default_pin_path_uses_localappdata(tmp_path):
    p = c.default_pin_path({"LOCALAPPDATA": str(tmp_path)})
    assert p == tmp_path / "Bossman" / "autonomy" / "constitution.sha256"


def test_missing_pin_blocks(files):
    doc, pin, sha = files
    st = c.verify(doc, pin)
    assert not st.ok and st.status == "BLOCKED" and st.sha == sha and "not pinned" in st.reason


def test_missing_constitution_blocks(tmp_path, files):
    _, pin, _ = files
    st = c.verify(tmp_path / "nope.md", pin)
    assert not st.ok and "unreadable" in st.reason


def test_malformed_pin_blocks(files):
    doc, pin, _ = files
    pin.parent.mkdir(parents=True)
    pin.write_text("hello")
    st = c.verify(doc, pin)
    assert not st.ok and "pin unreadable" in st.reason


def test_mismatch_blocks_and_match_passes(files):
    doc, pin, sha = files
    pin.parent.mkdir(parents=True)
    pin.write_text(sha + "\n")
    assert c.verify(doc, pin).ok
    doc.write_text("# rules\n1. spend money freely\n", encoding="utf-8")      # an agent edits the goals
    st = c.verify(doc, pin)
    assert not st.ok and st.pinned_sha == sha and st.sha != sha and "mismatch" in st.reason
    assert st.as_dict()["status"] == "BLOCKED"


def test_pin_refused_without_tty(files):
    doc, pin, sha = files
    r = c.pin(doc, pin, stdin=Tty(sha[:12] + "\n", tty=False), stdout=Tty(), env=CLEAN_ENV)
    assert not r.ok and "not an interactive terminal" in r.reason and not pin.exists()
    r = c.pin(doc, pin, stdin=Tty(sha[:12] + "\n"), stdout=Tty(tty=False), env=CLEAN_ENV)
    assert not r.ok and not pin.exists()


@pytest.mark.parametrize("marker", ["CLAUDECODE", "CODEX_SANDBOX", "BOSSMAN_AGENT_SESSION", "CI"])
def test_pin_refused_in_agent_session_even_on_tty(files, marker):
    doc, pin, sha = files
    j_root = pin.parent.parent / "journal"
    r = c.pin(doc, pin, stdin=Tty(sha[:12] + "\n"), stdout=Tty(), env={marker: "1"}, journal=Journal(j_root))
    assert not r.ok and marker in r.reason and not pin.exists()
    assert Journal(j_root).entries(kind="constitution")[0]["kind"] == "constitution.pin_refused"


def test_pin_refused_inside_the_repository(files):
    doc, _, sha = files
    inside = c.repo_root() / "constitution.sha256"
    r = c.pin(doc, inside, stdin=Tty(sha[:12] + "\n"), stdout=Tty(), env=CLEAN_ENV)
    assert not r.ok and "outside the repository" in r.reason and not inside.exists()


def test_pin_cancelled_on_wrong_confirmation(files):
    doc, pin, _ = files
    r = c.pin(doc, pin, stdin=Tty("y\n"), stdout=Tty(), env=CLEAN_ENV)
    assert not r.ok and "cancelled" in r.reason and not pin.exists()


def test_interactive_pin_then_verify(files, tmp_path):
    doc, pin, sha = files
    out = Tty()
    j = Journal(tmp_path / "j")
    r = c.pin(doc, pin, stdin=Tty(sha[:12].upper() + "\n"), stdout=out, env=CLEAN_ENV, journal=j)
    assert r.ok and r.sha == sha
    assert pin.read_text().strip() == sha and sha in out.getvalue()
    assert c.verify(doc, pin).ok
    assert j.entries()[-1]["kind"] == "constitution.pinned" and j.verify().ok


def test_agent_markers_detect_this_kind_of_session():
    assert c.agent_markers({"CLAUDECODE": "1", "PATH": "x"}) == ["CLAUDECODE"]
    assert c.agent_markers({"CLAUDECODE": ""}) == []
