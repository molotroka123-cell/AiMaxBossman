"""Audit 2026-09-28: the declared EVIDENCE_TTL_S is enforced and a placeholder
head_sha ("unknown") is not a revision binding for VERIFIED."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learning import trace  # noqa: E402
from learning.lessons import CoachingEpisode, LessonBook, LessonError, Provenance  # noqa: E402

_spec = importlib.util.spec_from_file_location("_root_test_learning_trace_fresh",
                                               Path(__file__).with_name("test_learning_trace.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_case = _mod._case

VERIFIER = {"principal_id": "tool:pytest#hidden", "independence_class": "external_tool", "model_id": "", "run_id": "ci-1"}
YEAR = 365 * 24 * 3600


def _book(tmp_path: Path):
    book = LessonBook(tmp_path / "learning")
    ep = CoachingEpisode(attempt_id="a1", task_id="t1", project_id="p", failure_observation="ImportError on start",
                         correction="Install the missing package before running the tests.", source="student",
                         provenance=Provenance(who="student-1", what="fix", model="qwen"), model="qwen")
    book.save(ep)
    return book, ep.lesson_id


@pytest.mark.parametrize("restated_collected", [True, False])
def test_year_old_observation_does_not_verify_a_lesson(tmp_path, restated_collected):
    book, lid = _book(tmp_path)
    old = time.time() - YEAR
    evidence = {"source": "pytest", "expected": "pass", "actual": "pass", "head_sha": "abc123",
                "environment": "win32", "observed_at": old}
    if restated_collected:
        evidence["collected_at"] = old
    with pytest.raises(LessonError, match="EVIDENCE_TTL_S"):
        book.verify(lid, verifier=VERIFIER, evidence=evidence)
    assert book.retrieve(project_id="p") == []


def test_fresh_observation_still_verifies(tmp_path):
    book, lid = _book(tmp_path)
    rec = book.verify(lid, verifier=VERIFIER, evidence={"source": "pytest", "expected": "pass", "actual": "pass",
                                                       "head_sha": "abc123", "environment": "win32"})
    assert rec["learning_status"] == "VERIFIED" and len(book.retrieve(project_id="p")) == 1


def test_missing_head_sha_is_not_bound_to_unknown(tmp_path):
    book, lid = _book(tmp_path)
    with pytest.raises(trace.ValidationError, match="head_sha"):
        book.verify(lid, verifier=VERIFIER, evidence={"source": "pytest", "expected": "pass", "actual": "pass",
                                                     "environment": "win32"})


def test_store_refuses_unknown_head_sha_and_stale_collection():
    case = _case(end_sha="")
    case["evidence_records"][0]["head_sha"] = "unknown"
    assert "evidence_record head_sha 'unknown' is not bound to a revision" in trace.validate(case)
    stale = _case()
    rec = stale["evidence_records"][0]
    rec["collected_at"] = rec["observed_at"] + trace.EVIDENCE_TTL_S + 1
    assert any("EVIDENCE_TTL_S" in e for e in trace.validate(stale))
    assert trace.validate(_case()) == []                   # a dated, promptly collected record is fine
