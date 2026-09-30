"""Line B skills: episodic traces, redaction, the compile conditions, versioned revocable store."""
from __future__ import annotations

import dataclasses

import pytest

from bcc.autonomy import skills as S
from bcc.autonomy.types import Review as ContractReview

SK_PREFIX = "sk-"


def trace(**over):
    base = dict(goal_id="G", start_state={"branch": "x"}, instruction="restart the stuck ollama model",
                actions=[{"action": "run_tests", "target": "isolated_worktree", "request_hash": "a" * 64, "ok": True}],
                observations=["model answered"], errors=[], recovery=[], result={"ok": True},
                reviews=[], user_decision=None, app="ollama", env="windows")
    base.update(over)
    return S.capture_trace(**base)


def spec_for(t, **over):
    base = dict(name="restart-ollama-model", description="restart the stuck ollama model safely", app="ollama",
                env="windows", parameters={"model": "model tag"}, preconditions=["ollama service is running"],
                steps=[{"action": "unload_model", "args": {"model": "{model}"}}],
                failure_detection=["canary answer is wrong"], rollback=["reload the previous model"],
                timeout_s=120, source_trace_hash=t.trace_hash(), keywords=["ollama", "restart"])
    base.update(over)
    return S.SkillSpec(**base)


def approvals(t, spec, who=("claude", "codex"), **over):
    return [ContractReview(goal_id=over.get("goal_id", t.goal_id), reviewer=r, sha=over.get("sha", spec.artifact_hash()),
                           diff_sha256=over.get("diff", t.trace_hash()), verdict="APPROVE", notes="") for r in who]


def test_redaction_removes_secrets_and_transient_values():
    raw = {"cmd": r"C:\Users\timur\proj run", "mail": "me@example.com", "at": "2026-09-29T10:00:00Z",
           "url": "http://127.0.0.1:8801/api", "tok": SK_PREFIX + "A" * 30, "pw": "password: hunter2",
           "request_hash": "b" * 64}
    red = S.redact(raw)
    blob = str(red)
    for leaked in ("timur", "me@example.com", "10:00:00", "8801", "A" * 30, "hunter2"):
        assert leaked not in blob
    assert red["request_hash"] == "b" * 64
    assert S.is_redacted(red) and not S.is_redacted(raw)


def test_capture_redacts_immediately():
    t = trace(observations=["token=" + "Z" * 20])
    assert "Z" * 20 not in str(t.as_dict())


def test_compile_happy_path():
    t = trace()
    spec = spec_for(t)
    out = S.compile_skill(t, spec, acceptance_passed=True, approvals=approvals(t, spec), staging_successes=1)
    assert out.evidence == [t.trace_hash()]


@pytest.mark.parametrize("case,expect", [
    ("acceptance", "1:"), ("one_approval", "2:"), ("trace_only_approval", "2:"), ("staging", "5:"),
    ("no_rollback", "4:"), ("no_timeout", "timeout"), ("web_origin", "origin"), ("no_request_hash", "request_hash"),
    ("unredacted", "3:"),
])
def test_compile_refuses_unless_all_conditions_hold(case, expect):
    t = trace()
    spec = spec_for(t)
    kw = dict(acceptance_passed=True, approvals=approvals(t, spec), staging_successes=1)
    if case == "acceptance":
        kw["acceptance_passed"] = False
    elif case == "one_approval":
        kw["approvals"] = approvals(t, spec, who=("claude",))
    elif case == "trace_only_approval":
        kw["approvals"] = approvals(t, spec, sha=t.trace_hash())       # approved the trace, not the artifact
    elif case == "staging":
        kw["staging_successes"] = 0
    elif case == "no_rollback":
        spec = dataclasses.replace(spec, rollback=[])
        kw["approvals"] = approvals(t, spec)
    elif case == "no_timeout":
        spec = dataclasses.replace(spec, timeout_s=0)
        kw["approvals"] = approvals(t, spec)
    elif case == "web_origin":
        t = trace(origin="web")
        spec = spec_for(t)
        kw["approvals"] = approvals(t, spec)
    elif case == "no_request_hash":
        t = trace(actions=[{"action": "curl", "ok": True}])
        spec = spec_for(t)
        kw["approvals"] = approvals(t, spec)
    elif case == "unredacted":
        spec = dataclasses.replace(spec, description="login as me@example.com")
        kw["approvals"] = approvals(t, spec)
    with pytest.raises(S.SkillRefused) as err:
        S.compile_skill(t, spec, **kw)
    assert expect in str(err.value)


def test_changed_artifact_invalidates_approvals():
    t = trace()
    spec = spec_for(t)
    appr = approvals(t, spec)
    changed = dataclasses.replace(spec, steps=[{"action": "kill_all"}])
    with pytest.raises(S.SkillRefused):
        S.compile_skill(t, changed, acceptance_passed=True, approvals=appr, staging_successes=1)


def test_store_versions_revocation_confidence_expiry_and_retrieval(tmp_path):
    now = [1000.0]
    store = S.SkillStore(tmp_path, clock=lambda: now[0], ttl_s=100)
    t = trace()
    spec = S.compile_skill(t, spec_for(t), acceptance_passed=True, approvals=approvals(t, spec_for(t)),
                           staging_successes=1)
    v1 = store.add(spec)
    v2 = store.add(spec)
    assert (v1.version, v2.version) == (1, 2)
    assert [s.version for s in store.retrieve("ollama model is stuck, restart it", app="ollama")] == [2]
    assert store.retrieve("ollama restart", app="telegram") == []
    assert store.retrieve("completely unrelated words") == []
    store.revoke("restart-ollama-model", 2, "bad step")
    assert [s.version for s in store.retrieve("ollama restart")] == [1]
    s = store.record_outcome("restart-ollama-model", 1, success=False)
    assert s.confidence < S.MIN_CONFIDENCE and store.retrieve("ollama restart") == []
    s = store.record_outcome("restart-ollama-model", 1, success=True)
    assert s.successes == 1 and s.failures == 1
    now[0] += 1000
    assert store.retrieve("ollama restart", min_confidence=0.0) == []            # expired
