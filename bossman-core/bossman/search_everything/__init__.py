"""Этап 5 — Search Everything (единый поиск поверх context_engine).

НЕ второй RAG: connectors → Ingestor (chunking) → ContextStore (единый
SQLite/WAL индекс) → HybridRetriever (lexical + vector + rerank + dedup +
sensitivity allow-list). Секреты не индексируются (SecretPolicy) и не выдаются
без права (sensitivity gate). Интеграция с ядром — по импорту: api.py лениво
вызывает build_subsystem() и подключает router.
"""
from __future__ import annotations

from .connectors import (
    SecretPolicy,
    filesystem_documents,
    history_documents,
    memory_documents,
)
from .engine import (
    DEFAULT_ALLOW,
    SafeReranker,
    SearchDocument,
    SearchEngine,
    SearchHit,
    to_search_hit,
)
from .router import router
from .service import (
    SearchService,
    build_subsystem,
    get_active_service,
    get_service,
    set_active_service,
)
from .tools import register_tools

__all__ = [
    # форма результата (floor acceptance-теста)
    "SearchEngine", "SearchDocument", "SearchHit",
    # адаптер/утилиты
    "SafeReranker", "to_search_hit", "DEFAULT_ALLOW", "fuse",
    # коннекторы + гейт секретов
    "SecretPolicy", "filesystem_documents", "memory_documents", "history_documents",
    # подсистема + интеграция
    "SearchService", "build_subsystem", "get_service", "set_active_service",
    "get_active_service", "register_tools", "router",
]

# Утилита RRF-слияния как модульный атрибут (совместимость с прототипом).
fuse = SearchEngine.fuse
