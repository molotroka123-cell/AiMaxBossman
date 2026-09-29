"""Audit 2026-09-28 (learning): LearningStore durability regressions.

5. A foreign reader holding a snapshot on Windows made os.replace fail, and add()
   raised AFTER its journal commit (the caller retried and minted a duplicate).
6. One undecodable or malformed line made EVERY read of the store raise.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learning import lifecycle, trace  # noqa: E402
from learning.trace import LearningStore, case_id  # noqa: E402

_spec = importlib.util.spec_from_file_location("_root_test_learning_trace_audit",
                                               Path(__file__).with_name("test_learning_trace.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_case = _mod._case


def _store(tmp_path: Path) -> LearningStore:
    return LearningStore(data_dir=tmp_path / "data", docs_dir=tmp_path / "docs")


# ---------------------------------------------------------------- 5. commit semantics
def test_add_survives_a_reader_holding_the_snapshot(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.add(_case(), write_markdown=False)
    monkeypatch.setattr(trace, "_REPLACE_BACKOFF_S", 0.02)
    holder = open(store.verified_path, "rb")            # a foreign reader (dashboard, AV, backup)
    threading.Timer(0.2, holder.close).start()
    saved = store.add(_case(task_id="F-TEST-002"), write_markdown=False)
    holder.close()
    assert saved["case_id"] in {c["case_id"] for c in store.verified()}


def test_snapshot_failure_after_commit_does_not_fail_the_committed_add(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.add(_case(), write_markdown=False)

    def broken(self):
        raise PermissionError(13, "held by another process")

    monkeypatch.setattr(LearningStore, "_materialize", broken)
    saved = store.add(_case(task_id="F-TEST-003"), write_markdown=False)   # committed: must not raise
    monkeypatch.undo()
    assert any(t["case"]["case_id"] == saved["case_id"] for t in store._journal())
    assert saved["case_id"] in {c["case_id"] for c in store.verified()}   # next read heals the snapshot


def test_checkpoint_write_survives_a_reader_holding_it(tmp_path, monkeypatch):
    life = lifecycle.MemoryLifecycle(state_dir=tmp_path)
    life.checkpoint(task_id="t1", project_id="p", next_action="a")
    monkeypatch.setattr(trace, "_REPLACE_BACKOFF_S", 0.02)
    holder = open(life.checkpoint_path("t1"), "rb")
    threading.Timer(0.2, holder.close).start()
    life.checkpoint(task_id="t1", next_action="b")
    holder.close()
    assert life.load_checkpoint("t1").next_action == "b"


# ---------------------------------------------------------------- 6. one bad line
@pytest.mark.parametrize("tail", [b'{"txn": 99, "case": {"task_id": "\xd0',            # torn UTF-8 tail
                                  '{"task_id": "задача"}\n'.encode("cp1251")])         # host code page
def test_undecodable_line_is_one_corrupt_line_not_a_dead_store(tmp_path, tail):
    store = _store(tmp_path)
    store.add(_case(), write_markdown=False)
    with open(store.journal_path, "ab") as fh:
        fh.write(tail)
    assert [c["task_id"] for c in store.verified()] == ["F-TEST-001"]
    assert store.corrupt_lines >= 1


def test_orphan_with_non_numeric_timestamp_is_skipped_not_fatal(tmp_path):
    store = _store(tmp_path)
    store.add(_case(), write_markdown=False)
    orphan = _case(task_id="ORPH-1", evidence_records=[{"observed_at": "yesterday", "collected_at": "today"}])
    orphan["case_id"] = case_id(orphan)
    with open(store.verified_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(orphan) + "\n")
    assert [c["task_id"] for c in store.verified()] == ["F-TEST-001"]      # not adopted, no ValueError
    assert "evidence_record timestamps must be numbers" in trace.validate(orphan)


def test_hand_edited_version_string_does_not_break_reads(tmp_path):
    store = _store(tmp_path)
    store.add(_case(), write_markdown=False)
    rows = [json.loads(l) for l in store.verified_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["version"] = "two"
    store.verified_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert [c["task_id"] for c in store.verified()] == ["F-TEST-001"]
