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

import importlib
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


def _load(dotted: str, symbol: str) -> Any | None:
    """Один символ из ядра — или None, если ядра нет.

    Guard стоит на КАЖДОМ импорте, а не только на верхнем. Владелец получил
    `500 ModuleNotFoundError: No module named 'bossman_v3'` именно так: пакет
    `bossman` импортировался прекрасно, верхний guard считал ядро на месте, а
    следующий, более глубокий импорт падал на чужой зависимости — и улетал
    наружу как ошибка сервера. Успех мелкого импорта ничего не говорит о
    глубоком, поэтому проверяется ровно тот модуль, который будет вызван.

    Ловится сбой ИМПОРТА, то есть факт конфигурации. Падение самого вызова —
    это уже рантайм ядра, и прятать его под «модуль не подключён» значило бы
    выдавать поломку за отсутствие.
    """
    try:
        module = importlib.import_module(dotted)
    except Exception:  # noqa: BLE001 — отсутствие ядра не должно ронять фичи
        return None
    return getattr(module, symbol, None)


def _core() -> Any | None:
    """Ядро торгового модуля или None. Импорт внутри функции — намеренно."""
    try:
        from bossman import trading_learning        # noqa: WPS433
        return trading_learning
    except Exception:  # noqa: BLE001
        return None


def status_payload() -> dict:
    pipeline_status = _load("bossman.trading_learning.routes", "pipeline_status")
    if pipeline_status is None:
        return dict(UNWIRED)
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
    seed_report = _load("bossman.trading_learning.seed", "seed_report")
    if seed_report is None:
        return dict(UNWIRED)
    return seed_report()


@router.get("/trading-lab/benchmark")
async def trading_lab_benchmark() -> dict:
    """Прогон бенчмарка. Вердикт READY выдаётся только без единого блокера."""
    run_benchmark = _load("bossman.trading_learning.benchmark", "run_benchmark")
    if run_benchmark is None:
        return dict(UNWIRED)
    return run_benchmark().as_dict()


@router.get("/trading-lab/memory")
async def trading_lab_memory() -> dict:
    """Состояние слоёв памяти.

    Процесс Command Center не обучается сам, поэтому слои пустые — и это
    показывается как есть. Нарисовать сюда «12 выученных правил» означало бы
    выдать документацию за реализацию.
    """
    TradingMemory = _load("bossman.trading_learning.memory", "TradingMemory")
    if TradingMemory is None:
        return dict(UNWIRED)
    snapshot = TradingMemory().snapshot()
    snapshot["note"] = ("память процесса пуста: обучение запускается из CLI ядра "
                        "(bossman.trading_learning.cli), а не из дашборда")
    return snapshot


FEATURE = Feature(name="trading_lab", router=router)
