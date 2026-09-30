"""Line B review gate: dual approval bound to (sha, diff hash, evidence hash)."""
from __future__ import annotations

import json

from .autonomy_fakes import FakeJournal
from bcc.autonomy.review import Candidate, ReviewGate, parse_review
from bcc.autonomy.types import Review

C1 = Candidate(sha="a" * 40, diff_sha256="d" * 64, evidence_sha256="e" * 64, author="claude", author_session="w1")
C2 = Candidate(sha="b" * 40, diff_sha256="f" * 64, evidence_sha256="e" * 64, author="claude", author_session="w2")


def rv(reviewer, verdict="APPROVE", cand=C1, notes="ok"):
    return Review(goal_id="G", reviewer=reviewer, sha=cand.sha, diff_sha256=cand.diff_sha256, verdict=verdict,
                  notes=notes)


def text(verdict="APPROVE", cand=C1, **extra):
    return "Looks fine.\n" + json.dumps({"verdict": verdict, "notes": "n", "sha": cand.sha,
                                         "diff_sha256": cand.diff_sha256, **extra})


def gate(**kw):
    g = ReviewGate("G", journal=FakeJournal(), **kw)
    g.set_candidate(C1)
    return g


def test_parse_accepts_exact_bound_verdict():
    review, why = parse_review(text(), goal_id="G", reviewer="codex", candidate=C1)
    assert why == "" and review.verdict == "APPROVE" and review.sha == C1.sha


def test_parse_rejects_malformed_output():
    bad = [
        "APPROVE",                                                    # prose only
        text(verdict="LGTM"),                                        # unknown verdict
        text(cand=C2),                                               # bound to another sha
        text(extra_field=1),                                         # extra fields
        text() + "\n" + text(verdict="REJECT"),                      # conflicting verdicts
        json.dumps({"verdict": "APPROVE", "notes": 1, "sha": C1.sha, "diff_sha256": C1.diff_sha256}),
    ]
    for t in bad:
        review, why = parse_review(t, goal_id="G", reviewer="codex", candidate=C1)
        assert review is None and why, t
    assert parse_review(text(), goal_id="G", reviewer="nemotron", candidate=C1)[0] is None


def test_both_approvals_on_same_tuple_are_required():
    g = gate()
    assert g.submit(rv("claude"), evidence_sha256=C1.evidence_sha256, session_id="r1") == "accepted"
    assert g.status() == "PENDING"
    g.submit(rv("codex"), evidence_sha256=C1.evidence_sha256, session_id="r2")
    assert g.status() == "APPROVED" and g.approvals() == {"claude": True, "codex": True}


def test_new_commit_invalidates_both_approvals():
    g = gate()
    g.submit(rv("claude"), evidence_sha256=C1.evidence_sha256, session_id="r1")
    g.submit(rv("codex"), evidence_sha256=C1.evidence_sha256, session_id="r2")
    assert g.set_candidate(C2) is True
    assert g.status() == "PENDING" and g.approvals() == {"claude": False, "codex": False}
    assert g.submit(rv("claude", cand=C1), evidence_sha256=C1.evidence_sha256, session_id="r3") == "stale"
    assert any(h["kind"] == "approvals_invalidated" for h in g.s.history)


def test_changed_test_evidence_is_a_new_candidate():
    g = gate()
    g.submit(rv("claude"), evidence_sha256=C1.evidence_sha256, session_id="r1")
    assert g.submit(rv("codex"), evidence_sha256="0" * 64, session_id="r2") == "stale"
    g.set_candidate(Candidate(C1.sha, C1.diff_sha256, "0" * 64, "claude", "w1"))
    assert g.approvals() == {"claude": False, "codex": False}


def test_writer_session_cannot_review_and_author_never_approves_alone():
    g = gate()
    assert g.submit(rv("claude"), evidence_sha256=C1.evidence_sha256, session_id="w1") == "self_session"
    g.submit(rv("claude"), evidence_sha256=C1.evidence_sha256, session_id="r1")    # author, fresh read-only
    assert g.s.verdicts["claude"]["own_revision"] is True
    assert g.status() == "PENDING"                                                # not alone
    g.submit(rv("codex"), evidence_sha256=C1.evidence_sha256, session_id="r2")    # independent approval
    assert g.status() == "APPROVED"


def test_reject_blocks():
    g = gate()
    g.submit(rv("claude", "REJECT"), evidence_sha256=C1.evidence_sha256, session_id="r1")
    assert g.status() == "BLOCKED" and "rejected by claude" in g.blocked_reason
    assert g.submit(rv("codex"), evidence_sha256=C1.evidence_sha256, session_id="r2") == "blocked"


def test_disagreement_blocks_at_threshold():
    g = gate(max_disagreements=1)
    g.submit(rv("claude"), evidence_sha256=C1.evidence_sha256, session_id="r1")
    g.submit(rv("codex", "REQUEST_CHANGES"), evidence_sha256=C1.evidence_sha256, session_id="r2")
    assert g.status() == "BLOCKED" and "disagree" in g.blocked_reason


def test_disagreement_twice_blocks_with_threshold_two():
    g = gate(max_disagreements=2)
    g.submit(rv("claude"), evidence_sha256=C1.evidence_sha256, session_id="r1")
    g.submit(rv("codex", "REQUEST_CHANGES", notes="fix x"), evidence_sha256=C1.evidence_sha256, session_id="r2")
    assert g.status() == "CHANGES_REQUESTED" and "fix x" in g.change_notes()
    g.set_candidate(C2)
    g.submit(rv("claude", cand=C2), evidence_sha256=C2.evidence_sha256, session_id="r3")
    g.submit(rv("codex", "REQUEST_CHANGES", cand=C2), evidence_sha256=C2.evidence_sha256, session_id="r4")
    assert g.status() == "BLOCKED" and g.disagreements == 2


def test_failures_are_not_approvals_and_block():
    g = gate()
    g.record_failure("codex", "malformed verdict")
    assert g.status() == "BLOCKED" and g.approvals() == {"claude": False, "codex": False}


def test_state_round_trips():
    g = gate()
    g.submit(rv("claude"), evidence_sha256=C1.evidence_sha256, session_id="r1")
    g2 = ReviewGate("G", state=g.to_dict())
    assert g2.candidate == C1 and g2.approvals()["claude"] is True
    assert g2.evidence()["status"] == "PENDING"
