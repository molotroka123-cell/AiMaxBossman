"""Обнаружение возможностей браузерного пути и автоматическое понижение.

`44_BROWSER_FALLBACK_CAPABILITY_DISCOVERY` запрещает считать каждый элемент
интерфейса возможностью автоматизации. Возможность повышается только после
списка условий, последнее из которых — **свидетельство настоящего браузера**.

Отсюда главное решение этого файла и всего потока:

    Возможность, проверенная только на фикстуре, остаётся EXPERIMENTAL.

Не «почти готова», не «работает, но не проверена» — `EXPERIMENTAL`, и в матрице
возможностей аккаунта она НЕ actionable. Живого аккаунта Instagram и человека у
экрана в этой среде нет, поэтому ни одна возможность Instagram здесь до
`VERIFIED_BROWSER` не поднимается. Повышение требует свидетельства, которого
некому выдать.

Второе решение — про понижение. Три подряд детерминированных отказа на ОДНОЙ
версии пакета селекторов означают, что интерфейс провайдера сменился, а не что
нам не повезло. Возможность уходит в `BROKEN_UI_VERSION`, в матрице становится
`TEMPORARILY_DISABLED` на время паузы, и повторы прекращаются. Числа — из
конфигурации (`config.py`), не константы: порог «когда беспокоить владельца» —
его решение, а не свойство сборки.

Какие отказы считаются детерминированными, спека не говорит. Решение:
детерминированный — это отказ, который повторится сам по себе на той же
странице (цель не найдена, цель неоднозначна, постусловие не выполнилось).
Устаревшая цель детерминированной НЕ считается: страница изменилась между
снимком и действием, это гонка, и следующая попытка вполне может пройти.
Передача человеку — тем более: это не поломка интерфейса.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from ..domain.capability import Capability, CapabilitySnapshot, CapabilityStatus
from .config import BrowserConfig


class BrowserCapabilityState(str, Enum):
    """Четыре состояния из `44_...`. Перечень закрытый."""

    EXPERIMENTAL = "EXPERIMENTAL"
    VERIFIED_BROWSER = "VERIFIED_BROWSER"
    BROKEN_UI_VERSION = "BROKEN_UI_VERSION"
    DISABLED = "DISABLED"


class FailureKind(str, Enum):
    """Почему действие не получилось. Считается только детерминированное."""

    TARGET_MISSING = "TARGET_MISSING"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    CONFIRMATION_MISMATCH = "CONFIRMATION_MISMATCH"
    STALE_TARGET = "STALE_TARGET"
    TAKEOVER_REQUIRED = "TAKEOVER_REQUIRED"
    TRANSIENT = "TRANSIENT"


# Отказы, которые повторятся сами по себе на той же странице.
DETERMINISTIC_FAILURES = frozenset({
    FailureKind.TARGET_MISSING, FailureKind.TARGET_AMBIGUOUS,
    FailureKind.POSTCONDITION_FAILED, FailureKind.CONFIRMATION_MISMATCH,
})


class PromotionRefused(ValueError):
    """Возможность нельзя повысить: нет требуемого свидетельства."""


@dataclass(frozen=True, slots=True)
class Evidence:
    """Чем подтверждена возможность браузерного пути.

    `real_browser` — не «Playwright запустился», а «действие прошло на живом
    аккаунте провайдера и результат проверен». Фикстура даёт `fixture_only`.
    """

    deterministic_navigation: bool = False
    target_identity: bool = False
    successful_test: bool = False
    failure_behavior: bool = False
    policy_classification: bool = False
    account_type_compatible: bool = False
    real_browser: bool = False
    note: str = ""

    @classmethod
    def fixture_only(cls, note: str = "проверено на локальной фикстуре") -> "Evidence":
        """Всё, кроме живого браузера. Ровно то, что здесь достижимо."""
        return cls(deterministic_navigation=True, target_identity=True,
                   successful_test=True, failure_behavior=True,
                   policy_classification=True, account_type_compatible=True,
                   real_browser=False, note=note)

    def missing(self) -> list[str]:
        gaps = []
        for name in ("deterministic_navigation", "target_identity", "successful_test",
                     "failure_behavior", "policy_classification",
                     "account_type_compatible", "real_browser"):
            if not getattr(self, name):
                gaps.append(name)
        return gaps

    def to_dict(self) -> dict[str, Any]:
        return {"deterministic_navigation": self.deterministic_navigation,
                "target_identity": self.target_identity,
                "successful_test": self.successful_test,
                "failure_behavior": self.failure_behavior,
                "policy_classification": self.policy_classification,
                "account_type_compatible": self.account_type_compatible,
                "real_browser": self.real_browser, "note": self.note}


@dataclass(slots=True)
class BrowserCapabilityRecord:
    """Состояние одной возможности браузерного пути на одном аккаунте."""

    name: str
    state: BrowserCapabilityState = BrowserCapabilityState.EXPERIMENTAL
    selector_pack_version: str = ""
    consecutive_failures: int = 0
    failing_pack_version: str = ""
    disabled_until: str | None = None
    evidence: Evidence = field(default_factory=Evidence)
    reason: str = ""
    observed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state.value,
                "selector_pack_version": self.selector_pack_version,
                "consecutive_failures": self.consecutive_failures,
                "failing_pack_version": self.failing_pack_version,
                "disabled_until": self.disabled_until,
                "evidence": self.evidence.to_dict(), "reason": self.reason,
                "observed_at": self.observed_at}


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


@dataclass(slots=True)
class BrowserCapabilityLedger:
    """Что браузерный путь умеет на этом аккаунте — и почему считается, что умеет."""

    account_id: str
    provider: str = "instagram"
    config: BrowserConfig = field(default_factory=BrowserConfig)
    records: dict[str, BrowserCapabilityRecord] = field(default_factory=dict)

    # ---- объявление и повышение

    def declare(self, name: str, *, selector_pack_version: str,
                evidence: Evidence | None = None,
                now: datetime | None = None) -> BrowserCapabilityRecord:
        """Объявить возможность. Всегда начинается с `EXPERIMENTAL`."""
        record = BrowserCapabilityRecord(
            name=name, selector_pack_version=selector_pack_version,
            evidence=evidence or Evidence(), observed_at=_now(now).isoformat(),
            reason="объявлена; свидетельства настоящего браузера нет")
        self.records[name] = record
        return record

    def promote(self, name: str, *, evidence: Evidence,
                now: datetime | None = None) -> BrowserCapabilityRecord:
        """Повысить до `VERIFIED_BROWSER`. Без живого браузера — отказ.

        Отказ намеренно громкий (исключение, а не тихое «осталось как было»):
        тихое непонижение статуса выглядит в отчёте как «повышено», и ровно так
        появляются возможности, которые никто не проверял.
        """
        record = self.records.get(name)
        if record is None:
            raise PromotionRefused(f"возможность {name} не объявлена")
        gaps = evidence.missing()
        if gaps:
            record.evidence = evidence
            record.reason = ("до VERIFIED_BROWSER не хватает свидетельств: "
                             + ", ".join(gaps))
            record.observed_at = _now(now).isoformat()
            raise PromotionRefused(
                f"возможность {name} остаётся {record.state.value}: {record.reason}")
        record.state = BrowserCapabilityState.VERIFIED_BROWSER
        record.evidence = evidence
        record.reason = evidence.note or "подтверждена на настоящем браузере"
        record.observed_at = _now(now).isoformat()
        record.consecutive_failures = 0
        return record

    def disable(self, name: str, reason: str = "выключено владельцем",
                now: datetime | None = None) -> BrowserCapabilityRecord:
        record = self.records.setdefault(name, BrowserCapabilityRecord(name=name))
        record.state = BrowserCapabilityState.DISABLED
        record.reason = reason
        record.observed_at = _now(now).isoformat()
        return record

    # ---- учёт отказов

    def record_success(self, name: str, *, selector_pack_version: str,
                       now: datetime | None = None) -> BrowserCapabilityRecord:
        record = self.records.setdefault(
            name, BrowserCapabilityRecord(name=name,
                                          selector_pack_version=selector_pack_version))
        record.consecutive_failures = 0
        record.failing_pack_version = ""
        record.observed_at = _now(now).isoformat()
        if record.state is BrowserCapabilityState.BROKEN_UI_VERSION:
            # Интерфейс снова узнаётся — но обратно в VERIFIED_BROWSER
            # возможность не возвращается сама: подтверждение надо получить
            # заново, а не вывести из одного удачного нажатия.
            record.state = BrowserCapabilityState.EXPERIMENTAL
            record.disabled_until = None
            record.reason = "интерфейс снова узнаётся; подтверждение нужно заново"
        return record

    def record_failure(self, name: str, *, selector_pack_version: str,
                       kind: FailureKind,
                       now: datetime | None = None) -> BrowserCapabilityRecord:
        """Учесть отказ. Понижение происходит здесь и только здесь."""
        moment = _now(now)
        record = self.records.setdefault(
            name, BrowserCapabilityRecord(name=name,
                                          selector_pack_version=selector_pack_version))
        record.observed_at = moment.isoformat()
        if kind not in DETERMINISTIC_FAILURES:
            # Гонка или человек — счётчик не трогаем. Иначе одна перерисовка
            # страницы отключала бы возможность, которая работает.
            record.reason = f"недетерминированный отказ {kind.value}: счётчик не тронут"
            return record
        if record.failing_pack_version != selector_pack_version:
            # Счётчик привязан к версии пакета: отказы на старой версии ничего
            # не говорят о новой.
            record.failing_pack_version = selector_pack_version
            record.consecutive_failures = 0
        record.consecutive_failures += 1
        record.reason = (f"детерминированный отказ {kind.value} "
                         f"({record.consecutive_failures} подряд) на пакете "
                         f"{selector_pack_version}")
        if record.consecutive_failures >= self.config.deterministic_failure_threshold:
            record.state = BrowserCapabilityState.BROKEN_UI_VERSION
            record.disabled_until = (
                moment + timedelta(minutes=self.config.cooldown_minutes)).isoformat()
            record.reason = (
                f"{record.consecutive_failures} подряд детерминированных отказов на "
                f"пакете селекторов {selector_pack_version} — интерфейс провайдера "
                f"сменился. Возможность отключена до {record.disabled_until}; "
                f"нужна новая версия пакета, а не повтор")
        return record

    def cooling_down(self, name: str, now: datetime | None = None) -> bool:
        record = self.records.get(name)
        if record is None or not record.disabled_until:
            return False
        deadline = datetime.fromisoformat(record.disabled_until)
        return _now(now) < deadline

    # ---- матрица возможностей

    def status_of(self, name: str, now: datetime | None = None) -> CapabilityStatus:
        """Как возможность выглядит в матрице аккаунта.

        Actionable через браузер — ровно одно состояние, `VERIFIED_BROWSER`.
        Остальные три отображаются в `TEMPORARILY_DISABLED`, а не в
        `NOT_SUPPORTED`: «провайдер этого не умеет» — неправда, мы просто не
        доказали, что умеем мы. Разница видна владельцу и ведёт к разным
        действиям: «ждать нечего» против «проверьте и включите».
        """
        record = self.records.get(name)
        if record is None:
            return CapabilityStatus.NOT_SUPPORTED
        if record.state is BrowserCapabilityState.VERIFIED_BROWSER:
            return CapabilityStatus.SUPPORTED_BROWSER
        return CapabilityStatus.TEMPORARILY_DISABLED

    def reason_of(self, name: str) -> str:
        record = self.records.get(name)
        if record is None:
            return "возможность браузерного пути не объявлена"
        if record.state is BrowserCapabilityState.EXPERIMENTAL:
            gap = ", ".join(record.evidence.missing()) or "нет"
            return (f"EXPERIMENTAL: до VERIFIED_BROWSER не хватает свидетельств "
                    f"({gap}). {record.reason}".strip())
        return f"{record.state.value}: {record.reason}"

    def snapshot(self, now: datetime | None = None,
                 adapter_version: str = "") -> CapabilitySnapshot:
        """Снимок возможностей браузерного пути в доменном виде."""
        moment = _now(now)
        observed = moment.isoformat()
        expires = (moment + timedelta(
            hours=self.config.capability_snapshot_ttl_hours)).isoformat()
        capabilities: dict[str, Capability] = {}
        for name, record in self.records.items():
            status = self.status_of(name, now=moment)
            capabilities[name] = Capability(
                name=name, status=status, source=f"{self.provider}/browser",
                observed_at=observed, adapter_version=adapter_version,
                reason=self.reason_of(name),
                expires_at=record.disabled_until if (
                    record.state is BrowserCapabilityState.BROKEN_UI_VERSION
                ) else expires)
        return CapabilitySnapshot(
            account_id=self.account_id, provider=self.provider, observed_at=observed,
            adapter_version=adapter_version, capabilities=capabilities)


__all__ = ["DETERMINISTIC_FAILURES", "BrowserCapabilityLedger",
           "BrowserCapabilityRecord", "BrowserCapabilityState", "Evidence",
           "FailureKind", "PromotionRefused"]
