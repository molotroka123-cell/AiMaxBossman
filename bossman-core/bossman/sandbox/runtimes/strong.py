"""Stage 8 — адаптеры сильной изоляции: gVisor-класс (CONTAINER) и MicroVM.

Здесь ЧЕСТНЫЕ адаптеры, а не заглушки, притворяющиеся рабочими. Ключевое
свойство: `capabilities()` определяется РЕАЛЬНЫМ наличием бинаря/устройства на
хосте. Если runsc не установлен или /dev/kvm недоступен, адаптер объявляет
пустой набор tiers — и `PolicyEngine.resolve` сам отдаёт `IsolationUnavailable`.
Это и есть fail closed: HOSTILE-задача никогда не «съедет» на слабый рантайм.

Исполнение делегируется SafeRuntime-механике (копия рабочей области, argv без
шелла, rlimits, снос), но команда оборачивается в соответствующий изолятор.
Полная интеграция конкретного гипервизора (CubeSandbox и т.п.) — отдельный шаг;
см. _staging/s8/stage8/RUNTIME_SELECTION.md. Контракт готов уже сейчас.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..models import IsolationTier, NetworkMode, RuntimeCapabilities, SandboxSession
from .safe import SafeRuntime


def gvisor_available() -> bool:
    """gVisor (runsc) реально установлен?"""
    return shutil.which("runsc") is not None


def kvm_available() -> bool:
    """Аппаратная виртуализация доступна?"""
    return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)


class GvisorRuntime(SafeRuntime):
    """CONTAINER-tier через gVisor. Без runsc — tiers пуст (fail closed)."""

    name = "gvisor"

    def capabilities(self) -> RuntimeCapabilities:
        tiers = frozenset({IsolationTier.ROOTLESS, IsolationTier.CONTAINER}) if gvisor_available() else frozenset()
        return RuntimeCapabilities(
            name=self.name,
            tiers=tiers,
            supports_offline=gvisor_available(),   # сеть отключается флагом изолятора
            supports_allowlist=False,              # egress-фильтра по хостам ещё нет
            supports_readonly_root=gvisor_available(),
            supports_seccomp=gvisor_available(),
            supports_pid_limit=True,
            supports_mem_limit=True,
        )

    def _argv(self, session: SandboxSession) -> list[str]:
        inner = super()._argv(session)
        # super() уже мог добавить unshare для OFFLINE — под runsc это не нужно:
        # берём исходную команду из labels и оборачиваем сами.
        raw = session.spec.labels.get("argv")
        cmd = [str(a) for a in raw] if isinstance(raw, (list, tuple)) and raw else inner
        policy = session.policy
        offline = policy is None or policy.network_mode == NetworkMode.OFFLINE
        wrapper = ["runsc", "run", "--network", "none" if offline else "host", "--"]
        return wrapper + cmd


class MicroVMRuntime(SafeRuntime):
    """MICROVM-tier (HOSTILE lab). Без /dev/kvm — tiers пуст (fail closed),
    поэтому HOSTILE-задача будет отвергнута, а не запущена в контейнере."""

    name = "microvm"

    def __init__(self, *, workspace_root: str | Path | None = None,
                 launcher: str = "cube") -> None:
        super().__init__(workspace_root=workspace_root)
        self.launcher = launcher

    def _launcher_available(self) -> bool:
        return shutil.which(self.launcher) is not None

    def capabilities(self) -> RuntimeCapabilities:
        ok = kvm_available() and self._launcher_available()
        tiers = frozenset({IsolationTier.ROOTLESS, IsolationTier.CONTAINER,
                           IsolationTier.MICROVM}) if ok else frozenset()
        return RuntimeCapabilities(
            name=self.name,
            tiers=tiers,
            supports_offline=ok,
            supports_allowlist=False,
            supports_readonly_root=ok,
            supports_seccomp=ok,
            supports_pid_limit=True,
            supports_mem_limit=True,
        )

    def _argv(self, session: SandboxSession) -> list[str]:
        raw = session.spec.labels.get("argv")
        cmd = [str(a) for a in raw] if isinstance(raw, (list, tuple)) and raw else list(super()._argv(session))
        policy = session.policy
        offline = policy is None or policy.network_mode == NetworkMode.OFFLINE
        wrapper = [self.launcher, "run", "--net", "none" if offline else "nat", "--"]
        return wrapper + cmd
