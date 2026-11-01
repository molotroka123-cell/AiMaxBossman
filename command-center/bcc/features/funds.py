"""Funds — остатки по провайдерам для локального дашборда (owner 01.11.2026).

GET /api/funds → {updated_at, providers:[{id,name,currency,balance,limit,spent_today,status,note}]}.
• Баланс спрашиваем только у провайдеров с известным эндпоинтом (OpenRouter: GET /auth/key → limit_remaining).
  Остальные платные — balance null, status "no_probe"; локальные (127.0.0.1) — currency "free".
• spent_today — сумма task_runs.cost_usd за сегодня по моделям провайдера (наш учёт, не счёт провайдера).
• Ключи не покидают процесс: расшифровка только для заголовка Authorization; в ответе ключей нет.
• Кэш 60 с: дашборд опрашивает раз в минуту, провайдеров не дёргаем чаще.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Request

from . import Feature

router = APIRouter()
CACHE_TTL = 60.0
PROBE_TIMEOUT = 8.0
_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _is_local(base_url: str) -> bool:
    return "127.0.0.1" in base_url or "localhost" in base_url


async def probe_openrouter(base_url: str, key: str) -> dict:
    """OpenRouter: /auth/key отдаёт limit (USD или null) и limit_remaining."""
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        r = await client.get(base_url.rstrip("/") + "/auth/key", headers={"Authorization": f"Bearer {key}"})
    if r.status_code == 401:
        return {"balance": None, "limit": None, "status": "key_invalid", "note": "ключ отклонён (401) — обновите через /key"}
    r.raise_for_status()
    data = r.json().get("data", {})
    remaining = data.get("limit_remaining")
    limit = data.get("limit")
    return {"balance": remaining if isinstance(remaining, (int, float)) else None,
            "limit": limit if isinstance(limit, (int, float)) else None, "status": "ok", "note": ""}


PROBES = {"openrouter.ai": probe_openrouter}


def _probe_for(base_url: str):
    for host, fn in PROBES.items():
        if host in base_url:
            return fn
    return None


async def _spent_today(svc) -> dict[int, float]:
    from ..db import models as models_t, task_runs as runs_t
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(models_t.c.provider_id, models_t.c.name, models_t.c.alias))).fetchall()
        alias_to_provider: dict[str, int] = {}
        for pid, name, alias in rows:
            for k in (name, alias):
                if k:
                    alias_to_provider[str(k)] = int(pid)
        res = await s.execute(sa.select(runs_t.c.model_alias, sa.func.coalesce(sa.func.sum(runs_t.c.cost_usd), 0.0))
                              .where(runs_t.c.started_at >= day).group_by(runs_t.c.model_alias))
        out: dict[int, float] = {}
        for alias, cost in res.fetchall():
            pid = alias_to_provider.get(str(alias))
            if pid is not None:
                out[pid] = round(out.get(pid, 0.0) + float(cost or 0.0), 4)
    return out


async def collect(svc) -> dict:
    from ..db import providers as providers_t
    async with svc.db.session() as s:
        providers = [dict(r._mapping) for r in (await s.execute(sa.select(providers_t))).fetchall()]
    spent = await _spent_today(svc)
    items = []
    for p in providers:
        base = str(p.get("base_url") or "")
        item = {"id": int(p["id"]), "name": p["name"], "currency": "USD", "balance": None, "limit": None,
                "spent_today": spent.get(int(p["id"]), 0.0), "status": "no_probe", "note": ""}
        if _is_local(base):
            item.update(currency="free", status="ok", note="локально, бесплатно")
        else:
            probe = _probe_for(base)
            enc = p.get("api_key_enc")
            if probe is None:
                item["note"] = "у провайдера нет публичного API баланса — смотрите в кабинете"
            elif not enc:
                item.update(status="no_key", note="ключ не задан")
            else:
                try:
                    item.update(await probe(base, svc.vault.decrypt(enc)))
                except Exception as exc:  # noqa: BLE001 — дашборд должен жить при любом провайдере
                    item.update(status="error", note=type(exc).__name__)
        items.append(item)
    return {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "providers": items,
            "source": "LIVE probe (OpenRouter) + our task_runs ledger; other paid providers: NOT_AVAILABLE"}


@router.get("/funds")
async def http_funds(request: Request, refresh: bool = False):
    now = time.monotonic()
    if not refresh and _cache["data"] is not None and now - _cache["at"] < CACHE_TTL:
        return _cache["data"]
    data = await collect(request.app.state.svc)
    _cache.update(at=now, data=data)
    return data


FEATURE = Feature(name="funds", router=router)
