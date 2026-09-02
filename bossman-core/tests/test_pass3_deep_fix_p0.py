"""PASS3 P0-01 (canonical path containment) и P0-02 (typed verifier identity +
fresh, bound evidence) для Deep Fix Mode."""
from __future__ import annotations

import os
import time

import pytest

from bossman.deep_fix import DeepFixGateError, DeepFixRun, Evidence, Principal

CODER = Principal(principal_id="agent:qwen#r1", model_id="qwen-14b", role="coder", run_id="r1")


def _run(**kw) -> DeepFixRun:
    kw.setdefault("run_id", "r1"); kw.setdefault("head_sha", "h1"); kw.setdefault("environment", "e1")
    kw.setdefault("coder_principal", CODER)
    return DeepFixRun(task_id="T", coder="qwen", allowed_paths=("bossman/toolkit/",), **kw)


# ------------------------------------------------------------ P0-01

@pytest.mark.parametrize("bad", [
    "bossman/toolkit/../runner.py",            # REPRO: раньше True
    "bossman/toolkit/sub/../../runner.py",
    "../bossman/toolkit/x.py",
    "/etc/passwd", "//server/share/x.py",
    "C:\\\\repo\\\\bossman\\\\toolkit\\\\x.py", "c:/repo/x.py",
    "bossman\\\\toolkit\\\\..\\\\runner.py",
    "bossman/toolkit/x\x00.py", "", ".", "bossman/toolkit/./../runner.py",
])
def test_repro_traversal_and_absolute_forms_refused(bad):
    assert _run()._allowed(bad) is False, bad


@pytest.mark.parametrize("ok", ["bossman/toolkit/files.py", "bossman/toolkit/./files.py",
                                "bossman\\\\toolkit\\\\files.py", "bossman/toolkit/sub/deep.py"])
def test_canonical_paths_inside_scope_allowed(ok):
    assert _run()._allowed(ok) is True, ok


def test_symlink_escape_refused_with_repo_root(tmp_path):
    root = tmp_path / "repo"; (root / "bossman" / "toolkit").mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "secret.py").write_text("x")
    os.symlink(outside / "secret.py", root / "bossman" / "toolkit" / "link.py")
    os.symlink(outside, root / "bossman" / "toolkit" / "linkdir", target_is_directory=True)
    (root / "bossman" / "toolkit" / "real.py").write_text("y")
    run = _run(repo_root=str(root))
    assert run._allowed("bossman/toolkit/real.py") is True
    assert run._allowed("bossman/toolkit/new_file.py") is True          # ещё не существует, родитель внутри
    assert run._allowed("bossman/toolkit/link.py") is False              # symlink-файл наружу
    assert run._allowed("bossman/toolkit/linkdir/secret.py") is False    # через symlink-каталог
    run.context_ready(repo_map=[], targeted=["bossman/toolkit/real.py"])
    run.reproduced(Evidence("repro", "r", True, "pytest"))
    run.root_cause_proposed(["h"], [], "h")
    run.fix_planned("p")
    with pytest.raises(DeepFixGateError, match="outside the declared scope"):
        run.patched(["bossman/toolkit/link.py"])


def test_file_replaced_by_symlink_between_check_and_use(tmp_path):
    """TOCTOU: файл был обычным при проверке, стал symlink наружу к моменту patched()."""
    root = tmp_path / "repo"; (root / "bossman" / "toolkit").mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "s.py").write_text("x")
    f = root / "bossman" / "toolkit" / "f.py"; f.write_text("ok")
    run = _run(repo_root=str(root))
    assert run._allowed("bossman/toolkit/f.py") is True
    f.unlink(); os.symlink(outside / "s.py", f)
    assert run._allowed("bossman/toolkit/f.py") is False              # повторная проверка перед use


# ------------------------------------------------------------ P0-02

VER = Principal(principal_id="tool:pytest#r2", role="verifier", run_id="r2", independence_class="external_tool")


def _ready(run: DeepFixRun) -> DeepFixRun:
    run.context_ready(repo_map=[], targeted=["bossman/toolkit/files.py"])
    run.reproduced(Evidence("repro", "r", True, "pytest"))
    run.root_cause_proposed(["h"], [], "h")
    run.fix_planned("p")
    run.patched(["bossman/toolkit/files.py"])
    run.focused_tested([Evidence("test", "t", True)])
    run.adversarial_tested(Evidence("repro", "blocked", False), [Evidence("variant", "v", False)])
    run.regression_tested(Evidence("regression", "ok", True))
    return run


def _obs(**over) -> Evidence:
    base = dict(kind="observation", detail="reopened", passed=True, source="pytest", task_id="T",
                run_id="r1", principal_id=VER.principal_id, environment="e1", head_sha="h1",
                expected="contained", actual="contained")
    base.update(over)
    now = time.time()
    base.setdefault("at", now)
    if "collected_at" not in over:      # ledger time: not before observation, never in the future
        base["collected_at"] = max(float(base["at"]), now)
    return Evidence(**base)


def test_alias_of_same_principal_is_not_independent():
    run = _ready(_run())
    alias = Principal(principal_id=CODER.principal_id, model_id="qwen-14b", role="verifier",
                      run_id="r9", independence_class="cross_model")
    with pytest.raises(DeepFixGateError, match="same principal"):
        run.verified(verifier=alias, evidence=_obs())
    same_run = Principal(principal_id="other", model_id="gpt", role="verifier", run_id="r1",
                         independence_class="cross_model")
    with pytest.raises(DeepFixGateError, match="same run"):
        run.verified(verifier=same_run, evidence=_obs())
    same_model = Principal(principal_id="verifier:qwen-14b", model_id="qwen-14b", role="verifier",
                           run_id="r2", independence_class="cross_model")
    with pytest.raises(DeepFixGateError, match="same principal \\(alias\\)|same model"):
        run.verified(verifier=same_model, evidence=_obs())
    declared_same = Principal(principal_id="x", model_id="gpt", role="verifier", run_id="r2",
                              independence_class="same_model")
    with pytest.raises(DeepFixGateError, match="not independent"):
        run.verified(verifier=declared_same, evidence=_obs())
    assert run.state == "REGRESSION_TESTED"


@pytest.mark.parametrize("bad, why", [
    ({"at": 0.0}, "observed_at"),
    ({"source": ""}, "source"),
    ({"run_id": "r7"}, "another run"),
    ({"task_id": "OTHER"}, "another task"),
    ({"task_id": ""}, "not bound"),
    ({"head_sha": "old"}, "another head"),
    ({"environment": "e2"}, "environment"),
    ({"expected": ""}, "expected/actual"),
    ({"principal_id": "someone-else"}, "different principal"),
])
def test_stale_or_unbound_evidence_never_verifies(bad, why):
    run = _ready(_run())
    with pytest.raises(DeepFixGateError, match=why):
        run.verified(verifier=VER, evidence=_obs(**bad))
    assert run.state == "REGRESSION_TESTED"


def test_evidence_before_plan_or_patch_and_expired_ttl_refused():
    run = _run()
    early = _obs(at=time.time() - 10)                    # наблюдение ДО плана/патча
    _ready(run)
    with pytest.raises(DeepFixGateError, match="before the plan was bound|before the patch"):
        run.verified(verifier=VER, evidence=early)
    with pytest.raises(DeepFixGateError, match="TTL"):
        run.verified(verifier=VER, evidence=_obs(ttl_s=1), now=time.time() + 5)
    with pytest.raises(DeepFixGateError, match="collected_at"):
        run.verified(verifier=VER, evidence=_obs(collected_at=time.time() - 3600))
    run.verified(verifier=VER, evidence=_obs())
    assert run.state == "VERIFIED"
    rec = run.learning_record(model="qwen", start_sha="a", end_sha="b", task="t", symptom="s")
    assert rec["verifiers"][0]["independence_class"] == "external_tool"
    assert rec["evidence_records"][0]["head_sha"] == "h1" and rec["run_id"] == "r1"
