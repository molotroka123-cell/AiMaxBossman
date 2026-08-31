"""Capability registry для Computer Operator — ЧЕСТНОЕ раскрытие возможностей.

Канон: `computer.<domain>.<verb>`. Реестр НЕ выдумывает возможности: capability
считается доступной, только если (а) она отображается на типизированное
действие и (б) реально существующий backend подтверждает поддержку на этом
хосте. Иначе — `supported=False` с причиной (нет backend / не та ОС / нет
зависимости). Планировщику отдаём только поддержанное — модель не может
запросить то, чего на хосте нет.

Никакого дублирования: реестр не исполняет действия и не решает политику; он
лишь описывает поверхность и опрашивает уже существующие адаптеры/ActionRouter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .models import ActionKind, ComputerAction, ExpectedState

# Канонические имена → типизированное действие. None = наблюдение/запрос,
# исполняется не через ActionRouter (см. observe-путь), но всё равно объявляется.
CAPABILITY_ACTIONS: dict[str, ActionKind | None] = {
    "computer.mouse.click":          ActionKind.CLICK,
    "computer.mouse.double_click":   ActionKind.DOUBLE_CLICK,
    "computer.mouse.drag":           ActionKind.DRAG,
    "computer.mouse.scroll":         ActionKind.SCROLL,
    "computer.keyboard.type":        ActionKind.TYPE,
    "computer.keyboard.hotkey":      ActionKind.HOTKEY,
    "computer.window.focus":         ActionKind.FOCUS,
    "computer.ui.invoke":            ActionKind.UI_INVOKE,
    "computer.application.launch":   ActionKind.APP_LAUNCH,
    "computer.application.close":    ActionKind.APP_CLOSE,
    "computer.browser.control":      ActionKind.BROWSER,
    "computer.screen.observe":       ActionKind.TAKE_SCREENSHOT,
    "computer.wait":                 ActionKind.WAIT,
}

# Домены, которые обслуживает наблюдатель (structured/screenshot), а не router.
OBSERVATION_CAPABILITIES = ("computer.window.list", "computer.screen.observe",
                            "computer.accessibility.tree")


@dataclass(frozen=True)
class Capability:
    name: str
    action: ActionKind | None
    supported: bool
    backend: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "action": self.action.value if self.action else None,
                "supported": self.supported, "backend": self.backend, "reason": self.reason}


class CapabilityRegistry:
    """Опрашивает РЕАЛЬНЫЕ backends; ничего не утверждает без подтверждения."""

    def __init__(self, backends: Iterable[Any]) -> None:
        self.backends = list(backends)

    async def probe(self, observation: Any = None) -> list[Capability]:
        """Спросить каждый backend, поддерживает ли он пробное действие."""
        out: list[Capability] = []
        for name, kind in CAPABILITY_ACTIONS.items():
            if kind is None:
                out.append(Capability(name, None, False, reason="not a typed action"))
                continue
            probe_action = _probe_action(kind)
            backend = await self._first_supporting(probe_action, observation)
            if backend:
                out.append(Capability(name, kind, True, backend=backend))
            else:
                out.append(Capability(name, kind, False,
                                      reason="no backend on this host supports this action"))
        return out

    async def _first_supporting(self, action: ComputerAction, observation: Any) -> str:
        for b in self.backends:
            try:
                if await b.supports(action, observation):
                    return getattr(b, "name", b.__class__.__name__)
            except Exception:
                continue          # сломанный backend != поддержка
        return ""

    async def supported_names(self, observation: Any = None) -> list[str]:
        return [c.name for c in await self.probe(observation) if c.supported]

    async def is_supported(self, name: str, observation: Any = None) -> bool:
        if name not in CAPABILITY_ACTIONS:
            return False          # неизвестная capability → deny-by-default
        return any(c.supported for c in await self.probe(observation) if c.name == name)


def _probe_target(kind: ActionKind) -> str:
    """Цель пробы, осмысленная для КОНКРЕТНОГО вида действия.

    Некоторые backends сужают `supports()` по цели (APP_LAUNCH принимает только
    allowlisted-приложение). Проба нейтральной строкой давала бы ЛОЖНОЕ
    «не поддерживается» — то есть враньё о возможностях в другую сторону.
    """
    if kind is ActionKind.APP_LAUNCH:
        from .applist import APP_ALLOWLIST
        return next(iter(APP_ALLOWLIST), "__probe__")   # реальное allowlisted-имя
    return "__probe__"


def _probe_action(kind: ActionKind) -> ComputerAction:
    """Безопасное пробное действие: НЕ исполняется, только для supports().

    source="planner" намеренно: vision-адаптер принимает лишь source="vision",
    поэтому проба не «проходит» через пиксельный fallback и отражает наличие
    СТРУКТУРНОГО backend'а.
    """
    return ComputerAction.make(kind, expected=ExpectedState(),
                               target=_probe_target(kind), text="", source="planner")
