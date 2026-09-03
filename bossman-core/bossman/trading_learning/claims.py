"""Из сегмента материала — в типизированный claim. Детерминированно.

Почему правила, а не «спросим модель»: экстрактор — это место, где чужой текст
превращается в объект, влияющий на торговые решения. Детерминированный
экстрактор проверяется тестом и не подвержен инъекции в промпт. Модель здесь
может лишь ДОПОЛНИТЬ разметку (extraction_model проставляется явно), но базовая
типизация и карантин обязаны работать без неё.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .models import Claim, ClaimType, VerificationStatus
from .safety import EvidenceClass, utcnow
from .sanitize import sanitize

# Маркеры типов. Порядок важен: более специфичное правило проверяется раньше,
# иначе «стоп под уровнем» уедет в общий RISK_RULE.
_RULES: tuple[tuple[ClaimType, re.Pattern[str]], ...] = (
    (ClaimType.INVALIDATION, re.compile(
        r"(инвалидац|отмена\s+сценар|сетап\s+сломан|если\s+.*\s+то\s+идея\s+не\s+работает|"
        r"invalidat|setup\s+is\s+dead)", re.I)),
    (ClaimType.EXIT_CONDITION, re.compile(
        r"(выход|фиксир|закрыва|тейк|take\s*profit|частичн(ую|ая)\s+фиксац|exit|close\s+the\s+position)", re.I)),
    (ClaimType.RISK_RULE, re.compile(
        r"(стоп|stop\s*loss|риск|не\s+усредн|не\s+добавля|защища(ть|ем)\s+прибыль|position\s+risk)", re.I)),
    (ClaimType.POSITION_MANAGEMENT, re.compile(
        r"(добавля|усредн|наращива|перевод\s+в\s+безубыт|breakeven|scale\s+in|add\s+to\s+the\s+position)", re.I)),
    (ClaimType.ENTRY_CONDITION, re.compile(
        r"(вход|захожу|беру\s+от|покупаю\s+от|entry|enter\s+(long|short)|набира(ю|ем)\s+позиц)", re.I)),
    (ClaimType.MARKET_OBSERVATION, re.compile(
        r"(cvd|открыт(ый|ого)\s+интерес|open\s+interest|\boi\b|ликвидац|liquidation|"
        r"объ[её]м|volume|зона\s+спроса|уровень|value\s+area|poc)", re.I)),
    (ClaimType.EXPECTED_OUTCOME, re.compile(
        r"(думаю\s+пойд[её]м|цель\s+\d|ожида(ю|ем)\s+рост|ожида(ю|ем)\s+паден|target\s+\d|"
        r"should\s+go\s+(up|down))", re.I)),
    (ClaimType.RETROSPECTIVE_COMMENTARY, re.compile(
        r"(как\s+я\s+и\s+говорил|как\s+видите|в\s+итоге\s+вышло|получилось\s+как|"
        r"as\s+i\s+said|told\s+you|in\s+hindsight|оказалось\s+что)", re.I)),
    (ClaimType.HYPOTHESIS, re.compile(
        r"(гипотез|предполож|возможно|скорее\s+всего|hypothes|probably|maybe)", re.I)),
)

_PRICE = re.compile(r"(\d{2,3}[.,]\d{1,2})\s*[kк]\b|\b(\d{4,7})(?:[.,]\d+)?\b")
_TIMEFRAME = re.compile(r"\b(1m|3m|5m|15m|30m|1h|2h|4h|1d|1w|м1|м5|м15|н1|ч1|ч4|д1)\b", re.I)


@dataclass(frozen=True, slots=True)
class Segment:
    """Кусок материала с временными метками. Вход экстрактора."""

    start: float
    end: float
    text: str
    frame_ref: str = ""
    channel: str = "transcript"     # transcript | subtitles | chat | overlay | ocr


# Каналы, которые по своей природе читают ГРАФИК, а не мнение автора: OCR и
# оверлей показывают числа с экрана. Отнести их к AUTHOR_CLAIM значит объявить
# распознанную цену «мнением» и вывести её из-под проверки данными — ровно та
# дыра, через которую поддельная цена из OCR попадает в анализ непроверенной.
_CHART_CHANNELS = frozenset({"ocr", "overlay"})


def _classify(text: str, channel: str = "transcript") -> ClaimType:
    for claim_type, pattern in _RULES:
        if pattern.search(text):
            return claim_type
    if (channel or "").strip().lower() in _CHART_CHANNELS:
        return ClaimType.MARKET_OBSERVATION
    # Ничего не опознали — это мнение автора, а не наблюдение рынка.
    return ClaimType.AUTHOR_CLAIM


def parse_prices(text: str) -> list[float]:
    """Числа-цены из текста. Нужны верификатору, чтобы сверить с рынком."""
    found: list[float] = []
    for m in _PRICE.finditer(text):
        if m.group(1):
            found.append(float(m.group(1).replace(",", ".")) * 1000.0)
        elif m.group(2):
            value = float(m.group(2))
            if 100.0 <= value <= 10_000_000.0:
                found.append(value)
    return found


def extract_claims(segments: list[Segment], *, source_id: str, video_hash: str,
                   asset: str, venue: str, timeframe: str,
                   extraction_model: str = "deterministic-rules/v1",
                   collected_at: datetime | None = None,
                   market_state: str = "unknown") -> list[Claim]:
    """Сегменты → claim'ы. Подозрительный текст сразу уходит в карантин.

    collected_at — время, когда наблюдение было СДЕЛАНО (дата стрима), а не
    время запуска экстрактора. Путаница этих двух дат — типовой способ выдать
    устаревшее наблюдение за свежее.
    """
    now = utcnow()
    observed = collected_at or now
    out: list[Claim] = []
    for seg in segments:
        clean = sanitize(seg.text)
        if not clean.text:
            continue
        claim_type = _classify(clean.text, seg.channel)
        status = (VerificationStatus.QUARANTINED if clean.must_quarantine
                  else VerificationStatus.UNVERIFIED)
        # Уверенность падает от одного факта подозрительности: даже безобидная
        # реклама означает, что фрагмент не про рынок.
        confidence = 0.2 if clean.suspicious else 0.6
        tf = timeframe
        tf_match = _TIMEFRAME.search(clean.text)
        if tf_match:
            tf = tf_match.group(1).lower()
        consumers = ("analysis",) if claim_type is not ClaimType.AUTHOR_CLAIM else ("analysis_only",)
        out.append(Claim(
            claim_type=claim_type, source_id=source_id, video_hash=video_hash,
            timestamp_start=seg.start, timestamp_end=seg.end, asset=asset, venue=venue,
            timeframe=tf, market_state=market_state,
            raw_quote_or_frame_ref=seg.frame_ref or clean.text[:280],
            confidence=confidence, extraction_model=extraction_model,
            created_at=now, collected_at=observed, verification_status=status,
            contradictions=(), allowed_consumers=consumers,
            evidence_class=EvidenceClass.MOCK, sanitized=True,
            injection_flags=clean.flags))
    return out


def dedupe(claims: list[Claim], *, window: float = 3.0) -> list[Claim]:
    """Дедупликация повторов транскрипта: одна мысль — один claim.

    Экономия токенов начинается здесь, а не в промпте: повторённая трижды фраза
    не должна трижды попасть в контекст сильной модели.
    """
    seen: dict[tuple[str, str], float] = {}
    out: list[Claim] = []
    for claim in sorted(claims, key=lambda c: c.timestamp_start):
        key = (claim.claim_type.value, claim.raw_quote_or_frame_ref.strip().lower()[:120])
        last = seen.get(key)
        if last is not None and claim.timestamp_start - last <= window:
            continue
        seen[key] = claim.timestamp_start
        out.append(claim)
    return out
