"""Deep Fix Mode — гейты состояния: воспроизведение до патча, область патча,
варианты после патча, регрессия, независимый верификатор, авто-learning-record."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from bossman import deep_fix as df
from bossman.deep_fix import DeepFixGateError, DeepFixRun, Evidence, Principal

ROOT = Path(__file__).resolve().parents[2]


CODER = Principal(principal_id="agent:qwen-14b#run-1", model_id="qwen-14b", role="coder", run_id="run-1")
VERIFIER = Principal(principal_id="tool:pytest#run-2", model_id="", role="verifier", run_id="run-2",
                     independence_class="external_tool")


def _run(**kw) -> DeepFixRun:
    kw.setdefault("run_id", "run-1"); kw.setdefault("head_sha", "abc123"); kw.setdefault("environment", "env-a")
    kw.setdefault("coder_principal", CODER)
    return DeepFixRun(task_id="T-1", coder="qwen-14b", allowed_paths=("bossman/toolkit/",), **kw)


def _obs(passed=True, detail="file reopened: contained", **over) -> Evidence:
    base = dict(kind="observation", detail=detail, passed=passed, source="verifier:pytest",
                task_id="T-1", run_id="run-1", principal_id=VERIFIER.principal_id,
                environment="env-a", head_sha="abc123", expected="contained", actual="contained")
    base.update(over)
    now = time.time()
    base.setdefault("at", now)
    if "collected_at" not in over:      # ledger time: not before observation, never in the future
        base["collected_at"] = max(float(base["at"]), now)
    return Evidence(**base)


def _happy(run: DeepFixRun) -> DeepFixRun:
    run.context_ready(repo_map=["bossman/"], targeted=["bossman/toolkit/files.py"])
    run.reproduced(Evidence("repro", "poc: canary read outside root", True, "pytest"))
    run.root_cause_proposed(["glob unresolved", "startswith"], ["startswith: resolved ok"], "glob unresolved")
    run.fix_planned("resolve each candidate and check containment")
    run.patched(["bossman/toolkit/files.py"])
    run.focused_tested([Evidence("test", "tests/test_tools.py::test_glob", True, "pytest")])
    run.adversarial_tested(Evidence("repro", "poc after patch: blocked", False, "pytest"),
                           [Evidence("variant", "junction variant: blocked", False, "pytest")])
    run.regression_tested(Evidence("regression", "1234 passed", True, "pytest"))
    return run


def test_flag_off_by_default(monkeypatch):
    monkeypatch.delenv(df.FLAG, raising=False)
    assert df.enabled() is False
    monkeypatch.setenv(df.FLAG, "1")
    assert df.enabled() is True


def test_happy_path_reaches_done_with_verified_record():
    run = _happy(_run())
    run.verified(verifier=VERIFIER, evidence=_obs())
    assert run.state == "VERIFIED"
    rec = run.learning_record(model="qwen", start_sha="a", end_sha="abc123", task="fix", symptom="escape",
                              generalizable_lessons=["resolve then contain"], tags={"domain": "security"})
    assert rec["learning_status"] == "VERIFIED" and rec["verified_by"] == [VERIFIER.principal_id]
    assert run.state == "LEARNING_RECORDED"
    run.done()
    assert run.state == "DONE"
    sys.path.insert(0, str(ROOT))
    from learning import validate
    assert validate(rec) == [], validate(rec)


def test_reproduction_required_before_patch():
    run = _run()
    run.context_ready(repo_map=[], targeted=["x.py"])
    with pytest.raises(DeepFixGateError, match="reproduction missing"):
        run.reproduced(None)
    run.reproduced(None, not_reproducible_reason="needs docker")
    assert run.state == "NOT_REPRODUCIBLE"
    with pytest.raises(DeepFixGateError):
        run.fix_planned("x")


def test_patch_scope_is_enforced():
    run = _run()
    run.context_ready(repo_map=[], targeted=["bossman/toolkit/files.py"])
    run.reproduced(Evidence("repro", "r", True))
    run.root_cause_proposed(["h"], [], "h")
    run.fix_planned("p")
    with pytest.raises(DeepFixGateError, match="outside the declared scope"):
        run.patched(["bossman/toolkit/files.py", "bossman/runner.py"])
    run.patched(["bossman/toolkit/files.py"])
    assert run.state == "PATCHED"


def test_variant_required_and_original_repro_must_fail_after_patch():
    run = _run()
    run.context_ready(repo_map=[], targeted=["bossman/toolkit/files.py"])
    run.reproduced(Evidence("repro", "r", True))
    run.root_cause_proposed(["h"], [], "h")
    run.fix_planned("p")
    run.patched(["bossman/toolkit/files.py"])
    run.focused_tested([Evidence("test", "t", True)])
    with pytest.raises(DeepFixGateError, match="variant"):
        run.adversarial_tested(Evidence("repro", "still blocked", False), [])
    run.adversarial_tested(Evidence("repro", "still reproduces", True), [Evidence("variant", "v", False)])
    assert run.state == "VERIFICATION_FAILED"
    rec = run.learning_record(model="m", start_sha="a", end_sha="b", task="t", symptom="s")
    assert rec["learning_status"] == "FAILED_EXPERIMENT"


def test_coder_cannot_self_certify():
    run = _happy(_run())
    with pytest.raises(DeepFixGateError, match="typed Principal"):
        run.verified(verifier="verifier:qwen-14b", evidence=_obs())          # строка ≠ identity
    same = Principal(principal_id=CODER.principal_id, model_id="qwen-14b", role="verifier", run_id="run-1")
    with pytest.raises(DeepFixGateError, match="independent"):
        run.verified(verifier=same, evidence=_obs())
    with pytest.raises(DeepFixGateError, match="fresh observation"):
        run.verified(verifier=VERIFIER, evidence=_obs(kind="test"))
    run.verified(verifier=VERIFIER, evidence=_obs(passed=False, detail="not ok"))
    assert run.state == "VERIFICATION_FAILED"


def test_regression_failure_is_terminal_and_record_is_not_verified():
    run = _run()
    run.context_ready(repo_map=[], targeted=["bossman/toolkit/files.py"])
    run.reproduced(Evidence("repro", "r", True))
    run.root_cause_proposed(["h"], [], "h")
    run.fix_planned("p")
    run.patched(["bossman/toolkit/files.py"])
    run.focused_tested([Evidence("test", "t", True)])
    run.adversarial_tested(Evidence("repro", "blocked", False), [Evidence("variant", "v", False)])
    run.regression_tested(Evidence("regression", "3 failed", False))
    assert run.state == "REGRESSION"
    rec = run.learning_record(model="m", start_sha="a", end_sha="b", task="t", symptom="s")
    assert rec["learning_status"] == "FAILED_EXPERIMENT" and rec["verified_by"] == []


def test_store_learning_record_routes_partial_to_failed_corpus(tmp_path, monkeypatch):
    run = _run()
    run.context_ready(repo_map=[], targeted=["bossman/toolkit/files.py"])
    rec = run.learning_record(model="m", start_sha="a", end_sha="b", task="t", symptom="s")
    assert rec["learning_status"] == "PARTIAL"
    sys.path.insert(0, str(ROOT))
    import learning.trace as lt
    monkeypatch.setattr(lt, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(lt, "DOCS_DIR", tmp_path / "docs")
    saved = df.store_learning_record(rec)
    assert saved is not None and (tmp_path / "data" / "failed_experiments.jsonl").exists()
    assert lt.LearningStore(tmp_path / "data", tmp_path / "docs").verified() == []
