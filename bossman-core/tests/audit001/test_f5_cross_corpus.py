"""AUDIT-ONLY-001 / F5-PROMOTION-CROSS-CORPUS -- independent verification.

Fable's claim: "Candidate and SecuritySnapshot carry no corpus/domain binding, so
evidence gathered on corpus B can promote a candidate trained on corpus A."

What the real code actually looks like (bossman/learning_guard/autonomy_trainer.py,
bossman/apprentice/skills.py):

* ``learning_guard.models.Candidate`` really has no scope -- but it is an internal
  stage machine; its only production caller is ``autonomy_trainer.promote_candidate``.
* ``AutonomyCandidate`` DOES have a binding under a different name: ``scope``,
  populated by ``SkillPromoter.evaluate`` with ``task_class`` / ``environment`` /
  ``model_version`` (and by ``runtime_bridge.observe_learning_record`` with
  ``task_class``).  So "no binding exists" is WRONG.
* The defect that survives is narrower and real: that binding is DECORATIVE.
  Neither ``evaluate_candidate`` nor ``promote_candidate`` ever compares the
  evidence it is handed against ``cand.scope``.  ``evaluate_candidate`` only
  checks internal self-consistency of the episodes (all episodes share ONE
  environment / ONE model_version) and that ``scope["task_class"]`` is a
  non-empty string.  ``promote_candidate`` reads ``cand.scope`` zero times.

These tests are therefore written against the invariant the scope field claims to
express: evidence must belong to the corpus/environment/version the candidate is
scoped to.  Tests that go RED are real gaps; tests marked PIN are green
characterisation tests for guards that already exist and must not be lost.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CORE = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(CORE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from bossman.learning_guard.autonomy_trainer import (  # noqa: E402
    AutonomyCandidate,
    Episode,
    evaluate_candidate,
    promote_candidate,
)
from bossman.learning_guard.models import (  # noqa: E402
    ABResult,
    Candidate,
    PromotionStage,
    RollbackInfo,
    SecuritySnapshot,
)
from bossman.learning_guard.promotion import MIN_SHADOW_RUNS  # noqa: E402

CORPUS_A = "notes.save"          # what the candidate was trained on
CORPUS_B = "support.chat"        # an unrelated corpus / domain
ENV_A = "env:notes-1.0"
ENV_B = "env:chat-9.9"
MODEL_A = "planner:sim-v1"
MODEL_B = "planner:sim-v2"

CLEAN = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=1.0)
ROLLBACK = RollbackInfo(prev_stage="SHADOW", prev_ref="skill@v0", reason="test")


# ------------------------------------------------------------------ builders
def _episode(i: int, *, environment: str = ENV_A, model: str = MODEL_A, ok: bool = True) -> Episode:
    """An episode that passes every existing ``episode_rejection`` guard."""
    return Episode(
        task_id=f"{environment}-task-{i}",
        state_hash=f"h{i}",
        action_type="skill",
        semantic_anchor="button:Save",
        fresh_observation=True,
        verified_success=ok,
        planner_principal="apprentice:planner",
        verifier_principal="verifier:pytest",
        verifier_independence_class="external_tool",
        environment_fingerprint=environment,
        model_version=model,
        self_reported_only=False,
    )


def _candidate(*, task_class: str = CORPUS_A, environment: str = ENV_A,
               model_version: str = MODEL_A, **extra_scope) -> AutonomyCandidate:
    """Exactly the shape ``SkillPromoter.evaluate`` builds (apprentice/skills.py:269)."""
    scope = {"task_class": task_class, "environment": environment,
             "model_version": model_version, "risky": False}
    scope.update(extra_scope)
    return AutonomyCandidate(candidate_id="skill_notes_save", kind="skill", scope=scope,
                             hypothesis="save a note", rollback_ref="skill_notes_save@v0")


def _shadow(cand: AutonomyCandidate, episodes) -> AutonomyCandidate:
    out = evaluate_candidate(cand, episodes, baseline_success=0.5)
    assert out.status == "SHADOW", out.reasons
    return out


def _ab(task_class: str, n: int = MIN_SHADOW_RUNS) -> list[ABResult]:
    """A/B evidence that passes every existing anti-degradation gate."""
    return [ABResult(task_id=f"{task_class}-{i}", task_class=task_class,
                     raw_verified=False, guarded_verified=True) for i in range(n)]


def _promote(cand: AutonomyCandidate, ab, *, before=CLEAN, after=CLEAN) -> AutonomyCandidate:
    return promote_candidate(cand, ab, security_before=before, security_after=after,
                             shadow_runs=MIN_SHADOW_RUNS, owner_approved=True,
                             rollback_tested=True, rollback=ROLLBACK)


# ============================================================ PIN: guards that DO exist
def test_pin_scope_binding_field_exists_and_task_class_is_required():
    """PIN: the binding Fable says is missing exists under the name ``scope``."""
    cand = _candidate()
    assert cand.scope["task_class"] == CORPUS_A
    assert cand.scope["environment"] == ENV_A
    assert cand.scope["model_version"] == MODEL_A
    # scope.task_class is enforced as PRESENT (not as matching the evidence).
    no_class = AutonomyCandidate(candidate_id="c", kind="skill", scope={}, hypothesis="h",
                                 rollback_ref="c@v0")
    out = evaluate_candidate(no_class, [_episode(i) for i in range(3)], baseline_success=0.5)
    assert out.status == "CANDIDATE"
    assert any("scope.task_class missing" in r for r in out.reasons)


def test_pin_episodes_from_two_environments_are_rejected_at_evaluate():
    """PIN: episodes may not MIX environments/model versions (self-consistency guard)."""
    mixed_env = [_episode(0), _episode(1), _episode(2, environment=ENV_B)]
    out = evaluate_candidate(_candidate(), mixed_env, baseline_success=0.5)
    assert out.status == "CANDIDATE"
    assert any("one explicit environment fingerprint" in r for r in out.reasons)

    mixed_model = [_episode(0), _episode(1), _episode(2, model=MODEL_B)]
    out2 = evaluate_candidate(_candidate(), mixed_model, baseline_success=0.5)
    assert out2.status == "CANDIDATE"
    assert any("one explicit environment fingerprint" in r for r in out2.reasons)


def test_pin_happy_path_same_corpus_promotes():
    """PIN: the legitimate same-corpus path must keep working after any fix."""
    cand = _shadow(_candidate(), [_episode(i) for i in range(3)])
    out = _promote(cand, _ab(CORPUS_A))
    assert out.status == "PROMOTED", out.reasons


# ============================================================ RED: cross-corpus evidence
def test_promote_rejects_ab_evidence_from_a_different_task_class():
    """RED. Candidate scoped to corpus A; every A/B episode belongs to corpus B.

    ``promote_candidate`` never reads ``cand.scope``, so corpus-B evidence
    promotes a corpus-A candidate.
    """
    cand = _shadow(_candidate(task_class=CORPUS_A), [_episode(i) for i in range(3)])
    out = _promote(cand, _ab(CORPUS_B))
    assert out.status != "PROMOTED", (
        f"corpus-{CORPUS_B} A/B evidence promoted a candidate scoped to {CORPUS_A}")


def test_promote_rejects_ab_evidence_that_does_not_cover_the_scoped_class():
    """RED. Mixed corpora where the scoped class is absent -> still promotes.

    A domain-specific result (corpus B) is treated as a global one.
    """
    cand = _shadow(_candidate(task_class=CORPUS_A), [_episode(i) for i in range(3)])
    ab = _ab(CORPUS_B, 10) + _ab("billing.refund", 10)
    out = _promote(cand, ab)
    assert out.status != "PROMOTED", (
        "A/B evidence containing zero episodes of the scoped task_class promoted the candidate")


# ============================================================ RED: environment / version drift
def test_evaluate_rejects_episodes_from_an_environment_other_than_scope():
    """RED. scope.environment == prod-A, every episode came from env B.

    ``evaluate_candidate`` only checks the episodes agree WITH EACH OTHER; it never
    compares them to ``cand.scope['environment']``.
    """
    cand = _candidate(environment=ENV_A)
    out = evaluate_candidate(cand, [_episode(i, environment=ENV_B) for i in range(3)],
                             baseline_success=0.5)
    assert out.status != "SHADOW", (
        f"episodes from {ENV_B} moved a candidate scoped to {ENV_A} into SHADOW")


def test_evaluate_rejects_episodes_from_a_model_version_other_than_scope():
    """RED. scope.model_version == v1, every episode was produced by v2."""
    cand = _candidate(model_version=MODEL_A)
    out = evaluate_candidate(cand, [_episode(i, model=MODEL_B) for i in range(3)],
                             baseline_success=0.5)
    assert out.status != "SHADOW", (
        f"episodes from {MODEL_B} moved a candidate scoped to {MODEL_A} into SHADOW")


# ============================================================ RED: unversioned corpus identity
def test_same_corpus_name_different_dataset_hash_is_not_interchangeable():
    """RED. Corpus identity is a bare NAME: no immutable dataset hash anywhere.

    Two candidates whose corpus was silently re-cut under the same name are
    indistinguishable, and the A/B evidence carries no dataset identity either.
    """
    cand = _shadow(_candidate(dataset_hash="sha256:aaaa"), [_episode(i) for i in range(3)])
    ab = _ab(CORPUS_A)                     # same corpus NAME, no dataset identity attached
    out = _promote(cand, ab)
    assert out.status != "PROMOTED", (
        "evidence with no dataset identity promoted a candidate pinned to a dataset hash")


def test_same_corpus_different_policy_version_is_not_interchangeable():
    """RED. Nothing binds evidence to the policy version in force when it was taken."""
    cand = _shadow(_candidate(policy_version="policy@v7"), [_episode(i) for i in range(3)])
    ab = _ab(CORPUS_A)                     # same corpus, no policy identity attached
    out = _promote(cand, ab)
    assert out.status != "PROMOTED", (
        "evidence with no policy version promoted a candidate pinned to policy@v7")


@pytest.mark.xfail(strict=True, reason=(
    "BACKLOG AUDIT001-F5-REPLAY (P2, open): the two candidates differ only by rollback_ref and are "
    "handed byte-identical evidence, so NO stateless predicate over (candidate, evidence) can "
    "separate them. Refusing the replay requires a durable single-use evidence ledger, which would "
    "import cross-process and restart-safety obligations into a currently pure value layer. The "
    "underlying gap is real - ABResult carries no candidate id/version - and is tracked as its own "
    "finding rather than forced green here. strict=True: implementing the ledger makes this test "
    "pass and fails the run until this marker is removed."))
def test_ab_evidence_cannot_be_replayed_for_a_newer_candidate_version():
    """RED. One A/B run promotes candidate v1 AND, replayed verbatim, candidate v9.

    ``ABResult`` carries no candidate id / version, so stale evidence for an older
    candidate version is accepted for a newer one.
    """
    episodes = [_episode(i) for i in range(3)]
    ab = _ab(CORPUS_A)                                    # measured once, against v1
    v1 = _shadow(_candidate(), episodes)
    v1 = AutonomyCandidate(**{**v1.as_dict(), "status": v1.status,
                              "reasons": (), "rollback_ref": "skill_notes_save@v1"})
    assert _promote(v1, ab).status == "PROMOTED"
    v9 = _shadow(_candidate(), episodes)
    v9 = AutonomyCandidate(**{**v9.as_dict(), "status": v9.status,
                              "reasons": (), "rollback_ref": "skill_notes_save@v9"})
    out = _promote(v9, ab)                                # same evidence, different version
    assert out.status != "PROMOTED", (
        "A/B evidence measured for candidate v1 was replayed to promote v9")


# ============================================================ RED: security snapshot binding
def test_security_snapshots_must_be_bound_to_the_candidate_scope():
    """RED. before/after snapshots carry no corpus/environment identity at all.

    A weak "before" measured on an easy corpus and a strong "after" measured on
    another cannot be detected as incomparable by ``assert_no_security_regression``.
    """
    fields = set(SecuritySnapshot.__dataclass_fields__)
    assert fields & {"scope", "scope_ref", "corpus", "corpus_ref", "task_class",
                     "environment", "dataset_hash", "binding"}, (
        f"SecuritySnapshot has no corpus/scope binding: {sorted(fields)}")


@pytest.mark.xfail(strict=True, reason=(
    "BACKLOG AUDIT001-F5-PROVENANCE (P2, open): both snapshots here carry NO provenance and the "
    "candidate declares no corpus identity, so the only rule that could refuse them is 'reject an "
    "unexplained security improvement between two unidentified measurements' - a gate that would "
    "block legitimate real improvements in production purely to turn a test green. The principled "
    "half IS implemented: SecuritySnapshot.scope_ref exists, mismatched refs are refused by "
    "assert_no_security_regression, and a candidate declaring a corpus identity requires matching "
    "provenance on both snapshots. Making provenance MANDATORY everywhere must first update every "
    "measurement producer; until then this stays an executable specification."))
def test_incomparable_security_snapshots_do_not_pass_the_gate():
    """RED. "before" from corpus B (containment 0.50) vs "after" from corpus A (0.90)
    reads as an IMPROVEMENT, so the security hard gate waves the promotion through."""
    cand = _shadow(_candidate(task_class=CORPUS_A), [_episode(i) for i in range(3)])
    before_corpus_b = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=0.50)
    after_corpus_a = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=0.90)
    out = _promote(cand, _ab(CORPUS_A), before=before_corpus_b, after=after_corpus_a)
    assert out.status != "PROMOTED", (
        "security snapshots taken on different corpora were compared and passed the gate")


# ============================================================ RED: migration / legacy records
def test_legacy_candidate_without_scope_is_not_promotable():
    """RED (migration). A pre-existing / deserialised SHADOW record with no scope.

    ``promote_candidate`` gates only on ``status == 'SHADOW'``; it never re-checks
    that the candidate carries a scope, so a scope-less legacy record promotes.
    """
    legacy = AutonomyCandidate(candidate_id="legacy_skill", kind="skill", scope={},
                               hypothesis="", rollback_ref="legacy_skill@v0",
                               status="SHADOW", sample_count=3,
                               independently_verified=True, verified_success_delta=0.1)
    out = _promote(legacy, _ab(CORPUS_A))
    assert out.status != "PROMOTED", (
        "a legacy candidate with an empty scope was silently promoted (must be re-evaluated, "
        "never treated as VERIFIED)")


# ============================================================ RED: reachable from the shipped API
def test_skillpromoter_promotes_a_skill_with_evidence_from_another_corpus(tmp_path, monkeypatch):
    """RED. Same defect through the real production entry point, not the internals.

    ``SkillPromoter.promote`` (bossman/apprentice/skills.py) forwards the caller's
    ``ab`` list straight into ``promote_candidate``; ``ab_results_from_replays``
    takes ``task_class`` as a free string.  A skill whose ``task_type`` is
    ``notes.save`` is promoted to READY on ``support.chat`` evidence.
    """
    from bossman.apprentice import flags
    from bossman.apprentice.skills import SkillPromoter, ab_results_from_replays

    for f in (flags.MASTER, flags.SKILL_RECORDING, flags.SKILL_SHADOW_REPLAY, flags.SKILL_PROMOTION):
        monkeypatch.setenv(f, "1")

    class _Mem:                      # stand-in for ApprenticeMemory: storage is not under test
        def __init__(self):
            self.stored = None

        def store_skill(self, skill, *, expected_version=None):
            self.stored = dict(skill, version=int(expected_version or 0) + 1)
            return self.stored

    skill = {"skill_id": "skill_notes_save", "record_type": "skill", "task_id": "skill_notes_save",
             "task_type": CORPUS_A, "environment": ENV_A, "model": MODEL_A,
             "learning_status": "VERIFIED", "skill_state": "SHADOW", "version": 1,
             "tags": {"domain": CORPUS_A, "risk": "low"}}
    mem = _Mem()
    cand = _shadow(_candidate(task_class=CORPUS_A), [_episode(i) for i in range(3)])
    replays = [{"task_id": f"{CORPUS_B}-{i}", "ok": True} for i in range(MIN_SHADOW_RUNS)]
    ab = ab_results_from_replays(CORPUS_B, replays, {})          # <-- evidence for corpus B
    promoter = SkillPromoter(mem)
    stored, final = promoter.promote(skill, cand, ab, security_before=CLEAN, security_after=CLEAN,
                                     shadow_runs=MIN_SHADOW_RUNS, owner_approved=True,
                                     rollback_tested=True)
    assert final.status != "PROMOTED" and stored.get("skill_state") != "READY", (
        f"skill scoped to {CORPUS_A} reached READY on {CORPUS_B} shadow-replay evidence")


# ============================================================ context: the low-level stage machine
def test_pin_low_level_candidate_has_no_scope_and_is_internal_only():
    """PIN: ``learning_guard.models.Candidate`` genuinely has no scope field.

    This is context for the fix: the binding belongs in ``autonomy_trainer``
    (the only production caller of ``promote``/``guard_promotion``), NOT here.
    """
    fields = set(Candidate.__dataclass_fields__)
    assert {"kind", "ref", "stage", "reasons", "rollback"} <= fields
    assert PromotionStage.VERIFIED < PromotionStage.OWNER_PROMOTED


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
