"""Типизированное наблюдение дашборда Coinwise. Только чтение.

Смысл файла в одном: отличить «я это видел» от «я это додумал». Дашборд —
картинка на чужом сайте, и почти всё, что с неё можно взять, берётся с разной
надёжностью: число из DOM — это число, то же число из OCR по мутному скриншоту
— это догадка. Поэтому у каждого поля есть свой источник и своя уверенность, а
не одна общая пометка на всё наблюдение.

Главное правило: НЕТ ЗНАЧЕНИЯ — ЗНАЧИТ UNKNOWN. Пустое поле никогда не
заполняется нулём, средним, «вчерашним» или «похоже на 105 000». Ноль на
дашборде и отсутствие числа — разные факты, и путать их нельзя: на разнице
между ними держится весь смысл наблюдения.

Второе правило: скриншот — не доказательство сделки. Классы доказательности
здесь описывают, ЧТО наблюдали, и ни один из них не означает, что торговля
разрешена или что данные подтверждены биржей.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from ..safety import utcnow

# Свежесть. Дашборд обновляется потоком, и минутной давности кадр — это уже
# другой рынок. Порог намеренно короткий: лучше честное STALE, чем красивое
# число, под которым цена уже другая.
FRESH_SECONDS = 30.0
STALE_SECONDS = 120.0
# Допуск на расхождение часов. Метка времени со страницы приходит из чужого
# браузера, и «из будущего» бывает и от честного рассинхрона, и от подмены.
# Больше допуска — уже не расхождение часов, а негодное свидетельство.
MAX_CLOCK_SKEW_SECONDS = 90.0


class SourceMethod(str, Enum):
    """Чем именно получено значение. Порядок — от надёжного к спорному."""

    DOM = "DOM"
    ACCESSIBILITY_TREE = "ACCESSIBILITY_TREE"
    LOCAL_OCR = "LOCAL_OCR"
    LOCAL_VISION = "LOCAL_VISION"
    MANUAL_OWNER_INPUT = "MANUAL_OWNER_INPUT"


# Насколько методу можно верить в принципе, до всякой уверенности распознавания.
METHOD_CEILING: dict[SourceMethod, float] = {
    SourceMethod.DOM: 1.0,
    SourceMethod.ACCESSIBILITY_TREE: 0.95,
    SourceMethod.MANUAL_OWNER_INPUT: 0.9,     # владелец тоже может опечататься
    SourceMethod.LOCAL_OCR: 0.75,
    SourceMethod.LOCAL_VISION: 0.6,
}

# Ниже этого значение считается нечитаемым и превращается в UNKNOWN. Спорное
# число хуже отсутствующего: отсутствующее видно, спорное — нет.
MIN_FIELD_CONFIDENCE = 0.55


class ObservationEvidence(str, Enum):
    """Класс доказательности НАБЛЮДЕНИЯ.

    Отдельная шкала от `safety.EvidenceClass`: та отвечает на вопрос «чем
    доказана сделка», эта — «чем доказано, что мы это видели». Смешивать их
    нельзя, иначе `REAL_BROWSER_READONLY` однажды прочитают как «торговали
    по-настоящему».
    """

    MOCK = "MOCK"                                   # фикстура, регрессия
    SCREENSHOT_OBSERVED = "SCREENSHOT_OBSERVED"     # картинка + локальный OCR
    REAL_BROWSER_READONLY = "REAL_BROWSER_READONLY"  # живой DOM открытой владельцем вкладки
    BLOCKED = "BLOCKED"                             # не смогли посмотреть
    STALE = "STALE"                                 # смотрели, но давно
    INVALID = "INVALID"                             # смотрели не то или не сходится


class ValidationStatus(str, Enum):
    OK = "OK"
    STALE = "STALE"
    MISMATCH = "MISMATCH"
    CLOCK_SKEW = "CLOCK_SKEW"
    INJECTION_SUSPECTED = "INJECTION_SUSPECTED"
    PARSE_FAILED = "PARSE_FAILED"
    NOT_APPROVED = "NOT_APPROVED"


class InjectionScan(str, Enum):
    CLEAN = "CLEAN"
    FLAGGED = "FLAGGED"
    QUARANTINED = "QUARANTINED"
    NOT_SCANNED = "NOT_SCANNED"


# Поля рынка, которые дашборд может показывать. Список закрытый: поле, которого
# здесь нет, не появится в наблюдении «само» из разбора чужой разметки.
MARKET_FIELDS: tuple[str, ...] = (
    "price", "cvd", "open_interest", "liquidations", "buy_volume", "sell_volume",
    "daily_open", "vah", "val", "vpoc", "tpoc", "vwap", "tvah", "tval",
    "prev_day_high", "prev_day_low",
)


@dataclass(frozen=True, slots=True)
class FieldValue:
    """Одно значение с его собственной родословной.

    `value is None` — это UNKNOWN, и такой случай законен для любого поля.
    Отсутствие значения всегда объясняется: `note` говорит, ПОЧЕМУ не видно, —
    иначе владелец не отличит «на дашборде этого нет» от «мы не смогли прочесть».
    """

    value: float | None
    method: SourceMethod | None = None
    confidence: float = 0.0
    note: str = ""
    raw_text: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "method": self.method.value if self.method else None,
                "confidence": round(self.confidence, 3), "note": self.note,
                "raw_text": self.raw_text[:120]}


def unknown(note: str) -> FieldValue:
    """Явное «не знаю». Отдельная функция, чтобы UNKNOWN писался одинаково."""
    return FieldValue(value=None, method=None, confidence=0.0, note=note or "не видно")


def measured(value: float, method: SourceMethod, confidence: float, *,
             raw_text: str = "") -> FieldValue:
    """Значение с потолком по методу и отсечкой по уверенности.

    Уверенность не может быть выше потолка метода: OCR, уверенный в себе на
    0.99, всё равно остаётся OCR. Ниже отсечки значение выбрасывается — читать
    сомнительное число как факт опаснее, чем не читать вовсе.
    """
    ceiling = METHOD_CEILING.get(method, 0.5)
    score = max(0.0, min(float(confidence), ceiling))
    if score < MIN_FIELD_CONFIDENCE:
        return FieldValue(value=None, method=method, confidence=score,
                          note=f"распознано ненадёжно ({score:.2f}) — считаем, что не видно",
                          raw_text=raw_text)
    return FieldValue(value=float(value), method=method, confidence=score, raw_text=raw_text)


@dataclass(frozen=True, slots=True)
class ViewportMeta:
    """Что именно было на экране. Без этого скриншот не воспроизводим."""

    width: int = 0
    height: int = 0
    device_pixel_ratio: float = 1.0
    zoom: float = 1.0
    tab_id: str = ""
    browser_session_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"width": self.width, "height": self.height,
                "device_pixel_ratio": self.device_pixel_ratio, "zoom": self.zoom,
                "tab_id": self.tab_id, "browser_session_id": self.browser_session_id}


@dataclass(frozen=True, slots=True)
class LiquidityZone:
    """Видимая зона ликвидности. Границы — то, что нарисовано, а не расчёт."""

    low: float
    high: float
    side: str = ""            # ask | bid | ""
    strength: float = 0.0
    method: SourceMethod = SourceMethod.LOCAL_VISION
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"low": self.low, "high": self.high, "side": self.side,
                "strength": self.strength, "method": self.method.value,
                "confidence": round(self.confidence, 3)}


@dataclass(frozen=True, slots=True)
class CoinwiseObservation:
    """Одно наблюдение дашборда. Неизменяемое: свидетельство не правят задним числом."""

    observation_id: str
    task_id: str
    run_id: str
    session_id: str
    source_url: str
    symbol: str
    venue: str
    timeframe: str
    observed_at: datetime                 # что написано на странице
    collected_at: datetime                # когда мы это забрали
    monotonic_collected_at: float         # монотонные часы: их не переводят назад
    freshness_seconds: float
    source_method: SourceMethod
    fields: dict[str, FieldValue] = field(default_factory=dict)
    liquidity_zones: tuple[LiquidityZone, ...] = ()
    dashboard_state: str = "UNKNOWN"
    stream_state: str = "UNKNOWN"
    content_hash: str = ""                # хеш DOM или скриншота
    viewport: ViewportMeta = field(default_factory=ViewportMeta)
    model_version: str = ""
    head_sha: str = ""
    environment: str = ""
    evidence_class: ObservationEvidence = ObservationEvidence.MOCK
    injection_scan_status: InjectionScan = InjectionScan.NOT_SCANNED
    validation_status: ValidationStatus = ValidationStatus.OK
    notes: tuple[str, ...] = ()

    # ------------------------------------------------------------ доступ

    def field_value(self, name: str) -> FieldValue:
        """Поля, которого нет, не бывает: вместо KeyError — честное UNKNOWN."""
        return self.fields.get(name) or unknown("поле не извлекалось")

    def get(self, name: str) -> float | None:
        return self.field_value(name).value

    def known_fields(self) -> tuple[str, ...]:
        return tuple(n for n in MARKET_FIELDS if self.field_value(n).known)

    def missing_fields(self) -> tuple[str, ...]:
        return tuple(n for n in MARKET_FIELDS if not self.field_value(n).known)

    @property
    def field_confidence(self) -> dict[str, float]:
        return {n: round(self.field_value(n).confidence, 3) for n in MARKET_FIELDS}

    @property
    def usable(self) -> bool:
        """Годится ли наблюдение для интерпретации.

        Годным считается только свежее, проверенное и не подозреваемое в
        инъекции. Всё остальное можно показать владельцу, но не рассуждать по нему.
        """
        return (self.validation_status is ValidationStatus.OK
                and self.evidence_class in (ObservationEvidence.REAL_BROWSER_READONLY,
                                            ObservationEvidence.SCREENSHOT_OBSERVED,
                                            ObservationEvidence.MOCK)
                and self.injection_scan_status is not InjectionScan.QUARANTINED
                and self.freshness_seconds <= STALE_SECONDS)

    @property
    def is_live_proof(self) -> bool:
        """Всегда False, и это не заглушка.

        Наблюдение за чужим дашбордом не доказывает ни исполнения, ни цены на
        бирже. Свойство существует, чтобы на него можно было СОСЛАТЬСЯ в
        отчёте, а не гадать по классу доказательности.
        """
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id, "task_id": self.task_id,
            "run_id": self.run_id, "session_id": self.session_id,
            "source_url": self.source_url, "symbol": self.symbol, "venue": self.venue,
            "timeframe": self.timeframe,
            "observed_at": self.observed_at.isoformat(),
            "collected_at": self.collected_at.isoformat(),
            "monotonic_collected_at": self.monotonic_collected_at,
            "freshness_seconds": round(self.freshness_seconds, 3),
            "source_method": self.source_method.value,
            "fields": {n: self.field_value(n).as_dict() for n in MARKET_FIELDS},
            "field_confidence": self.field_confidence,
            "known_fields": list(self.known_fields()),
            "missing_fields": list(self.missing_fields()),
            "liquidity_zones": [z.as_dict() for z in self.liquidity_zones],
            "dashboard_state": self.dashboard_state, "stream_state": self.stream_state,
            "content_hash": self.content_hash, "viewport": self.viewport.as_dict(),
            "model_version": self.model_version, "head_sha": self.head_sha,
            "environment": self.environment,
            "evidence_class": self.evidence_class.value,
            "injection_scan_status": self.injection_scan_status.value,
            "validation_status": self.validation_status.value,
            "usable": self.usable, "is_live_proof": self.is_live_proof,
            "notes": list(self.notes),
            "read_only": True,
        }


def content_hash(payload: bytes | str) -> str:
    """Один способ хеширования и для DOM, и для скриншота."""
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload or b"")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def freshness(observed_at: datetime, collected_at: datetime) -> float:
    """Возраст данных в секундах. Отрицательный — это метка из будущего."""
    return (collected_at - observed_at).total_seconds()


def monotonic_now() -> float:
    return time.monotonic()


def classify_freshness(seconds: float) -> str:
    if seconds < 0:
        return "FUTURE"
    if seconds <= FRESH_SECONDS:
        return "FRESH"
    if seconds <= STALE_SECONDS:
        return "AGING"
    return "STALE"


def clock_skew_ok(observed_at: datetime, collected_at: datetime) -> bool:
    """Метка со страницы не имеет права опережать наши часы больше допуска."""
    return (observed_at - collected_at) <= timedelta(seconds=MAX_CLOCK_SKEW_SECONDS)


def utc(moment: datetime) -> datetime:
    """Наивное время считаем UTC: смешивать зоны в свидетельстве нельзя."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


__all__ = [
    "FRESH_SECONDS", "STALE_SECONDS", "MAX_CLOCK_SKEW_SECONDS", "MIN_FIELD_CONFIDENCE",
    "MARKET_FIELDS", "METHOD_CEILING", "SourceMethod", "ObservationEvidence",
    "ValidationStatus", "InjectionScan", "FieldValue", "unknown", "measured",
    "ViewportMeta", "LiquidityZone", "CoinwiseObservation", "content_hash",
    "freshness", "monotonic_now", "classify_freshness", "clock_skew_ok", "utc", "utcnow",
]
