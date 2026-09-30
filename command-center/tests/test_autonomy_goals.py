"""Autonomy goal store: validation, state machine, guards, approvals bound to SHA, restart resume."""
from __future__ import annotations

import json

import pytest

from bcc.autonomy.goals import (TRANSITIONS, GoalError, GoalNotFound, GoalStore, GuardError, TransitionError,
                                measurable)
from bcc.autonomy.types import GOAL_STATES, Budget, Goal, Review

SHA1, SHA2 = "1" * 40, "2" * 40
D1, D2 = "a" * 64, "b" * 64


def mk(**kw) -> Goal:
    base = dict(goal_id="JEFF-0042", problem="Jeff leaks the underlying model name",
                desired_result="Jeff answers as Bossman", constraints=("prompts only",),
                acceptance_tests=("pytest:tests/test_identity.py::test_no_leak", "identity_redteam.leaks == 0"),
                budget=Budget(max_minutes=60, max_agent_turns=5, max_cost_usd=0.0), risk_tier="prompts_models",
                target_metric="identity_redteam.leaks", protected_metrics=("task_success", "latency_ms"))
    base.update(kw)
    return Goal(**base)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    return GoalStore(tmp_path, clock=Clock())


def rv(reviewer, verdict="APPROVE", sha=SHA1, diff=D1):
    return Review(goal_id="JEFF-0042", reviewer=reviewer, sha=sha, diff_sha256=diff, verdict=verdict, notes="n")


def to_review(store, author="jev"):
    store.create(mk())
    store.transition("JEFF-0042", "PLANNED", {"plan": "p"})
    store.transition("JEFF-0042", "BUILDING", {})
    store.set_candidate("JEFF-0042", SHA1, D1, author=author, branch="auto/jeff-0042", base_sha=SHA2,
                        target_branch="release/bossman-owner")
    store.transition("JEFF-0042", "TESTING", {})
    store.record_tests("JEFF-0042", SHA1, D1, True, {"passed": 12})
    return store.transition("JEFF-0042", "CLAUDE_REVIEW", {})


def to_user(store):
    to_review(store)
    store.record_review(rv("claude"))
    store.transition("JEFF-0042", "CODEX_REVIEW", {})
    store.record_review(rv("codex"))
    store.transition("JEFF-0042", "STAGING", {})
    store.record_staging("JEFF-0042", {"sha": SHA1, "passed": True, "checks": {"memory": True}})
    return store.transition("JEFF-0042", "USER_APPROVAL", {})


# ------------------------------------------------------------------ validation

@pytest.mark.parametrize("kw,needle", [
    ({"budget": None}, "budget"),
    ({"acceptance_tests": ()}, "acceptance"),
    ({"acceptance_tests": ("make it nicer",)}, "not measurable"),
    ({"target_metric": ""}, "target_metric"),
    ({"risk_tier": "yolo"}, "risk_tier"),
    ({"target_metric": "task_success"}, "protected"),
    ({"budget": Budget(max_minutes=0, max_agent_turns=1)}, "max_minutes"),
])
def test_create_refuses_unmeasurable_or_unbudgeted_goals(store, kw, needle):
    with pytest.raises(GoalError) as exc:
        store.create(mk(**kw))
    assert needle in str(exc.value)
    assert store.list() == []


def test_measurable():
    assert measurable("pytest:tests/x.py::t") and measurable("check:telegram_fake")
    assert measurable("latency p95 <= 800 ms") and measurable("zero identity leaks")
    assert not measurable("better vibes") and not measurable("")


def test_create_is_persisted_journaled_and_unique(store, tmp_path):
    rec = store.create(mk())
    assert rec["state"] == "PROPOSED"
    assert json.loads((tmp_path / "goals" / "JEFF-0042.json").read_text())["state"] == "PROPOSED"
    assert store.journal.entries()[0]["kind"] == "goal.created"
    with pytest.raises(GoalError):
        store.create(mk())
    with pytest.raises(GoalNotFound):
        store.get("../etc")


# ------------------------------------------------------------------ table

def test_transition_table_covers_every_state_and_complete_is_terminal():
    assert set(TRANSITIONS) == set(GOAL_STATES)
    assert TRANSITIONS["COMPLETE"] == frozenset()
    for src, dsts in TRANSITIONS.items():
        assert dsts <= set(GOAL_STATES)
        if src not in ("COMPLETE", "BLOCKED"):
            assert "BLOCKED" in dsts, src


@pytest.mark.parametrize("target", ["BUILDING", "STAGING", "DEPLOYED", "COMPLETE", "NOPE"])
def test_illegal_transitions_raise_and_do_not_journal(store, target):
    store.create(mk())
    before = len(store.journal.entries())
    with pytest.raises(TransitionError):
        store.transition("JEFF-0042", target, {})
    assert store.get("JEFF-0042")["state"] == "PROPOSED"
    assert len(store.journal.entries()) == before


def test_every_transition_is_journaled(store):
    to_review(store)
    moves = [(e["payload"]["from"], e["payload"]["to"]) for e in store.journal.entries(kind="goal.transition")]
    assert moves == [("PROPOSED", "PLANNED"), ("PLANNED", "BUILDING"), ("BUILDING", "TESTING"),
                     ("TESTING", "CLAUDE_REVIEW")]
    assert store.journal.verify().ok
    assert [h["to"] for h in store.get("JEFF-0042")["history"]] == ["PLANNED", "BUILDING", "TESTING", "CLAUDE_REVIEW"]


# ------------------------------------------------------------------ guards

def test_testing_needs_a_candidate_and_review_needs_passing_tests(store):
    store.create(mk())
    store.transition("JEFF-0042", "PLANNED", {})
    store.transition("JEFF-0042", "BUILDING", {})
    with pytest.raises(GuardError):
        store.transition("JEFF-0042", "TESTING", {})
    store.set_candidate("JEFF-0042", SHA1, D1)
    store.transition("JEFF-0042", "TESTING", {})
    with pytest.raises(GuardError):
        store.transition("JEFF-0042", "CLAUDE_REVIEW", {})
    store.record_tests("JEFF-0042", SHA1, D1, False)
    with pytest.raises(GuardError):
        store.transition("JEFF-0042", "CLAUDE_REVIEW", {})


def test_full_happy_path_to_complete(store):
    to_user(store)
    with pytest.raises(GuardError):                                  # no Apply yet
        store.transition("JEFF-0042", "DEPLOYED", {"owner_confirmed": True})
    store.record_user_decision("JEFF-0042", "apply", sha=SHA1, diff_sha256=D1)
    with pytest.raises(GuardError):                                  # Apply alone is not a release
        store.transition("JEFF-0042", "DEPLOYED", {})
    store.transition("JEFF-0042", "DEPLOYED", {"owner_confirmed": True})
    store.transition("JEFF-0042", "MONITORING", {})
    with pytest.raises(GuardError):
        store.transition("JEFF-0042", "COMPLETE", {"gate": "REJECT"})
    assert store.transition("JEFF-0042", "COMPLETE", {"gate": "ACCEPT"})["state"] == "COMPLETE"
    assert store.resumable() == []


def test_codex_review_needs_claude_approval_and_staging_needs_both(store):
    to_review(store)
    with pytest.raises(GuardError):
        store.transition("JEFF-0042", "CODEX_REVIEW", {})
    store.record_review(rv("claude"))
    store.transition("JEFF-0042", "CODEX_REVIEW", {})
    with pytest.raises(GuardError):
        store.transition("JEFF-0042", "STAGING", {})


def test_review_of_a_different_sha_blocks(store):
    to_review(store)
    rec = store.record_review(rv("claude", sha=SHA2))
    assert rec["state"] == "BLOCKED" and "different SHA" in rec["blocked_reason"]
    assert rec["approvals"] == {}


def test_reject_blocks_and_disagreement_blocks(store):
    to_review(store)
    rec = store.record_review(rv("claude", "REJECT"))
    assert rec["state"] == "BLOCKED" and "rejected" in rec["blocked_reason"]


def test_author_cannot_approve_own_unreviewed_candidate(store):
    to_review(store, author="claude")
    with pytest.raises(GuardError):
        store.record_review(rv("claude"))
    store.record_review(rv("codex"))                                  # the other reviewer first
    store.record_review(rv("claude"))                                 # now allowed
    assert GoalStore.approvals_valid(store.get("JEFF-0042"))


def test_changed_candidate_invalidates_approvals_and_blocks(store):
    to_user(store)
    assert GoalStore.approvals_valid(store.get("JEFF-0042"))
    rec = store.set_candidate("JEFF-0042", SHA2, D2)
    assert rec["state"] == "BLOCKED" and "candidate changed" in rec["blocked_reason"]
    assert rec["approvals"] == {} and rec["staging"] is None
    assert any(e["kind"] == "goal.approvals_invalidated" for e in store.journal.entries())
    with pytest.raises(GuardError):                                   # resume re-checks guards
        store.resume("JEFF-0042")


def test_revision_invalidates_both_approvals(store):
    to_user(store)
    rec = store.record_user_decision("JEFF-0042", "revise", sha=SHA1, diff_sha256=D1, note="shorter")
    assert rec["state"] == "BUILDING" and rec["approvals"] == {} and rec["revisions"] == 1


def test_user_reject_completes_with_outcome(store):
    to_user(store)
    rec = store.record_user_decision("JEFF-0042", "reject", sha=SHA1, diff_sha256=D1)
    assert rec["state"] == "COMPLETE" and rec["outcome"] == "rejected_by_user"


def test_user_approval_to_complete_only_as_reject(store):
    to_user(store)
    with pytest.raises(GuardError):
        store.transition("JEFF-0042", "COMPLETE", {"gate": "ACCEPT"})
    assert store.transition("JEFF-0042", "COMPLETE", {"outcome": "rejected_by_user"})["outcome"] ==         "rejected_by_user"


def test_line_b_style_evidence_binds_candidate_tests_reviews_and_staging(store):
    """A cycle with its own review gate carries the binding facts in the transition evidence."""
    store.create(mk())
    store.transition("JEFF-0042", "PLANNED", {})
    store.transition("JEFF-0042", "BUILDING", {})
    store.transition("JEFF-0042", "TESTING", {"sha": SHA1, "diff_sha256": D1, "writer": "claude"})
    assert store.get("JEFF-0042")["candidate"]["author"] == "claude"
    with pytest.raises(GuardError):                                   # no evidence hash: not tested
        store.transition("JEFF-0042", "CLAUDE_REVIEW", {"sha": SHA1})
    store.transition("JEFF-0042", "CLAUDE_REVIEW", {"sha": SHA1, "evidence_sha256": "e" * 64})
    with pytest.raises(GuardError):                                   # approval of another sha is ignored
        store.transition("JEFF-0042", "CODEX_REVIEW", {"claude": "APPROVE", "sha": SHA2})
    store.transition("JEFF-0042", "CODEX_REVIEW", {"claude": "APPROVE", "sha": SHA1})
    gate = {"verdicts": {"claude": {"verdict": "APPROVE", "key": [SHA1, D1, "e" * 64], "notes": ""},
                         "codex": {"verdict": "APPROVE", "key": [SHA1, D2, "e" * 64], "notes": ""}}}
    with pytest.raises(GuardError):                                   # codex approved another diff
        store.transition("JEFF-0042", "STAGING", {"approvals": gate})
    gate["verdicts"]["codex"]["key"] = [SHA1, D1, "e" * 64]
    store.transition("JEFF-0042", "STAGING", {"approvals": gate})
    rec = store.transition("JEFF-0042", "USER_APPROVAL", {"sha": SHA1, "staging_ok": True})
    assert GoalStore.approvals_valid(rec) and GoalStore.staging_passed(rec)
    with pytest.raises(GuardError):                                   # the loop cannot deploy by itself
        store.transition("JEFF-0042", "DEPLOYED", {"sha": SHA1, "user_approved": True})
    store.transition("JEFF-0042", "BUILDING", {"reason": "user asked for a revision"})
    assert store.get("JEFF-0042")["approvals"] == {}                  # revision invalidated both approvals
    rec = store.transition("JEFF-0042", "TESTING", {"sha": SHA2, "diff_sha256": D2})
    assert rec["candidate"]["sha"] == SHA2 and rec["tests"] is None


def test_docs_tests_auto_tier_deploy_needs_approvals_and_staging(tmp_path):
    s = GoalStore(tmp_path, clock=Clock())
    s.create(mk(risk_tier="docs_tests"))
    for st, ev in (("PLANNED", {}), ("BUILDING", {}), ("TESTING", {"sha": SHA1, "diff_sha256": D1}),
                   ("CLAUDE_REVIEW", {"sha": SHA1, "evidence_sha256": "e" * 64}),
                   ("CODEX_REVIEW", {"claude": "APPROVE", "sha": SHA1})):
        s.transition("JEFF-0042", st, ev)
    s.record_review(rv("codex"))
    s.transition("JEFF-0042", "STAGING", {})
    with pytest.raises(GuardError):                                   # no staging evidence yet
        s.transition("JEFF-0042", "DEPLOYED", {"auto_tier": True})
    assert s.transition("JEFF-0042", "DEPLOYED", {"auto_tier": True, "sha": SHA1, "staging_ok": True}
                        )["state"] == "DEPLOYED"


def test_auto_tier_is_refused_above_docs_tests(store):
    to_review(store)
    store.record_review(rv("claude"))
    store.transition("JEFF-0042", "CODEX_REVIEW", {})
    store.record_review(rv("codex"))
    store.transition("JEFF-0042", "STAGING", {})
    with pytest.raises(GuardError):
        store.transition("JEFF-0042", "DEPLOYED", {"auto_tier": True, "sha": SHA1, "staging_ok": True})


def test_user_decision_bound_to_sha(store):
    to_user(store)
    with pytest.raises(GuardError):
        store.record_user_decision("JEFF-0042", "apply", sha=SHA2, diff_sha256=D1)


def test_staging_on_other_sha_or_failure_blocks(store):
    to_review(store)
    store.record_review(rv("claude"))
    store.transition("JEFF-0042", "CODEX_REVIEW", {})
    store.record_review(rv("codex"))
    store.transition("JEFF-0042", "STAGING", {})
    rec = store.record_staging("JEFF-0042", {"sha": SHA1, "passed": False, "reason": "memory probe"})
    assert rec["state"] == "BLOCKED" and "staging failed" in rec["blocked_reason"]


def test_blocked_needs_reason_and_resumes_to_origin(store):
    store.create(mk())
    store.transition("JEFF-0042", "PLANNED", {})
    with pytest.raises(GuardError):
        store.transition("JEFF-0042", "BLOCKED", {})
    store.block("JEFF-0042", "ambiguous state")
    assert store.get("JEFF-0042")["blocked_from"] == "PLANNED"
    assert store.resume("JEFF-0042")["state"] == "PLANNED"


def test_budget_exhaustion_blocks(tmp_path):
    clock = Clock()
    s = GoalStore(tmp_path, clock=clock)
    s.create(mk())
    s.charge("JEFF-0042", agent_turns=5)
    assert s.get("JEFF-0042")["state"] == "PROPOSED"
    assert s.charge("JEFF-0042", agent_turns=1)["state"] == "BLOCKED"
    s2 = GoalStore(tmp_path / "b", clock=clock)
    s2.create(mk())
    assert s2.charge("JEFF-0042", cost_usd=0.01)["blocked_reason"] == "budget exhausted: cost"
    s3 = GoalStore(tmp_path / "c", clock=clock)
    s3.create(mk())
    clock.t += 7 * 24 * 3600
    assert s3.sweep_budgets() == []                   # PROPOSED for a week: the budget clock has not started
    s3.transition("JEFF-0042", "PLANNED", {})
    clock.t += 61 * 60
    assert s3.sweep_budgets() == ["JEFF-0042"]
    assert s3.get("JEFF-0042")["blocked_reason"] == "budget exhausted: time"


def test_restart_resumes_from_persisted_state(tmp_path):
    a = GoalStore(tmp_path, clock=Clock())
    to_review(a)
    a.record_review(rv("claude"))
    b = GoalStore(tmp_path, clock=Clock())                            # new process, same root
    assert [r["goal"]["goal_id"] for r in b.resumable()] == ["JEFF-0042"]
    assert b.transition("JEFF-0042", "CODEX_REVIEW", {})["state"] == "CODEX_REVIEW"
    assert b.goal("JEFF-0042") == mk()
    assert b.journal.verify().ok
