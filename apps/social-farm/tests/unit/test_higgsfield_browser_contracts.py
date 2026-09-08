from pathlib import Path
import time
import pytest

from social_farm.generation.higgsfield_browser_contracts import (
    ArtifactReceipt,
    BrowserGenerationObservation,
    BrowserGenerationRequest,
    BrowserGenerationState,
    MediaKind,
    SubmissionReceipt,
    transition_allowed,
)


def request(**overrides):
    values = dict(
        mission_id="mission-1",
        media_kind=MediaKind.VIDEO,
        prompt="cinematic city street, consistent character",
        output_workspace=Path("media/out"),
        duration_seconds=8,
        deadline_epoch_s=time.time() + 60,
    )
    values.update(overrides)
    return BrowserGenerationRequest(**values)


def test_request_is_bounded():
    with pytest.raises(ValueError):
        request(max_attempts=0)
    with pytest.raises(ValueError):
        request(max_attempts=6)


def test_submission_requires_evidence_not_just_a_click_claim():
    with pytest.raises(ValueError):
        SubmissionReceipt("job", "higgsfield-browser", time.time(), evidence={})


def test_human_challenge_is_terminal_and_owner_required():
    obs = BrowserGenerationObservation(
        "job", BrowserGenerationState.HUMAN_CHALLENGE, time.time(), "owner action required"
    )
    assert obs.terminal
    assert obs.owner_action_required
    assert not transition_allowed(BrowserGenerationState.HUMAN_CHALLENGE, BrowserGenerationState.READY)


def test_normal_generation_happy_path_transitions():
    path = [
        BrowserGenerationState.CREATED,
        BrowserGenerationState.STARTING,
        BrowserGenerationState.AUTH_CHECK,
        BrowserGenerationState.READY,
        BrowserGenerationState.SUBMITTING,
        BrowserGenerationState.WAITING_PROVIDER,
        BrowserGenerationState.OUTPUT_READY,
        BrowserGenerationState.COLLECTING,
        BrowserGenerationState.VERIFYING,
        BrowserGenerationState.COMPLETE,
    ]
    assert all(transition_allowed(a, b) for a, b in zip(path, path[1:]))


def test_no_skip_from_ready_to_complete():
    assert not transition_allowed(BrowserGenerationState.READY, BrowserGenerationState.COMPLETE)


def test_artifact_receipt_requires_valid_hash_and_nonempty_file():
    with pytest.raises(ValueError):
        ArtifactReceipt("job", Path("x.mp4"), "0" * 64, MediaKind.VIDEO, 0, time.time())
    with pytest.raises(ValueError):
        ArtifactReceipt("job", Path("x.mp4"), "not-a-hash".ljust(64, "x"), MediaKind.VIDEO, 100, time.time())


def test_valid_artifact_receipt():
    receipt = ArtifactReceipt(
        "job", Path("x.mp4"), "a" * 64, MediaKind.VIDEO, 100, time.time(), {"decode": "pass"}
    )
    assert receipt.bytes_size == 100
