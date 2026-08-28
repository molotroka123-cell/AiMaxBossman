"""Реестр селекторов с версионированием.

`62_SELECTOR_REGISTRY`: селекторов не должно быть россыпью по коду действий.
Здесь они собраны в версионированные пакеты, каждый пакет проверяется схемой
`selector_pack.schema.json`, и каждый может быть отключён отдельно после
поломки, не трогая остальные.

Главное правило пакета — **порядок стратегий**, и оно жёстче схемы (решение
G22 из `PRE_IMPLEMENTATION_AUDIT`):

* первой идёт семантическая стратегия — роль, доступное имя, видимая метка,
  стабильный атрибут; страница может перерисоваться, но кнопка «Опубликовать»
  останется кнопкой с этим именем;
* `css` и `xpath` — только в хвосте и только как последняя надежда;
* **для разрушающих действий `css` и `xpath` запрещены совсем.** Удаление,
  архивирование и смена профиля по хрупкому пути — это удаление не того
  объекта. Разрушающее действие, которое мы не умеем опознать семантически, не
  выполняется вообще.

Разрушающие действия дополнительно обязаны нести текст подтверждения и
постусловие: нажатие должно быть проверяемо задним числом, иначе «успешно
нажато» ничего не значит.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..domain.safety import SafetyClass, safety_of

# Стратегии, устойчивые к перерисовке. Порядок — предпочтительность.
SEMANTIC_KINDS: tuple[str, ...] = ("role", "accessible_name", "label", "stable_attribute")
# Стратегии последней надежды. Хрупкие по определению.
LAST_RESORT_KINDS: tuple[str, ...] = ("css", "xpath")
ALL_KINDS: frozenset[str] = frozenset(SEMANTIC_KINDS) | frozenset(LAST_RESORT_KINDS)

# Классы действий, для которых хрупкая стратегия запрещена независимо от того,
# что написано в пакете. Сюда попадает и SECURITY: поле пароля, найденное по
# `css`, — это пароль, введённый неизвестно куда.
BRITTLE_FORBIDDEN_CLASSES: frozenset[SafetyClass] = frozenset({
    SafetyClass.DESTRUCTIVE, SafetyClass.SECURITY, SafetyClass.MODERATION,
})

# Классы, где мало найти цель — нужно ещё, чтобы страница подтвердила, что
# происходит, и чтобы результат был проверяем. Ввод в поле сюда не входит:
# подтверждать нечего, пока ничего не отправлено.
CONFIRMATION_REQUIRED_CLASSES: frozenset[SafetyClass] = frozenset({
    SafetyClass.DESTRUCTIVE, SafetyClass.MODERATION,
})


class SelectorPackError(ValueError):
    """Пакет селекторов не соответствует схеме или нарушает правило порядка."""


@dataclass(frozen=True, slots=True)
class Strategy:
    """Один способ найти цель.

    `value` — не сырой селектор Playwright, а значение в грамматике вида:
    `role` → `"button|Опубликовать"` (роль и доступное имя через `|`);
    `accessible_name`/`label` → текст; `stable_attribute` → `"data-testid=share"`;
    `css`/`xpath` → сам селектор. Грамматика разбирается в `dom.py` одинаково
    и для настоящего браузера, и для фикстуры — иначе тесты на фикстуре ничего
    не доказывали бы о настоящем.
    """

    kind: str
    value: str

    def __post_init__(self) -> None:
        if self.kind not in ALL_KINDS:
            raise SelectorPackError(
                f"неизвестная стратегия {self.kind!r}; допустимы: "
                f"{sorted(ALL_KINDS)}")
        if not str(self.value).strip():
            raise SelectorPackError(f"стратегия {self.kind} без значения")

    @property
    def semantic(self) -> bool:
        return self.kind in SEMANTIC_KINDS

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value}


@dataclass(frozen=True, slots=True)
class SelectorAction:
    """Одно действие пакета: чем оно является и как найти его цель."""

    action: str
    target: str
    strategies: tuple[Strategy, ...]
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    # Текст, который обязан присутствовать в подтверждающем диалоге. Для
    # разрушающих действий обязателен.
    confirmation_text: str = ""
    # Возможность, к которой относится действие. По ней определяется класс
    # безопасности: сам пакет класс не назначает — иначе пакет данных мог бы
    # объявить удаление «обратимой записью».
    capability: str = ""
    verified_at: str = ""

    @property
    def safety(self) -> SafetyClass:
        return safety_of(self.capability or self.action)

    @property
    def brittle_forbidden(self) -> bool:
        """Хрупкие стратегии этому действию запрещены."""
        return self.safety in BRITTLE_FORBIDDEN_CLASSES

    @property
    def destructive(self) -> bool:
        """Нужны текст подтверждения на экране и проверяемое постусловие."""
        return self.safety in CONFIRMATION_REQUIRED_CLASSES

    @property
    def semantic_strategies(self) -> tuple[Strategy, ...]:
        return tuple(s for s in self.strategies if s.semantic)

    @property
    def last_resort_strategies(self) -> tuple[Strategy, ...]:
        return tuple(s for s in self.strategies if not s.semantic)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action": self.action, "target": self.target,
            "strategies": [s.to_dict() for s in self.strategies],
        }
        if self.preconditions:
            out["preconditions"] = list(self.preconditions)
        if self.postconditions:
            out["postconditions"] = list(self.postconditions)
        if self.confirmation_text:
            out["confirmation_text"] = self.confirmation_text
        if self.capability:
            out["capability"] = self.capability
        if self.verified_at:
            out["verified_at"] = self.verified_at
        return out


@dataclass(frozen=True, slots=True)
class SelectorPack:
    """Версионированный набор действий одного провайдера."""

    provider: str
    version: str
    ui_revision: str
    actions: dict[str, SelectorAction]
    locale: str = ""

    def get(self, action: str) -> SelectorAction | None:
        return self.actions.get(action)

    def require(self, action: str) -> SelectorAction:
        found = self.actions.get(action)
        if found is None:
            raise SelectorPackError(
                f"в пакете {self.provider}/{self.version} нет действия {action!r}; "
                f"есть: {sorted(self.actions)}")
        return found

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"provider": self.provider, "version": self.version,
                               "ui_revision": self.ui_revision,
                               "actions": [a.to_dict() for a in self.actions.values()]}
        if self.locale:
            out["locale"] = self.locale
        return out


# --------------------------------------------------------------- проверка схемой

_PACK_REQUIRED = ("provider", "version", "ui_revision", "actions")
_PACK_ALLOWED = frozenset({"provider", "version", "ui_revision", "locale", "actions"})
_ACTION_REQUIRED = ("action", "target", "strategies")
_STRATEGY_REQUIRED = ("kind", "value")


def _require_str(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SelectorPackError(f"{where}: поле {key!r} обязано быть непустой строкой")
    return value


def validate_pack_document(raw: dict[str, Any]) -> None:
    """Проверка по `selector_pack.schema.json`.

    Схема проверяется вручную, а не библиотекой: `jsonschema` нет в
    зависимостях, а тащить её ради одного документа означало бы поставить
    браузерный резерв в зависимость от установки, которой у приложения может не
    быть. Проверяются ровно те правила, что записаны в схеме, включая
    `additionalProperties: false` на уровне пакета.
    """
    if not isinstance(raw, dict):
        raise SelectorPackError("пакет селекторов должен быть объектом")
    for key in _PACK_REQUIRED:
        if key not in raw:
            raise SelectorPackError(f"в пакете нет обязательного поля {key!r}")
    unknown = set(raw) - _PACK_ALLOWED
    if unknown:
        raise SelectorPackError(f"неизвестные поля пакета: {sorted(unknown)}")
    for key in ("provider", "version", "ui_revision"):
        _require_str(raw, key, "пакет")
    if "locale" in raw and not isinstance(raw["locale"], str):
        raise SelectorPackError("locale должен быть строкой")
    actions = raw.get("actions")
    if not isinstance(actions, list) or not actions:
        raise SelectorPackError("actions должен быть непустым списком")
    for index, action in enumerate(actions):
        where = f"действие #{index}"
        if not isinstance(action, dict):
            raise SelectorPackError(f"{where}: должно быть объектом")
        for key in _ACTION_REQUIRED:
            if key not in action:
                raise SelectorPackError(f"{where}: нет обязательного поля {key!r}")
        _require_str(action, "action", where)
        _require_str(action, "target", where)
        strategies = action.get("strategies")
        if not isinstance(strategies, list) or not strategies:
            raise SelectorPackError(f"{where}: strategies должен быть непустым списком")
        for position, strategy in enumerate(strategies):
            spot = f"{where}, стратегия #{position}"
            if not isinstance(strategy, dict):
                raise SelectorPackError(f"{spot}: должна быть объектом")
            for key in _STRATEGY_REQUIRED:
                if key not in strategy:
                    raise SelectorPackError(f"{spot}: нет обязательного поля {key!r}")
            if strategy["kind"] not in ALL_KINDS:
                raise SelectorPackError(
                    f"{spot}: стратегия {strategy['kind']!r} вне перечня схемы")
            _require_str(strategy, "value", spot)
        for key in ("preconditions", "postconditions"):
            if key in action and not (
                    isinstance(action[key], list)
                    and all(isinstance(item, str) for item in action[key])):
                raise SelectorPackError(f"{where}: {key} должен быть списком строк")


def check_strategy_order(action: SelectorAction) -> None:
    """Правило порядка стратегий (G22). Жёстче схемы и намеренно.

    Схема разрешает `css` и `xpath` где угодно. Мы — нет: семантика первой,
    хрупкое в хвосте, а на разрушающем действии хрупкого нет вовсе.
    """
    if not action.strategies:
        raise SelectorPackError(f"{action.action}: нет ни одной стратегии")
    if not action.strategies[0].semantic:
        raise SelectorPackError(
            f"{action.action}: первой стратегией стоит {action.strategies[0].kind!r}; "
            f"первой обязана быть семантическая ({', '.join(SEMANTIC_KINDS)}) — "
            f"иначе действие ищет цель по разметке, а не по смыслу")
    seen_last_resort = False
    for strategy in action.strategies:
        if strategy.semantic and seen_last_resort:
            raise SelectorPackError(
                f"{action.action}: семантическая стратегия {strategy.kind!r} стоит "
                f"после хрупкой; хрупкие допустимы только в хвосте")
        seen_last_resort = seen_last_resort or not strategy.semantic
    if action.brittle_forbidden:
        brittle = action.last_resort_strategies
        if brittle:
            kinds = ", ".join(sorted({s.kind for s in brittle}))
            raise SelectorPackError(
                f"{action.action}: действие класса {action.safety.value} не может "
                f"использовать {kinds}. Хрупкий селектор на удалении — это "
                f"удаление не того объекта")
    if action.destructive:
        if not action.confirmation_text:
            raise SelectorPackError(
                f"{action.action}: разрушающее действие обязано нести текст "
                f"подтверждения (`confirmation_text`)")
        if not action.postconditions:
            raise SelectorPackError(
                f"{action.action}: разрушающее действие обязано нести постусловие — "
                f"без него «успешно нажато» ничего не означает")


def load_pack(raw: dict[str, Any]) -> SelectorPack:
    """Проверить документ схемой и правилом порядка и собрать пакет."""
    validate_pack_document(raw)
    actions: dict[str, SelectorAction] = {}
    for entry in raw["actions"]:
        action = SelectorAction(
            action=str(entry["action"]), target=str(entry["target"]),
            strategies=tuple(Strategy(kind=str(s["kind"]), value=str(s["value"]))
                             for s in entry["strategies"]),
            preconditions=tuple(str(p) for p in entry.get("preconditions") or ()),
            postconditions=tuple(str(p) for p in entry.get("postconditions") or ()),
            confirmation_text=str(entry.get("confirmation_text") or ""),
            capability=str(entry.get("capability") or ""),
            verified_at=str(entry.get("verified_at") or ""))
        if action.action in actions:
            raise SelectorPackError(f"действие {action.action} объявлено дважды")
        check_strategy_order(action)
        actions[action.action] = action
    return SelectorPack(provider=str(raw["provider"]), version=str(raw["version"]),
                        ui_revision=str(raw["ui_revision"]), actions=actions,
                        locale=str(raw.get("locale") or ""))


# --------------------------------------------------------------------- реестр

@dataclass(slots=True)
class SelectorRegistry:
    """Пакеты по провайдерам и версиям, с возможностью отключить один пакет.

    «A selector pack can be disabled independently after breakage» — отключение
    живёт здесь, а не в самом пакете: пакет — это описание чужого интерфейса, а
    отключение — наше решение о нём.
    """

    packs: dict[tuple[str, str], SelectorPack] = field(default_factory=dict)
    disabled: set[tuple[str, str]] = field(default_factory=set)
    active: dict[str, str] = field(default_factory=dict)

    def register(self, pack: SelectorPack, *, make_active: bool = True) -> SelectorPack:
        key = (pack.provider, pack.version)
        self.packs[key] = pack
        if make_active:
            self.active[pack.provider] = pack.version
        return pack

    def register_document(self, raw: dict[str, Any], *,
                          make_active: bool = True) -> SelectorPack:
        return self.register(load_pack(raw), make_active=make_active)

    def disable(self, provider: str, version: str, reason: str = "") -> None:
        self.disabled.add((provider, version))

    def enable(self, provider: str, version: str) -> None:
        self.disabled.discard((provider, version))

    def is_enabled(self, provider: str, version: str) -> bool:
        return (provider, version) in self.packs and (provider, version) not in self.disabled

    def versions(self, provider: str) -> list[str]:
        return sorted(v for p, v in self.packs if p == provider)

    def resolve(self, provider: str, version: str = "") -> SelectorPack:
        """Взять пакет. Отключённый пакет не отдаётся — это и есть отключение."""
        version = version or self.active.get(provider, "")
        if not version:
            raise SelectorPackError(f"для провайдера {provider} не выбран пакет селекторов")
        key = (provider, version)
        if key not in self.packs:
            raise SelectorPackError(
                f"пакет {provider}/{version} не зарегистрирован; "
                f"есть версии: {self.versions(provider)}")
        if key in self.disabled:
            raise SelectorPackError(
                f"пакет {provider}/{version} отключён после поломки интерфейса; "
                f"нужна новая версия пакета, а не повтор")
        return self.packs[key]

    def all_packs(self) -> Iterable[SelectorPack]:
        return list(self.packs.values())


__all__ = ["ALL_KINDS", "BRITTLE_FORBIDDEN_CLASSES", "CONFIRMATION_REQUIRED_CLASSES",
           "LAST_RESORT_KINDS", "SEMANTIC_KINDS", "SelectorAction", "SelectorPack",
           "SelectorPackError", "SelectorRegistry", "Strategy", "check_strategy_order",
           "load_pack", "validate_pack_document"]
