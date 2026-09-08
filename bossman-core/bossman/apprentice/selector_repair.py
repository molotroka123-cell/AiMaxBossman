"""Починка селекторов силами кодирующего работника — и границы этой починки.

Когда браузерный адаптер сообщает `UI_CHANGED`, чинить надо селекторы, а не
работу. Чинит их OpenHands в изолированной копии репозитория. Этот модуль
решает две вещи, и обе про границу, а не про сам ремонт.

**Что ему дают.** Только пакет наблюдения, собранный вычитанием на стороне
Social Farm (`generation/drift_report.py`): роли, доступные имена, метки. Ни
разметки, ни значений полей, ни cookie, ни строки запроса. Здесь пакет
проверяется ещё раз — не потому, что первой проверке не доверяют, а потому что
источник пакета может смениться, а граница остаться должна.

**Что ему разрешают тронуть.** Пакет селекторов, адаптер и тесты. Всё, что
делает браузерный путь безопасным, — распознавание проверок человека,
изоляция аккаунтов, редакция секретов, реестр возможностей, каталог классов
безопасности и статический скан независимости — лежит в защищённых путях. Не
потому что кодирующий работник злонамерен, а потому что «починить» отказ
проще всего, отключив то, что отказывает. Возможность так починить у него
отсутствует, а не осуждается.

Права работник не получает никаких: коммит, ветку, удалённый репозиторий и
завершение миссии `openhands_client` запрещает механически, и этот модуль
ничего из этого не ослабляет.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .openhands_client import OpenHandsError, OpenHandsRequest, OpenHandsResult

SCHEMA = "social_farm.ui_drift.v1"
_FARM = "apps/social-farm"

# Что чинится. Селекторы — данные об интерфейсе; адаптер — код, который их
# читает; тесты — то, чем ремонт доказывается.
ALLOWED_PATHS: tuple[str, ...] = (
    f"{_FARM}/src/social_farm/generation/higgsfield_selectors.py",
    f"{_FARM}/src/social_farm/generation/higgsfield_adapter.py",
    f"{_FARM}/tests/unit/",
)

# Что не чинится никогда. Отказ проще всего «починить», отключив то, что
# отказывает, — поэтому такой возможности здесь нет.
PROTECTED_PATHS: tuple[str, ...] = (
    f"{_FARM}/src/social_farm/browser/challenge.py",
    f"{_FARM}/src/social_farm/browser/session.py",
    f"{_FARM}/src/social_farm/browser/isolation.py",
    f"{_FARM}/src/social_farm/browser/secrets.py",
    f"{_FARM}/src/social_farm/browser/capabilities.py",
    f"{_FARM}/src/social_farm/browser/states.py",
    f"{_FARM}/src/social_farm/domain/safety.py",
    f"{_FARM}/tests/unit/test_independence.py",
    f"{_FARM}/tests/unit/test_browser_session.py",
    ".github/",
    "bossman-core/",
    "command-center/",
)

# Действия, ради которых починка вообще запускается. Вход в аккаунт и ввод
# секретов сюда не входят: там чинить нечего, там всё делает человек.
REPAIRABLE_ACTIONS: frozenset[str] = frozenset({
    "generation.prompt.fill", "generation.aspect.fill", "generation.duration.fill",
    "generation.preset.fill", "generation.submit", "generation.job_card",
    "generation.result.download", "account.identity.read",
})

_SECRET_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "provider key"),
    (re.compile(r"(?i)\bghp_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}"), "authorization header"),
    (re.compile(r"(?i)\b(?:session|csrf|auth)[_-]?(?:id|token)\s*[=:]\s*\S{8,}"),
     "session identifier"),
    (re.compile(r"(?i)\bvault://\S+"), "secret reference"),
    (re.compile(r"(?i)\bpassword\s*[=:]\s*\S+"), "password"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "JWT"),
    (re.compile(r"(?i)\bcookie\s*[=:]"), "cookie"),
)

# Признаки «починки», которая на самом деле выключает проверку. Ищутся в диффе,
# а не в намерениях: намерения в дифф не попадают.
_INADMISSIBLE_DIFF: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\+.*detect_challenge\s*\(.*\)\s*(?:#.*)?$", re.M),
     "переопределение распознавания проверок человека"),
    (re.compile(r"^\+\s*except\s+(?:Exception|BaseException)\s*:\s*(?:#.*)?$", re.M),
     "перехват всех исключений вместо разбора отказа"),
    (re.compile(r"^\+\s*(?:pass|return\s+True)\s*(?:#.*)?$\n?(?=^\+?\s*$)?", re.M),
     ""),  # проверяется отдельно ниже, слишком общий образец
    (re.compile(r"^\+.*@pytest\.mark\.skip", re.M), "отключение теста"),
    (re.compile(r"^\+.*pytest\.skip\(", re.M), "отключение теста"),
    (re.compile(r"^-\s*assert\s", re.M), "удаление проверки из теста"),
    (re.compile(r"^\+.*(?:page\.mouse|click_at|coordinates?\s*=)", re.M),
     "нажатие по координатам вместо семантической цели"),
)


class RepairRefused(OpenHandsError):
    """Миссию починки нельзя составить или её результат нельзя принять."""


def _flatten(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def scan_packet(report: Mapping[str, Any]) -> str:
    """Найти в пакете следы секретов. Пустая строка — ничего не найдено."""
    text = _flatten(report)
    for pattern, what in _SECRET_SHAPES:
        if pattern.search(text):
            return what
    return ""


def _surface_lines(report: Mapping[str, Any], limit: int = 40) -> str:
    rows = report.get("surface") or []
    if not isinstance(rows, list):
        return "(поверхность не описана)"
    out = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        out.append(f"  - tag={row.get('tag','')!r} role={row.get('role','')!r} "
                   f"name={row.get('name','')!r} label={row.get('label','')!r}"
                   + (" disabled" if row.get("disabled") else ""))
    return "\n".join(out) or "  (на поверхности не нашлось ни одного элемента)"


def build_instruction(report: Mapping[str, Any]) -> str:
    """Текст миссии. Сначала воспроизвести отказ, потом чинить."""
    expected = report.get("expected_strategies") or []
    strategies = "\n".join(
        f"  - {item.get('kind','')}: {item.get('value','')!r}"
        for item in expected if isinstance(item, dict)) or "  (в пакете не объявлены)"
    return f"""Интерфейс провайдера {report.get('provider','')} изменился, и действие
{report.get('failing_action','')!r} больше не находит цель.

Версия пакета селекторов: {report.get('selector_pack_version','')}
Версия адаптера: {report.get('adapter_version','')}
Состояние сессии в момент отказа: {report.get('session_state','')}
Адрес (без строки запроса): {report.get('url','')}
Подробность: {report.get('detail','') or '(нет)'}

Стратегии, которыми цель искали и не нашли:
{strategies}

Что видно на поверхности сейчас (роли и доступные имена, больше ничего):
{_surface_lines(report)}

Порядок работы, и он не переставляется:

1. СНАЧАЛА воспроизведите отказ детерминированной страницей в
   apps/social-farm/tests/unit/ — тестом, который падает на текущем коде.
   Починка, не начавшаяся с воспроизведения, чинит предположение.
2. Затем поправьте пакет селекторов. Первой стратегией обязана стоять
   семантическая: роль, доступное имя, метка или устойчивый атрибут.
   `css`/`xpath` допустимы только в хвосте.
3. Прогоните tests/unit/ целиком.
4. Верните дифф.

Чего делать нельзя, и это не пожелания:

- не трогать распознавание проверок человека, изоляцию аккаунтов, редакцию
  секретов, реестр возможностей и каталог классов безопасности: они в
  защищённых путях, и попытка будет отвергнута механически;
- не «чинить» отказ перехватом всех исключений, нажатием по координатам,
  отключением или удалением тестов, ослаблением проверок;
- не коммитить, не создавать веток, не добавлять удалённых репозиториев:
  песочница это запрещает, и обход её означает отвергнутый результат;
- завершённой миссию объявляете не вы. Ваш результат — дифф и доказательства.
"""


def build_repair_mission(report: Mapping[str, Any], *, workspace: str | Path,
                         model: str | None = None,
                         timeout_seconds: int = 900) -> OpenHandsRequest:
    """Составить миссию починки из пакета наблюдения."""
    if report.get("schema") != SCHEMA:
        raise RepairRefused(
            f"пакет о смене интерфейса должен быть {SCHEMA}, получено "
            f"{report.get('schema')!r}")
    action = str(report.get("failing_action") or "")
    if action not in REPAIRABLE_ACTIONS:
        raise RepairRefused(
            f"действие {action!r} не чинится кодирующим работником; починке "
            f"подлежат только {sorted(REPAIRABLE_ACTIONS)}")
    found = scan_packet(report)
    if found:
        raise RepairRefused(
            f"пакет содержит {found}; такой пакет не уходит в изолированную "
            f"копию ни в каком виде")
    return OpenHandsRequest(
        instruction=build_instruction(report),
        workspace=Path(workspace),
        allowed_paths=ALLOWED_PATHS,
        protected_paths=PROTECTED_PATHS,
        model=model,
        timeout_seconds=timeout_seconds,
        metadata={"kind": "ui_drift_repair",
                  "provider": str(report.get("provider") or ""),
                  "failing_action": action,
                  "selector_pack_version": str(
                      report.get("selector_pack_version") or "")})


def admissible(result: OpenHandsResult) -> tuple[bool, str]:
    """Можно ли принять результат к рассмотрению человеком.

    Проверяется дифф, а не объяснение: объяснение в репозиторий не попадает.
    Ответ «нет» здесь не означает «работник плохой» — он означает, что этот
    конкретный дифф чинит отказ выключением того, что отказывает.
    """
    if result.status != "completed":
        return False, f"работник вернул статус {result.status!r}"
    if not result.changed_files:
        return False, "починка без единого изменённого файла ничего не чинит"
    diff = result.diff or ""
    for pattern, what in _INADMISSIBLE_DIFF:
        if not what:
            continue
        if pattern.search(diff):
            return False, f"в диффе {what}"
    if not any(path.startswith(f"{_FARM}/tests/") for path in result.changed_files):
        return False, ("починка без теста не доказана: отказ обязан быть "
                       "воспроизведён детерминированной страницей")
    return True, "дифф допущен к разбору человеком; правами это не является"


__all__ = ["ALLOWED_PATHS", "PROTECTED_PATHS", "REPAIRABLE_ACTIONS", "SCHEMA",
           "RepairRefused", "admissible", "build_instruction",
           "build_repair_mission", "scan_packet"]
