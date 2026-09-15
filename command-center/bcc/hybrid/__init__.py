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
    ContextStoreRuntime,
    ContextNamespace,
    ContextProvenance,
    ContextRecord,
    ContextCandidate,
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
from .context_filter import FilterResult, SecretMaterialRefused, scrub_for_memory
from .context_store import (
    BossmanNativeContextStore,
    ContextStorePlanner,
    OpenContextShadowStore,
    OpenContextTransport,
    StaleContextWrite,
    build_record,
    make_planner,
)

__all__ = [
    "DesktopRuntime",
    "BrowserRuntime",
    "WebEditorRuntime",
    "VideoCompositionRuntime",
    "LocalModelRuntime",
    "ContextStoreRuntime",
    "ContextNamespace",
    "ContextProvenance",
    "ContextRecord",
    "ContextCandidate",
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
    "BossmanNativeContextStore",
    "ContextStorePlanner",
    "OpenContextShadowStore",
    "OpenContextTransport",
    "StaleContextWrite",
    "build_record",
    "make_planner",
    "FilterResult",
    "SecretMaterialRefused",
    "scrub_for_memory",
]
