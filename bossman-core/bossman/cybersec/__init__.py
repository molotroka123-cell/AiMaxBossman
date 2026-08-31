"""CyberSec AI V1 — защитный слой ПОВЕРХ существующих авторитетов Bossman.

Слой, а не вторая система: Policy/Approval/Memory/EventBus/Verifier/ToolRegistry/
Computer Control/Recovery остаются каноничными. Модули здесь только УЖЕСТОЧАЮТ
решения и добавляют детекцию; ни один из них не выдаёт разрешений.

Всё ВЫКЛЮЧЕНО по умолчанию (`BOSSMAN_CYBERSEC_V1_ENABLED`), а тренировочная
лаборатория дополнительно заморожена тройным гейтом.
"""
from __future__ import annotations

from . import (
    benchmark, blast_radius, defender, evidence, gates, ids, injection,
    learning, recovery, redteam, repo_scanner, secret_guardian, security_memory,
    supply_chain, training, trust,
)

__all__ = [
    "benchmark", "blast_radius", "defender", "evidence", "gates", "ids",
    "injection", "learning", "recovery", "redteam", "repo_scanner",
    "secret_guardian", "security_memory", "supply_chain", "training", "trust",
]

#: Карта «модуль CyberSec V1 → существующий авторитет, который он усиливает».
LAYERED_OVER = {
    "injection": "context ingest boundary (untrusted text never gains authority)",
    "ids": "EventBus / audit signals (raises required confidence, never grants)",
    "secret_guardian": "bossman.obs redact (canonical) + egress checks",
    "repo_scanner": "tools/ci_secret_scan.py (canonical secret authority)",
    "blast_radius": "Policy decision (can only tighten, never loosen)",
    "supply_chain": "Tool Registry admission (gate placed before registration)",
    "recovery": "Computer Operator recovery / Recovery Kernel",
    "security_memory": "canonical failure_memory (Postgres) — no second store",
    "benchmark": "security-specific metrics for the existing benchmark path",
    "redteam": "typed AttackIntent only — never shell/secrets/network",
    "training": "FROZEN red-vs-blue harness behind a triple gate",
}
