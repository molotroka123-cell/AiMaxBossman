"""Типы знания. Свободного текста в памяти модуля не существует.

Почему так: «бот посмотрел видео и запомнил» — это способ протащить в
процедурную память рекламу, оговорку и взгляд задним числом. Поэтому каждый
фрагмент материала превращается в типизированный claim с происхождением, и
именно тип claim'а решает, что с ним вообще разрешено делать дальше.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from .safety import EvidenceClass, utcnow


class ClaimType(str, Enum):
    AUTHOR_CLAIM = "AUTHOR_CLAIM"                       # мнение автора, не факт
    MARKET_OBSERVATION = "MARKET_OBSERVATION"           # наблюдение с графика/данных
    HYPOTHESIS = "HYPOTHESIS"
    ENTRY_CONDITION = "ENTRY_CONDITION"
    EXIT_CONDITION = "EXIT_CONDITION"
    INVALIDATION = "INVALIDATION"
    RISK_RULE = "RISK_RULE"
    POSITION_MANAGEMENT = "POSITION_MANAGEMENT"
    EXPECTED_OUTCOME = "EXPECTED_OUTCOME"
    RETROSPECTIVE_COMMENTARY = "RETROSPECTIVE_COMMENTARY"  # разбор постфактум


class VerificationStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    DATA_SUPPORTED = "DATA_SUPPORTED"
    DATA_CONTRADICTED = "DATA_CONTRADICTED"
    UNVERIFIABLE = "UNVERIFIABLE"
    QUARANTINED = "QUARANTINED"


class Phase(str, Enum):
    """Разделение времени эпизода. Основа защиты от подглядывания в будущее."""

    T0 = "T0"   # что было доступно ДО сигнала
    T1 = "T1"   # момент решения
    T2 = "T2"   # независимая проверка исхода
    T3 = "T3"   # разбор


# В момент решения запрещены не только будущие данные, но и целые типы знания:
# ожидаемый исход и ретроспективный комментарий учителя — это ответ, а не вход.
FORBIDDEN_AT_DECISION = frozenset({ClaimType.EXPECTED_OUTCOME,
                                   ClaimType.RETROSPECTIVE_COMMENTARY})

# AUTHOR_CLAIM никогда не переносится напрямую в процедурную память.
NEVER_PROCEDURAL = frozenset({ClaimType.AUTHOR_CLAIM, ClaimType.HYPOTHESIS,
                              ClaimType.RETROSPECTIVE_COMMENTARY,
                              ClaimType.EXPECTED_OUTCOME})


class MarketRegime(str, Enum):
    """Режимы, которые система обязана различать (иначе правило непереносимо)."""

    PRICE_UP_CVD_UP_OI_UP = "PRICE_UP_CVD_UP_OI_UP"
    PRICE_UP_CVD_WEAK_OI_UP = "PRICE_UP_CVD_WEAK_OI_UP"
    PRICE_DOWN_OI_DOWN = "PRICE_DOWN_OI_DOWN"
    PRICE_DOWN_OI_UP = "PRICE_DOWN_OI_UP"
    SHORT_SQUEEZE = "SHORT_SQUEEZE"
    LONG_SQUEEZE = "LONG_SQUEEZE"
    CONTINUATION = "CONTINUATION"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class MemoryLayer(str, Enum):
    WORKING_STATE = "WORKING_STATE"
    EPISODIC_MEMORY = "EPISODIC_MEMORY"
    PROCEDURAL_MEMORY = "PROCEDURAL_MEMORY"
    QUARANTINE = "QUARANTINE"


class Decision(str, Enum):
    """Главная цель модуля — уметь честно сказать «не знаю», а не угадывать."""

    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


_ASSET = re.compile(r"^[A-Z0-9]{2,20}$")


class ClaimError(ValueError):
    """Claim без происхождения не создаётся. Ошибка, а не предупреждение."""


@dataclass(frozen=True, slots=True)
class Claim:
    """Единица знания. Все поля происхождения обязательны по построению."""

    claim_type: ClaimType
    source_id: str
    video_hash: str
    timestamp_start: float          # секунды от начала материала
    timestamp_end: float
    asset: str
    venue: str
    timeframe: str
    market_state: str
    raw_quote_or_frame_ref: str
    confidence: float
    extraction_model: str
    created_at: datetime
    collected_at: datetime          # когда наблюдение было СДЕЛАНО (не когда записано)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    contradictions: tuple[str, ...] = ()
    allowed_consumers: tuple[str, ...] = ("analysis",)
    evidence_class: EvidenceClass = EvidenceClass.MOCK
    sanitized: bool = False
    injection_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("source_id", "video_hash", "asset", "venue", "timeframe",
                     "raw_quote_or_frame_ref", "extraction_model"):
            if not str(getattr(self, name) or "").strip():
                raise ClaimError(f"claim field {name!r} is required (no provenance, no claim)")
        if not _ASSET.match(self.asset):
            raise ClaimError(f"asset {self.asset!r} must be an uppercase symbol")
        if self.timestamp_end < self.timestamp_start:
            raise ClaimError("timestamp_end precedes timestamp_start")
        if not 0.0 <= self.confidence <= 1.0:
            raise ClaimError("confidence must be within [0,1]")
        for name in ("created_at", "collected_at"):
            value: datetime = getattr(self, name)
            if value.tzinfo is None:
                raise ClaimError(f"{name} must be timezone-aware")
        if self.collected_at > utcnow():
            # Наблюдение из будущего — либо испорченные метаданные, либо подлог.
            raise ClaimError("collected_at is in the future")

    # ------------------------------------------------------------------ права
    def is_author_opinion(self) -> bool:
        return self.claim_type in NEVER_PROCEDURAL

    def usable_at_decision(self) -> bool:
        """Можно ли брать этот claim во вход модели в момент T1."""
        return self.claim_type not in FORBIDDEN_AT_DECISION

    def with_status(self, status: VerificationStatus,
                    contradictions: tuple[str, ...] = ()) -> "Claim":
        return replace(self, verification_status=status,
                       contradictions=tuple(contradictions) or self.contradictions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_type": self.claim_type.value, "source_id": self.source_id,
            "video_hash": self.video_hash, "timestamp_start": self.timestamp_start,
            "timestamp_end": self.timestamp_end, "asset": self.asset, "venue": self.venue,
            "timeframe": self.timeframe, "market_state": self.market_state,
            "raw_quote_or_frame_ref": self.raw_quote_or_frame_ref[:300],
            "confidence": self.confidence, "extraction_model": self.extraction_model,
            "created_at": self.created_at.isoformat(), "collected_at": self.collected_at.isoformat(),
            "verification_status": self.verification_status.value,
            "contradictions": list(self.contradictions),
            "allowed_consumers": list(self.allowed_consumers),
            "evidence_class": self.evidence_class.value,
            "sanitized": self.sanitized, "injection_flags": list(self.injection_flags),
        }


@dataclass(frozen=True, slots=True)
class Candle:
    """Свеча рынка вместе с потоковыми метриками — то, чем проверяются claim'ы."""

    ts: datetime                # время ЗАКРЫТИЯ свечи
    open: float
    high: float
    low: float
    close: float
    volume: float
    cvd: float = 0.0            # кумулятивная дельта
    open_interest: float = 0.0
    long_liquidations: float = 0.0
    short_liquidations: float = 0.0

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ClaimError("candle ts must be timezone-aware")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ClaimError("candle OHLC is inconsistent")


@dataclass(frozen=True, slots=True)
class TradeResult:
    """Исход одной симулированной сделки. R — в единицах начального риска."""

    episode_id: str
    direction: Decision
    entry: float
    exit: float
    stop: float
    size: float
    fees: float
    funding: float
    slippage: float
    regime: MarketRegime
    out_of_sample: bool = False

    @property
    def gross_pnl(self) -> float:
        sign = 1.0 if self.direction is Decision.LONG else -1.0
        return sign * (self.exit - self.entry) * self.size

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees - self.funding - self.slippage

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def r_multiple(self) -> float:
        risk = self.risk_per_unit * self.size
        return self.net_pnl / risk if risk > 0 else 0.0


@dataclass
class Episode:
    """Учебный эпизод: окно данных, решение и исход, разнесённые по фазам."""

    episode_id: str
    asset: str
    venue: str
    timeframe: str
    decision_time: datetime
    candles: list[Candle] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    outcome: TradeResult | None = None
    evidence_class: EvidenceClass = EvidenceClass.MOCK
    labels: tuple[str, ...] = ()
