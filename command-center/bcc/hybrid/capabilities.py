"""
Capability interface contracts for Bossman Hybrid OSS.
Strictly typed, resilient, cancellable, and correlation-tracked.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class HybridRuntimeError(Exception):
    """Base exception for hybrid OSS runtime failures."""
    pass


class BackendUnavailableError(HybridRuntimeError):
    """The requested backend process or sidecar is unavailable or uninstalled."""
    pass


class BackendVersionMismatchError(HybridRuntimeError):
    """The backend reported an unsupported protocol or engine version."""
    pass


class OperationTimeoutError(HybridRuntimeError):
    """The operation exceeded its allocated execution deadline."""
    pass


class OperationCancelledError(HybridRuntimeError):
    """The operation was cancelled by owner or supervisory policy."""
    pass


class PolicyRevokedError(HybridRuntimeError):
    """Effect authorization was revoked in-flight prior to effect commitment."""
    pass


class MalformedResponseError(HybridRuntimeError):
    """The external engine returned a response that violates contract schema."""
    pass


class StateVerificationFailedError(HybridRuntimeError):
    """Independent Bossman post-state verification did not match external claims."""
    pass


@dataclass(frozen=True)
class RuntimeIdentity:
    name: str
    version: str
    backend_type: str
    pid: Optional[int] = None
    endpoint: Optional[str] = None
    is_healthy: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EffectCorrelation:
    correlation_id: str
    task_id: str
    run_id: str
    deadline_epoch_s: float
    issued_at_iso: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    expected_target_fingerprint: Optional[str] = None
    cancellation_token: Optional[Callable[[], bool]] = None

    def is_cancelled(self) -> bool:
        if self.cancellation_token is not None:
            return self.cancellation_token()
        return False

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc).timestamp() > self.deadline_epoch_s


@dataclass(frozen=True)
class Observation:
    correlation_id: str
    raw_data: Dict[str, Any]
    observed_at_iso: str
    source_runtime: RuntimeIdentity
    verified_by_bossman: bool = False


@dataclass(frozen=True)
class EvidenceCandidate:
    correlation_id: str
    evidence_type: str
    digest: str
    payload: Dict[str, Any]
    source_runtime: RuntimeIdentity
    verified_by_bossman: bool = False
    is_completion_proof: bool = False


class DesktopRuntime(ABC):
    """Interface for desktop UI mechanics."""

    @abstractmethod
    def get_runtime_identity(self) -> RuntimeIdentity:
        pass

    @abstractmethod
    def launch_app(self, executable: str, args: List[str], correlation: EffectCorrelation) -> Dict[str, Any]:
        pass

    @abstractmethod
    def click_element(self, target_spec: Dict[str, Any], correlation: EffectCorrelation) -> Observation:
        pass

    @abstractmethod
    def get_window_state(self, target_spec: Dict[str, Any], correlation: EffectCorrelation) -> Observation:
        pass

    @abstractmethod
    def close_app(self, pid: int, correlation: EffectCorrelation) -> bool:
        pass


class BrowserRuntime(ABC):
    """Interface for browser mechanics."""

    @abstractmethod
    def get_runtime_identity(self) -> RuntimeIdentity:
        pass

    @abstractmethod
    def navigate(self, url: str, correlation: EffectCorrelation) -> Observation:
        pass

    @abstractmethod
    def click(self, selector: str, correlation: EffectCorrelation) -> Observation:
        pass

    @abstractmethod
    def extract_dom(self, correlation: EffectCorrelation) -> Observation:
        pass


class WebEditorRuntime(ABC):
    """Interface for Web Designer mechanics."""

    @abstractmethod
    def get_runtime_identity(self) -> RuntimeIdentity:
        pass

    @abstractmethod
    def load_project(self, project_data: Dict[str, Any], correlation: EffectCorrelation) -> Observation:
        pass

    @abstractmethod
    def apply_operation(self, op_type: str, params: Dict[str, Any], correlation: EffectCorrelation) -> Observation:
        pass

    @abstractmethod
    def export_project(self, correlation: EffectCorrelation) -> Dict[str, Any]:
        pass


class VideoCompositionRuntime(ABC):
    """Interface for video composition and timeline mechanics."""

    @abstractmethod
    def get_runtime_identity(self) -> RuntimeIdentity:
        pass

    @abstractmethod
    def compose_timeline(self, timeline_spec: Dict[str, Any], correlation: EffectCorrelation) -> Observation:
        pass

    @abstractmethod
    def render_preview(self, params: Dict[str, Any], correlation: EffectCorrelation) -> Observation:
        pass

    @abstractmethod
    def export_media(self, params: Dict[str, Any], correlation: EffectCorrelation) -> Dict[str, Any]:
        pass


class LocalModelRuntime(ABC):
    """Interface for local model serving and admission mechanics."""

    @abstractmethod
    def get_runtime_identity(self) -> RuntimeIdentity:
        pass

    @abstractmethod
    def load_model(self, model_id: str, memory_budget_mb: int, correlation: EffectCorrelation) -> Observation:
        pass

    @abstractmethod
    def unload_model(self, model_id: str, correlation: EffectCorrelation) -> bool:
        pass

    @abstractmethod
    def health_probe(self, correlation: EffectCorrelation) -> Dict[str, Any]:
        pass
