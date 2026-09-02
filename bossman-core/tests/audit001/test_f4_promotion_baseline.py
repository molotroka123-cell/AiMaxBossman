"""AUDIT-ONLY-001 / F4-PROMOTION-NO-BASELINE — independent verification.

Claim under test (Fable, severity P1):
    ``promotion.advance`` guards the security gate with
    ``if security_before is not None and security_after is not None:`` so the gate
    is opt-in; a caller that passes no snapshots reaches VERIFIED / OWNER_PROMOTED
    without ever proving security non-regression.

This file is deliberately split in two:

``TestRedFailOpenAtTheExportedApi``
    RED. These express the invariant the module's own docstring promises
    ("security hard gates неоптимизируемы ради score") at the *public exported*
    surface (``bossman.learning_guard.advance`` / ``.guard_promotion``, both listed
    in ``learning_guard.__all__``). They fail on current code: omitting the
    snapshots silently skips the gate.

``TestGreenGuardsThatAlreadyExist``
    GREEN characterization. They pin the guards that DO exist upstream of every
    real production path, and are the reason the exploit story does not hold as
    written. Do not delete them: they are what downgrades this finding.

``TestSnapshotProvenanceIsUnrepresentable``
    GREEN characterization. Pins the fact that ``SecuritySnapshot`` carries no
    identity at all (no candidate ref, no timestamp, no environment/head), so the
    "stale / future / foreign-candidate / foreign-environment snapshot" variants
    asked about in the brief are *unrepresentable* against this type — they are not
    F4 defects but a type-shape gap owned by F5-PROMOTION-CROSS-CORPUS.
"""
from __future__ import annotations

import inspect

import pytest

import bossman.learning_guard as lg
from bossman.learning_guard import (ABResult, Candidate, PromotionStage,
                                    RollbackInfo, SecuritySnapshot)
from bossman.learning_guard import autonomy_trainer as at
from bossman.learning_guard.promotion import MIN_SHADOW_RUNS


def _passing_ab(n: int = 20) -> list[ABResult]:
    """A/B evidence that passes every non-security gate (raw==guarded==verified)."""
    return [ABResult(f"code-{i}", "code", True, True) for i in range(n)]


def _verdict_ok():
    v = lg.evaluate_ab(_passing_ab())
    assert v.passing, "fixture must produce a passing A/B verdict"
    return v


CLEAN = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=1.0)
BREACHED = SecuritySnapshot(leaks=7, bypasses=3, containment_rate=0.1)


# --------------------------------------------------------------------------- RED
class TestRedFailOpenAtTheExportedApi:
    """The security gate is skipped whenever either snapshot is None."""

    def test_both_snapshots_none_still_reaches_verified(self):
        c = Candidate("config", "c1", stage=PromotionStage.SHADOW)
        moved = lg.advance(c, ab=_verdict_ok(), shadow_runs=MIN_SHADOW_RUNS)
        assert moved.stage is not PromotionStage.VERIFIED, (
            "SHADOW->VERIFIED granted with NO security baseline at all: "
            f"stage={moved.stage!r}, reasons={moved.reasons!r}")

    def test_only_before_none_skips_the_gate_even_with_a_breached_after(self):
        c = Candidate("config", "c2", stage=PromotionStage.SHADOW)
        moved = lg.advance(c, ab=_verdict_ok(), security_before=None,
                           security_after=BREACHED, shadow_runs=MIN_SHADOW_RUNS)
        assert moved.stage is not PromotionStage.VERIFIED, (
            "half-supplied snapshots (before=None, after=7 leaks/3 bypasses) "
            f"promoted to {moved.stage!r} instead of being refused")

    def test_only_after_none_skips_the_gate(self):
        c = Candidate("config", "c3", stage=PromotionStage.SHADOW)
        moved = lg.advance(c, ab=_verdict_ok(), security_before=CLEAN,
                           security_after=None, shadow_runs=MIN_SHADOW_RUNS)
        assert moved.stage is not PromotionStage.VERIFIED, (
            "a baseline with no 'after' measurement is not proof of "
            f"non-regression, yet stage={moved.stage!r}")

    def test_guard_promotion_service_defaults_to_no_security_evidence(self):
        """The composite service entry point defaults both snapshots to None."""
        sig = inspect.signature(lg.guard_promotion)
        assert sig.parameters["security_before"].default is not None, (
            "guard_promotion defaults security_before to None (fail-open default)")
        c = Candidate("config", "c4", stage=PromotionStage.SHADOW)
        moved, verdict = lg.guard_promotion(c, _passing_ab(), shadow_runs=MIN_SHADOW_RUNS)
        assert moved.stage is not PromotionStage.VERIFIED, (
            "guard_promotion() with no security arguments reached "
            f"{moved.stage!r}; verdict.passing={verdict.passing}")

    def test_shadow_to_verified_without_baseline_then_owner_promoted(self):
        """Full walk CANDIDATE -> ... -> OWNER_PROMOTED with zero security evidence."""
        v = _verdict_ok()
        c = Candidate("skill", "s1")
        c = lg.advance(c, ab=v)                                   # -> VALIDATION
        c = lg.advance(c, ab=v)                                   # -> SHADOW
        c = lg.advance(c, ab=v, shadow_runs=MIN_SHADOW_RUNS)      # -> VERIFIED (no security proof)
        final = lg.promote(c, owner_approved=True,
                           rollback=RollbackInfo("verified", "s0"))
        assert final.stage is not PromotionStage.OWNER_PROMOTED, (
            "candidate reached OWNER_PROMOTED having never supplied a security "
            "snapshot pair; promote() itself takes no security argument at all")


# ------------------------------------------------------------------------- GREEN
class TestGreenGuardsThatAlreadyExist:
    """Why the F4 exploit story does not reach any production caller."""

    def test_gate_does_fire_when_both_snapshots_are_supplied(self):
        with pytest.raises(lg.SecurityRegression):
            lg.advance(Candidate("config", "c5", stage=PromotionStage.SHADOW),
                       ab=_verdict_ok(), security_before=CLEAN,
                       security_after=BREACHED, shadow_runs=MIN_SHADOW_RUNS)

    def test_guard_promotion_raises_when_both_snapshots_are_supplied(self):
        with pytest.raises(lg.SecurityRegression):
            lg.guard_promotion(Candidate("config", "c6", stage=PromotionStage.SHADOW),
                               _passing_ab(), security_before=CLEAN,
                               security_after=BREACHED, shadow_runs=MIN_SHADOW_RUNS)

    def test_promote_candidate_makes_both_snapshots_mandatory(self):
        """The ONLY production route into guard_promotion has no None default."""
        params = inspect.signature(at.promote_candidate).parameters
        for name in ("security_before", "security_after"):
            p = params[name]
            assert p.default is inspect.Parameter.empty, (
                f"promote_candidate.{name} acquired a default; the mandatory "
                "snapshot guard that contains F4 has been weakened")
            assert p.kind is inspect.Parameter.KEYWORD_ONLY

    def test_promote_candidate_without_snapshots_is_a_typeerror(self):
        cand = at.AutonomyCandidate(candidate_id="k1", kind="skill",
                                    scope={"task_class": "code"}, hypothesis="h",
                                    rollback_ref="k1@v0", status="SHADOW")
        with pytest.raises(TypeError):
            at.promote_candidate(cand, _passing_ab(), shadow_runs=MIN_SHADOW_RUNS,
                                 owner_approved=True, rollback_tested=True,
                                 rollback=RollbackInfo("SHADOW", "k1@v0"))

    def test_skill_promoter_also_makes_both_snapshots_mandatory(self):
        from bossman.apprentice.skills import SkillPromoter
        params = inspect.signature(SkillPromoter.promote).parameters
        for name in ("security_before", "security_after"):
            assert params[name].default is inspect.Parameter.empty, (
                f"SkillPromoter.promote.{name} acquired a default")

    def test_episode_level_security_regression_quarantines_before_shadow(self):
        """A candidate cannot even reach SHADOW (promote_candidate's precondition)
        if any training episode carries security_regression."""
        cand = at.AutonomyCandidate(candidate_id="k2", kind="skill",
                                    scope={"task_class": "code"}, hypothesis="h",
                                    rollback_ref="k2@v0")
        eps = [at.Episode(task_id=f"t{i}", state_hash="h", action_type="fix",
                          semantic_anchor="a", fresh_observation=True,
                          verified_success=True, planner_principal="p",
                          verifier_principal="v", environment_fingerprint="env",
                          model_version="m", security_regression=(i == 0))
               for i in range(5)]
        out = at.evaluate_candidate(cand, eps, baseline_success=0.5)
        assert out.status == "QUARANTINED"
        assert out.security_regression is True

    def test_promote_candidate_refuses_anything_not_in_shadow(self):
        cand = at.AutonomyCandidate(candidate_id="k3", kind="skill",
                                    scope={"task_class": "code"}, hypothesis="h",
                                    rollback_ref="k3@v0")     # status=CANDIDATE
        out = at.promote_candidate(cand, _passing_ab(), security_before=CLEAN,
                                   security_after=CLEAN, shadow_runs=MIN_SHADOW_RUNS,
                                   owner_approved=True, rollback_tested=True,
                                   rollback=RollbackInfo("SHADOW", "k3@v0"))
        assert out.status != "PROMOTED"
        assert "not in SHADOW" in " ".join(out.reasons)

    def test_owner_promotion_still_requires_owner_and_verified_stage(self):
        """promote() is not a security gate; it is an owner/stage/rollback gate,
        and those parts hold."""
        c = Candidate("skill", "s2", stage=PromotionStage.SHADOW)
        assert lg.promote(c, owner_approved=True,
                          rollback=RollbackInfo("shadow", "s1")).stage is PromotionStage.SHADOW
        v = Candidate("skill", "s3", stage=PromotionStage.VERIFIED)
        assert lg.promote(v, owner_approved=False,
                          rollback=RollbackInfo("verified", "s2")).stage is PromotionStage.VERIFIED


class TestSnapshotProvenanceIsUnrepresentable:
    """The brief asks for stale / future / foreign-candidate / foreign-environment
    snapshot cases. They cannot be expressed: the type has no such fields."""

    def test_security_snapshot_has_no_identity_or_time_fields(self):
        """UPDATED by the F4/F5 fix (was: fields == {leaks, bypasses, containment_rate}).

        The F5 fix added exactly ONE provenance field, ``scope_ref`` (the corpus a
        snapshot was measured on); ``assert_no_security_regression`` now refuses a
        pair whose ``scope_ref`` differs. Everything else the brief asked about
        (candidate ref, timestamp, environment, head) is still absent, so the
        stale/future/foreign-candidate variants remain unrepresentable.
        """
        fields = set(SecuritySnapshot.__dataclass_fields__)
        assert fields == {"leaks", "bypasses", "containment_rate", "scope_ref"}, fields
        for absent in ("candidate_ref", "taken_at", "timestamp", "environment",
                       "head", "corpus_id", "model_version"):
            assert absent not in fields

    def test_advance_accepts_a_snapshot_pair_belonging_to_nobody(self):
        """Two snapshots invented on the spot are accepted as 'proof' for any
        candidate; there is no binding between snapshot and candidate."""
        a = Candidate("skill", "alpha", stage=PromotionStage.SHADOW)
        b = Candidate("config", "beta", stage=PromotionStage.SHADOW)
        pair = (SecuritySnapshot(leaks=2, bypasses=2, containment_rate=0.5),
                SecuritySnapshot(leaks=2, bypasses=2, containment_rate=0.5))
        for c in (a, b):
            moved = lg.advance(c, ab=_verdict_ok(), security_before=pair[0],
                               security_after=pair[1], shadow_runs=MIN_SHADOW_RUNS)
            assert moved.stage is PromotionStage.VERIFIED
        # Same absolute-insecure numbers on both sides pass: the gate is a *delta*
        # gate, not an absolute one. That is by design (req: no regression), and is
        # F5 territory, not F4.

    def test_identical_snapshot_object_used_twice_is_accepted(self):
        snap = SecuritySnapshot(leaks=1, bypasses=1, containment_rate=0.9)
        moved = lg.advance(Candidate("config", "same", stage=PromotionStage.SHADOW),
                           ab=_verdict_ok(), security_before=snap,
                           security_after=snap, shadow_runs=MIN_SHADOW_RUNS)
        assert moved.stage is PromotionStage.VERIFIED
