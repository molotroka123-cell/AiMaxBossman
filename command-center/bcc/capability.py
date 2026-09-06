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
    # Что владелец может СДЕЛАТЬ с этим отказом (см. REMEDIATION ниже). Пусто у
    # выданной способности; у отказа пусто быть не должно — отказ без выхода
    # это тупик, а не безопасность.
    remediation: list[dict[str, str]] = field(default_factory=list)

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
                     reason="способность не зарегистрирована в этом процессе",
                     missing=[UNREGISTERED], remediation=remediation_for([UNREGISTERED]))
    effect, why = decide_effect(spec, args, agent, policy_rules)
    policy_ok = effect != "deny"
    missing = runtime_missing(cap, platform=platform, probes=probes)
    runtime_ok = not missing
    reason = ""
    blockers: list[str] = []
    if not policy_ok:
        reason = f"политика: {why}"
        blockers.append(POLICY)
    elif not runtime_ok:
        reason = "рантайм не готов: " + ", ".join(missing)
    blockers.extend(missing)
    return Grant(capability_id=cap.capability_id, granted=policy_ok and runtime_ok,
                 capability_ok=True, policy_ok=policy_ok, runtime_ok=runtime_ok,
                 reason=reason, effect=str(effect), missing=missing,
                 remediation=[] if (policy_ok and runtime_ok) else remediation_for(blockers))


# --------------------------------------------------------------- remediation
#
# Отказ без выхода — это тупик того же рода, что и «манифест сказал да, а
# исполнителя нет»: владелец видит «нельзя» и не знает, что с этим делать.
# Ниже — закрытая таблица «чего не хватает → что это значит → что сделать».
# Ключи совпадают с элементами `Grant.missing` и с причинами отказа выдачи;
# выдумывать текст на неизвестную причину нельзя, поэтому для незнакомого
# ключа возвращается честное «причина известна, готового способа нет».

NO_EXECUTOR = "no_executor"
UNREGISTERED = "unregistered"
POLICY = "policy"

REMEDIATION: dict[str, dict[str, str]] = {
    "chromium": {
        "what": "Chromium не установлен или Playwright его не находит.",
        "how": "Установите браузер: `python -m playwright install chromium` "
               "(или укажите PLAYWRIGHT_BROWSERS_PATH на готовую сборку).",
    },
    "terminal_roots": {
        "what": "Ни один разрешённый корень терминала не существует на диске.",
        "how": "Задайте существующий каталог в настройке `terminal.roots` "
               "(Настройки → Терминал) — объявленный, но отсутствующий путь способности не даёт.",
    },
    "platform": {
        "what": "Способность не поддерживается операционной системой этой машины.",
        "how": "Выполните действие на поддерживаемой платформе — локальной заменой это не лечится.",
    },
    POLICY: {
        "what": "Политика владельца запрещает этот вызов (DENY).",
        "how": "Снимите или сузьте правило запрета для этого инструмента "
               "(Управление → Политики), либо выполните действие вручную.",
    },
    UNREGISTERED: {
        "what": "Инструмент не зарегистрирован в этом процессе.",
        "how": "Включите модуль-исполнитель этой способности и перезапустите сервер; "
               "пока его нет, задача с таким шагом будет честно заблокирована, а не «выполнена».",
    },
    NO_EXECUTOR: {
        "what": "Для этой способности в сборке нет НИ ОДНОГО исполнителя, "
                "которого может вызвать модель.",
        "how": "Сделайте это действие сами через соответствующую страницу интерфейса "
               "или дождитесь появления инструмента: повтор задачи исполнителя не создаёт.",
    },
}

UNKNOWN_REMEDIATION = {
    "what": "Причина отказа известна, готового способа устранения для неё не записано.",
    "how": "См. причину отказа целиком и решите вручную; выдумывать шаг здесь нельзя.",
}


def remediation_for(missing: list[str] | tuple[str, ...]) -> list[dict[str, str]]:
    """`Grant.missing`/причины → список «что не так и что с этим делать».

    `platform:linux` приводится к ключу `platform`; неизвестный ключ отдаёт
    честную заглушку, а не молчание и не придуманный совет.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in missing or ():
        key = str(item).split(":", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        entry = REMEDIATION.get(key, UNKNOWN_REMEDIATION)
        out.append({"missing": str(item), **entry})
    return out


def blocked(capability_id: str, *, reason_key: str, detail: str = "") -> Grant:
    """Честный отказ там, где нет самого исполнителя (или он не зарегистрирован).

    Это НЕ «выполнено с оговоркой» и НЕ пустой успех: `granted=False`,
    `capability_ok=False`, причина названа, remediation приложена. Используется
    там, где `CapabilitySpec` построить не из чего — способность в сборке
    отсутствует как таковая.
    """
    entry = REMEDIATION.get(reason_key, UNKNOWN_REMEDIATION)
    reason = detail or entry["what"]
    return Grant(capability_id=capability_id, granted=False, capability_ok=False,
                 policy_ok=False, runtime_ok=False, reason=reason, effect="deny",
                 missing=[reason_key], remediation=remediation_for([reason_key]))


def surface(registry, *, probes: dict[str, bool] | None = None, agent: dict | None = None,
            policy_rules: list[dict] | None = None, platform: str | None = None) -> list[dict[str, Any]]:
    """Читаемая владельцем поверхность способностей этой сборки.

    На каждую способность: чем она исполняется (`executor` — источник
    ToolSpec, то есть конкретное семейство-исполнитель), чем доказывается её
    эффект, выдана ли она сейчас, и если нет — почему и что с этим делать.
    Источник один — живой реестр инструментов, поэтому список не может
    разойтись с тем, что реально исполняется.
    """
    items: list[dict[str, Any]] = []
    for cap in manifest(registry):
        spec = registry.get(cap.tool)
        g = grant(cap, spec=spec, args={}, agent=dict(agent or {"permissions": []}),
                  policy_rules=policy_rules, platform=platform, probes=probes)
        items.append({**cap.to_dict(), "executor": cap.source, "grant": g.to_dict()})
    return items
