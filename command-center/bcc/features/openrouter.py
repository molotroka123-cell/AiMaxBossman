"""Feature 02/04 (часть) — OpenRouter как first-class провайдер.

Поверх готовых bcc/v2/openrouter_catalog_service (sync каталога, stale-пометка,
pin в реестр) и capability_probe (chat/tools/structured/vision пробы). BOSSMAN
остаётся верхним роутером; каталог ≠ активный реестр; алиасы/история переживают
refresh. Ключ хранится шифрованным (как у всех провайдеров).
"""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import models as models_t, providers as providers_t, utcnow
from ..v2.capability_probe import probe_chat, probe_structured_output, probe_tools
from ..v2.openrouter_catalog_service import OpenRouterCatalogService
from ..v2.openrouter_ext import OpenRouterClient
from ..v2.tables import model_capability_checks as caps_t, provider_catalog_models as catalog_t
from . import Feature

router = APIRouter()


def _svc_or_404(request):
    return request.app.state.svc


async def _openrouter_provider(svc, provider_id: int) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(providers_t).where(
            providers_t.c.id == provider_id))).first()
    if row is None:
        raise HTTPException(404, {"message": "провайдер не найден"})
    return dict(row._mapping)


@router.post("/openrouter/{provider_id}/sync")
async def sync_catalog(provider_id: int, request: Request):
    """Синхронизировать удалённый каталог OpenRouter (не активирует модели авто)."""
    svc = _svc_or_404(request)
    service = OpenRouterCatalogService(svc.db, svc.vault)
    try:
        result = await service.sync(provider_id)
    except ValueError as exc:            # нет ключа
        raise HTTPException(422, {"message": str(exc),
                                  "hint": "добавьте api_key в провайдере OpenRouter"})
    except LookupError:
        raise HTTPException(404, {"message": "провайдер не найден"})
    except Exception as exc:             # сеть/HTTP — наружу человекочитаемо
        raise HTTPException(502, {"message": f"OpenRouter недоступен: {exc}"})
    return result


@router.get("/openrouter/{provider_id}/catalog")
async def catalog(provider_id: int, request: Request, q: str | None = None,
                  limit: int = 50, include_stale: bool = False):
    """Каталог (метаданные): контекст, цены, модальности, параметры, capabilities."""
    svc = _svc_or_404(request)
    async with svc.db.session() as s:
        query = sa.select(catalog_t).where(catalog_t.c.provider_id == provider_id)
        if not include_stale:
            query = query.where(catalog_t.c.stale.is_(False))
        if q:
            query = query.where(sa.or_(catalog_t.c.remote_id.ilike(f"%{q}%"),
                                       catalog_t.c.display_name.ilike(f"%{q}%")))
        rows = (await s.execute(query.order_by(catalog_t.c.remote_id).limit(min(limit, 200)))
                ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/openrouter/{provider_id}/pin")
async def pin_model(provider_id: int, request: Request):
    """Закрепить модель каталога в активном реестре BOSSMAN (создать models-запись).
    Существующие алиасы/история не трогаются."""
    svc = _svc_or_404(request)
    body = await request.json()
    remote_id = body.get("remote_id")
    alias = body.get("alias") or remote_id
    async with svc.db.session() as s:
        card = (await s.execute(sa.select(catalog_t).where(
            catalog_t.c.provider_id == provider_id,
            catalog_t.c.remote_id == remote_id))).first()
        if card is None:
            raise HTTPException(404, {"message": "модель не найдена в каталоге",
                                      "hint": "сначала выполните sync"})
        c = card._mapping
        # алиас уже занят → не разрушаем историю, возвращаем существующую
        exists = (await s.execute(sa.select(models_t.c.id).where(
            models_t.c.alias == alias))).first()
        if exists:
            return {"model_id": exists._mapping["id"], "alias": alias, "already": True}
        caps = {}
        adv = c["advertised_caps"] if isinstance(c["advertised_caps"], dict) else {}
        if adv.get("tools"):
            caps["tools"] = True
        if "image" in (c["input_modalities"] or []):
            caps["vision"] = True
        res = await s.execute(sa.insert(models_t).values(
            provider_id=provider_id, name=remote_id, alias=alias, kind="cloud",
            context_window=c["context_window"] or 8192,
            price_in=c["price_in"] or 0.0, price_out=c["price_out"] or 0.0,
            caps=caps, status="unknown"))
        mid = int(res.inserted_primary_key[0])
        await s.commit()
    await svc.bus.emit("model.created", id=mid, alias=alias)
    return {"model_id": mid, "alias": alias}


@router.post("/openrouter/models/{model_id}/probe")
async def probe(model_id: int, request: Request):
    """Живые пробы модели: chat + (tools/structured/vision где заявлены).
    Пишет advertised vs verified в model_capability_checks."""
    svc = _svc_or_404(request)
    async with svc.db.session() as s:
        model = (await s.execute(sa.select(models_t).where(models_t.c.id == model_id))).first()
        if model is None:
            raise HTTPException(404, {"message": "модель не найдена"})
        m = dict(model._mapping)
        provider = (await s.execute(sa.select(providers_t).where(
            providers_t.c.id == m["provider_id"]))).first()
    p = dict(provider._mapping)
    key = svc.vault.decrypt(p.get("api_key_enc")) or "test"
    client = OpenRouterClient(key, base_url=p.get("base_url") or "https://openrouter.ai/api/v1")

    caps = m["caps"] if isinstance(m["caps"], dict) else {}
    results = [await probe_chat(client, m["name"])]
    if caps.get("tools"):
        results.append(await probe_tools(client, m["name"]))
        results.append(await probe_structured_output(client, m["name"]))
    # vision-проба в паке не реализована — заявленная vision-способность остаётся
    # advertised без verified (честно, без выдуманного результата)

    async with svc.db.session() as s:
        for r in results:
            await s.execute(sa.insert(caps_t).values(
                model_id=model_id, capability=r.capability,
                advertised=caps.get(r.capability, r.capability == "chat"),
                verified=r.ok, detail=r.detail[:500], checked_at=utcnow()))
        await s.commit()
    return {"model_id": model_id,
            "probes": [{"capability": r.capability, "verified": r.ok, "detail": r.detail}
                       for r in results]}


@router.get("/openrouter/models/{model_id}/capabilities")
async def capabilities(model_id: int, request: Request):
    """Advertised vs verified capabilities (последние пробы)."""
    svc = _svc_or_404(request)
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(caps_t).where(caps_t.c.model_id == model_id)
                                .order_by(caps_t.c.id.desc()))).fetchall()
    seen, out = set(), []
    for r in rows:
        c = r._mapping
        if c["capability"] in seen:
            continue
        seen.add(c["capability"])
        out.append({"capability": c["capability"], "advertised": c["advertised"],
                    "verified": c["verified"], "detail": c["detail"],
                    "checked_at": c["checked_at"]})
    return out


FEATURE = Feature(name="openrouter", router=router)
