"""Пакет для починки адаптера после смены интерфейса — и что в него НЕ кладут.

Когда провайдер перерисовал страницу, чинить надо селекторы. Чинит их
кодирующий работник, и работает он в изолированной копии репозитория — то есть
за пределами машины владельца. Всё, что попадёт в этот пакет, окажется там.

Поэтому пакет собирается вычитанием, а не добавлением. Берётся снимок страницы,
уже прошедший редактор сессии, и из него остаётся ровно то, чем ищут цель:
роль, доступное имя, метка, тег, признак «выключено». Не остаётся ничего
другого:

* **разметки нет.** В ней живут cookie в скрытых полях, токены форм и чужая
  переписка. Ради починки селектора разметка не нужна: цель ищут по смыслу;
* **значений полей нет.** Ни обычных, ни тем более секретных;
* **ссылок на элементы нет.** Они привязаны к поколению снимка и вне его
  бессмысленны, зато выглядят как что-то важное;
* **строки запроса в адресе нет.** Именно туда формы с `method=GET` уносят
  пароли и одноразовые коды.

Сверху стоит проверка на явные следы секретов. Она не доказывает, что их нет —
такое доказать нельзя, — но пакет, в котором нашлось похожее на ключ, наружу не
уходит вообще, а не уходит «после очистки».
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..browser.selectors import SelectorPackError
from ..browser.session import AccountBrowserSession
from .higgsfield_selectors import PACK_VERSION, PROVIDER

# Сколько элементов поверхности берём. Больше — это уже страница целиком.
SURFACE_LIMIT = 40
# Длина имени, за которой начинается не имя, а содержимое.
NAME_LIMIT = 120

# Следы, при которых пакет не отправляется. Список заведомо неполный, и в этом
# нет противоречия: он ловит появление секрета, а не доказывает его отсутствие.
_SECRET_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "ключ провайдера"),
    (re.compile(r"(?i)\bghp_[A-Za-z0-9]{20,}"), "токен GitHub"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}"), "заголовок авторизации"),
    (re.compile(r"(?i)\b(?:session|csrf|auth)[_-]?(?:id|token)\s*[=:]\s*\S{8,}"),
     "идентификатор сессии"),
    (re.compile(r"(?i)\bvault://\S+"), "ссылка на хранилище секретов"),
    (re.compile(r"(?i)\bpassword\s*[=:]\s*\S+"), "пароль"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "JWT"),
)


class UnsafeDriftReport(RuntimeError):
    """В пакете нашлось похожее на секрет. Наружу он не уходит."""

    def __init__(self, what: str) -> None:
        super().__init__(
            f"пакет о смене интерфейса содержит {what}; он не отправляется "
            f"кодирующему работнику ни в каком виде")
        self.what = what


def scan_for_secrets(text: str) -> str:
    for pattern, what in _SECRET_SHAPES:
        if pattern.search(text or ""):
            return what
    return ""


@dataclass(frozen=True, slots=True)
class SurfaceElement:
    """Элемент так, как его видит поиск цели. Больше о нём знать не нужно."""

    tag: str
    role: str
    name: str
    label: str
    disabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"tag": self.tag, "role": self.role, "name": self.name,
                "label": self.label, "disabled": self.disabled}


@dataclass(frozen=True, slots=True)
class UiDriftReport:
    """Что известно о сменившемся интерфейсе. И ничего сверх этого."""

    provider: str
    adapter_version: str
    selector_pack_version: str
    failing_action: str
    expected_strategies: tuple[tuple[str, str], ...]
    session_state: str
    url: str
    surface: tuple[SurfaceElement, ...]
    observed_at_epoch_s: float
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "social_farm.ui_drift.v1",
                "provider": self.provider,
                "adapter_version": self.adapter_version,
                "selector_pack_version": self.selector_pack_version,
                "failing_action": self.failing_action,
                "expected_strategies": [{"kind": kind, "value": value}
                                        for kind, value in self.expected_strategies],
                "session_state": self.session_state,
                "url": self.url,
                "surface": [element.to_dict() for element in self.surface],
                "observed_at_epoch_s": self.observed_at_epoch_s,
                "detail": self.detail}

    def fixture_stub(self) -> str:
        """Заготовка детерминированной страницы под наблюдаемый интерфейс.

        Кодирующий работник обязан сначала воспроизвести отказ, и воспроизводить
        его надо на странице, а не на словах. Здесь только роли и имена —
        того же, чем ищут цель, достаточно, чтобы собрать фикстуру.
        """
        lines = ["# автосборка из наблюдения; проверьте и допишите руками",
                 "def drifted_page_elements():", "    return ["]
        for element in self.surface:
            lines.append(
                f"        FixtureElement(tag={element.tag!r}, "
                f"role={element.role!r}, accessible_name={element.name!r}, "
                f"label={element.label!r}, disabled={element.disabled!r}),")
        lines.append("    ]")
        return "\n".join(lines)


def _clean_url(url: str) -> str:
    """Адрес без строки запроса: именно туда `method=GET` уносит пароли."""
    return str(url or "").split("?", 1)[0].split("#", 1)[0]


def _element(raw: Mapping[str, Any]) -> SurfaceElement:
    return SurfaceElement(
        tag=str(raw.get("tag") or "")[:40],
        role=str(raw.get("role") or "")[:40],
        name=str(raw.get("accessible_name") or "")[:NAME_LIMIT],
        label=str(raw.get("label") or "")[:NAME_LIMIT],
        disabled=bool(raw.get("disabled")))


async def build_drift_report(session: AccountBrowserSession, *,
                             failing_action: str, adapter_version: str = "",
                             detail: str = "", limit: int = SURFACE_LIMIT,
                             now: float | None = None) -> UiDriftReport:
    """Собрать пакет из снимка страницы. Вычитанием, а не добавлением."""
    snapshot = await session.snapshot()
    try:
        action = session.pack().get(failing_action)
    except SelectorPackError:
        # Пакет уже отключён после поломки — а пакет отключают ровно тогда,
        # когда этот отчёт и нужен. Падать здесь означало бы: чем хуже дела,
        # тем меньше о них известно.
        action = None
    expected = tuple((strategy.kind, strategy.value)
                     for strategy in (action.strategies if action else ()))

    report = UiDriftReport(
        provider=session.provider,
        adapter_version=(adapter_version
                         or f"{PROVIDER}/{session.pack_version or PACK_VERSION}"),
        selector_pack_version=session.pack_version,
        failing_action=failing_action,
        expected_strategies=expected,
        session_state=snapshot.state.value,
        url=_clean_url(snapshot.url),
        surface=tuple(_element(raw) for raw in snapshot.elements[:max(1, limit)]),
        observed_at_epoch_s=time.time() if now is None else now,
        detail=session.redactor.text(detail)[:400])

    found = scan_for_secrets(_flatten(report.to_dict()))
    if found:
        raise UnsafeDriftReport(found)
    return report


def _flatten(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = ["NAME_LIMIT", "SURFACE_LIMIT", "SurfaceElement", "UiDriftReport",
           "UnsafeDriftReport", "build_drift_report", "scan_for_secrets"]
