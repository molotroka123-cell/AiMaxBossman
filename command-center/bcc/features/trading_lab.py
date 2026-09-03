"""Trading Learning Lab — витрина обучающего торгового модуля в Command Center.

Модуль ЧИТАЮЩИЙ. Он не создаёт ордеров, не ходит на биржу и не хранит вторую
копию памяти: вся логика живёт в `bossman.trading_learning` (bossman-core), а
здесь только тонкая ручка, отдающая его состояние экрану.

Почему импорт ленивый и почему отсутствие ядра — не ошибка: command-center по
pyproject не зависит от bossman-core, поэтому в сборке без ядра ручка обязана
честно ответить DEAD_OR_UNWIRED, а не уронить загрузку всех фич. Тихо
подставить заглушку с бодрым «ready» здесь было бы худшим вариантом: экран
показал бы работающий торговый модуль там, где его нет.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import Feature

router = APIRouter()

# Класс доказательности для случая «ядро не подключено». Он же уезжает на экран
# и запрещает бейдж PAPER: неподключённый модуль ничем не лучше отсутствующего.
UNWIRED = {
    "available": False,
    "evidence_class": "DEAD_OR_UNWIRED",
    "reason": ("bossman.trading_learning недоступен в этой сборке: command-center "
               "не зависит от bossman-core"),
    "badge": "UNWIRED",
}


def _core() -> Any | None:
    """Ядро торгового модуля или None. Импорт внутри функции — намеренно."""
    try:
        from bossman import trading_learning        # noqa: WPS433
        return trading_learning
    except Exception:  # noqa: BLE001 — отсутствие ядра не должно ронять фичи
        return None


def status_payload() -> dict:
    core = _core()
    if core is None:
        return dict(UNWIRED)
    from bossman.trading_learning.routes import pipeline_status   # noqa: WPS433
    payload = pipeline_status()
    payload["available"] = True
    payload["evidence_class"] = ("HISTORICAL_REPLAY" if payload["pipeline_complete"]
                                 else "BLOCKED")
    return payload


@router.get("/trading-lab/status")
async def trading_lab_status() -> dict:
    """Состояние пайплайна, режим безопасности и заблокированные шаги."""
    return status_payload()


@router.get("/trading-lab/seed")
async def trading_lab_seed() -> dict:
    """Затравочный эпизод K1mba — строго SCREENSHOT_OBSERVED."""
    if _core() is None:
        return dict(UNWIRED)
    from bossman.trading_learning.seed import seed_report        # noqa: WPS433
    return seed_report()


@router.get("/trading-lab/benchmark")
async def trading_lab_benchmark() -> dict:
    """Прогон бенчмарка. Вердикт READY выдаётся только без единого блокера."""
    if _core() is None:
        return dict(UNWIRED)
    from bossman.trading_learning.benchmark import run_benchmark  # noqa: WPS433
    return run_benchmark().as_dict()


@router.get("/trading-lab/memory")
async def trading_lab_memory() -> dict:
    """Состояние слоёв памяти.

    Процесс Command Center не обучается сам, поэтому слои пустые — и это
    показывается как есть. Нарисовать сюда «12 выученных правил» означало бы
    выдать документацию за реализацию.
    """
    if _core() is None:
        return dict(UNWIRED)
    from bossman.trading_learning.memory import TradingMemory     # noqa: WPS433
    snapshot = TradingMemory().snapshot()
    snapshot["note"] = ("память процесса пуста: обучение запускается из CLI ядра "
                        "(bossman.trading_learning.cli), а не из дашборда")
    return snapshot


FEATURE = Feature(name="trading_lab", router=router)
