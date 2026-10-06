"""Audit 2026-09-28: a merge restore goes through the store's lock as one atomic
journal rewrite and does not import VERIFIED claims the store would refuse."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learning import backup as bk  # noqa: E402
from learning import trace  # noqa: E402
from learning.trace import LearningStore  # noqa: E402

_spec = importlib.util.spec_from_file_location("_root_test_learning_trace_backup",
                                               Path(__file__).with_name("test_learning_trace.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_case = _mod._case


def _backup_with(tmp_path: Path, *cases: dict) -> Path:
    src = tmp_path / "src"
    store = LearningStore(src, tmp_path / "src-docs")
    for c in cases:
        store.add(c, write_markdown=False)
    out = tmp_path / "backup"
    bk.backup(out, learning_dir=src)
    return out


def test_merge_refuses_a_forged_verified_transaction(tmp_path):
    out = _backup_with(tmp_path, _case(task_id="GOOD-1"))
    forged = {"txn": 99, "case": {"task_id": "FORGED", "case_id": "f0f0f0f0f0f0f0f0", "version": 1,
                                  "learning_status": "VERIFIED", "root_cause": "trust me"}}
    journal = out / bk.LEARNING_DIR / "journal.jsonl"
    journal.write_text(journal.read_text(encoding="utf-8") + json.dumps(forged) + "\n", encoding="utf-8")
    dest = tmp_path / "dest"
    report = bk.restore(out, learning_dir=dest, mode="merge")
    assert (report.learning_txns_applied, report.learning_txns_rejected) == (1, 1)
    assert [c["task_id"] for c in LearningStore(dest, tmp_path / "d-docs").verified()] == ["GOOD-1"]
    assert report.as_dict()["learning_txns_rejected"] == 1


def test_merge_waits_for_the_store_lock_and_keeps_a_concurrent_add(tmp_path):
    out = _backup_with(tmp_path, _case(task_id="FROM-BACKUP"))
    dest = tmp_path / "dest"
    local = LearningStore(dest, tmp_path / "d-docs")
    local.add(_case(task_id="LOCAL-1"), write_markdown=False)
    done = threading.Event()

    def hold_lock_then_add():
        with local._locked():                      # a writer's read-modify-write in flight
            entries = local._read(local.journal_path)
            time.sleep(0.3)
            c = _case(task_id="LOCAL-2")
            c["case_id"] = trace.case_id(c); c["version"] = 1
            local._rewrite(local.journal_path, entries + [{"txn": len(entries) + 1, "case": c}])
            local._materialize()
        done.set()

    t = threading.Thread(target=hold_lock_then_add)
    t.start()
    time.sleep(0.05)
    bk.restore(out, learning_dir=dest, mode="merge")
    t.join()
    assert done.is_set()
    ids = sorted(c["task_id"] for c in LearningStore(dest, tmp_path / "d-docs").verified())
    assert ids == ["FROM-BACKUP", "LOCAL-1", "LOCAL-2"]


def test_merge_keeps_existing_journal_bytes_verbatim(tmp_path):
    out = _backup_with(tmp_path, _case(task_id="FROM-BACKUP"))
    dest = tmp_path / "dest"
    LearningStore(dest, tmp_path / "d-docs").add(_case(task_id="LOCAL-1"), write_markdown=False)
    journal = dest / "journal.jsonl"
    with open(journal, "ab") as fh:
        fh.write(b'{"torn": "\xd0')                 # a torn line: stays exactly as it was
    before = journal.read_bytes()
    report = bk.restore(out, learning_dir=dest, mode="merge")
    assert report.learning_txns_applied == 1
    assert journal.read_bytes().startswith(before)
