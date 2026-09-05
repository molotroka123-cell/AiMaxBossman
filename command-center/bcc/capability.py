"""CapabilitySpec — что система УМЕЕТ и чем это доказывается (TRUTH-003 §17).

Узкий адаптер над `ToolSpec`, а не второй реестр: список способностей выводится
из зарегистрированных инструментов, поэтому не может разойтись с тем, что
реально исполняется. Отвечает на вопрос владельца «эта способность у меня
есть?» честно — вместе с тем, ЧЕМ её результат будет доказан.

Правило выдачи (все три условия обязательны, любое «нет» — отказ):

    capability ∧ policy ∧ runtime

  capability — инструмент зарегистрирован в этом процессе;
  policy     — `decide_effect` для этого агента/аргументов не даёт DENY;
  runtime    — платформа и внешние предпосылки на месте (Chromium для браузера,
               разрешённые корни для терминала и т.п.).

`verification_strategy` — вид пост-состояния из `bcc.v2.verification.KINDS`,
которым исход подтверждается независимым чтением. `none` значит: доказать
эффект нечем, и такая способность НЕ может закрыть шаг с side effect'ом
(INV: TOOL_CALLED ≠ SIDE_EFFECT_VERIFIED).
"""
from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from .tools import ToolSpec, decide_effect
from .v2.verification import KINDS

ALL_PLATFORMS = ("linux", "darwin", "win32")

# Вид доказательства по источнику/имени инструмента. Ключ — префикс имени;
# значение — вид из KINDS либо "" (доказать нечем).
_VERIFICATION: tuple[tuple[str, str], ...] = (
    ("terminal.", "terminal"),
    ("fs.", "file"),
    ("files.", "file"),
    ("code.", "file"),
    ("browser.", "browser"),
    ("apps.", "app"),
    ("memory.", "memory"),
    ("facts.", "memory"),
    ("schedule.", "schedule"),
    ("github.", "github"),
    ("git.", "github"),
)
# Способности, ограниченные платформой: значение — кортеж поддерживаемых.
_PLATFORMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("apps.", ("linux", "darwin", "win32")),
    ("terminal.", ("linux", "darwin", "win32")),
)


def _platform() -> str:
    return sys.platform if sys.platform in ALL_PLATFORMS else sys.platform


@dataclass(frozen=True)
class CapabilitySpec:
    """Способность = инструмент + чем доказывается + при каких условиях выдаётся."""

    capability_id: str
    tool: str
    description: str
    effect_class: str                     # read | write | exec | send | admin
    verification_strategy: str            # вид пост-состояния или "" — доказать нечем
    idempotency: str                      # idempotent | non_idempotent
    approval_requirement: str             # auto | ask | deny (default_effect инструмента)
    permission: str                       # право из bcc.permissions ("" — не требует)
    privacy_requirement: str              # local_only | any
    supported_platforms: tuple[str, ...] = ALL_PLATFORMS
    source: str = "builtin"
    generation: int = 0

    @property
    def side_effect(self) -> bool:
        return self.effect_class in ("write", "exec", "send", "admin")

    @property
    def provable(self) -> bool:
        """Эффект можно подтвердить независимым чтением пост-состояния."""
        return self.verification_strategy in KINDS

    def platform_supported(self, platform: str | None = None) -> bool:
        return (platform or _platform()) in self.supported_platforms

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["supported_platforms"] = list(self.supported_platforms)
        d["side_effect"] = self.side_effect
        d["provable"] = self.provable
        return d


@dataclass
class Grant:
    """Решение о выдаче способности: три независимых условия и причина отказа."""

    capability_id: str
    granted: bool
    capability_ok: bool
    policy_ok: bool
    runtime_ok: bool
    reason: str = ""
    effect: str = ""
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def from_tool(spec: ToolSpec) -> CapabilitySpec:
    """Вывести способность из зарегистрированного инструмента (без догадок)."""
    kind = ""
    for prefix, k in _VERIFICATION:
        if spec.name.startswith(prefix):
            kind = k
            break
    platforms = ALL_PLATFORMS
    for prefix, plats in _PLATFORMS:
        if spec.name.startswith(prefix):
            platforms = plats
            break
    return CapabilitySpec(
        capability_id=spec.name,
        tool=spec.name,
        description=spec.description,
        effect_class=spec.category,
        verification_strategy=kind,
        idempotency="idempotent" if spec.idempotent else "non_idempotent",
        approval_requirement=str(spec.default_effect),
        permission=spec.permission,
        # Локальные источники не выходят за пределы машины; всё прочее может.
        privacy_requirement="local_only" if spec.source in ("terminal", "memory", "builtin") else "any",
        supported_platforms=platforms,
        source=spec.source,
        generation=spec.generation,
    )


def manifest(registry) -> list[CapabilitySpec]:
    return [from_tool(s) for s in sorted(registry.all(), key=lambda s: s.name)]


def runtime_missing(cap: CapabilitySpec, *, platform: str | None = None,
                    probes: dict[str, bool] | None = None) -> list[str]:
    """Чего не хватает рантайму. Пустой список = предпосылки на месте.

    `probes` — измеренные факты окружения (например {"chromium": False}).
    Неизвестная предпосылка НЕ считается выполненной: её отсутствие в probes
    для способности, которая её требует, — тоже причина отказа.
    """
    missing: list[str] = []
    if not cap.platform_supported(platform):
        missing.append(f"platform:{platform or _platform()}")
    p = probes or {}
    if cap.capability_id.startswith("browser.") and not p.get("chromium", False):
        missing.append("chromium")
    if cap.capability_id.startswith("terminal.") and not p.get("terminal_roots", False):
        missing.append("terminal_roots")
    return missing


def grant(cap: CapabilitySpec | None, *, spec: ToolSpec | None, args: dict, agent: dict,
          policy_rules: list[dict] | None = None, platform: str | None = None,
          probes: dict[str, bool] | None = None) -> Grant:
    """capability ∧ policy ∧ runtime. Fail-closed: любое «нет» — отказ."""
    if cap is None or spec is None:
        return Grant(capability_id=getattr(cap, "capability_id", ""), granted=False,
                     capability_ok=False, policy_ok=False, runtime_ok=False,
                     reason="способность не зарегистрирована в этом процессе")
    effect, why = decide_effect(spec, args, agent, policy_rules)
    policy_ok = effect != "deny"
    missing = runtime_missing(cap, platform=platform, probes=probes)
    runtime_ok = not missing
    reason = ""
    if not policy_ok:
        reason = f"политика: {why}"
    elif not runtime_ok:
        reason = "рантайм не готов: " + ", ".join(missing)
    return Grant(capability_id=cap.capability_id, granted=policy_ok and runtime_ok,
                 capability_ok=True, policy_ok=policy_ok, runtime_ok=runtime_ok,
                 reason=reason, effect=str(effect), missing=missing)
