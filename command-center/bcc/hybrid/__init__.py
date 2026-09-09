"""
Bossman Hybrid OSS Integration Architecture.
Authoritative control remains with Bossman; external engines provide mechanics only.
"""

from .capabilities import (
    DesktopRuntime,
    BrowserRuntime,
    WebEditorRuntime,
    VideoCompositionRuntime,
    LocalModelRuntime,
    RuntimeIdentity,
    EffectCorrelation,
    Observation,
    EvidenceCandidate,
    HybridRuntimeError,
    BackendUnavailableError,
    BackendVersionMismatchError,
    OperationTimeoutError,
    OperationCancelledError,
    PolicyRevokedError,
    MalformedResponseError,
    StateVerificationFailedError,
)
from .registry import AdapterRegistry, CapabilityName, BackendType
from .sidecar import SidecarProcessManager, SidecarConfig, SidecarStatus
from .evidence import EvidenceNormalizer

__all__ = [
    "DesktopRuntime",
    "BrowserRuntime",
    "WebEditorRuntime",
    "VideoCompositionRuntime",
    "LocalModelRuntime",
    "RuntimeIdentity",
    "EffectCorrelation",
    "Observation",
    "EvidenceCandidate",
    "HybridRuntimeError",
    "BackendUnavailableError",
    "BackendVersionMismatchError",
    "OperationTimeoutError",
    "OperationCancelledError",
    "PolicyRevokedError",
    "MalformedResponseError",
    "StateVerificationFailedError",
    "AdapterRegistry",
    "CapabilityName",
    "BackendType",
    "SidecarProcessManager",
    "SidecarConfig",
    "SidecarStatus",
    "EvidenceNormalizer",
]
