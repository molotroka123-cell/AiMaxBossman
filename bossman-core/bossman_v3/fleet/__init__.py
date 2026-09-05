"""Bossman Fleet OS (V3) — ГДЕ исполняется работа.

Organization = КТО; Fleet = ГДЕ; Model Broker (bcc/v2/model_router) = КАКАЯ
МОДЕЛЬ; V3/V2 = КАК и ДОКАЗАНО ЛИ. Флот не выбирает модель и не исполнителя,
не исполняет и не верифицирует. PLACED ≠ DISPATCHED ≠ EXECUTED ≠ VERIFIED.

Флаг: BOSSMAN_V3_ENABLED + BOSSMAN_V3_FLEET.
Удалённый транспорт узлов не реализован (REMOTE_TRANSPORT_PRODUCTION_READY=NO).
"""
from .artifacts import ArtifactRegistry, describe_file, sha256_file, verify_file
from .control_plane import FleetControlPlane, FleetExecutionBridge, FleetLearning
from .credentials import CredentialBroker, GrantDenied, SecretProvider
from .flight import DistributedFlightRecorder
from .journal import FleetEventJournal
from .leases import LeaseConflict, LeaseManager, StaleLease
from .models import (CLOUD, INTERNAL, LEGAL_TRANSITIONS, LOCAL_ONLY, PRIVATE, PUBLIC, TRUSTED_LOCAL, TRUSTED_REMOTE,
                     ArtifactDescriptor, CredentialGrant, FailureClass, FleetEventType, FlightRecord, FlightState,
                     Heartbeat, IllegalTransition, Lease, NodeExplanation, NodeState, NodeStatus, Placement,
                     PlacementRequirement, RetryPolicy, classify_failure, mutation_key)
from .node_agent import (LocalNodeTransport, NodeExecutionRequest, NodeTransport, NodeUnavailable,
                         RemoteNodeTransport, RemoteTransportUnavailable)
from .privacy import PrivacyDecision, PrivacyRouter
from .queue import Claim, WorkQueue
from .registry import HealthReport, NodeRegistry
from .resume import FleetResumeKernel, ResumeDecision
from .scheduler import FleetScheduler
from .store import FleetStore
from .twin import FleetDigitalTwin

__all__ = [
    "CLOUD", "INTERNAL", "LEGAL_TRANSITIONS", "LOCAL_ONLY", "PRIVATE", "PUBLIC", "TRUSTED_LOCAL", "TRUSTED_REMOTE",
    "ArtifactDescriptor", "ArtifactRegistry", "Claim", "CredentialBroker", "CredentialGrant",
    "DistributedFlightRecorder", "FailureClass", "FleetControlPlane", "FleetDigitalTwin", "FleetEventJournal",
    "FleetEventType", "FleetExecutionBridge", "FleetLearning", "FleetResumeKernel", "FleetScheduler", "FleetStore",
    "FlightRecord", "FlightState", "GrantDenied", "HealthReport", "Heartbeat", "IllegalTransition", "Lease",
    "LeaseConflict", "LeaseManager", "LocalNodeTransport", "NodeExecutionRequest", "NodeExplanation",
    "NodeRegistry", "NodeState", "NodeStatus", "NodeTransport", "NodeUnavailable", "Placement",
    "PlacementRequirement", "PrivacyDecision", "PrivacyRouter", "RemoteNodeTransport",
    "RemoteTransportUnavailable", "ResumeDecision", "RetryPolicy", "SecretProvider", "StaleLease", "WorkQueue",
    "classify_failure", "describe_file", "mutation_key", "sha256_file", "verify_file",
]
