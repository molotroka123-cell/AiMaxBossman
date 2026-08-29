"""Stage 8 — адаптеры рантаймов песочницы.

Каждый адаптер реализует `sandbox.runtime.SandboxRuntime` и ЧЕСТНО объявляет
свои возможности в `capabilities()`. Честность обязательна: политика по ним
принимает fail-closed решение — рантайм, не умеющий нужный tier, приводит к
`IsolationUnavailable`, а не к тихому даунгрейду.
"""
from __future__ import annotations

from .safe import SafeRuntime, safe_runtime_available
from .strong import (
    GvisorRuntime,
    MicroVMRuntime,
    gvisor_available,
    kvm_available,
)

__all__ = ["SafeRuntime", "safe_runtime_available", "GvisorRuntime", "MicroVMRuntime",
           "gvisor_available", "kvm_available"]
