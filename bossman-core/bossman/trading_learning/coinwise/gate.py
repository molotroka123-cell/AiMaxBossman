"""Допуск наблюдения: что мы согласны считать увиденным.

Каждая проверка здесь закрывает случай, в котором наблюдение выглядит
нормальным, а свидетельством не является:

  * вкладку никто не разрешал смотреть — значит это не наблюдение, а слежка;
  * на экране другой инструмент или другой таймфрейм, чем просили, — число
    настоящее, но отвечает не на тот вопрос;
  * кадр старый — рынок с тех пор другой;
  * метка времени из будущего — либо часы разошлись, либо кадр подсунули;
  * свидетельство из чужой задачи или чужой сессии — самый тихий из способов
    «подтвердить» гипотезу данными, которые к ней не относятся;
  * в тексте страницы лежит инструкция агенту — реклама, чат стрима, подпись
    на картинке.

Все проверки fail-closed: сомнение даёт отказ, а не пропуск. Ошибка здесь
стоит владельцу денег, а «наверное, всё в порядке» деньгами не пахнет.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..safety import OwnerApproval, OwnerApprovalRequired, require_owner_approval
from ..sanitize import sanitize
from .schema import (STALE_SECONDS, InjectionScan, ValidationStatus, clock_skew_ok,
                     classify_freshness, utc)

# Стадия из ALLOWED_STAGES, к которой относится наблюдение дашборда. Наблюдение
# — это анализ, а не симуляция и не бумажная торговля.
OBSERVE_STAGE = "historical_analysis"
# Адрес, который владелец открывает сам. Ходить туда сами мы не будем никогда:
# ни логина, ни обхода оплаты, ни капчи — только чтение уже открытой вкладки.
COINWISE_HOST = "coinwise.com"
DASHBOARD_PATH = "/dashboard"


class ObservationRefused(RuntimeError):
    """Наблюдение не принято. Причина всегда называется."""

    def __init__(self, status: ValidationStatus, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class Binding:
    """К чему привязано наблюдение. Всё вместе, а не по одному полю.

    Смысл связки в том, что подменить одно поле недостаточно: свидетельство
    принадлежит конкретной задаче, конкретному прогону, конкретной сессии
    браузера и конкретной вкладке. Совпасть случайно эта четвёрка не может.
    """

    task_id: str
    run_id: str
    session_id: str
    browser_session_id: str
    tab_id: str
    symbol: str
    timeframe: str

    def matches(self, other: "Binding") -> tuple[bool, str]:
        for name in ("task_id", "run_id", "session_id", "browser_session_id",
                     "tab_id", "symbol", "timeframe"):
            mine, theirs = getattr(self, name), getattr(other, name)
            if str(mine).strip() != str(theirs).strip():
                return False, f"{name}: ожидали {mine!r}, пришло {theirs!r}"
        return True, ""


def check_url(url: str) -> None:
    """Наблюдаем ровно дашборд Coinwise и ничего другого.

    Проверка не косметическая: без неё «наблюдение за дашбордом» стало бы
    универсальным чтением любой страницы, которую откроет владелец, — а это
    уже другая возможность с другими рисками.
    """
    text = str(url or "").strip()
    if not text.startswith("https://"):
        raise ObservationRefused(ValidationStatus.MISMATCH,
                                 f"адрес обязан быть https, получено {text[:60]!r}")
    rest = text[len("https://"):]
    host = rest.split("/", 1)[0].split("?", 1)[0].lower().rstrip(".")
    if host != COINWISE_HOST and not host.endswith("." + COINWISE_HOST):
        raise ObservationRefused(ValidationStatus.MISMATCH,
                                 f"это не Coinwise: {host!r}")
    path = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    if not path.split("?", 1)[0].rstrip("/").endswith(DASHBOARD_PATH.rstrip("/")):
        raise ObservationRefused(ValidationStatus.MISMATCH,
                                 f"это не дашборд: {path[:60]!r}")


def check_approval(approval: OwnerApproval | None, *, source_url: str) -> OwnerApproval:
    """Смотреть чужую вкладку можно только с разрешения владельца.

    Разрешение выдаёт человек и ровно на этот адрес: `require_owner_approval`
    отдельно отвергает одобрение, выданное самой моделью.
    """
    try:
        return require_owner_approval(approval, subject=source_url, stage=OBSERVE_STAGE)
    except OwnerApprovalRequired as exc:
        raise ObservationRefused(ValidationStatus.NOT_APPROVED, str(exc)) from exc


def check_binding(expected: Binding, actual: Binding) -> None:
    ok, why = expected.matches(actual)
    if not ok:
        raise ObservationRefused(ValidationStatus.MISMATCH,
                                 f"свидетельство не от этой работы — {why}")


def check_time(observed_at: datetime, collected_at: datetime) -> float:
    """Возраст кадра и защита от метки из будущего."""
    observed, collected = utc(observed_at), utc(collected_at)
    if not clock_skew_ok(observed, collected):
        raise ObservationRefused(
            ValidationStatus.CLOCK_SKEW,
            f"метка времени страницы опережает наши часы: {observed.isoformat()} "
            f"против {collected.isoformat()}")
    age = (collected - observed).total_seconds()
    if age > STALE_SECONDS:
        raise ObservationRefused(
            ValidationStatus.STALE,
            f"кадру {age:.0f} с, предел {STALE_SECONDS:.0f} с — рынок с тех пор другой")
    return age


def scan_untrusted(chunks: dict[str, str]) -> tuple[InjectionScan, tuple[str, ...]]:
    """Текст со страницы — данные, а не указания.

    Сюда идёт всё, что написано людьми и рекламой: чат стрима, баннеры,
    подписи, тикер новостей, ники. Жёсткий флаг означает карантин: такое
    наблюдение показать владельцу можно, а рассуждать по нему — нет.
    """
    flags: list[str] = []
    hard = False
    for label, raw in (chunks or {}).items():
        clean = sanitize(raw or "")
        for flag in clean.flags:
            flags.append(f"{label}:{flag}")
        hard = hard or clean.must_quarantine
    if hard:
        return InjectionScan.QUARANTINED, tuple(flags)
    if flags:
        return InjectionScan.FLAGGED, tuple(flags)
    return InjectionScan.CLEAN, ()


def freshness_label(age_seconds: float) -> str:
    return classify_freshness(age_seconds)


def admit(*, approval: OwnerApproval | None, source_url: str,
          expected: Binding, actual: Binding,
          observed_at: datetime, collected_at: datetime,
          untrusted: dict[str, str] | None = None) -> dict[str, Any]:
    """Все проверки разом. Либо паспорт наблюдения, либо ObservationRefused.

    Порядок не случаен: сперва право смотреть, потом — что смотрели, и только
    потом содержимое. Разбирать текст страницы, на которую нет разрешения, —
    значит уже её прочитать.
    """
    check_url(source_url)
    granted = check_approval(approval, source_url=source_url)
    check_binding(expected, actual)
    age = check_time(observed_at, collected_at)
    scan, flags = scan_untrusted(untrusted or {})
    return {"approved_by": granted.granted_by, "freshness_seconds": age,
            "freshness": freshness_label(age), "injection_scan_status": scan,
            "injection_flags": flags,
            "validation_status": (ValidationStatus.INJECTION_SUSPECTED
                                  if scan is InjectionScan.QUARANTINED
                                  else ValidationStatus.OK)}


__all__ = ["OBSERVE_STAGE", "COINWISE_HOST", "DASHBOARD_PATH", "ObservationRefused",
           "Binding", "check_url", "check_approval", "check_binding", "check_time",
           "scan_untrusted", "freshness_label", "admit"]
