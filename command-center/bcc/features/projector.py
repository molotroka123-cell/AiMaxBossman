"""Pump.fun Projector в Command Center — тонкая ручка над bossman.trading_learning.projector (bossman-core).

Отдельный модуль, а не trading_lab: тот по инварианту только читает (тест test_the_module_exposes_no_write_route).
Здесь POST /projector/refresh делает ОДИН цикл discovery (DexScreener, только чтение) -> риск -> БУМАЖНАЯ книга.
Ордеров, ключей и подписей нет; ядро не подключено -> честный UNWIRED, как в trading_lab.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request

from . import Feature
from .trading_lab import UNWIRED, _load

router = APIRouter()


def _state_dir(request: Request) -> Path:
    return Path(request.app.state.svc.settings.data_dir) / "projector"


@router.get("/projector")
async def projector_latest(request: Request) -> dict:
    if _load("bossman.trading_learning.projector", "refresh") is None:
        return dict(UNWIRED)
    latest = _state_dir(request) / "latest.json"
    if not latest.exists():
        return {"available": True, "status": "NOT_RUN", "note": "ещё не было обновления: POST /api/projector/refresh"}
    data = json.loads(latest.read_text(encoding="utf-8"))
    data["available"] = True
    return data


@router.post("/projector/refresh")
async def projector_refresh(request: Request, pump_only: bool = False) -> dict:
    refresh = _load("bossman.trading_learning.projector", "refresh")
    if refresh is None:
        return dict(UNWIRED)
    run = await asyncio.to_thread(refresh, _state_dir(request), None, pump_only=pump_only)
    run["available"] = True
    return run


FEATURE = Feature(name="projector", router=router)
