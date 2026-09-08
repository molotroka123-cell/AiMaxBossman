"""Contracts for browser-driven media generation.

This module intentionally contains no site-specific automation.  It defines the
fail-closed job/evidence states that a Higgsfield browser adapter must satisfy.
Authentication remains in the owner's local browser profile and human
challenges are never automated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence
import time
import uuid


class MediaKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class BrowserGenerationState(str, Enum):
    CREATED = "created"
    STARTING = "starting"
    AUTH_CHECK = "auth_check"
    READY = "ready"
    SUBMITTING = "submitting"
    WAITING_PROVIDER = "waiting_provider"
    OUTPUT_READY = "output_ready"
    COLLECTING = "collecting"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    NEEDS_OWNER_AUTH = "needs_owner_auth"
    HUMAN_CHALLENGE = "human_challenge"
    RATE_LIMITED = "rate_limited"
    UI_CHANGED = "ui_changed"
    POLICY_BLOCKED = "policy_blocked"
    TIMEOUT = "timeout"
    FAILED = "failed"


TERMINAL_STATES = frozenset(
    {
        BrowserGenerationState.COMPLETE,
        BrowserGenerationState.NEEDS_OWNER_AUTH,
        BrowserGenerationState.HUMAN_CHALLENGE,
        BrowserGenerationState.RATE_LIMITED,
        BrowserGenerationState.UI_CHANGED,
        BrowserGenerationState.POLICY_BLOCKED,
        BrowserGenerationState.TIMEOUT,
        BrowserGenerationState.FAILED,
    }
)


@dataclass(frozen=True)
class BrowserGenerationRequest:
    mission_id: str
    media_kind: MediaKind
    prompt: str
    output_workspace: Path
    aspect_ratio: str = "9:16"
    duration_seconds: float | None = None
    reference_assets: Sequence[Path] = ()
    preset: str | None = None
    deadline_epoch_s: float | None = None
    max_attempts: int = 2
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if not self.mission_id.strip():
            raise ValueError("mission_id is required")
        if not self.prompt.strip():
            raise ValueError("prompt is required")
        if self.max_attempts < 1 or self.max_attempts > 5:
            raise ValueError("max_attempts must be in [1, 5]")
        if self.duration_seconds is not None and self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if self.deadline_epoch_s is not None and self.deadline_epoch_s <= time.time():
            raise ValueError("deadline must be in the future")


@dataclass(frozen=True)
class SubmissionReceipt:
    job_id: str
    provider: str
    submitted_at_epoch_s: float
    evidence: Mapping[str, str]
    provider_job_id: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id or not self.provider:
            raise ValueError("job_id and provider are required")
        if not self.evidence:
            raise ValueError("submission requires observable evidence")


@dataclass(frozen=True)
class ArtifactReceipt:
    job_id: str
    path: Path
    sha256: str
    media_kind: MediaKind
    bytes_size: int
    accepted_at_epoch_s: float
    qa_verdicts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.bytes_size <= 0:
            raise ValueError("artifact must be non-empty")
        if len(self.sha256) != 64:
            raise ValueError("sha256 must be a 64-character hex digest")
        try:
            int(self.sha256, 16)
        except ValueError as exc:
            raise ValueError("sha256 must be hexadecimal") from exc


@dataclass(frozen=True)
class BrowserGenerationObservation:
    job_id: str
    state: BrowserGenerationState
    observed_at_epoch_s: float
    safe_message: str = ""
    evidence: Mapping[str, str] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def owner_action_required(self) -> bool:
        return self.state in {
            BrowserGenerationState.NEEDS_OWNER_AUTH,
            BrowserGenerationState.HUMAN_CHALLENGE,
        }


_ALLOWED_TRANSITIONS: dict[BrowserGenerationState, frozenset[BrowserGenerationState]] = {
    BrowserGenerationState.CREATED: frozenset({BrowserGenerationState.STARTING}),
    BrowserGenerationState.STARTING: frozenset({BrowserGenerationState.AUTH_CHECK, BrowserGenerationState.FAILED}),
    BrowserGenerationState.AUTH_CHECK: frozenset({
        BrowserGenerationState.READY,
        BrowserGenerationState.NEEDS_OWNER_AUTH,
        BrowserGenerationState.HUMAN_CHALLENGE,
        BrowserGenerationState.POLICY_BLOCKED,
        # Дрейф интерфейса и ограничение частоты обнаруживаются ДО отправки —
        # именно там, где их и надо обнаруживать. Без этих двух рёбер оба
        # честных исхода превращались в `FAILED`, а `FAILED` работник пробует
        # заново: попытки уходили в тот же изменившийся интерфейс и в тот же
        # лимит. Это ровно тот слепой повтор, которого контракт не допускает.
        BrowserGenerationState.RATE_LIMITED,
        BrowserGenerationState.UI_CHANGED,
    }),
    BrowserGenerationState.READY: frozenset({BrowserGenerationState.SUBMITTING, BrowserGenerationState.UI_CHANGED}),
    BrowserGenerationState.SUBMITTING: frozenset({
        BrowserGenerationState.WAITING_PROVIDER,
        BrowserGenerationState.RATE_LIMITED,
        BrowserGenerationState.UI_CHANGED,
        BrowserGenerationState.FAILED,
    }),
    BrowserGenerationState.WAITING_PROVIDER: frozenset({
        BrowserGenerationState.OUTPUT_READY,
        BrowserGenerationState.RATE_LIMITED,
        BrowserGenerationState.TIMEOUT,
        BrowserGenerationState.FAILED,
        BrowserGenerationState.HUMAN_CHALLENGE,
        # Оба состояния работник уже умел получать от наблюдения, но перехода
        # для них не было — и попытка их записать падала исключением прямо
        # посреди ожидания. Страница провайдера вправе сказать «интерфейс
        # другой» или «этого нет в вашем плане» и после отправки тоже.
        BrowserGenerationState.POLICY_BLOCKED,
        BrowserGenerationState.UI_CHANGED,
    }),
    BrowserGenerationState.OUTPUT_READY: frozenset({BrowserGenerationState.COLLECTING}),
    BrowserGenerationState.COLLECTING: frozenset({BrowserGenerationState.VERIFYING, BrowserGenerationState.FAILED}),
    BrowserGenerationState.VERIFYING: frozenset({BrowserGenerationState.COMPLETE, BrowserGenerationState.FAILED}),
}


def transition_allowed(current: BrowserGenerationState, target: BrowserGenerationState) -> bool:
    """Return whether a normal automatic transition is allowed.

    Terminal/intervention states have no automatic outgoing transition.  A
    resumed owner-auth flow must create/recover a job explicitly rather than
    pretending a challenge never occurred.
    """
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())
