"""Bossman Cognitive 10/10 — замкнутые контуры памяти, контекста, reasoning, длинных задач.

Замкнутый контур каждого модуля::

    Наблюдение → проверка → сохранение → использование
         ↑                              ↓
         └──── измерение результата ←───┘

Каждое улучшение обязано доказать рост ``VerifiedSuccess`` без ухудшения
безопасности и без необоснованного роста расходов (см. ``verify.py``).

Состав пакета::

    storage.py   — SQLite-хранилище, часы, хэши, tombstones.
    memory.py    — 5 уровней, фильтр записи, R-формула, конфликты, забывание.
    context.py   — Context Compiler P0-P4, critical-fact ledger, сжатие, fallback.
    reasoning.py — Thought State, D-оценка, режимы, multi-hypothesis, stop, Fable EV.
    tasks.py     — Durable Task Journal, DAG, checkpoint, resume, revalidation.
    verify.py    — метрики 10/10, A/B, holdout, независимый verifier, cost accounting.
    runtime.py   — единый runtime, wiring, telemetry, restart.

Пакет намеренно зависит только от stdlib, поэтому работает без Postgres/Redis
и не ломает существующий ``bossman.context_engine``. Проводка к Postgres,
``HybridRetriever``, ``WorkingMemory``, gateway и Fable описана в
``docs/cognitive-10-10/06-INTEGRATION-GUIDE.md`` и реализована адаптерами
в ``runtime.py`` (интерфейсы, а не жёсткие импорты — чтобы аудит мог
подключить их отдельно).
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = [
    "storage",
    "memory",
    "context",
    "reasoning",
    "tasks",
    "verify",
    "runtime",
]
