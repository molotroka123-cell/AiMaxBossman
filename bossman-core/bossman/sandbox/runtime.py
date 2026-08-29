"""Stage 8 — абстракция рантайма песочницы + детерминированный FakeRuntime.

CORE важнее конкретного гипервизора: менеджер работает через этот интерфейс, а
реальные рантаймы (rootless SAFE, gVisor-класс, MicroVM) подключаются адаптерами.
FakeRuntime детерминированно симулирует сценарии для тестов ядра — без реальных
процессов, контейнеров и sleep-ов.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .. import errors
from .models import (
    IsolationTier,
    RuntimeCapabilities,
    SandboxSession,
    SandboxState,
)


class RuntimeCrash(RuntimeError):
    """Рантайм сообщил о падении среды исполнения."""


class RuntimeTimeout(RuntimeError):
    """Среда не уложилась в wall-time."""


class DestroyFailure(RuntimeError):
    """Снос среды не удался — ресурсы могут быть не освобождены рантаймом."""


@runtime_checkable
class SandboxRuntime(Protocol):
    name: str

    def capabilities(self) -> RuntimeCapabilities: ...

    async def prepare(self, session: SandboxSession) -> None:
        """PREPARING: подготовить рабочую область/mounts. Бросает при неудаче."""

    async def start(self, session: SandboxSession) -> None:
        """READY→RUNNING: запустить исполнение."""

    async def poll(self, session: SandboxSession) -> SandboxState:
        """Опросить статус: вернуть RUNNING / COMPLETED / FAILED."""

    async def freeze(self, session: SandboxSession) -> None:
        """Заморозить среду для расследования/форка."""

    async def cancel(self, session: SandboxSession) -> None:
        """Прервать исполнение (RUNNING/PAUSED)."""

    async def destroy(self, session: SandboxSession) -> None:
        """Снести среду и освободить ресурсы рантайма. Может бросить DestroyFailure."""


# Сценарии для FakeRuntime — задаются через spec.labels["fake_scenario"].
FAKE_SCENARIOS = frozenset({
    "success", "slow_start", "timeout", "crash", "destroy_failure", "partial_output",
})


class FakeRuntime:
    """Детерминированный рантайм для тестов ядра. Никаких реальных ресурсов.

    Сценарий берётся из session.spec.labels['fake_scenario'] (по умолчанию
    'success'). Поведение полностью предсказуемо и не зависит от времени/потоков.
    """

    name = "fake"

    def __init__(self, *, tier: IsolationTier = IsolationTier.MICROVM,
                 supports_allowlist: bool = True) -> None:
        # Fake умеет выдавать любой tier, чтобы не мешать тестам политики; тесты
        # fail-closed используют отдельный FakeRuntime с урезанными tiers.
        self._caps = RuntimeCapabilities(
            name=self.name,
            tiers=frozenset({IsolationTier.ROOTLESS, IsolationTier.CONTAINER, IsolationTier.MICROVM})
            if tier == IsolationTier.MICROVM else frozenset({t for t in IsolationTier if t.rank <= tier.rank}),
            supports_offline=True,
            supports_allowlist=supports_allowlist,
            supports_readonly_root=True,
            supports_seccomp=True,
            supports_pid_limit=True,
            supports_mem_limit=True,
        )
        self.prepared: set[str] = set()
        self.started: set[str] = set()
        self.destroyed: set[str] = set()

    def capabilities(self) -> RuntimeCapabilities:
        return self._caps

    @staticmethod
    def _scenario(session: SandboxSession) -> str:
        return session.spec.labels.get("fake_scenario", "success")

    async def prepare(self, session: SandboxSession) -> None:
        sc = self._scenario(session)
        if sc == "slow_start":
            # Симулируем провал подготовки как детерминированную неудачу.
            raise errors.IsolationUnavailable("fake: slow_start — prepare exceeded budget")
        self.prepared.add(session.id)

    async def start(self, session: SandboxSession) -> None:
        if session.id not in self.prepared:
            raise RuntimeError("fake: start before prepare")
        if self._scenario(session) == "crash":
            raise RuntimeCrash("fake: crash on start")
        self.started.add(session.id)

    async def poll(self, session: SandboxSession) -> SandboxState:
        sc = self._scenario(session)
        if sc == "timeout":
            raise RuntimeTimeout("fake: wall-time exceeded")
        if sc == "crash":
            raise RuntimeCrash("fake: crash during run")
        # success и partial_output оба завершаются COMPLETED (артефакты отдельно
        # проходят Artifact Gate; partial_output влияет на набор артефактов, не на статус).
        session.exit_code = 0
        return SandboxState.COMPLETED

    async def freeze(self, session: SandboxSession) -> None:
        return None

    async def cancel(self, session: SandboxSession) -> None:
        self.started.discard(session.id)

    async def destroy(self, session: SandboxSession) -> None:
        if self._scenario(session) == "destroy_failure":
            raise DestroyFailure("fake: destroy failed")
        self.destroyed.add(session.id)
