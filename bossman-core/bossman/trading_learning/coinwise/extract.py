"""Извлечение значений: сперва DOM, потом локальный OCR, облако — никогда по умолчанию.

Порядок продиктован не только ценой, хотя и ею тоже. DOM отдаёт ЧИСЛО — ровно
то, что нарисовал сайт. OCR отдаёт ДОГАДКУ о числе по картинке, и догадка тем
хуже, чем мельче шрифт и пестрее фон. Поэтому OCR включается только там, где
DOM промолчал, а не «для надёжности» поверх него: два источника на одно поле
означали бы, что кто-то однажды выберет из них тот, что нравится.

Облачное зрение выключено константой, а не настройкой. Скриншот дашборда — это
позиции владельца, его баланс и его биржа; отправлять такое наружу можно только
по отдельному явному решению человека, и решение это принимается не здесь.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from ..adapters import Capability, probe_ocr
from .schema import (MARKET_FIELDS, FieldValue, LiquidityZone, SourceMethod,
                     measured, unknown)

# Облачное зрение. Не флаг окружения: чтобы включить, нужно менять код и
# отвечать за это глазами человека, а не опечаткой в .env.
CLOUD_VISION_DEFAULT = False
CLOUD_VISION_ENV = "COINWISE_CLOUD_VISION"

# Как поле называется на дашборде. Ключ — наше имя, значения — то, чем сайт
# может его подписать. Список закрытый: неизвестная подпись не превращается в
# известное поле «по похожести».
FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "price": ("price", "last", "цена"),
    "cvd": ("cvd", "cumulative volume delta", "кумулятивная дельта"),
    "open_interest": ("open interest", "oi", "aggregated oi", "открытый интерес"),
    "liquidations": ("liquidations", "liqs", "ликвидации"),
    "buy_volume": ("buy volume", "buy vol", "покупки"),
    "sell_volume": ("sell volume", "sell vol", "продажи"),
    "daily_open": ("daily open", "day open", "открытие дня"),
    "vah": ("vah", "value area high"),
    "val": ("val", "value area low"),
    "vpoc": ("vpoc", "volume poc"),
    "tpoc": ("tpoc", "time poc"),
    "vwap": ("vwap",),
    "tvah": ("tvah",),
    "tval": ("tval",),
    "prev_day_high": ("prev day high", "pdh", "previous day high"),
    "prev_day_low": ("prev day low", "pdl", "previous day low"),
}

# Число на дашборде: 105 234.5 / 105,234.50 / -1.2M / +3.4K / 1.2B / 12%
_NUM = re.compile(
    r"(?P<sign>[+-])?\s*(?P<body>\d{1,3}(?:[  ,]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<suffix>[KMB]|тыс|млн|млрд)?", re.I)
_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9, "тыс": 1e3, "млн": 1e6, "млрд": 1e9}
# Символы, которые OCR путает чаще всего. Их присутствие снижает доверие, а не
# «исправляется» подстановкой: подставить O вместо 0 — это выдумать цифру.
_OCR_CONFUSABLE = re.compile(r"[OoIlSB|]")


def cloud_vision_enabled() -> bool:
    """Облако включено, только если владелец сказал это явно И код разрешает."""
    if not CLOUD_VISION_DEFAULT:
        return False
    return os.environ.get(CLOUD_VISION_ENV, "").strip().lower() in ("1", "true", "yes")


def parse_number(raw: str) -> float | None:
    """Число из подписи. None — прочитать не удалось, и это законный исход."""
    text = str(raw or "").strip()
    if not text:
        return None
    match = _NUM.search(text)
    if match is None:
        return None
    body = match.group("body").replace(" ", "").replace(" ", "").replace(",", "")
    try:
        value = float(body)
    except ValueError:
        return None
    suffix = (match.group("suffix") or "").lower()
    value *= _SUFFIX.get(suffix, 1.0)
    if match.group("sign") == "-":
        value = -value
    return value


def normalize_label(raw: str) -> str:
    return re.sub(r"[^a-zа-я ]+", " ", str(raw or "").lower()).strip()


def field_for_label(raw: str) -> str | None:
    """Наше имя поля по подписи с дашборда. None — подпись незнакомая.

    Сопоставление точное по нормализованной подписи, а не «содержит»: подстрока
    `val` живёт внутри `value area high`, и нестрогое совпадение однажды
    запишет VAH в VAL. Такую ошибку по числу не заметить.
    """
    label = normalize_label(raw)
    if not label:
        return None
    for name, variants in FIELD_LABELS.items():
        if any(label == normalize_label(v) for v in variants):
            return name
    return None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Что удалось снять и чем. Пустые поля перечислены явно."""

    fields: dict[str, FieldValue]
    liquidity_zones: tuple[LiquidityZone, ...]
    method: SourceMethod
    dashboard_state: str = "UNKNOWN"
    stream_state: str = "UNKNOWN"
    symbol: str = ""
    venue: str = ""
    timeframe: str = ""
    notes: tuple[str, ...] = ()

    @property
    def known_count(self) -> int:
        return sum(1 for v in self.fields.values() if v.known)


def _blank_fields(note: str) -> dict[str, FieldValue]:
    return {name: unknown(note) for name in MARKET_FIELDS}


def from_dom(payload: dict[str, Any]) -> ExtractionResult:
    """Разбор снимка DOM/дерева доступности открытой владельцем вкладки.

    Вход — уже снятая структура, а не живая страница: ходить в браузер отсюда
    нельзя, этим занимается вызывающий, у которого есть разрешение владельца.
    """
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, dict):
        raise ValueError("снимок DOM без раздела fields")

    fields = _blank_fields("на дашборде не найдено")
    notes: list[str] = []
    for label, raw_value in raw_fields.items():
        name = field_for_label(label)
        if name is None:
            notes.append(f"незнакомая подпись: {str(label)[:40]}")
            continue
        value = parse_number(raw_value if isinstance(raw_value, str) else str(raw_value))
        if value is None:
            fields[name] = unknown(f"подпись есть, число не читается: {str(raw_value)[:40]}")
            continue
        # DOM отдаёт то, что нарисовал сайт: гадать здесь не о чем.
        fields[name] = measured(value, SourceMethod.DOM, 1.0,
                                raw_text=str(raw_value)[:120])

    zones = tuple(
        LiquidityZone(low=float(z["low"]), high=float(z["high"]),
                      side=str(z.get("side") or ""), strength=float(z.get("strength") or 0.0),
                      method=SourceMethod.DOM, confidence=1.0)
        for z in (payload.get("liquidity_zones") or [])
        if isinstance(z, dict) and z.get("low") is not None and z.get("high") is not None)

    method = (SourceMethod.ACCESSIBILITY_TREE
              if str(payload.get("source") or "").lower() in ("a11y", "accessibility", "ax")
              else SourceMethod.DOM)
    return ExtractionResult(
        fields=fields, liquidity_zones=zones, method=method,
        dashboard_state=str(payload.get("dashboard_state") or "UNKNOWN"),
        stream_state=str(payload.get("stream_state") or "UNKNOWN"),
        symbol=str(payload.get("symbol") or ""), venue=str(payload.get("venue") or ""),
        timeframe=str(payload.get("timeframe") or ""), notes=tuple(notes[:10]))


def ocr_capability() -> Capability:
    """Есть ли локальный OCR. Ответ проверкой, а не верой в requirements."""
    return probe_ocr()


def from_ocr(lines: list[dict[str, Any]], *, capability: Capability | None = None
             ) -> ExtractionResult:
    """Разбор строк локального OCR: `{"label": …, "text": …, "confidence": …}`.

    Уверенность приходит от движка и здесь только ограничивается сверху
    потолком метода. Дополнительно снижается там, где в тексте есть символы,
    которые OCR путает: `0`/`O`, `1`/`l`, `5`/`S`. Исправлять их подстановкой
    запрещено — это и есть «выдумать цифру со скриншота».
    """
    cap = capability or ocr_capability()
    if not cap.available:
        return ExtractionResult(fields=_blank_fields(f"локального OCR нет: {cap.detail}"),
                                liquidity_zones=(), method=SourceMethod.LOCAL_OCR,
                                notes=(f"BLOCKED: {cap.detail}",))

    fields = _blank_fields("на дашборде не найдено")
    notes: list[str] = []
    for row in lines or []:
        if not isinstance(row, dict):
            continue
        name = field_for_label(row.get("label"))
        if name is None:
            continue
        text = str(row.get("text") or "")
        value = parse_number(text)
        if value is None:
            fields[name] = unknown(f"OCR не разобрал число: {text[:40]}")
            continue
        score = float(row.get("confidence") or 0.0)
        if _OCR_CONFUSABLE.search(text):
            # Не «поправляем», а снижаем доверие: спорный символ в числе — повод
            # усомниться во всём числе, а не заменить его на правдоподобное.
            score *= 0.7
            notes.append(f"{name}: спорные символы в {text[:20]!r}")
        fields[name] = measured(value, SourceMethod.LOCAL_OCR, score, raw_text=text[:120])

    return ExtractionResult(fields=fields, liquidity_zones=(), method=SourceMethod.LOCAL_OCR,
                            notes=tuple(notes[:10]))


def merge(primary: ExtractionResult, fallback: ExtractionResult) -> ExtractionResult:
    """DOM плюс OCR ТОЛЬКО там, где DOM промолчал.

    Перебивать прочитанное из DOM догадкой по картинке нельзя ни при какой
    уверенности OCR: это ровно тот случай, когда система «уточняет» верное
    значение неверным.
    """
    fields = dict(primary.fields)
    for name, value in fallback.fields.items():
        if not fields.get(name, unknown("")).known and value.known:
            fields[name] = value
    return ExtractionResult(
        fields=fields,
        liquidity_zones=primary.liquidity_zones or fallback.liquidity_zones,
        method=primary.method,
        dashboard_state=primary.dashboard_state or fallback.dashboard_state,
        stream_state=primary.stream_state or fallback.stream_state,
        symbol=primary.symbol or fallback.symbol,
        venue=primary.venue or fallback.venue,
        timeframe=primary.timeframe or fallback.timeframe,
        notes=tuple((*primary.notes, *fallback.notes))[:12])


__all__ = ["CLOUD_VISION_DEFAULT", "CLOUD_VISION_ENV", "FIELD_LABELS", "ExtractionResult",
           "cloud_vision_enabled", "parse_number", "field_for_label", "normalize_label",
           "from_dom", "from_ocr", "ocr_capability", "merge"]
