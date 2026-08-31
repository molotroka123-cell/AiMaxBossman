"""V2.6 — Failure Pattern Learner (модуль C): извлечение паттернов над
КАНОНИЧЕСКОЙ failure memory.

НЕ Failure Memory V2: пишет и читает та же таблица `failures`
(bossman/failure_memory.py). Здесь три вещи:
1) `classify_error` — детерминированная классификация симптома в error_class
   (до этого runner писал вырожденное "task_failed" на всё);
2) `extract_patterns` — консервативные кластеры сигнатур над ЗАВЕРШЁННЫМИ
   исходами: паттерн существует только при >= MIN_PATTERN_EPISODES эпизодах,
   свежих (decay) и с совпадающим окружением. Один эпизод НИКОГДА не рождает
   паттерн;
3) `recommended_recovery` — advisory-стратегия из паттернов. ТОЛЬКО
   рекомендация (proposal-only): применение стратегии остаётся за существующими
   recovery-контурами и владельцем; durable-продвижение выученной стратегии
   обязано пройти Learning Quality Guard (secret holdout уже отфильтрован на
   записи runner'ом; здесь фильтруем ещё раз — защита в глубину).
"""
from __future__ import annotations

import re
import time as _time
from dataclasses import dataclass, field

MIN_PATTERN_EPISODES = 3          # никаких выводов из 1-2 эпизодов
PATTERN_MAX_AGE_DAYS = 45.0       # decay: старые эпизоды не образуют паттерн

# Порядок важен: первый матч выигрывает (от специфичного к общему).
_CLASS_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("timeout", re.compile(r"превышен timeout|timed? ?out|deadline", re.I)),
    ("budget_steps", re.compile(r"превышен max_steps", re.I)),
    ("budget_tokens", re.compile(r"превышен max_tokens|token limit", re.I)),
    ("cloud_denied", re.compile(r"облако отклонен|отправка в облаке?|cloud_policy|CloudDenied", re.I)),
    ("approval_rejected", re.compile(r"отклонено пользователем|rejected", re.I)),
    ("network", re.compile(r"connect|network|dns|refused|reset|unreachable|502|503|504", re.I)),
    ("tool_error", re.compile(r"ошибка [a-z_.]+:|нет такого инструмента|PolicyDenied", re.I)),
    ("loop_error", re.compile(r"ошибка петли", re.I)),
)


def classify_error(symptom: str) -> str:
    """Детерминированный error_class по тексту симптома; unknown — честный
    остаток, а не свалка."""
    text = symptom or ""
    for name, rx in _CLASS_RULES:
        if rx.search(text):
            return name
    return "task_failed"  # прежнее имя сохраняем как «прочее» — без ломки данных


@dataclass(frozen=True, slots=True)
class FailurePattern:
    signature: str                  # error_class + ключи окружения
    error_class: str
    episodes: int
    environment: dict = field(default=None)  # общее окружение кластера
    successful_recovery: str | None = None   # стратегия, если ДОКАЗАНА (см. ниже)
    evidence_refs: tuple[str, ...] = ()


def _signature(error_class: str, environment: dict | None) -> str:
    env = environment or {}
    agent = str(env.get("agent") or "?")
    return f"{error_class}|agent={agent}"


def _fresh(created_at, *, now: float | None = None) -> bool:
    if created_at is None:
        return False
    ts = created_at.timestamp() if hasattr(created_at, "timestamp") else float(created_at)
    age_days = ((now or _time.time()) - ts) / 86400.0
    return age_days <= PATTERN_MAX_AGE_DAYS


def _holdout_filtered(records: list) -> list:
    """Защита в глубину: эпизоды holdout-задач не участвуют в обучении, даже
    если каким-то путём попали в таблицу."""
    try:
        from .learning_guard import get_holdout
        h = get_holdout()
        if h is None:
            return records
        return [r for r in records if not h.is_holdout(str(getattr(r, "task_id", "")))]
    except Exception:  # noqa: BLE001
        return records


def extract_patterns(records: list, *, now: float | None = None) -> list[FailurePattern]:
    """Кластеры сигнатур над записями failure memory (FailureRecord-совместимые
    объекты: .task_id .error_class .environment .created_at .resolved
    .attempted_fix .result).

    Консервативно: паттерн — только >= MIN_PATTERN_EPISODES СВЕЖИХ эпизодов
    одной сигнатуры. `successful_recovery` заполняется, только если >=
    MIN_PATTERN_EPISODES РАЗРЕШЁННЫХ эпизодов имеют ОДИНАКОВЫЙ attempted_fix —
    одна удача не делает стратегию.
    """
    records = _holdout_filtered(records)
    clusters: dict[str, list] = {}
    for r in records:
        if not _fresh(getattr(r, "created_at", None), now=now):
            continue
        sig = _signature(getattr(r, "error_class", "") or "",
                         getattr(r, "environment", None))
        clusters.setdefault(sig, []).append(r)

    out: list[FailurePattern] = []
    for sig, rs in sorted(clusters.items()):
        if len(rs) < MIN_PATTERN_EPISODES:
            continue                       # мало эпизодов — паттерна нет
        fixes: dict[str, int] = {}
        for r in rs:
            if getattr(r, "resolved", False) and (getattr(r, "attempted_fix", "") or "").strip():
                fixes[r.attempted_fix.strip()] = fixes.get(r.attempted_fix.strip(), 0) + 1
        recovery = None
        if fixes:
            best, n = max(fixes.items(), key=lambda kv: kv[1])
            if n >= MIN_PATTERN_EPISODES:  # стратегия доказана многократно
                recovery = best
        out.append(FailurePattern(
            signature=sig, error_class=rs[0].error_class, episodes=len(rs),
            environment=getattr(rs[0], "environment", None),
            successful_recovery=recovery,
            evidence_refs=tuple(getattr(r, "failure_id", "") for r in rs[:10])))
    return out


def recommended_recovery(error_class: str, environment: dict | None,
                         patterns: list[FailurePattern]) -> FailurePattern | None:
    """Advisory: паттерн ТОЧНО той же сигнатуры (класс + окружение) с доказанной
    стратегией. Чужое окружение не переносится (wrong-environment transfer)."""
    sig = _signature(error_class, environment)
    for p in patterns:
        if p.signature == sig and p.successful_recovery:
            return p
    return None


async def patterns_for_agent(agent: str, *, limit: int = 200) -> list[FailurePattern]:
    """Паттерны из живой failure memory (канонический read-API, не второй
    движок). Ошибка БД → пустой список, вызывающий не падает."""
    try:
        from . import failure_memory
        records = await failure_memory.query_failures(limit=limit)
        records = [r for r in records
                   if (getattr(r, "environment", None) or {}).get("agent") == agent]
        return extract_patterns(records)
    except Exception:  # noqa: BLE001
        return []
