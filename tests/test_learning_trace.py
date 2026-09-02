"""Learning layer — схема, статусы, корпуса, редактирование, retrieval."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from learning import FORBIDDEN_FIELDS, LearningStore, ValidationError, case_id, redact_text, validate

ROOT = Path(__file__).resolve().parent.parent


def _case(**over) -> dict:
    base = {
        "task_id": "F-TEST-001", "model": "claude-fable-5-1", "agent": "lead",
        "start_sha": "aaaa", "end_sha": "bbbb", "task": "fix path escape",
        "symptom": "fs.search returned files outside workdir",
        "reproduction": ["python .agents/redteam/poc_search_glob.py"],
        "evidence": ["PoC printed canary line from ../../outside"],
        "root_cause_hypotheses": ["glob not resolved", "startswith prefix check"],
        "rejected_hypotheses": ["OS-level ACL — same failure on tmpfs"],
        "root_cause": "rglob(glob) never passed through _resolve containment",
        "relevant_code_paths": ["bossman/toolkit/files.py:fs_search"],
        "fix_strategy": "resolve each candidate; skip if not contained in root",
        "alternatives_considered": ["forbid '..' in glob (bypassable via junctions)"],
        "why_this_fix": "containment on resolved path covers junctions and encoded forms",
        "files_changed": ["bossman/toolkit/files.py"],
        "tests_added": ["tests/test_tools.py::test_fs_search_glob_cannot_escape_workdir"],
        "original_repro_result": "blocked", "adversarial_variants": ["junction+default glob: blocked"],
        "regression_result": "1234 passed", "external_verification": "pytest re-run of PoC + variants on fresh checkout",
        "generalizable_lessons": ["compare resolved paths, never string prefixes"],
        "teach_local_model": ["Recognize: path arg from model reaching a glob/walk"],
        "confidence": 0.9, "limitations": ["Windows junctions not exercised on Linux host"],
        "verified_by": ["pytest", "fable-5.1-redteam-retest"], "learning_status": "VERIFIED",
        "run_id": "run-lead-1", "principal_id": "agent:lead#run-lead-1",
        "verifiers": [{"principal_id": "tool:pytest#ci-77", "model_id": "", "role": "verifier",
                       "run_id": "ci-77", "independence_class": "external_tool"}],
        "evidence_records": [{"observed_at": 1_700_000_000.0, "collected_at": 1_700_000_001.0,
                              "task_id": "F-TEST-001", "run_id": "run-lead-1", "source": "pytest",
                              "principal_id": "tool:pytest#ci-77", "environment": "linux-ci",
                              "head_sha": "bbbb", "expected": "PoC blocked", "actual": "PoC blocked"}],
        "tags": {"domain": "security", "bug_class": "path_traversal", "component": "toolkit.files",
                 "severity": "HIGH", "security_boundary": "filesystem"},
        "outcome": "FIXED", "finding_ids": ["F-001"],
    }
    base.update(over)
    if "evidence_records" not in over:      # доказательство привязано к задаче/HEAD записи
        for r in base["evidence_records"]:
            r["task_id"], r["head_sha"] = base["task_id"], base["end_sha"]
    return base


def test_valid_case_passes_and_id_is_deterministic():
    assert validate(_case()) == []
    assert case_id(_case()) == case_id(_case(symptom="different wording"))
    assert case_id(_case()) != case_id(_case(end_sha="cccc"))


def test_required_fields_and_status_enum():
    c = _case(); del c["root_cause"]
    assert any("root_cause" in e for e in validate(c))
    assert any("learning_status" in e for e in validate(_case(learning_status="DONE")))
    assert any("unknown field" in e for e in validate(_case(extra="x")))
    assert any("confidence" in e for e in validate(_case(confidence=1.5)))


def test_verified_requires_evidence_and_independent_verifier():
    assert any("evidence" in e for e in validate(_case(evidence=[])))
    assert any("external_verification" in e for e in validate(_case(external_verification="")))
    assert any("verified_by" in e for e in validate(_case(verified_by=[])))
    # самосертификация: тот же principal / тот же run / та же модель под alias'ом
    same = {"principal_id": "agent:lead#run-lead-1", "independence_class": "cross_model", "model_id": "x"}
    assert any("independent" in e for e in validate(_case(verifiers=[same])))
    same_run = {"principal_id": "verifier:alias", "run_id": "run-lead-1", "independence_class": "cross_model"}
    assert any("independent" in e for e in validate(_case(verifiers=[same_run])))
    same_model = {"principal_id": "verifier:claude", "model_id": "claude-fable-5-1", "run_id": "r9",
                  "independence_class": "cross_model"}
    assert any("independent" in e for e in validate(_case(verifiers=[same_model])))
    # legacy: только строки в verified_by → UNVERIFIED-подобная ошибка, не VERIFIED
    legacy = _case(); legacy.pop("verifiers"); legacy.pop("evidence_records")
    assert any("typed verifiers" in e for e in validate(legacy))
    # свежесть/привязка доказательства
    bad_ev = dict(_case()["evidence_records"][0]); bad_ev["observed_at"] = 0
    assert any("observed_at" in e for e in validate(_case(evidence_records=[bad_ev])))
    other_task = dict(_case()["evidence_records"][0]); other_task["task_id"] = "OTHER"
    assert any("another task" in e for e in validate(_case(evidence_records=[other_task])))
    old_head = dict(_case()["evidence_records"][0]); old_head["head_sha"] = "aaaa"
    assert any("head_sha" in e for e in validate(_case(evidence_records=[old_head])))
    # FAILED_EXPERIMENT без верификации допустим
    assert validate(_case(learning_status="FAILED_EXPERIMENT", verified_by=[],
                          external_verification="", evidence=[])) == []


@pytest.mark.parametrize("field", sorted(FORBIDDEN_FIELDS))
def test_hidden_reasoning_fields_are_rejected(field):
    errs = validate(_case(**{field: "step 1: I think..."}))
    assert any("forbidden" in e for e in errs)


def test_secrets_are_redacted_before_storage_and_rejected_if_present(tmp_path):
    store = LearningStore(tmp_path / "data", tmp_path / "docs")
    c = store.add(_case(evidence=["header Authorization: Bearer abcdefgh12345678 seen",
                                  "canary BOSSMAN_TEST_SECRET_9F31A7 leaked", "api_key=sk-abcdefghijklmnop"]))
    joined = json.dumps(c)
    assert "BOSSMAN_TEST_SECRET_9F31A7" not in joined and "sk-abcdef" not in joined
    assert "abcdefgh12345678" not in joined and "***REDACTED***" in joined
    assert any("secret" in e for e in validate(_case(evidence=["token: BOSSMAN_TEST_SECRET_9F31A7"])))
    assert redact_text("plain text") == "plain text"


def test_corpora_are_separated_and_markdown_rendered(tmp_path):
    store = LearningStore(tmp_path / "data", tmp_path / "docs")
    store.add(_case())
    store.add(_case(task_id="F-TEST-002", learning_status="FAILED_EXPERIMENT",
                    verified_by=[], external_verification="", outcome="REJECTED"))
    store.add(_case(task_id="F-TEST-003", learning_status="UNVERIFIED", verified_by=[],
                    external_verification="", outcome="PARTIAL"))
    assert [c["task_id"] for c in store.verified()] == ["F-TEST-001"]
    assert {c["task_id"] for c in store.failed()} == {"F-TEST-002", "F-TEST-003"}
    assert all(c["learning_status"] != "VERIFIED" for c in store.failed())
    md = (tmp_path / "docs" / "F-TEST-001.md").read_text(encoding="utf-8")
    assert "## Root cause" in md and "## Teach local model" in md and "LEARNING_STATUS: VERIFIED" in md
    # повторная запись того же case_id — замена, не дубликат
    store.add(_case(confidence=0.95))
    assert len(store.verified()) == 1 and store.verified()[0]["confidence"] == 0.95


def test_add_rejects_invalid(tmp_path):
    store = LearningStore(tmp_path / "data", tmp_path / "docs")
    with pytest.raises(ValidationError):
        store.add(_case(verifiers=[{"principal_id": "agent:lead#run-lead-1", "independence_class": "cross_model"}]))
    assert not (tmp_path / "data" / "fix_cases.jsonl").exists()


def test_retrieval_filters_and_failed_are_warned(tmp_path):
    store = LearningStore(tmp_path / "data", tmp_path / "docs")
    store.add(_case())
    store.add(_case(task_id="F-TEST-004", finding_ids=["F-008"],
                    tags={"domain": "security", "bug_class": "fail_open", "component": "gateway",
                          "severity": "MEDIUM", "security_boundary": "egress"}))
    store.add(_case(task_id="F-TEST-005", learning_status="FAILED_EXPERIMENT", verified_by=[],
                    external_verification="", outcome="REJECTED",
                    tags={"domain": "security", "bug_class": "path_traversal",
                          "component": "toolkit.files", "severity": "HIGH"}))
    assert [c["task_id"] for c in store.retrieve(bug_class="path_traversal")] == ["F-TEST-001"]
    assert [c["task_id"] for c in store.retrieve(finding_id="F-008")] == ["F-TEST-004"]
    assert [c["task_id"] for c in store.retrieve(component="gateway", severity="MEDIUM")] == ["F-TEST-004"]
    assert [c["task_id"] for c in store.retrieve(text="junction")] == ["F-TEST-001", "F-TEST-004"]
    assert store.retrieve(domain="research") == []
    both = store.retrieve(bug_class="path_traversal", include_failed=True)
    assert {c["task_id"] for c in both} == {"F-TEST-001", "F-TEST-005"}
    failed = next(c for c in both if c["task_id"] == "F-TEST-005")
    assert "do NOT treat as preferred" in failed["retrieval_warning"]
    assert store.compact(store.verified()[0])["retrieval_warning"] is None
    assert all(c["learning_status"] == "VERIFIED" for c in store.export_sanitized())


def test_cli_validate_and_retrieve(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(_case()), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "learning.trace", "validate", str(f)],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "OK"
    f.write_text(json.dumps(_case(verifiers=[{"principal_id": "agent:lead#run-lead-1",
                                              "independence_class": "human"}])), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "learning.trace", "validate", str(f)],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1 and "independent" in r.stdout


def test_repo_corpus_files_validate():
    """Реальные корпуса в репозитории валидны и разделены по статусу."""
    store = LearningStore()
    for c in store.verified():
        assert validate(c) == [], c.get("task_id")
        assert c["learning_status"] == "VERIFIED"
    for c in store.failed():
        assert validate(c) == [], c.get("task_id")
        assert c["learning_status"] != "VERIFIED"
