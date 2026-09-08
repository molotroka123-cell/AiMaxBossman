"""Наблюдения для выбора пути порождения медиа.

Здесь НЕ выбирается путь. Здесь только публикуется то, что мир состояния уже
измерил, в форме, которую понимает генератор в `apps/social-farm`. Решение
остаётся там — у него один экземпляр правил, и второй экземпляр в плоскости
управления был бы ровно тем «дублирующим хранилищем правды», которого просили
не заводить.

Граница между сервисами держится на двух вещах, и обе односторонние:

* значение публикуется ТОЛЬКО из свежего факта. `STALE`, `MISSING` и
  `CONTESTED` не превращаются в число поменьше — они не превращаются ни во что,
  и получатель видит «не измерено»;
* получатель разбирает словарь защитно: незнакомое или пропавшее становится
  «не измерено», то есть закрытым путём. Поэтому расхождение версий двух
  сервисов способно только сузить возможности и не способно выдать разрешение.

Ни один ключ отсюда не является разрешением. Это наблюдения — «сколько памяти
свободно», «жив ли провайдер», — а не «можно ли туда идти». Право на действие
по-прежнему выдают права и одобрения, и они лежат не здесь.
"""
from __future__ import annotations

import time
from typing import Any

from . import world

# Ключи мира состояния, из которых собираются наблюдения.
HOST_KEY = "process.host"
PROVIDER_KEY = "provider.models"

# Как называются состояния на проводе. Совпадает с перечнями получателя;
# несовпадение читается им как «не измерено», а не как «здоров».
UNKNOWN = "unknown"
HEALTHY = "healthy"
DEGRADED = "degraded"
DOWN = "down"


def _fresh(svc, key: str, *, now: float) -> dict[str, Any] | None:
    """Значение факта, пока оно свежее. Иначе — ничего."""
    read = world.projection(svc).read(world.AMBIENT_SCOPE, key, now=now)
    if read.status != "FRESH" or read.fact is None:
        return None
    value = read.fact.value
    if not isinstance(value, dict):
        return None
    return {"value": value, "observed_at": read.fact.observed_at,
            "max_age_seconds": read.fact.max_age_seconds}


def memory_observation(svc, *, now: float | None = None) -> dict[str, Any]:
    """Свободная память как наблюдение, а не как число.

    Отметка времени обязательна и здесь, и у получателя: число без неё нельзя
    состарить, а значит нельзя и отличить измерение от чьего-то мнения.
    """
    moment = time.time() if now is None else now
    fresh = _fresh(svc, HOST_KEY, now=moment)
    if fresh is None:
        return {"value": None, "source": f"{HOST_KEY} не свеж"}
    available = fresh["value"].get("ram_available_mb")
    if not isinstance(available, (int, float)) or isinstance(available, bool):
        return {"value": None, "source": f"в {HOST_KEY} нет ram_available_mb"}
    return {"value": float(available), "source": "observer:process",
            "observed_at_epoch_s": float(fresh["observed_at"]),
            "max_age_s": float(fresh["max_age_seconds"])}


def provider_observation(svc, *, now: float | None = None) -> str:
    """Состояние облачных моделей по измеренным записям здоровья.

    «Не измерено» остаётся отдельным ответом, а не сливается со «здоров»:
    именно в этом смысл статуса, и получатель на нём закрывает путь.
    """
    moment = time.time() if now is None else now
    fresh = _fresh(svc, PROVIDER_KEY, now=moment)
    if fresh is None:
        return UNKNOWN
    summary = fresh["value"]
    healthy = summary.get("healthy")
    unhealthy = summary.get("unhealthy")
    if not isinstance(healthy, int) or not isinstance(unhealthy, int):
        return UNKNOWN
    if healthy > 0:
        return HEALTHY if unhealthy == 0 else DEGRADED
    return DOWN if unhealthy > 0 else UNKNOWN


def observations(svc, *, now: float | None = None,
                 browser: str = UNKNOWN, local_generator: str = UNKNOWN,
                 buffer: str | None = None) -> dict[str, Any]:
    """Полный набор наблюдений в форме получателя.

    `browser`, `local_generator` и `buffer` приходят параметрами, а не
    выдумываются: браузерная сессия живёт в другом сервисе, локального
    генератора на этой машине может не быть вовсе, а глубину буфера знает тот,
    кто его наполняет. Значение по умолчанию у всех трёх — «не измерено», и
    это закрывает соответствующий путь, а не открывает его.
    """
    moment = time.time() if now is None else now
    return {"at": moment,
            "free_memory_mb": memory_observation(svc, now=moment),
            "local_generator": local_generator,
            "cloud_provider": provider_observation(svc, now=moment),
            "browser": browser,
            "buffer": buffer,
            "note": ("наблюдения, а не разрешения: право на действие "
                     "по-прежнему выдают права и одобрения")}


__all__ = ["DEGRADED", "DOWN", "HEALTHY", "HOST_KEY", "PROVIDER_KEY", "UNKNOWN",
           "memory_observation", "observations", "provider_observation"]
