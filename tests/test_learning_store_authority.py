"""PASS3 P0-03 — единое authoritative состояние на case_id, атомарная запись,
версии/tombstone, блокировка писателей, CAS, повреждённый хвост."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from learning import LearningStore, ValidationError
from learning.trace import ConflictError

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_learning_trace import _case  # noqa: E402


def _store(tmp_path: Path) -> LearningStore:
    return LearningStore(tmp_path / "data", tmp_path / "docs")


def _unverified(**over):
    c = _case(learning_status="UNVERIFIED", verified_by=[], external_verification="", outcome="PARTIAL")
    c.pop("verifiers"); c.pop("evidence_records"); c.update(over)
    return c


def test_repro_verified_then_unverified_moves_authority(tmp_path):
    st = _store(tmp_path)
    v1 = st.add(_case())
    assert v1["version"] == 1 and [c["case_id"] for c in st.verified()] == [v1["case_id"]]
    v2 = st.add(_unverified())
    assert v2["case_id"] == v1["case_id"] and v2["version"] == 2 and v2["supersedes_version"] == 1
    assert st.verified() == []                                   # старый VERIFIED больше не authoritative
    assert [c["version"] for c in st.failed()] == [2]
    assert st.retrieve(bug_class="path_traversal") == []          # retrieval не отдаёт superseded VERIFIED
    hist = st.history()
    assert hist and hist[-1]["tombstone"] is True and hist[-1]["superseded_by_version"] == 2
    assert st.current(v1["case_id"])["learning_status"] == "UNVERIFIED"


def test_verified_to_failed_then_reverify(tmp_path):
    st = _store(tmp_path)
    st.add(_case())
    st.add(_unverified(learning_status="FAILED_EXPERIMENT", outcome="REJECTED"))
    assert st.verified() == [] and st.failed()[0]["learning_status"] == "FAILED_EXPERIMENT"
    v3 = st.add(_case())                                         # re-verify → снова один VERIFIED
    assert v3["version"] == 3 and len(st.verified()) == 1 and st.failed() == []
    assert len(st.history()) == 2


def test_duplicate_case_id_is_a_version_not_a_duplicate(tmp_path):
    st = _store(tmp_path)
    st.add(_case()); st.add(_case(confidence=0.95))
    rows = st.verified()
    assert len(rows) == 1 and rows[0]["version"] == 2 and rows[0]["confidence"] == 0.95


def test_cas_conflict_is_deterministic(tmp_path):
    st = _store(tmp_path)
    st.add(_case())
    with pytest.raises(ConflictError):
        st.add(_case(confidence=0.7), expected_version=5)
    st.add(_case(confidence=0.7), expected_version=1)
    assert st.verified()[0]["version"] == 2


def test_two_concurrent_writers_serialize(tmp_path):
    st_a, st_b = _store(tmp_path), _store(tmp_path)
    errors = []

    def writer(st, n):
        try:
            for i in range(n):
                st.add(_case(confidence=0.5 + i / 100))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    ta, tb = threading.Thread(target=writer, args=(st_a, 15)), threading.Thread(target=writer, args=(st_b, 15))
    ta.start(); tb.start(); ta.join(); tb.join()
    assert not errors
    rows = _store(tmp_path).verified()
    assert len(rows) == 1 and rows[0]["version"] == 30          # каждая запись увидела предыдущую
    assert len(_store(tmp_path).history()) == 29


def test_interrupted_write_and_corrupted_tail_are_not_authoritative(tmp_path):
    st = _store(tmp_path)
    st.add(_case())
    # оборванный хвост: частично записанная строка
    with open(st.verified_path, "a", encoding="utf-8") as fh:
        fh.write('{"task_id": "PARTIAL", "learning_status": "VERI')
    fresh = _store(tmp_path)
    assert [c["task_id"] for c in fresh.verified()] == ["F-TEST-001"] and fresh.corrupt_lines == 1
    # прерванная запись оставила temp-файл — он не читается как корпус
    (tmp_path / "data" / "fix_cases.jsonl.zzz.tmp").write_text('{"garbage": true}\n')
    assert [c["task_id"] for c in _store(tmp_path).verified()] == ["F-TEST-001"]
    assert not any(p.suffix == ".tmp" for p in [st.verified_path, st.failed_path])


def test_stale_reader_sees_latest_state_on_next_read(tmp_path):
    reader, writer = _store(tmp_path), _store(tmp_path)
    writer.add(_case())
    assert reader.verified()[0]["version"] == 1
    writer.add(_unverified())
    assert reader.verified() == [] and reader.failed()[0]["version"] == 2


def test_invalid_write_leaves_files_untouched(tmp_path):
    st = _store(tmp_path)
    st.add(_case())
    before = st.verified_path.read_bytes()
    with pytest.raises(ValidationError):
        st.add(_case(verifiers=[{"principal_id": "agent:lead#run-lead-1", "independence_class": "cross_model"}]))
    assert st.verified_path.read_bytes() == before
