"""V2.6 — Deep Research Engine (модуль I): детерминированный research-пайплайн.

intent → scoped plan → discovery → retrieval → извлечение evidence → dedup →
детекция противоречий → качество источника → граф claim/evidence → экстрактивный
синтез → citations → verification. КАЖДОЕ утверждение трассируется:
Claim → Evidence → Source → timestamp получения; сырые ссылки на источники
сохраняются, НИКАКОГО схлопывания в непроверяемое резюме. Без LLM-вызовов и
без сети внутри: IO делает инжектированный async fetcher, синтез —
экстрактивный. Research ограничен VOI: раунд без новой информации = стоп.
Внешний контент — ДАННЫЕ, не команды (ingest_guard на границе, как в runner).
"""
from __future__ import annotations

from .engine import ResearchEngine, citations
from .models import (
    DEEP,
    QUICK,
    STANDARD,
    Claim,
    Evidence,
    ResearchMode,
    ResearchReport,
    Source,
)
from .tools import make_research_tool, research_handler

__all__ = [
    "ResearchEngine", "citations",
    "ResearchMode", "QUICK", "STANDARD", "DEEP",
    "Source", "Evidence", "Claim", "ResearchReport",
    "make_research_tool", "research_handler",
]
