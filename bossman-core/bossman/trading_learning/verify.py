"""Независимый верификатор: где логика подтверждается данными, а где нет.

K1mba — источник учебного материала и гипотез, а не оракул. Поэтому проверяет
claim'ы модуль, который НЕ участвовал в их извлечении: он видит только claim и
рыночные данные, и не имеет доступа ни к тексту автора целиком, ни к его
выводу. Верификатор, знающий ответ учителя, — это не проверка.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .claims import parse_prices
from .models import Candle, Claim, ClaimType, VerificationStatus
from .safety import EvidenceClass
from . import market

# Допуск на цену из речи/OCR: человек округляет («вход около 76 800»).
PRICE_TOLERANCE = 0.01          # 1%
# Допуск для явно распознанного текста жёстче: OCR либо прочитал, либо нет.
OCR_TOLERANCE = 0.002


class VerifierError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VerificationResult:
    claim_ref: str
    status: VerificationStatus
    reason: str
    checked_against: str
    contradictions: tuple[str, ...] = ()
    evidence_class: EvidenceClass = EvidenceClass.HISTORICAL_REPLAY

    def as_dict(self) -> dict:
        return {"claim_ref": self.claim_ref, "status": self.status.value,
                "reason": self.reason, "checked_against": self.checked_against,
                "contradictions": list(self.contradictions),
                "evidence_class": self.evidence_class.value}


def _window_for(claim: Claim, candles: list[Candle], *,
                span: timedelta = timedelta(hours=6)) -> list[Candle]:
    """Свечи вокруг времени наблюдения. Будущее относительно claim'а не берём."""
    end = claim.collected_at
    start = end - span
    return [c for c in candles if start <= c.ts <= end]


def verify_claim(claim: Claim, candles: list[Candle], *,
                 verifier_id: str = "independent-replay/v1",
                 tolerance: float | None = None) -> VerificationResult:
    """Один claim против рыночных данных.

    Возвращаемые статусы намеренно включают UNVERIFIABLE: «нельзя проверить» —
    это законный и частый ответ, и подменять его на UNVERIFIED («ещё не
    смотрели») означало бы врать о полноте проверки.
    """
    if verifier_id.strip().lower() == claim.extraction_model.strip().lower():
        # Самопроверка запрещена так же, как в learning/trace.py.
        raise VerifierError("verifier must differ from the extraction model")
    ref = f"{claim.source_id}@{claim.timestamp_start:.1f}"

    if claim.verification_status is VerificationStatus.QUARANTINED:
        return VerificationResult(ref, VerificationStatus.QUARANTINED,
                                  "claim arrived quarantined (untrusted input flags)",
                                  "n/a", claim.injection_flags, EvidenceClass.BLOCKED)

    window = _window_for(claim, candles)
    if not window:
        return VerificationResult(ref, VerificationStatus.UNVERIFIABLE,
                                  "no market data covering the observation time",
                                  "market_data", (), EvidenceClass.BLOCKED)

    # Мнение автора не подтверждается и не опровергается ценой: оно остаётся
    # мнением. Проверять можно только конкретные наблюдения и условия.
    if claim.claim_type in (ClaimType.AUTHOR_CLAIM, ClaimType.HYPOTHESIS,
                            ClaimType.RETROSPECTIVE_COMMENTARY):
        return VerificationResult(ref, VerificationStatus.UNVERIFIABLE,
                                  f"{claim.claim_type.value} is an opinion, not a testable fact",
                                  "market_data", (), EvidenceClass.SIMULATED)

    tol = tolerance if tolerance is not None else PRICE_TOLERANCE
    prices = parse_prices(claim.raw_quote_or_frame_ref)
    lo = min(c.low for c in window)
    hi = max(c.high for c in window)
    contradictions: list[str] = []
    supported = 0
    for price in prices:
        band_lo, band_hi = lo * (1 - tol), hi * (1 + tol)
        if band_lo <= price <= band_hi:
            supported += 1
        else:
            contradictions.append(
                f"quoted price {price:.2f} outside traded band [{lo:.2f},{hi:.2f}]")

    if prices and not supported:
        # Цена, которой на рынке не было, — это либо ошибка OCR, либо подлог.
        return VerificationResult(ref, VerificationStatus.DATA_CONTRADICTED,
                                  "no quoted price matches the traded range",
                                  "candles", tuple(contradictions))
    if prices and contradictions:
        return VerificationResult(ref, VerificationStatus.PARTIALLY_SUPPORTED,
                                  f"{supported}/{len(prices)} quoted prices match",
                                  "candles", tuple(contradictions))
    if prices:
        return VerificationResult(ref, VerificationStatus.DATA_SUPPORTED,
                                  f"all {supported} quoted prices are within the traded range",
                                  "candles")

    # Числа не названы — сверяем направленное утверждение с режимом рынка.
    reading = market.classify(window)
    text = claim.raw_quote_or_frame_ref.lower()
    bullish = any(w in text for w in ("рост", "вверх", "лонг", "long", "покупа", "squeeze", "сквиз"))
    bearish = any(w in text for w in ("паден", "вниз", "шорт", "short", "продаж"))
    up = reading.price_change > market.PRICE_EPS
    down = reading.price_change < -market.PRICE_EPS
    if bullish and down:
        return VerificationResult(ref, VerificationStatus.DATA_CONTRADICTED,
                                  "bullish statement while the window traded down",
                                  f"regime={reading.regime.value}",
                                  (f"price_change={reading.price_change:.4f}",))
    if bearish and up:
        return VerificationResult(ref, VerificationStatus.DATA_CONTRADICTED,
                                  "bearish statement while the window traded up",
                                  f"regime={reading.regime.value}",
                                  (f"price_change={reading.price_change:.4f}",))
    if (bullish and up) or (bearish and down):
        return VerificationResult(ref, VerificationStatus.PARTIALLY_SUPPORTED,
                                  "direction agrees with the window, no numbers to check",
                                  f"regime={reading.regime.value}")
    return VerificationResult(ref, VerificationStatus.UNVERIFIABLE,
                              "no numeric or directional content to test",
                              f"regime={reading.regime.value}", (), EvidenceClass.SIMULATED)


def verify_claims(claims: list[Claim], candles: list[Candle], *,
                  verifier_id: str = "independent-replay/v1") -> list[VerificationResult]:
    return [verify_claim(c, candles, verifier_id=verifier_id) for c in claims]


def apply(claims: list[Claim], results: list[VerificationResult]) -> list[Claim]:
    """Перенести вердикты в claim'ы. Статус меняет только верификатор."""
    by_ref = {r.claim_ref: r for r in results}
    out: list[Claim] = []
    for claim in claims:
        ref = f"{claim.source_id}@{claim.timestamp_start:.1f}"
        res = by_ref.get(ref)
        out.append(claim.with_status(res.status, res.contradictions) if res else claim)
    return out


def summary(results: list[VerificationResult]) -> dict[str, int]:
    counts: dict[str, int] = {s.value: 0 for s in VerificationStatus}
    for r in results:
        counts[r.status.value] += 1
    return counts
