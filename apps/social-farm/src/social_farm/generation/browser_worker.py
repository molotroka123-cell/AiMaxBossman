"""Provider-neutral browser generation worker.

The worker drives an adapter through the safe state machine.  The adapter owns
site selectors and page interaction; this module owns bounded execution,
retries, persistence, evidence and fail-closed challenge semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import hashlib
import time

from .higgsfield_browser_contracts import (
    ArtifactReceipt,
    BrowserGenerationObservation,
    BrowserGenerationRequest,
    BrowserGenerationState,
    MediaKind,
    SubmissionReceipt,
    transition_allowed,
)
from .job_store import GenerationJobRecord, GenerationJobStore


class AdapterRefusal(RuntimeError):
    """Отказ адаптера, который НЕЛЬЗЯ повторять внутри той же работы.

    Обычная ошибка адаптера — «не получилось, попробуй ещё». Этот класс — про
    другое: «повторять запрещено, потому что повтор опаснее неудачи». Так
    выглядит неподтверждённая отправка (работа могла уйти провайдеру, и вторая
    попытка потратила бы квоту владельца дважды) и повторная отправка того же
    идентификатора.

    Отдельный тип нужен именно затем, чтобы работник различал эти два случая
    механически, а не по тексту сообщения.
    """

    state: "BrowserGenerationState" = None  # type: ignore[assignment]
    owner_action_required: bool = False


class BrowserGenerationAdapter(Protocol):
    provider_name: str

    async def prepare(self, request: BrowserGenerationRequest) -> BrowserGenerationObservation: ...
    async def submit(self, request: BrowserGenerationRequest) -> SubmissionReceipt: ...
    async def poll(self, request: BrowserGenerationRequest, receipt: SubmissionReceipt) -> BrowserGenerationObservation: ...
    async def collect(self, request: BrowserGenerationRequest, receipt: SubmissionReceipt) -> Path: ...


@dataclass(slots=True)
class BrowserWorkerConfig:
    poll_interval_s: float = 2.0
    max_poll_seconds: float = 900.0
    min_artifact_bytes: int = 1024

    def __post_init__(self) -> None:
        if self.poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        if self.max_poll_seconds <= 0:
            raise ValueError("max_poll_seconds must be positive")
        if self.min_artifact_bytes < 1:
            raise ValueError("min_artifact_bytes must be positive")


class BrowserGenerationWorker:
    def __init__(
        self,
        *,
        adapter: BrowserGenerationAdapter,
        store: GenerationJobStore,
        config: BrowserWorkerConfig | None = None,
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.config = config or BrowserWorkerConfig()

    def _set_state(
        self,
        record: GenerationJobRecord,
        target: BrowserGenerationState,
        *,
        safe_message: str = "",
        owner_action_required: bool = False,
        error_class: str | None = None,
    ) -> None:
        if record.state != target and not transition_allowed(record.state, target):
            raise RuntimeError(f"illegal browser generation transition: {record.state.value} -> {target.value}")
        record.state = target
        record.safe_message = safe_message
        record.owner_action_required = owner_action_required
        record.last_error_class = error_class
        self.store.save(record)

    async def run(self, request: BrowserGenerationRequest) -> ArtifactReceipt | BrowserGenerationObservation:
        record = self.store.create(job_id=request.job_id, mission_id=request.mission_id, provider=self.adapter.provider_name)
        if record.terminal:
            return BrowserGenerationObservation(
                job_id=record.job_id,
                state=record.state,
                observed_at_epoch_s=time.time(),
                safe_message=record.safe_message,
            )

        for attempt in range(max(1, record.attempt + 1), request.max_attempts + 1):
            record.attempt = attempt
            self._set_state(record, BrowserGenerationState.STARTING)

            prep = await self.adapter.prepare(request)
            self._set_state(record, BrowserGenerationState.AUTH_CHECK)
            if prep.state in {
                BrowserGenerationState.NEEDS_OWNER_AUTH,
                BrowserGenerationState.HUMAN_CHALLENGE,
                BrowserGenerationState.POLICY_BLOCKED,
                # Ограничение частоты и сменившийся интерфейс — тоже конечные
                # исходы подготовки. Повторять их внутри той же работы значит
                # долбиться в лимит и в пропавшую кнопку.
                BrowserGenerationState.RATE_LIMITED,
                BrowserGenerationState.UI_CHANGED,
            }:
                self._set_state(
                    record,
                    prep.state,
                    safe_message=prep.safe_message,
                    owner_action_required=prep.owner_action_required,
                )
                return prep
            if prep.state is not BrowserGenerationState.READY:
                self._set_state(record, BrowserGenerationState.FAILED, safe_message=prep.safe_message, error_class="prepare")
                if attempt < request.max_attempts:
                    record.state = BrowserGenerationState.CREATED
                    self.store.save(record)
                    continue
                return BrowserGenerationObservation(request.job_id, record.state, time.time(), record.safe_message)

            self._set_state(record, BrowserGenerationState.READY)
            self._set_state(record, BrowserGenerationState.SUBMITTING)
            try:
                receipt = await self.adapter.submit(request)
            except AdapterRefusal as refusal:
                # Повтора нет по решению адаптера, а не по исчерпанию попыток.
                state = refusal.state or BrowserGenerationState.FAILED
                self._set_state(record, state, safe_message=str(refusal)[:300],
                                owner_action_required=refusal.owner_action_required,
                                error_class=type(refusal).__name__)
                return BrowserGenerationObservation(
                    request.job_id, record.state, time.time(), record.safe_message)
            except Exception as exc:
                self._set_state(record, BrowserGenerationState.FAILED, safe_message=str(exc)[:300], error_class=type(exc).__name__)
                if attempt < request.max_attempts:
                    record.state = BrowserGenerationState.CREATED
                    self.store.save(record)
                    continue
                return BrowserGenerationObservation(request.job_id, record.state, time.time(), record.safe_message)

            record.provider_job_id = receipt.provider_job_id
            self.store.save(record)
            self._set_state(record, BrowserGenerationState.WAITING_PROVIDER)

            started = time.monotonic()
            while True:
                if request.deadline_epoch_s is not None and time.time() >= request.deadline_epoch_s:
                    self._set_state(record, BrowserGenerationState.TIMEOUT, safe_message="generation deadline exceeded")
                    return BrowserGenerationObservation(request.job_id, record.state, time.time(), record.safe_message)
                if time.monotonic() - started >= self.config.max_poll_seconds:
                    self._set_state(record, BrowserGenerationState.TIMEOUT, safe_message="provider polling timeout")
                    return BrowserGenerationObservation(request.job_id, record.state, time.time(), record.safe_message)

                observation = await self.adapter.poll(request, receipt)
                if observation.state is BrowserGenerationState.WAITING_PROVIDER:
                    import asyncio
                    await asyncio.sleep(self.config.poll_interval_s)
                    continue
                if observation.state is BrowserGenerationState.OUTPUT_READY:
                    self._set_state(record, BrowserGenerationState.OUTPUT_READY)
                    break
                if observation.state in {
                    BrowserGenerationState.RATE_LIMITED,
                    BrowserGenerationState.HUMAN_CHALLENGE,
                    BrowserGenerationState.POLICY_BLOCKED,
                    BrowserGenerationState.UI_CHANGED,
                    BrowserGenerationState.TIMEOUT,
                    BrowserGenerationState.FAILED,
                }:
                    self._set_state(
                        record,
                        observation.state,
                        safe_message=observation.safe_message,
                        owner_action_required=observation.owner_action_required,
                    )
                    return observation
                self._set_state(record, BrowserGenerationState.FAILED, safe_message="unexpected provider state", error_class="protocol")
                return BrowserGenerationObservation(request.job_id, record.state, time.time(), record.safe_message)

            self._set_state(record, BrowserGenerationState.COLLECTING)
            artifact_path = await self.adapter.collect(request, receipt)
            self._set_state(record, BrowserGenerationState.VERIFYING)
            artifact = self._verify_artifact(request, artifact_path)
            record.output_path = str(artifact.path)
            record.artifact_sha256 = artifact.sha256
            self._set_state(record, BrowserGenerationState.COMPLETE)
            return artifact

        self._set_state(record, BrowserGenerationState.FAILED, safe_message="attempt budget exhausted")
        return BrowserGenerationObservation(request.job_id, record.state, time.time(), record.safe_message)

    def _verify_artifact(self, request: BrowserGenerationRequest, path: Path) -> ArtifactReceipt:
        resolved_workspace = request.output_workspace.resolve()
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_workspace)
        except ValueError as exc:
            raise RuntimeError("generated artifact escaped output workspace") from exc
        if not resolved_path.is_file():
            raise RuntimeError("generated artifact does not exist")
        size = resolved_path.stat().st_size
        if size < self.config.min_artifact_bytes:
            raise RuntimeError("generated artifact is too small")
        digest = hashlib.sha256(resolved_path.read_bytes()).hexdigest()
        suffix = resolved_path.suffix.lower()
        if request.media_kind is MediaKind.IMAGE and suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise RuntimeError("unexpected image artifact extension")
        if request.media_kind is MediaKind.VIDEO and suffix not in {".mp4", ".mov", ".webm", ".mkv"}:
            raise RuntimeError("unexpected video artifact extension")
        return ArtifactReceipt(
            job_id=request.job_id,
            path=resolved_path,
            sha256=digest,
            media_kind=request.media_kind,
            bytes_size=size,
            accepted_at_epoch_s=time.time(),
            qa_verdicts={"basic_file_validation": "pass"},
        )


__all__ = ["AdapterRefusal", "BrowserGenerationAdapter", "BrowserGenerationWorker",
           "BrowserWorkerConfig"]
