
import pytest
import time
from bcc.hybrid.capabilities import (
    RuntimeIdentity,
    EffectCorrelation,
    Observation,
    StateVerificationFailedError,
)
from bcc.hybrid.evidence import EvidenceNormalizer

def test_external_observation_normalized_is_unverified():
    runtime = RuntimeIdentity("ext_engine", "1.0", "oss")
    raw = {"status": "success", "button_clicked": True, "task_done": True}
    obs = Observation("corr-123", raw, "2026-09-09T10:00:00Z", runtime, verified_by_bossman=False)

    candidate = EvidenceNormalizer.normalize_observation(obs, "ui_action")

    # Invariant checks: external claims do NOT equal Bossman verified truth
    assert candidate.verified_by_bossman is False
    assert candidate.is_completion_proof is False
    assert candidate.digest != ""
    assert candidate.payload == raw

def test_external_success_without_bossman_verification_fails():
    runtime = RuntimeIdentity("ext_engine", "1.0", "oss")
    raw = {"status": "success", "msg": "File saved successfully"}
    obs = Observation("corr-124", raw, "2026-09-09T10:00:00Z", runtime, verified_by_bossman=False)
    candidate = EvidenceNormalizer.normalize_observation(obs, "file_save")

    corr = EffectCorrelation("corr-124", "t1", "r1", time.time() + 60.0)

    # Simulated post-state verifier checking actual filesystem / DB: file was NOT created!
    def verify_file_exists(payload):
        return False  # Independent check fails!

    with pytest.raises(StateVerificationFailedError) as exc:
        EvidenceNormalizer.verify_candidate_against_bossman_truth(
            candidate=candidate,
            correlation=corr,
            post_state_verifier=verify_file_exists,
        )
    assert "Bossman independent post-state verification failed" in str(exc.value)

def test_bossman_verification_succeeds_when_poststate_confirmed():
    runtime = RuntimeIdentity("ext_engine", "1.0", "oss")
    raw = {"status": "success"}
    obs = Observation("corr-125", raw, "2026-09-09T10:00:00Z", runtime, verified_by_bossman=False)
    candidate = EvidenceNormalizer.normalize_observation(obs, "ui_action")

    corr = EffectCorrelation("corr-125", "t1", "r1", time.time() + 60.0)

    def verify_poststate(payload):
        return True  # Verified by real observation

    verified = EvidenceNormalizer.verify_candidate_against_bossman_truth(
        candidate=candidate,
        correlation=corr,
        post_state_verifier=verify_poststate,
    )
    assert verified.verified_by_bossman is True
    assert verified.is_completion_proof is True
