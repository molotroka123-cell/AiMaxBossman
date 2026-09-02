"""Deep Fix Mode — гейты состояния: воспроизведение до патча, область патча,
варианты после патча, регрессия, независимый верификатор, авто-learning-record."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from bossman import deep_fix as df
from bossman.deep_fix import DeepFixGateError, DeepFixRun, Evidence

ROOT = Path(__file__).resolve().parents[2]


def _run(**kw) -> DeepFixRun:
    return DeepFixRun(task_id="T-1", coder="qwen-14b", allowed_paths=("bossman/toolkit/",), **kw)


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
    run.verified(verifier="verifier:fable", evidence=Evidence("observation", "file reopened: contained", True, "verifier"))
    assert run.state == "VERIFIED"
    rec = run.learning_record(model="qwen", start_sha="a", end_sha="b", task="fix", symptom="escape",
                              generalizable_lessons=["resolve then contain"], tags={"domain": "security"})
    assert rec["learning_status"] == "VERIFIED" and rec["verified_by"] == ["verifier:fable"]
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
    with pytest.raises(DeepFixGateError, match="independent"):
        run.verified(verifier="qwen-14b", evidence=Evidence("observation", "ok", True))
    with pytest.raises(DeepFixGateError, match="fresh observation"):
        run.verified(verifier="other", evidence=Evidence("test", "claims ok", True))
    run.verified(verifier="other", evidence=Evidence("observation", "not ok", False))
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
    assert not (tmp_path / "data" / "fix_cases.jsonl").exists()
