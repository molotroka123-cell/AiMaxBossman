"""V2.6 модуль I — типизированные модели research-пайплайна.

Ключевой инвариант provenance: Claim держит tuple Evidence, Evidence держит
Source + excerpt + sha256 + timestamp получения. Утверждение без evidence не
существует как Claim — оно уходит в ResearchReport.unanswered. Режимы жёстко
ограничивают стоимость (источники × раунды); DEEP никогда не выбирается
автоматически — только явно.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ResearchMode:
    """Бюджет research-режима: потолок источников за раунд и раундов всего."""
    name: str
    max_sources: int
    max_rounds: int


QUICK = ResearchMode("quick", max_sources=3, max_rounds=1)
STANDARD = ResearchMode("standard", max_sources=6, max_rounds=2)
DEEP = ResearchMode("deep", max_sources=12, max_rounds=4)   # только явный выбор

#: Резолв по имени. DEEP присутствует, но default-путей к нему нет нигде.
MODES: dict[str, ResearchMode] = {m.name: m for m in (QUICK, STANDARD, DEEP)}


@dataclass(slots=True)
class Source:
    """Источник (web/file/local). trust ∈ [0,1] — априорное доверие; внешний
    текст источника при любом trust остаётся ДАННЫМИ, не инструкциями."""
    url_or_ref: str
    kind: str = "web"             # web | file | local
    trust: float = 0.5
    retrieved_at: float | None = None   # ставится движком после успешного fetch


@dataclass(frozen=True, slots=True)
class Evidence:
    """Единица evidence: дословный excerpt + provenance (источник, sha256
    excerpt'а, момент получения). Никогда не пересказ."""
    source: Source
    excerpt: str
    retrieved_at: float
    content_hash: str             # sha256(excerpt) hex — проверяемая целостность


@dataclass(frozen=True, slots=True)
class Claim:
    """Утверждение = экстрактивный текст + непустой tuple Evidence.
    confidence = f(число подтверждений, средний trust источников);
    contradicted — evidence этого claim участвует в противоречии."""
    text: str
    evidence: tuple[Evidence, ...]
    confidence: float
    contradicted: bool = False


@dataclass(frozen=True, slots=True)
class ResearchReport:
    """Итог research-прогона. rounds_used < mode.max_rounds означает ранний
    VOI-стоп (раунд не принёс новой информации — продолжать нерационально)."""
    question: str
    mode: ResearchMode
    claims: tuple[Claim, ...]
    sources: tuple[Source, ...]          # только УСПЕШНО полученные источники
    contradictions: tuple[str, ...]
    unanswered: tuple[str, ...]
    rounds_used: int
    fetch_errors: tuple[str, ...] = ()   # ошибки fetch — записаны, не фатальны
