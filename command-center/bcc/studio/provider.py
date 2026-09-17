"""Generation observations, never task-completion evidence. No network on import."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from bcc import model_health

STATES = frozenset({'queued','running','completed','failed','refused','canceled','timeout'})
REASONS = frozenset({'unauthorized','throttled','insufficient_credit','provider_down','content_policy','malformed','silent','timeout'})

@dataclass(frozen=True)
class GenerationPlane:
    model: str
    prompt: str
    settings: dict[str, Any] = field(default_factory=dict)
    media: tuple[dict[str, Any], ...] = ()

@dataclass(frozen=True)
class Submitted:
    request_id: str
    cancel_ref: str | None = None

@dataclass(frozen=True)
class ProviderOutput:
    ref: str

@dataclass(frozen=True)
class Fetched:
    path: Path
    bytes: int
    mime: str
    sha256: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None

@dataclass(frozen=True)
class ProviderStatus:
    state: str
    reason: str | None = None
    outputs: tuple[ProviderOutput, ...] = ()

    def __post_init__(self):
        if self.state not in STATES or (self.reason is not None and self.reason not in REASONS):
            raise ValueError('invalid provider state/reason')
        if self.state in {'failed','refused','timeout'} and self.reason is None:
            raise ValueError('failure requires a named reason')
        if self.state == 'completed' and (self.reason is not None or not self.outputs):
            raise ValueError('completed observation requires outputs and no refusal')
        if self.state != 'completed' and self.outputs:
            raise ValueError('outputs require completed observation')

    @property
    def is_evidence(self):
        return False  # only independent byte verification can satisfy the task gate

    @property
    def health(self):
        if self.reason == 'insufficient_credit': return model_health.THROTTLED
        if self.reason == 'content_policy': return model_health.UNMEASURED
        if self.reason: return self.reason
        return model_health.UNMEASURED  # even completed is not a capability probe

    @property
    def verdict(self):
        if self.reason in {'unauthorized','throttled','insufficient_credit'}:
            return 'OWNER_REQUIRED'
        if self.reason: return 'FAIL'
        return 'PARTIAL'

class ProviderFailure(RuntimeError):
    """Safe named failure; deliberately excludes raw provider text/credentials."""
    def __init__(self, status: ProviderStatus):
        self.status = status
        super().__init__(f'{status.state}: {status.reason}')


@runtime_checkable
class GenerationProvider(Protocol):
    name: str
    async def submit(self, plane: GenerationPlane) -> Submitted: ...
    async def status(self, request_id: str) -> ProviderStatus: ...
    async def cancel(self, request_id: str) -> None: ...
    async def fetch(self, output: ProviderOutput, dest: Path) -> Fetched: ...


def classify_response(status_code: int, body: Any) -> ProviderStatus:
    """Classify a normalized adapter response, NOT a guessed vendor API schema.

    Raw response text is never retained (it may contain a credential).
    HTTP failures take precedence over misleading completed bodies.
    """
    reason = {401:'unauthorized',403:'unauthorized',402:'insufficient_credit',429:'throttled',404:'provider_down'}.get(status_code)
    if reason is None and status_code >= 500: reason = 'provider_down'
    if reason is None and not 200 <= status_code < 300: reason = 'malformed'
    if reason: return ProviderStatus('failed',reason)
    if body is None or isinstance(body,str) and not body.strip():
        return ProviderStatus('failed','silent')
    if not isinstance(body,dict): return ProviderStatus('failed','malformed')
    error = body.get('error')
    error_code = error.get('code') if isinstance(error,dict) else error
    if error_code == 'insufficient_credit': return ProviderStatus('failed','insufficient_credit')
    state = body.get('status')
    if state in ('nsfw','refused') or error_code == 'content_policy':
        return ProviderStatus('refused','content_policy')
    if error: return ProviderStatus('failed','malformed')
    if state == 'completed':
        outputs = body.get('outputs')
        if not isinstance(outputs,list) or not outputs or any(not isinstance(x,dict) or not isinstance(x.get('ref'),str) or not x['ref'].strip() for x in outputs):
            return ProviderStatus('failed','malformed')
        return ProviderStatus('completed',outputs=tuple(ProviderOutput(x['ref']) for x in outputs))
    if state in ('queued','running','canceled'): return ProviderStatus(state)
    if state == 'timeout': return ProviderStatus('timeout','timeout')
    return ProviderStatus('failed','malformed')
