"""AUDIT001-F5-REPLAY closure — the single-use evidence ledger itself.

``tests/audit001/test_f5_cross_corpus.py`` proves the defect is gone through
``promote_candidate``. This file covers the ledger's own obligations, the ones
the backlog entry named as the reason it had not been built: cross-process and
restart safety.
"""
import json
import subprocess
import sys
from pathlib import Path

from bossman.learning_guard.evidence_ledger import (DurableEvidenceLedger, EvidenceLedger,
                                                    ENV_LEDGER_PATH, default_ledger,
                                                    evidence_key, reset_default_ledger)
from bossman.learning_guard.models import ABResult, SecuritySnapshot

CLEAN = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=1.0, scope_ref="corpus-a")


def _ab(n=3, task_class="notes.save"):
    return [ABResult(task_id=f"{task_class}-{i}", task_class=task_class,
                     raw_verified=False, guarded_verified=True) for i in range(n)]


def _key(ab=None, *, before=CLEAN, after=CLEAN, shadow_runs=20):
    return evidence_key(_ab() if ab is None else ab, before, after, shadow_runs)


# ---------------------------------------------------------------------- keying
def test_the_same_measurement_always_produces_the_same_key():
    assert _key() == _key()


def test_reordering_the_ab_rows_does_not_mint_a_fresh_key():
    rows = _ab()
    assert evidence_key(rows, CLEAN, CLEAN, 20) == evidence_key(list(reversed(rows)), CLEAN, CLEAN, 20)


def test_a_different_measurement_produces_a_different_key():
    assert _key() != _key(_ab(4))
    assert _key() != _key(shadow_runs=21)
    assert _key() != _key(after=SecuritySnapshot(leaks=1, scope_ref="corpus-a"))
    assert _key() != _key(after=SecuritySnapshot(containment_rate=1.0, scope_ref="corpus-b"))


# -------------------------------------------------------------------- in memory
def test_first_use_is_allowed_and_a_different_consumer_is_refused():
    ledger = EvidenceLedger()
    key = _key()
    assert ledger.consume(key, "skill@v1") is None
    refusal = ledger.consume(key, "skill@v9")
    assert refusal and "already spent" in refusal and "skill@v1" in refusal


def test_the_same_consumer_may_retry():
    ledger = EvidenceLedger()
    key = _key()
    assert ledger.consume(key, "skill@v1") is None
    assert ledger.consume(key, "skill@v1") is None
    assert ledger.consume(key, "skill@v1") is None


def test_the_ledger_is_bounded_and_does_not_grow_without_limit():
    ledger = EvidenceLedger(capacity=4)
    for i in range(50):
        assert ledger.consume(_key(_ab(3, f"corpus-{i}")), "skill@v1") is None
    assert len(ledger._records) == 4


# ---------------------------------------------------------------------- durable
def test_a_durable_ledger_refuses_the_replay_after_a_restart(tmp_path):
    path = tmp_path / "ledger.json"
    key = _key()
    assert DurableEvidenceLedger(path).consume(key, "skill@v1") is None
    reopened = DurableEvidenceLedger(path)                  # a fresh process would do this
    refusal = reopened.consume(key, "skill@v9")
    assert refusal and "already spent" in refusal
    assert reopened.consume(key, "skill@v1") is None         # the owner of the run may retry


def test_a_durable_ledger_writes_atomically_and_leaves_no_partial_file(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = DurableEvidenceLedger(path)
    for i in range(5):
        ledger.consume(_key(_ab(3, f"corpus-{i}")), f"skill@v{i}")
    assert not list(tmp_path.glob("*.tmp"))
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 5


def test_a_corrupt_ledger_file_is_not_deleted_and_records_keep_being_written(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    ledger = DurableEvidenceLedger(path)
    assert ledger.consume(_key(), "skill@v1") is None
    assert json.loads(path.read_text(encoding="utf-8")) != {}


def test_a_second_process_sees_the_spent_measurement(tmp_path):
    """Cross-process, not just cross-restart: two promotions running side by side."""
    path = tmp_path / "ledger.json"
    key = _key()
    assert DurableEvidenceLedger(path).consume(key, "skill@v1") is None
    root = Path(__file__).resolve().parents[2]
    program = (
        "import sys;"
        f"sys.path[:0]=[{str(root)!r},{str(root / 'bossman-core')!r}];"
        "from bossman.learning_guard.evidence_ledger import DurableEvidenceLedger;"
        f"print(DurableEvidenceLedger({str(path)!r}).consume({key!r}, 'skill@v9'))"
    )
    out = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "already spent" in out.stdout


# --------------------------------------------------------------------- selection
def test_default_ledger_is_durable_only_when_the_owner_points_it_at_a_file(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_LEDGER_PATH, raising=False)
    assert type(default_ledger()) is EvidenceLedger
    monkeypatch.setenv(ENV_LEDGER_PATH, str(tmp_path / "ledger.json"))
    durable = default_ledger()
    assert isinstance(durable, DurableEvidenceLedger) and durable.path == tmp_path / "ledger.json"


def test_the_process_wide_default_records_across_calls(monkeypatch):
    monkeypatch.delenv(ENV_LEDGER_PATH, raising=False)
    reset_default_ledger()
    key = _key()
    assert default_ledger().consume(key, "skill@v1") is None
    assert default_ledger().consume(key, "skill@v9")          # same in-memory ledger
    reset_default_ledger()
    assert default_ledger().consume(key, "skill@v9") is None  # reset clears it
