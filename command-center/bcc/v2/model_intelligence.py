"""Model Intelligence Foundation (spec Part H) — чистая логика, без БД и сети.

Принципы:
- Ничего не выдумываем: отсутствие данных = UNKNOWN (spec §39), никаких
  выдуманных бенчмарков.
- Классификация уровня мышления — ПРОЗРАЧНЫЕ пороги (spec §41), без ML.
- Confidence НИКОГДА не авторизует действия (spec §42) — только рекомендация
  к replan/глубине верификации/эскалации. Gateway/cloud-политика остаётся
  единственным авторитетом маршрутизации.
- Модуль не ходит в БД: строки таблиц `models` / `model_capability_checks`
  передаёт вызывающий код.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

REASONING_LEVELS = ("L0", "L1", "L2", "L3", "L4")
# L0 — детерминированно/без LLM; L1 — лёгкий локальный; L2 — обычный;
# L3 — сильная модель; L4 — мульти-модельная верификация.
CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")
_CAP_KEYS = ("coding", "planning", "tool_use", "structured_output")


@dataclass(slots=True)
class ModelCapabilityRecord:
    """Что мы ЗНАЕМ о модели. capabilities: "UNKNOWN"|"YES"|"NO" — проверенное
    знание; advertised-без-verification остаётся UNKNOWN (не YES)."""
    model_id: str
    provider: str = ""
    local: bool = True
    capabilities: dict[str, str] = field(default_factory=lambda: {
        k: "UNKNOWN" for k in _CAP_KEYS})
    vision: bool = False
    context_window: int | None = None
    cost_class: str = "local"
    latency_class: str = "UNKNOWN"


def _parse_caps(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    if isinstance(raw, (list, tuple, set)):
        return {str(c): True for c in raw}
    return {}


def capability_from_models_row(row: dict) -> ModelCapabilityRecord:
    """Толерантное чтение строки таблицы `models` (context_window, caps JSON).
    Никогда не выдумываем: отсутствующее → UNKNOWN/None. Реклама каталога
    (=True) НЕ превращается в YES — верификация живёт в capability checks."""
    row = row or {}
    caps = _parse_caps(row.get("caps") or row.get("capabilities"))

    def state(cap: str) -> str:
        v = caps.get(cap)
        if isinstance(v, str) and v.upper() in ("YES", "NO", "UNKNOWN"):
            return v.upper()
        if isinstance(v, bool):
            return "UNKNOWN" if v else "NO"   # True = advertised, не verified
        if v:
            return "UNKNOWN"
        return "UNKNOWN" if cap not in caps else "NO"

    cw = row.get("context_window")
    if not isinstance(cw, int) or isinstance(cw, bool) or cw <= 0:
        cw = None
    local = bool(row.get("local", True))
    return ModelCapabilityRecord(
        model_id=str(row.get("model_id") or row.get("id") or row.get("alias") or ""),
        provider=str(row.get("provider") or ""),
        local=local,
        capabilities={k: state(k) for k in _CAP_KEYS},
        vision=bool(caps.get("vision")),
        context_window=cw,
        cost_class=str(row.get("cost_class") or ("local" if local else "cloud")),
        latency_class=str(row.get("latency_class") or "UNKNOWN"))


def capability_from_checks(rows: list[dict]) -> dict[str, str]:
    """Слияние строк model_capability_checks: verified ok → YES, verified fail → NO,
    иначе advertised → UNKNOWN (реклама без пробы знанием не считается).
    Строки применяются по порядку; verified-результат сильнее advertised,
    при конфликте двух verified побеждает поздняя строка."""
    out: dict[str, str] = {}
    rank = {"YES": 2, "NO": 2, "UNKNOWN": 1}
    for row in rows or []:
        row = row or {}
        cap = str(row.get("capability") or "")
        if not cap:
            continue
        verified = row.get("verified")
        if verified is True:
            cand = "YES"
        elif verified is False:
            cand = "NO"
        else:
            cand = "UNKNOWN"   # advertised (даже True) без пробы = UNKNOWN
        prev = out.get(cap)
        if prev is None or rank[cand] >= rank[prev]:
            out[cap] = cand
    return out


@dataclass(slots=True)
class TaskComplexityFeatures:
    dependent_steps: int = 0
    security_impact: float = 0.0
    mutation_impact: float = 0.0
    previous_failures: int = 0
    ambiguity: float = 0.0
    tool_count: int = 0
    requires_verification: bool = False
    code_change_scope: float = 0.0


def classify_reasoning(f: TaskComplexityFeatures) -> tuple[str, list[str]]:
    """Детерминированная эвристика (spec §41), пороги задокументированы:

    - security_impact >= 0.7 ИЛИ mutation_impact >= 0.7 → минимум L3
      (безопасность/мутации требуют сильной модели);
    - requires_verification и previous_failures > 1 → L3;
      requires_verification и previous_failures >= 3 → L4
      (рекомендация мульти-модельной верификации);
    - ambiguity >= 0.6 ИЛИ dependent_steps >= 4 ИЛИ code_change_scope >= 0.6
      ИЛИ tool_count >= 3 → минимум L2 (обычная модель);
    - «тривиально» (0 зависимостей, 0 мутаций, tools <= 1, без верификации,
      security/mutation/ambiguity ниже порогов) → L0, либо L1 если есть
      изменения кода или недавние неудачи;
    - иначе базовый уровень L2.

    Возвращает (уровень, причины). Чистая функция, без ML и без сети.
    """
    reasons: list[str] = []
    floor = 1  # базовый уровень — L1 (lightweight-local)
    if f.security_impact >= 0.7:
        floor = max(floor, 3)
        reasons.append(f"security_impact {f.security_impact:.2f} >= 0.7 -> at least L3")
    if f.mutation_impact >= 0.7:
        floor = max(floor, 3)
        reasons.append(f"mutation_impact {f.mutation_impact:.2f} >= 0.7 -> at least L3")
    if f.requires_verification and f.previous_failures >= 3:
        floor = max(floor, 4)
        reasons.append("verification required after repeated failures -> L4 recommended")
    elif f.requires_verification and f.previous_failures > 1:
        floor = max(floor, 3)
        reasons.append("verification required after >1 failures -> at least L3")
    if f.ambiguity >= 0.6:
        floor = max(floor, 2)
        reasons.append(f"ambiguity {f.ambiguity:.2f} >= 0.6 -> at least L2")
    if f.dependent_steps >= 4:
        floor = max(floor, 2)
        reasons.append(f"{f.dependent_steps} dependent steps -> at least L2")
    if f.code_change_scope >= 0.6:
        floor = max(floor, 2)
        reasons.append(f"code_change_scope {f.code_change_scope:.2f} >= 0.6 -> at least L2")
    if f.tool_count >= 3:
        floor = max(floor, 2)
        reasons.append(f"tool_count {f.tool_count} >= 3 -> at least L2")

    trivial = (f.dependent_steps == 0 and f.mutation_impact < 0.7
               and f.security_impact < 0.7 and f.tool_count <= 1
               and not f.requires_verification and f.ambiguity < 0.6
               and f.code_change_scope < 0.6 and f.previous_failures == 0)
    if trivial and floor <= 1:
        level = "L0" if (f.code_change_scope == 0 and f.previous_failures == 0
                         and f.ambiguity == 0.0) else "L1"
        reasons.append("trivial: no deps, no mutation, <=1 tool -> "
                       + ("L0" if level == "L0" else "L1"))
        return level, reasons
    if not reasons:
        reasons.append("no escalation triggers -> baseline L2")
    return REASONING_LEVELS[floor], reasons


def classify_reasoning_level(f: TaskComplexityFeatures) -> str:
    return classify_reasoning(f)[0]


@dataclass(slots=True)
class Confidence:
    """Уровень доверия (spec §42). НИКОГДА не авторизует действия и не обходит
    policy: может лишь информировать replan, глубину верификации и рекомендацию
    эскалации. Gateway/cloud-policy остаётся единственным авторитетом."""
    level: str
    value: float | None = None
    basis: str = ""

    def __post_init__(self) -> None:
        if self.level not in CONFIDENCE_LEVELS:
            raise ValueError(
                f"confidence level must be one of {CONFIDENCE_LEVELS}, got {self.level!r}")


def recommend_escalation(confidence: Confidence, level: str) -> bool | None:
    """РЕКОМЕНДАЦИЯ эскалации (None = неизвестно) — и только.

    Это НЕ авторизация и НЕ маршрут: функция не может отправить задачу в облако
    и не обходит Gateway/cloud-policy — они остаются авторитетными. Она лишь
    предлагает человеку/политике рассмотреть эскалацию.
    """
    if confidence.level == "UNKNOWN":
        return None
    if confidence.level == "LOW":
        return True
    if confidence.level == "HIGH":
        return False
    # MEDIUM: на сильных уровнях (L3+) рекомендация уместна, иначе — неизвестно
    if level in REASONING_LEVELS and REASONING_LEVELS.index(level) >= 3:
        return True
    return None


@dataclass(slots=True)
class ModelScorecardEvent:
    """Evidence-сбор (spec §43): фактический результат прогона модели на классе
    задач. Оценки не выдумываются — только измеренные значения."""
    model: str
    task_class: str
    reasoning_level: str
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    structured_output_valid: bool | None = None
    tool_call_valid: bool | None = None
    retries: int = 0
    task_success: bool | None = None
    verification_result: str = "UNKNOWN"   # UNKNOWN — валидное значение (spec §39)

    def to_dict(self) -> dict:
        return asdict(self)
