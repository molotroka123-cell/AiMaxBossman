"""Stage 8 — SandboxManager: оркестратор жизненного цикла песочницы.

Связывает политику/риск, resource admission, рантайм, сеть, секреты, артефакты и
траекторию. Все смены состояния идут через _transition (запрещённый переход →
InvalidTransition). Инварианты: OFF значит OFF (выключено → ничего не поднимаем);
fail closed (нет нужной изоляции → отказ, не даунгрейд); аренда ресурса
освобождается на всех путях (успех/ошибка/отмена/снос/recovery); double release
безопасен.
"""
from __future__ import annotations

from pathlib import Path

from .. import correlation, errors, obs
from .artifacts import ArtifactGate
from .egress import EgressProxy
from .models import (
    NetworkMode,
    SandboxSession,
    SandboxSpec,
    SandboxState,
    can_transition,
    new_id,
)
from .network import NetworkGuard
from .policy import PolicyEngine, RiskEngine
from .resources import ResourceLeaseAdapter
from .runtime import DestroyFailure, RuntimeCrash, RuntimeTimeout, SandboxRuntime
from .trajectory import TrajectoryRecorder

log = obs.get_logger("bossman.sandbox")


class SandboxManager:
    def __init__(
        self,
        runtime: SandboxRuntime,
        *,
        enabled: bool,
        workspace_root: str | Path,
        resources: ResourceLeaseAdapter | None = None,
        policy_engine: PolicyEngine | None = None,
        risk_engine: RiskEngine | None = None,
        network_guard: NetworkGuard | None = None,
        broker=None,
    ) -> None:
        self.runtime = runtime
        self.enabled = enabled
        self.workspace_root = Path(workspace_root)
        self.resources = resources or ResourceLeaseAdapter()
        self.policy = policy_engine or PolicyEngine()
        self.risk = risk_engine or RiskEngine()
        self.network = network_guard or NetworkGuard()
        self.broker = broker
        self.sessions: dict[str, SandboxSession] = {}
        self.trajectories: dict[str, TrajectoryRecorder] = {}
        # Активные egress-прокси по песочницам (только для ALLOWLIST/INTERNET).
        self.proxies: dict[str, EgressProxy] = {}

    # ---- переходы ----

    def _transition(self, s: SandboxSession, dst: SandboxState, note: str = "") -> None:
        if not can_transition(s.state, dst):
            raise errors.InvalidTransition(
                f"illegal transition {s.state.value} -> {dst.value}",
                extra={"from": s.state.value, "to": dst.value, "sandbox": s.id})
        s.record(dst, note)
        tr = self.trajectories.get(s.id)
        if tr:
            tr.lifecycle(dst.value, note)

    def _traj(self, s: SandboxSession) -> TrajectoryRecorder:
        tr = self.trajectories.get(s.id)
        if tr is None:
            sink = self.workspace_root / s.id / "trajectory.jsonl"
            tr = TrajectoryRecorder(s.id, sink_path=sink)
            self.trajectories[s.id] = tr
        return tr

    # ---- create = заявка + допуск (риск, политика, resource admission) ----

    async def create(self, spec: SandboxSpec, *, snap=None) -> SandboxSession:
        # OFF значит OFF: выключенная песочница ничего не поднимает.
        if not self.enabled:
            raise errors.SandboxDisabled("sandbox feature is disabled (OFF=OFF)")

        s = SandboxSession(id=new_id("sbx"), spec=spec, state=SandboxState.REQUESTED)
        self.sessions[s.id] = s
        self._traj(s).lifecycle(SandboxState.REQUESTED.value, "created")

        try:
            # 1) риск → 2) политика (fail-closed) → 3) resource admission.
            s.risk = self.risk.assess(spec)
            caps = self.runtime.capabilities()
            s.policy = self.policy.resolve(spec, s.risk, caps)
            self.resources.reserve(s, snap=snap)
            self._traj(s).resource("reserved", s.lease_id)
            self._transition(s, SandboxState.ADMITTED, f"risk={s.risk.level.value}")
            return s
        except errors.BossmanError as exc:
            # Допуск не прошёл: освободить возможную аренду, пометить FAILED.
            self.resources.release(s)
            s.error = f"{exc.code.value}: {exc.detail}"
            self._traj(s).failure("create", s.error)
            self._transition(s, SandboxState.FAILED, s.error)
            raise

    # ---- start = подготовка среды + запуск ----

    async def start(self, s: SandboxSession) -> None:
        self._transition(s, SandboxState.PREPARING, "prepare env")
        try:
            await self.runtime.prepare(s)
            await self._start_egress(s)
            self._transition(s, SandboxState.READY, "env ready")
            self._transition(s, SandboxState.RUNNING, "start")
            await self.runtime.start(s)
        except (RuntimeCrash, RuntimeTimeout, errors.BossmanError, Exception) as exc:  # noqa: BLE001
            await self._fail(s, "start", exc)
            raise

    async def poll(self, s: SandboxSession) -> SandboxState:
        """Опросить состояние исполнения и продвинуть автомат."""
        if s.state not in (SandboxState.RUNNING, SandboxState.PAUSED):
            return s.state
        try:
            result = await self.runtime.poll(s)
        except (RuntimeCrash, RuntimeTimeout, Exception) as exc:  # noqa: BLE001
            await self._fail(s, "poll", exc)
            return s.state
        if result == SandboxState.COMPLETED:
            self._transition(s, SandboxState.COMPLETED, "runtime completed")
        elif result == SandboxState.FAILED:
            await self._fail(s, "poll", RuntimeError("runtime reported FAILED"))
        return s.state

    async def freeze(self, s: SandboxSession) -> None:
        if s.state not in (SandboxState.READY, SandboxState.RUNNING, SandboxState.PAUSED):
            raise errors.InvalidTransition(f"cannot freeze from {s.state.value}")
        await self.runtime.freeze(s)
        self._transition(s, SandboxState.FROZEN, "frozen for investigation")

    async def cancel(self, s: SandboxSession) -> None:
        try:
            await self.runtime.cancel(s)
        except Exception as exc:  # noqa: BLE001
            self._traj(s).failure("cancel", repr(exc))
        await self.destroy(s, note="cancelled")

    async def destroy(self, s: SandboxSession, *, note: str = "destroy") -> None:
        # Идемпотентно: уже снесённую не трогаем.
        if s.state == SandboxState.DESTROYED:
            return
        if s.state != SandboxState.DESTROYING:
            self._transition(s, SandboxState.DESTROYING, note)
        destroy_error: str | None = None
        await self._stop_egress(s)      # выход закрывается раньше сноса среды
        try:
            await self.runtime.destroy(s)
        except DestroyFailure as exc:
            destroy_error = repr(exc)
            self._traj(s).failure("destroy", destroy_error)
        except Exception as exc:  # noqa: BLE001
            destroy_error = repr(exc)
            self._traj(s).failure("destroy", destroy_error)
        # Аренда и секреты освобождаются ВСЕГДА (даже если снос рантайма упал).
        released = self.resources.release(s)
        self._traj(s).resource("released" if released else "release_noop", None)
        if self.broker is not None:
            try:
                self.broker.revoke_sandbox(s.id)
            except Exception:  # noqa: BLE001
                pass
        # Снос-фейл фиксируем как FAILED-примечание, но среду считаем снесённой
        # (fail closed: ресурсы освобождены, песочница нежива).
        self._transition(s, SandboxState.DESTROYED, note if not destroy_error else f"destroyed_with_error:{destroy_error}")

    async def _start_egress(self, s: SandboxSession) -> None:
        """Для ALLOWLIST/INTERNET поднять локальный CONNECT-прокси — единственный
        разрешённый выход песочницы. В OFFLINE не поднимается вовсе."""
        if s.policy is None or s.policy.network_mode == NetworkMode.OFFLINE:
            return
        proxy = EgressProxy(s.policy, guard=self.network, recorder=self._traj(s))
        await proxy.start()
        self.proxies[s.id] = proxy
        addr = proxy.address
        if addr:
            # Рантайм отдаёт адрес прокси процессу как http(s)_proxy.
            s.spec.labels["egress_proxy"] = f"{addr[0]}:{addr[1]}"
        self._traj(s).record("network", host="-", allowed=True,
                             reason=f"egress proxy on {s.spec.labels.get('egress_proxy')}")

    async def _stop_egress(self, s: SandboxSession) -> None:
        proxy = self.proxies.pop(s.id, None)
        if proxy is not None:
            try:
                await proxy.stop()
            except Exception:  # noqa: BLE001
                pass

    async def _fail(self, s: SandboxSession, where: str, exc: BaseException) -> None:
        s.error = repr(exc)
        self._traj(s).failure(where, s.error)
        if can_transition(s.state, SandboxState.FAILED):
            self._transition(s, SandboxState.FAILED, s.error)
        # Провал → немедленный снос и освобождение аренды.
        await self.destroy(s, note=f"failed:{where}")

    # ---- сеть / секреты (control-plane решения) ----

    def check_network(self, s: SandboxSession, host: str, port: int | None = None) -> bool:
        if s.policy is None:
            raise errors.NetworkDenied("no resolved policy")
        d = self.network.decide(host, s.policy, port)
        self._traj(s).network(d.host, d.allowed, d.reason)
        if not d.allowed:
            raise errors.NetworkDenied(f"egress blocked: {d.reason}", extra={"host": d.host})
        return True

    def grant_secret(self, s: SandboxSession, scope: str, ttl_seconds: float):
        if self.broker is None:
            raise errors.SecretDenied("no secret broker configured")
        if s.policy is None:
            raise errors.SecretDenied("no resolved policy")
        if scope not in s.spec.secret_scopes:
            raise errors.SecretDenied(f"scope '{scope}' not requested by spec")
        g = self.broker.grant(s.id, scope, ttl_seconds)
        self._traj(s).record("approval", kind_detail="secret_grant", scope=scope, grant_id=g.id)
        return g

    # ---- восстановление после рестарта ----

    async def recover(self) -> list[str]:
        """Незавершённые (нетерминальные) песочницы после рестарта: пометить и
        снести, освободив аренды (аналог runner.mark_interrupted). Возвращает id."""
        recovered: list[str] = []
        for s in list(self.sessions.values()):
            if s.state in (SandboxState.DESTROYED,):
                continue
            recovered.append(s.id)
            self._traj(s).failure("recover", "process restart — reclaiming")
            await self.destroy(s, note="recovered_after_restart")
        return recovered

    def artifact_gate(self, s: SandboxSession, secret_scanner=None) -> ArtifactGate:
        return ArtifactGate(self.workspace_root / s.id / "out", secret_scanner=secret_scanner)
