"""Затравочный эпизод по материалу K1mba. Строго SCREENSHOT_OBSERVED.

Почему он здесь и почему он безопасен: это хороший СТРУКТУРНЫЙ пример того,
как выглядит связка «зона → слабый CVD → запрет усреднения → ускорение CVD/OI →
шорт-сквиз → защита прибыли». Но это НЕ доказательство прибыльности: у нас нет
ни биржи, ни размера, ни плеча, ни фактических филлов, ни комиссий, ни funding,
ни сырых рыночных данных с метками времени.

Поэтому эпизод создаётся:
  * без свечей — его физически нельзя прогнать в бэктесте и получить «прибыль»;
  * с меткой NO_VERIFIED_TIMESTAMPS — верификатор ответит UNVERIFIABLE, а не
    «подтверждено»;
  * с классом доказательности MOCK и запретом ставить LIVE_PROVEN.
"""
from __future__ import annotations

from datetime import datetime

from .models import Claim, ClaimType, Episode, VerificationStatus
from .safety import EvidenceClass, utcnow

SEED_SOURCE_ID = "src_k1mba_seed_screenshot"
# Хеша видео нет — есть наблюдение по кадрам. Строка честно называет это, а не
# притворяется sha256 несуществующего файла.
SEED_VIDEO_HASH = "screenshot-observed:no-source-file"
SEED_LABELS = ("SCREENSHOT_OBSERVED", "NO_VERIFIED_TIMESTAMPS", "NOT_PROOF_OF_PNL")

# Наблюдения с кадров. Числа записаны как их видели, без интерпретации.
SEED_OBSERVATIONS: tuple[tuple[ClaimType, str], ...] = (
    (ClaimType.ENTRY_CONDITION,
     "BTCUSDT long, вход около 76 800 в зоне спроса и ликвидности 76.7-76.9k"),
    (ClaimType.MARKET_OBSERVATION,
     "на раннем отскоке CVD слабый: покупатель не подтверждает движение"),
    (ClaimType.RISK_RULE,
     "не усредняться и не усиливать позицию, пока CVD слабый"),
    (ClaimType.MARKET_OBSERVATION,
     "возврат в 77.8-78.0k и последующий проход 79k"),
    (ClaimType.MARKET_OBSERVATION,
     "CVD и open interest резко ускорились на проходе уровня"),
    (ClaimType.MARKET_OBSERVATION,
     "наблюдался short squeeze: ликвидации шортов доминировали"),
    (ClaimType.MARKET_OBSERVATION,
     "на финальных кадрах цена около 82.1k, CVD около 63.26B, OI около 20.17B, "
     "short liquidations около 28.18M"),
    (ClaimType.RISK_RULE,
     "не добавлять на вертикальном росте, защищать прибыль"),
)

# Числа с финальных кадров отдельно — их читает экран, а не парсер текста.
SEED_FINAL_FRAME = {
    "price_approx": 82_100.0, "cvd_approx": 63.26e9, "open_interest_approx": 20.17e9,
    "short_liquidations_approx": 28.18e6, "unit_note": "значения сняты с кадра, не с API биржи",
}


def build_seed_claims(*, collected_at: datetime | None = None,
                      asset: str = "BTCUSDT", venue: str = "unknown",
                      timeframe: str = "unknown") -> list[Claim]:
    """Claim'ы затравки.

    venue и timeframe по умолчанию 'unknown' — потому что с кадра их не видно,
    а придумать площадку значит подделать происхождение. Такой claim не
    нормализуется в правило, пока владелец не укажет их явно.
    """
    now = utcnow()
    observed = collected_at or now      # мы наблюдали КАДРЫ, а не рынок
    out: list[Claim] = []
    for index, (claim_type, text) in enumerate(SEED_OBSERVATIONS):
        out.append(Claim(
            claim_type=claim_type, source_id=SEED_SOURCE_ID, video_hash=SEED_VIDEO_HASH,
            timestamp_start=float(index), timestamp_end=float(index) + 1.0,
            asset=asset, venue=venue, timeframe=timeframe,
            market_state="SCREENSHOT_OBSERVED", raw_quote_or_frame_ref=text,
            confidence=0.4,                       # кадр без контекста — низкая уверенность
            extraction_model="manual-screenshot-transcription/v1",
            created_at=now, collected_at=observed,
            verification_status=VerificationStatus.UNVERIFIED,
            contradictions=(),
            allowed_consumers=("analysis_only",),  # в правила напрямую нельзя
            evidence_class=EvidenceClass.MOCK, sanitized=True, injection_flags=()))
    return out


def build_seed_episode(*, collected_at: datetime | None = None) -> Episode:
    """Эпизод без свечей: прогнать его в бэктесте невозможно по построению."""
    observed = collected_at or utcnow()
    return Episode(
        episode_id="ep_k1mba_seed_screenshot", asset="BTCUSDT", venue="unknown",
        timeframe="unknown", decision_time=observed, candles=[],
        claims=build_seed_claims(collected_at=observed), outcome=None,
        evidence_class=EvidenceClass.MOCK, labels=SEED_LABELS)


def seed_report() -> dict:
    """Карточка затравки для экрана и отчёта — с честной оговоркой."""
    return {
        "source_id": SEED_SOURCE_ID, "labels": list(SEED_LABELS),
        "evidence_class": EvidenceClass.MOCK.value,
        "final_frame": dict(SEED_FINAL_FRAME),
        "observations": [{"type": t.value, "text": text} for t, text in SEED_OBSERVATIONS],
        "disclaimer": ("Структурный пример разметки, а не доказательство прибыльности. "
                       "Нет биржи, размера, плеча, фактических филлов, комиссий, funding "
                       "и сырых рыночных данных с метками времени, поэтому LIVE_PROVEN "
                       "запрещён, а бэктест по этому эпизоду невозможен."),
    }
