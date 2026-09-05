"""Node Agent contract + транспорт (§11, §12).

Node Agent на машине: регистрируется, шлёт heartbeat, объявляет способности,
принимает АВТОРИЗОВАННУЮ работу и исполняет её через локальный V3-исполнитель
(`ExecutionBridge` → UniversalComputerAgent → V2). Он не обходит ни разрешения,
ни верификацию, ни бюджет, ни приватность — всё это ниже него.

Транспорт:
  * `LocalNodeTransport` — in-process: несколько логических узлов в одном
    процессе (одна машина, тесты, единственный Ai Max сегодня);
  * удалённый транспорт НЕ реализован. В репозитории есть device/session
    principals (bossman.remote_client.auth) и WS-аутентификация периметра, но
    нет подписи запросов, nonce/replay-окна, mTLS и ротации ключей узлов.
    Поэтому `RemoteNodeTransport` честно поднимает исключение:
    REMOTE_TRANSPORT_PRODUCTION_READY=NO, NODE_AUTH_PRODUCTION_READY=NO.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from ..organization.bridges import ExecutionBridge
from ..organization.contracts import DelegationContract
from ..organization.models import WorkResult


@dataclass(frozen=True)
class NodeExecutionRequest:
    work_id: str
    mission_id: str
    agent_id: str                 # КТО (решение организации)
    lease_id: str
    fence: int
    contract: DelegationContract
    timeout_seconds: float = 600.0
    context_policy: str = "FULL"  # FULL | MINIMIZED — что узлу можно видеть
    metadata: Mapping[str, Any] = field(default_factory=dict)


class NodeTransport(Protocol):
    def dispatch(self, node_id: str, request: NodeExecutionRequest) -> WorkResult: ...
    def cancel(self, node_id: str, work_id: str) -> bool: ...
    def probe(self, node_id: str) -> bool: ...


class NodeUnavailable(RuntimeError):
    pass


class RemoteTransportUnavailable(NotImplementedError):
    pass


class LocalNodeTransport:
    """Реестр in-process узлов: node_id → ExecutionBridge этого узла."""

    def __init__(self) -> None:
        self._runtimes: dict[str, ExecutionBridge] = {}
        self._down: set[str] = set()

    def attach(self, node_id: str, runtime: ExecutionBridge) -> None:
        self._runtimes[node_id] = runtime
        self._down.discard(node_id)

    def detach(self, node_id: str) -> None:
        self._runtimes.pop(node_id, None)
        self._down.add(node_id)

    def probe(self, node_id: str) -> bool:
        return node_id in self._runtimes and node_id not in self._down

    def dispatch(self, node_id: str, request: NodeExecutionRequest) -> WorkResult:
        rt = self._runtimes.get(node_id)
        if rt is None or node_id in self._down:
            raise NodeUnavailable(f"node {node_id!r} is not attached to the local transport")
        contract = request.contract
        if request.context_policy == "MINIMIZED":
            contract = _minimized(contract)
        # TRUTH-003 §12: исполнитель узла узнаёт fence аренды — он попадает в каждый
        # ActionReceipt шага; флот потом отвергает receipt'ы, записанные под устаревшим fence.
        contract.metadata["fleet_dispatch"] = {"fence": int(request.fence), "lease_id": request.lease_id,
                                               "node_id": node_id}
        return rt.execute(contract, agent_id=request.agent_id)

    def cancel(self, node_id: str, work_id: str) -> bool:
        return False                      # безопасной отмены у локального моста нет — честно


class RemoteNodeTransport:
    def dispatch(self, node_id: str, request: NodeExecutionRequest) -> WorkResult:
        raise RemoteTransportUnavailable(
            "remote node transport is not implemented: requires authenticated node identity, signed "
            "requests with replay window, encrypted channel and key rotation (REMOTE_TRANSPORT_PRODUCTION_READY=NO)")

    def cancel(self, node_id: str, work_id: str) -> bool:
        return False

    def probe(self, node_id: str) -> bool:
        return False


def _minimized(c: DelegationContract) -> DelegationContract:
    """Минимизированный контекст для недоверенного узла: без inputs/constraints/
    метаданных — только цель, способность, критерии, улики и шаги."""
    return DelegationContract(work_id=c.work_id, mission_id=c.mission_id, department_id=c.department_id, goal=c.goal,
                              required_capability=c.required_capability, success_criteria=list(c.success_criteria),
                              evidence_required=list(c.evidence_required), budget=c.budget, risk=c.risk,
                              priority=c.priority, required_role=c.required_role, side_effect=c.side_effect,
                              steps=[dict(s) for s in c.steps], privacy=c.privacy, placement=dict(c.placement))
