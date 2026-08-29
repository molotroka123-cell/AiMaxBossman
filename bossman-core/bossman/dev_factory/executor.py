"""Stage 10 — исполнитель: тесты идут ТОЛЬКО через песочницу Этапа 8.

Никакого второго рантайма и никакого исполнения чужого кода на хосте. Незнакомый
репозиторий недоверен, поэтому требует более сильной изоляции: если её нет —
падаем закрыто (IsolationUnavailable проходит наверх и даёт FAIL, а не «успех»).
"""
from __future__ import annotations

from pathlib import Path

from .. import errors, obs
from ..sandbox import (
    NetworkMode,
    PolicyMode,
    ResourceRequest,
    SandboxSpec,
)
from .models import DevJob, DevStep

log = obs.get_logger("bossman.dev_factory.exec")


class SandboxExecutor:
    """Гоняет шаги задания в песочнице Этапа 8."""

    def __init__(self, manager, *, snapshot=None, wall_time_seconds: int = 600,
                 editor=None) -> None:
        self.manager = manager        # bossman.sandbox.SandboxManager
        self.snapshot = snapshot
        self.wall_time_seconds = wall_time_seconds
        # Шов под модель/агента: async (job, step) -> None. Пишет ТОЛЬКО в
        # job.workspace (одноразовую копию), прод-дерево ему недоступно.
        self.editor = editor

    def _spec(self, job: DevJob, step: DevStep) -> SandboxSpec:
        # Недоверенный репозиторий → DEVELOPER (контейнерная изоляция).
        # Доверенный — SAFE. Сеть всегда OFFLINE: тестам интернет не нужен.
        mode = PolicyMode.SAFE if job.trusted_repo else PolicyMode.DEVELOPER
        return SandboxSpec(
            task=f"{job.id}:{step.kind.value}",
            policy_mode=mode,
            network_mode=NetworkMode.OFFLINE,
            workspace_source=job.workspace,
            trusted_source=job.trusted_repo,
            resources=ResourceRequest(wall_time_seconds=self.wall_time_seconds),
            labels={"argv": list(step.argv)} if step.argv else {},
        )

    async def run_tests(self, job: DevJob, step: DevStep) -> str:
        """Прогнать тесты в песочнице и вернуть вывод. Ошибки НЕ глушим:
        недоступная изоляция обязана привести к FAIL, а не к тихому успеху."""
        s = await self.manager.create(self._spec(job, step), snap=self.snapshot)
        try:
            await self.manager.start(s)
            await self.manager.poll(s)
            log_path = Path(self.manager.workspace_root) / s.id / "out" / "stdout.log"
            out = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
            if s.error:
                out += f"\n[sandbox error] {s.error}"
            return out
        finally:
            await self.manager.destroy(s)

    async def edit(self, job: DevJob, step: DevStep) -> None:
        """Правка кода в изолированной копии.

        Если задан `editor` — зовём его; иначе НИЧЕГО не пишем. Это намеренно:
        пустой прогон не должен выдавать себя за работу. Доказательство даёт
        только шаг TEST, а пустой патч не пройдёт ревью.

        Редактор получает ТОЛЬКО путь к рабочей копии: прод-дерево ему не видно.
        Его ошибки не глушим — сбой правки обязан привести к отсутствию патча,
        а не к тихому «успеху».
        """
        if self.editor is None:
            return None
        await self.editor(job, step)
