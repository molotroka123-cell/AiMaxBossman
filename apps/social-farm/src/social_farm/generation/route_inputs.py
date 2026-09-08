"""Откуда маршрутизатор берёт числа — и что делает, когда их нет.

`media_router` умеет ранжировать пути, но числа для ранжирования до сих пор
приносил вызывающий. `safe_available_memory_gb=64.0` он мог написать руками, и
маршрутизатор поверил бы: у него нет способа отличить измеренное значение от
выдуманного. Ровно так локальная модель на 70 ГБ и оказывается выбранной на
машине, где свободно восемь.

Поэтому здесь наблюдение — это не число, а число ВМЕСТЕ с тем, откуда оно и
когда снято. Из этого следует главное правило модуля:

    Неизмеренный факт не открывает путь. Он его закрывает.

Не «считаем по умолчанию, что памяти хватит» и не «считаем, что не хватает» —
путь, чья пригодность держится на факте, которого никто не измерил, просто не
предлагается, и в решении написано, какого факта не хватило. Это прямое
следствие инварианта V7 «убеждение мира ≠ проверенная внешняя правда»: у
убеждения есть свежесть, и просроченное убеждение значением не является.

Модуль намеренно ничего не импортирует из плоскости управления: наблюдения
приходят обычным словарём. Разбор словаря защитный — незнакомый или пропавший
ключ становится «не измерено», то есть закрытым путём. Расхождение контрактов
между сервисами может только сузить возможности, но не выдать разрешение,
которого никто не давал.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
import time

from .content_buffer import BufferHealth
from .higgsfield_browser_contracts import MediaKind
from .media_router import (GenerationRoute, RouteCandidate, RoutingContext,
                           RoutingDecision, rank_generation_routes)

# Сколько памяти оставляем системе. Не настройка: значение, при котором модель
# «влезает впритык», — это машина, которая встанет вместе с браузером владельца.
MEMORY_HEADROOM = 0.85
# Сколько живёт наблюдение, пока не станет просроченным.
DEFAULT_MAX_AGE_SECONDS = 120.0
MB_PER_GB = 1024.0

# Потолок качества каждого пути: лучшее, что он в состоянии дать. Это не оценка
# пути в ранжировании — это порог допуска.
#
# Разница принципиальная, и она из `AI_STREAMER_MASTER_ARCHITECTURE`:
# «cheapest measured-capable path first». Качество здесь — ГРАНИЦА, а не
# слагаемое: путь либо дотягивает до того, что просит работа, либо не
# предлагается. Среди дотянувших выбирает цена и риск, потому что качество выше
# запрошенного работе не нужно, а платить за него приходится.
#
# Если бы качество осталось слагаемым, браузерный путь выигрывал бы всегда — он
# лучший по картинке, — и локальная модель не запускалась бы никогда, даже там,
# где её достаточно. Это ровно та ошибка, ради которой лестница маршрутов и
# существует.
QUALITY_CEILING: dict[GenerationRoute, float] = {
    GenerationRoute.LOCAL: 0.55,
    GenerationRoute.CHEAP_CLOUD: 0.65,
    GenerationRoute.HIGGSFIELD_BROWSER: 0.85,
}

# Срок, относительно которого задержка вообще что-то значит. Без него формула
# ранжирования нормирует задержку саму на себя, и все пути получают одинаковый
# штраф — то есть задержка перестаёт различать пути вовсе.
DEFAULT_MAX_LATENCY_SECONDS = 600.0


class Freshness(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"


class Health(str, Enum):
    """Состояние пути по измерению. `UNKNOWN` — это НЕ «наверное, работает»."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


class BrowserReadiness(str, Enum):
    """Готовность браузерного пути с точки зрения владельца и площадки."""

    UNKNOWN = "unknown"
    READY = "ready"
    NEEDS_OWNER = "needs_owner"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class Reading:
    """Измеренное значение вместе с тем, чем и когда оно измерено."""

    value: float | None = None
    source: str = ""
    observed_at_epoch_s: float | None = None
    max_age_s: float = DEFAULT_MAX_AGE_SECONDS

    @property
    def measured(self) -> bool:
        return self.value is not None and self.observed_at_epoch_s is not None

    def freshness(self, now: float) -> Freshness:
        if not self.measured:
            return Freshness.MISSING
        age = now - float(self.observed_at_epoch_s or 0.0)
        return Freshness.FRESH if age <= self.max_age_s else Freshness.STALE

    def fresh_value(self, now: float) -> float | None:
        """Значение — только пока оно свежее.

        Просроченное наблюдение не возвращается «на всякий случай»: смысл
        просрочки в том, что значение больше не описывает мир, а не в том, что
        оно чуть менее точное.
        """
        return self.value if self.freshness(now) is Freshness.FRESH else None

    @classmethod
    def unmeasured(cls, reason: str) -> "Reading":
        return cls(value=None, source=reason)

    def to_dict(self, now: float) -> dict[str, Any]:
        return {"value": self.value, "source": self.source,
                "freshness": self.freshness(now).value,
                "measured": self.measured}


@dataclass(frozen=True, slots=True)
class GenerationObservations:
    """Всё, что известно о мире на момент выбора пути генерации."""

    free_memory_mb: Reading = field(default_factory=Reading)
    local_generator: Health = Health.UNKNOWN
    cloud_provider: Health = Health.UNKNOWN
    browser: BrowserReadiness = BrowserReadiness.UNKNOWN
    buffer: BufferHealth | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "GenerationObservations":
        """Разобрать наблюдения, пришедшие снаружи. Защитно и в одну сторону.

        Любая неожиданность — пропавший ключ, чужой тип, незнакомое значение —
        читается как «не измерено». Поэтому расхождение контрактов между
        сервисами способно только закрыть путь, но не открыть: разрешения,
        которого никто не выдавал, из мусора в словаре не получится.
        """
        raw = dict(payload or {})
        return cls(
            free_memory_mb=_reading(raw.get("free_memory_mb")),
            local_generator=_enum(Health, raw.get("local_generator"), Health.UNKNOWN),
            cloud_provider=_enum(Health, raw.get("cloud_provider"), Health.UNKNOWN),
            browser=_enum(BrowserReadiness, raw.get("browser"),
                          BrowserReadiness.UNKNOWN),
            buffer=_enum(BufferHealth, raw.get("buffer"), None))

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        moment = time.time() if now is None else now
        return {"free_memory_mb": self.free_memory_mb.to_dict(moment),
                "local_generator": self.local_generator.value,
                "cloud_provider": self.cloud_provider.value,
                "browser": self.browser.value,
                "buffer": self.buffer.value if self.buffer else None}


def _reading(raw: Any) -> Reading:
    if not isinstance(raw, Mapping):
        return Reading.unmeasured("не передано")
    value = raw.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return Reading.unmeasured(str(raw.get("source") or "не измерено"))
    observed = raw.get("observed_at_epoch_s")
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        return Reading.unmeasured("наблюдение без отметки времени не измерение")
    max_age = raw.get("max_age_s")
    return Reading(value=float(value), source=str(raw.get("source") or "внешнее"),
                   observed_at_epoch_s=float(observed),
                   max_age_s=(float(max_age)
                              if isinstance(max_age, (int, float))
                              and not isinstance(max_age, bool)
                              else DEFAULT_MAX_AGE_SECONDS))


def _enum(kind: Any, raw: Any, default: Any) -> Any:
    try:
        return kind(str(raw))
    except (ValueError, TypeError):
        return default


@dataclass(frozen=True, slots=True)
class RouteRequirements:
    """Чего работа хочет от пути, а не что путь умеет."""

    media_kind: MediaKind
    local_model_mb: float = 0.0
    max_cost_usd: float = 0.05
    max_latency_s: float | None = DEFAULT_MAX_LATENCY_SECONDS
    # Насколько хорошим обязан быть кадр. Это и есть рычаг, которым сцена
    # просит браузерный путь: пока порога хватает локальной модели, платить за
    # лучший путь незачем.
    min_quality_score: float = 0.0
    allow_local: bool = True
    allow_cloud: bool = True
    allow_browser: bool = True

    def __post_init__(self) -> None:
        if self.local_model_mb < 0:
            raise ValueError("размер модели не бывает отрицательным")
        if self.max_cost_usd < 0:
            raise ValueError("бюджет не бывает отрицательным")


@dataclass(frozen=True, slots=True)
class GenerationPlan:
    """Не один путь, а порядок, в котором их пробовать.

    Владелец просил цепочку: локальная модель → дешёвое облако → браузер →
    запасной вариант. Один «выбранный» путь такой цепочкой не является: когда
    он не сработает, решать придётся заново и без памяти о том, что уже
    отвергнуто и почему.
    """

    chain: tuple[GenerationRoute, ...]
    decision: RoutingDecision
    rejected: tuple[str, ...]
    unmeasured: tuple[str, ...]
    fallback: str = ""
    owner_action_required: bool = False

    @property
    def has_route(self) -> bool:
        return bool(self.chain)

    def to_dict(self) -> dict[str, Any]:
        return {"chain": [route.value for route in self.chain],
                "selected": self.decision.selected.value,
                "reason": self.decision.reason,
                "rejected": list(self.rejected),
                "unmeasured": list(self.unmeasured),
                "fallback": self.fallback,
                "owner_action_required": self.owner_action_required}


# Запасные варианты, когда генерировать нечем. Это не пути генерации: они
# ничего не порождают, они удерживают эфир и зовут владельца.
FALLBACK_EVERGREEN = "evergreen"
FALLBACK_WAIT_FOR_OWNER = "wait_for_owner"


def build_candidates(observations: GenerationObservations,
                     requirements: RouteRequirements, *,
                     now: float) -> tuple[list[RouteCandidate], list[str]]:
    """Собрать пути, ЗАКРЫВ те, чья пригодность держится на неизмеренном."""
    unmeasured: list[str] = []
    kinds = frozenset({MediaKind.IMAGE, MediaKind.VIDEO})

    memory_mb = observations.free_memory_mb.fresh_value(now)
    if memory_mb is None:
        unmeasured.append("free_memory_mb")

    local_ok = (observations.local_generator is Health.HEALTHY
                and memory_mb is not None)
    if observations.local_generator is Health.UNKNOWN:
        unmeasured.append("local_generator")
    if observations.cloud_provider is Health.UNKNOWN:
        unmeasured.append("cloud_provider")
    if observations.browser is BrowserReadiness.UNKNOWN:
        unmeasured.append("browser")

    def delivered(route: GenerationRoute) -> float:
        """Качество, которое путь даёт ЭТОЙ работе.

        Потолок пути ниже запрошенного — путь отсеется на пороге качества уже в
        ранжировании. Выше — обрезается: сверх запрошенного качество работе не
        нужно, и превращать его в преимущество значит всегда выбирать самый
        дорогой путь.
        """
        ceiling = QUALITY_CEILING[route]
        if ceiling < requirements.min_quality_score:
            return ceiling                   # отсеется по порогу, и это видно
        return requirements.min_quality_score

    candidates = [
        RouteCandidate(
            route=GenerationRoute.LOCAL, supported_kinds=kinds,
            healthy=observations.local_generator is Health.HEALTHY,
            available=local_ok,
            estimated_cost_usd=0.0, estimated_latency_s=90.0,
            quality_score=delivered(GenerationRoute.LOCAL),
            risk_score=0.10,
            required_memory_gb=requirements.local_model_mb / MB_PER_GB),
        RouteCandidate(
            route=GenerationRoute.CHEAP_CLOUD, supported_kinds=kinds,
            healthy=observations.cloud_provider is Health.HEALTHY,
            available=observations.cloud_provider in {Health.HEALTHY, Health.DEGRADED},
            estimated_cost_usd=0.01, estimated_latency_s=40.0,
            quality_score=delivered(GenerationRoute.CHEAP_CLOUD),
            risk_score=0.15),
        RouteCandidate(
            route=GenerationRoute.HIGGSFIELD_BROWSER, supported_kinds=kinds,
            healthy=observations.browser is BrowserReadiness.READY,
            available=observations.browser is BrowserReadiness.READY,
            estimated_cost_usd=0.0, estimated_latency_s=180.0,
            quality_score=delivered(GenerationRoute.HIGGSFIELD_BROWSER),
            risk_score=0.35,
            browser_owner_auth_ready=observations.browser is BrowserReadiness.READY),
    ]
    return candidates, unmeasured


def plan_generation_route(observations: GenerationObservations,
                          requirements: RouteRequirements, *,
                          now: float | None = None) -> GenerationPlan:
    """Построить цепочку путей и назвать запасной вариант, если её нет."""
    moment = time.time() if now is None else now
    candidates, unmeasured = build_candidates(observations, requirements, now=moment)
    memory_mb = observations.free_memory_mb.fresh_value(moment)
    safe_gb = ((memory_mb * MEMORY_HEADROOM) / MB_PER_GB) if memory_mb is not None else 0.0

    context = RoutingContext(
        media_kind=requirements.media_kind,
        safe_available_memory_gb=safe_gb,
        max_cost_usd=requirements.max_cost_usd,
        max_latency_s=requirements.max_latency_s,
        min_quality_score=requirements.min_quality_score,
        allow_browser=requirements.allow_browser,
        allow_cloud=requirements.allow_cloud,
        allow_local=requirements.allow_local)

    scored, rejected = rank_generation_routes(context, candidates)
    chain = tuple(candidate.route for _, candidate in scored)

    if scored:
        score, best = scored[0]
        decision = RoutingDecision(
            selected=best.route, score=score,
            reason=(f"selected {best.route.value}: quality={best.quality_score:.2f}, "
                    f"cost=${best.estimated_cost_usd:.4f}, "
                    f"latency={best.estimated_latency_s:.1f}s, "
                    f"risk={best.risk_score:.2f}"),
            rejected=tuple(rejected))
        return GenerationPlan(chain=chain, decision=decision,
                              rejected=tuple(rejected),
                              unmeasured=tuple(unmeasured))

    decision = RoutingDecision(
        selected=GenerationRoute.DEFER, score=float("-inf"),
        reason=("ни один путь генерации не пригоден"
                + (f"; не измерено: {', '.join(unmeasured)}" if unmeasured else "")),
        rejected=tuple(rejected))
    fallback, owner = _fallback_for(observations)
    return GenerationPlan(chain=(), decision=decision, rejected=tuple(rejected),
                          unmeasured=tuple(unmeasured), fallback=fallback,
                          owner_action_required=owner)


def _fallback_for(observations: GenerationObservations) -> tuple[str, bool]:
    """Что делать, когда генерировать нечем.

    Эфир нельзя оставить пустым и нельзя импровизировать в нём внешним
    действием. Остаётся утверждённый запас — и владелец, если его ждут.
    """
    owner_needed = observations.browser in {BrowserReadiness.NEEDS_OWNER,
                                            BrowserReadiness.BLOCKED}
    if observations.buffer in {BufferHealth.CRITICAL, BufferHealth.EMPTY}:
        return FALLBACK_EVERGREEN, owner_needed
    if owner_needed:
        return FALLBACK_WAIT_FOR_OWNER, True
    return FALLBACK_EVERGREEN, owner_needed


__all__ = [
    "DEFAULT_MAX_AGE_SECONDS", "DEFAULT_MAX_LATENCY_SECONDS", "FALLBACK_EVERGREEN",
    "FALLBACK_WAIT_FOR_OWNER", "MEMORY_HEADROOM", "QUALITY_CEILING",
    "BrowserReadiness", "Freshness", "GenerationObservations",
    "GenerationPlan", "Health", "Reading", "RouteRequirements", "build_candidates",
    "plan_generation_route",
]
