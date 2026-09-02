"""Audit P0: LearningStore transitions are transactional through one authoritative
journal; derived snapshots are rebuilt after a crash between writes or tampering."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from learning.trace import LearningStore  # noqa: E402
from test_learning_trace import _case  # noqa: E402


def _store(tmp_path: Path) -> LearningStore:
    return LearningStore(tmp_path)


def test_crash_after_journal_commit_is_healed_on_next_open(tmp_path, monkeypatch):
    st = _store(tmp_path)
    first = st.add(_case(), write_markdown=False)
    c2 = _case(); c2["learning_status"] = "UNVERIFIED"; c2.pop("verifiers", None); c2.pop("evidence_records", None)
    c2["outcome"] = "REJECTED"
    calls = {"n": 0}
    real = LearningStore._materialize

    def crash_once(self):
        calls["n"] += 1
        raise OSError("simulated crash between journal commit and snapshot rewrite")

    monkeypatch.setattr(LearningStore, "_materialize", crash_once)
    with pytest.raises(OSError):
        st.add(c2, write_markdown=False)
    monkeypatch.setattr(LearningStore, "_materialize", real)
    # snapshots are stale on disk: the case is still in the VERIFIED corpus
    stale = [json.loads(l) for l in (tmp_path / "fix_cases.jsonl").read_text().splitlines() if l.strip()]
    cid = first["case_id"]
    assert any(c["case_id"] == cid for c in stale)
    fresh = _store(tmp_path)
    assert fresh.current(cid)["version"] == 2
    assert all(c["case_id"] != cid for c in fresh.verified())
    assert sum(1 for c in fresh.failed() if c["case_id"] == cid) == 1
    hist = fresh.history()
    assert len(hist) == 1 and hist[0]["tombstone"] and hist[0]["superseded_by_version"] == 2


def test_tampered_or_deleted_snapshot_is_rebuilt_from_journal(tmp_path):
    st = _store(tmp_path)
    case = st.add(_case(), write_markdown=False)
    (tmp_path / "fix_cases.jsonl").unlink()
    assert [c["case_id"] for c in _store(tmp_path).verified()] == [case["case_id"]]
    (tmp_path / "fix_cases.jsonl").write_text(json.dumps({**case, "version": 99, "learning_status": "VERIFIED"}) + "\n")
    assert _store(tmp_path).verified()[0]["version"] == 1        # journal wins over a forged snapshot


def test_legacy_snapshots_bootstrap_the_journal(tmp_path):
    case = _case(); case["case_id"] = "legacy-1"; case["version"] = 1
    (tmp_path / "fix_cases.jsonl").write_text(json.dumps(case) + "\n")
    st = _store(tmp_path)
    assert st.current("legacy-1")["version"] == 1
    assert (tmp_path / "journal.jsonl").exists()
    entries = [json.loads(l) for l in (tmp_path / "journal.jsonl").read_text().splitlines() if l.strip()]
    assert entries[0]["case"]["case_id"] == "legacy-1"


def test_corrupt_journal_tail_is_not_authoritative(tmp_path):
    st = _store(tmp_path)
    case = st.add(_case(), write_markdown=False)
    with (tmp_path / "journal.jsonl").open("a") as fh:
        fh.write('{"txn": 2, "case": {"case_id": "' + case["case_id"] + '", "version": 7, "learning_st')
    fresh = _store(tmp_path)
    assert fresh.current(case["case_id"])["version"] == 1
    assert fresh.corrupt_lines >= 1
