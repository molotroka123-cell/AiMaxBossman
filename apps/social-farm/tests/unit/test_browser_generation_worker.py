from pathlib import Path
import time

import pytest

from social_farm.generation.browser_worker import BrowserGenerationWorker, BrowserWorkerConfig
from social_farm.generation.higgsfield_browser_contracts import (
    BrowserGenerationObservation,
    BrowserGenerationRequest,
    BrowserGenerationState,
    MediaKind,
    SubmissionReceipt,
)
from social_farm.generation.job_store import GenerationJobStore


class ReadyAdapter:
    provider_name = "higgsfield-browser"

    def __init__(self, artifact: Path) -> None:
        self.artifact = artifact
        self.polls = 0

    async def prepare(self, request):
        return BrowserGenerationObservation(request.job_id, BrowserGenerationState.READY, time.time())

    async def submit(self, request):
        return SubmissionReceipt(
            job_id=request.job_id,
            provider=self.provider_name,
            submitted_at_epoch_s=time.time(),
            provider_job_id="provider-123",
            evidence={"page": "generation", "submitted": "true"},
        )

    async def poll(self, request, receipt):
        self.polls += 1
        state = BrowserGenerationState.OUTPUT_READY if self.polls >= 1 else BrowserGenerationState.WAITING_PROVIDER
        return BrowserGenerationObservation(request.job_id, state, time.time())

    async def collect(self, request, receipt):
        return self.artifact


class ChallengeAdapter(ReadyAdapter):
    async def prepare(self, request):
        return BrowserGenerationObservation(
            request.job_id,
            BrowserGenerationState.HUMAN_CHALLENGE,
            time.time(),
            safe_message="owner interaction required",
        )


@pytest.mark.asyncio
async def test_worker_completes_and_records_artifact(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    artifact = output / "clip.mp4"
    artifact.write_bytes(b"x" * 2048)
    store = GenerationJobStore(tmp_path / "jobs.json")
    worker = BrowserGenerationWorker(
        adapter=ReadyAdapter(artifact),
        store=store,
        config=BrowserWorkerConfig(poll_interval_s=0.001, max_poll_seconds=1, min_artifact_bytes=100),
    )
    request = BrowserGenerationRequest(
        mission_id="m1",
        media_kind=MediaKind.VIDEO,
        prompt="cinematic city at night",
        output_workspace=output,
        max_attempts=1,
    )

    result = await worker.run(request)

    assert result.path == artifact.resolve()
    record = store.get(request.job_id)
    assert record is not None
    assert record.state is BrowserGenerationState.COMPLETE
    assert record.provider_job_id == "provider-123"
    assert record.artifact_sha256 == result.sha256


@pytest.mark.asyncio
async def test_worker_fails_closed_on_human_challenge(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    store = GenerationJobStore(tmp_path / "jobs.json")
    worker = BrowserGenerationWorker(adapter=ChallengeAdapter(output / "unused.mp4"), store=store)
    request = BrowserGenerationRequest(
        mission_id="m2",
        media_kind=MediaKind.VIDEO,
        prompt="test",
        output_workspace=output,
        max_attempts=1,
    )

    result = await worker.run(request)

    assert result.state is BrowserGenerationState.HUMAN_CHALLENGE
    record = store.get(request.job_id)
    assert record is not None
    assert record.state is BrowserGenerationState.HUMAN_CHALLENGE
    assert record.owner_action_required is True


@pytest.mark.asyncio
async def test_worker_rejects_artifact_outside_workspace(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    artifact = tmp_path / "escape.mp4"
    artifact.write_bytes(b"x" * 2048)
    store = GenerationJobStore(tmp_path / "jobs.json")
    worker = BrowserGenerationWorker(
        adapter=ReadyAdapter(artifact),
        store=store,
        config=BrowserWorkerConfig(poll_interval_s=0.001, max_poll_seconds=1, min_artifact_bytes=100),
    )
    request = BrowserGenerationRequest(
        mission_id="m3",
        media_kind=MediaKind.VIDEO,
        prompt="test",
        output_workspace=output,
        max_attempts=1,
    )

    with pytest.raises(RuntimeError, match="escaped output workspace"):
        await worker.run(request)
