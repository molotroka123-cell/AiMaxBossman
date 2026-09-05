"""Feature 02/04 (часть) — OpenRouter как first-class провайдер.

Поверх готовых bcc/v2/openrouter_catalog_service (sync каталога, stale-пометка,
pin в реестр) и capability_probe (chat/tools/structured/vision пробы). BOSSMAN
остаётся верхним роутером; каталог ≠ активный реестр; алиасы/история переживают
refresh. Ключ хранится шифрованным (как у всех провайдеров).
"""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import models as models_t, providers as providers_t, utcnow
from ..v2.capability_probe import probe_model
from ..v2.openrouter_catalog_service import OpenRouterCatalogService
from ..v2.openrouter_ext import DEFAULT_BASE, OpenRouterClient
from ..v2.tables import model_capability_checks as caps_t, provider_catalog_models as catalog_t
from . import Feature

router = APIRouter()


class ApiKeyIn(BaseModel):
    api_key: str


def _svc_or_404(request):
    return request.app.state.svc


async def _openrouter_provider(svc, provider_id: int) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(providers_t).where(
            providers_t.c.id == provider_id))).first()
    if row is None:
        raise HTTPException(404, {"message": "провайдер не найден"})
    return dict(row._mapping)


def _client_for(svc, provider: dict) -> OpenRouterClient:
    key = svc.vault.decrypt(provider.get("api_key_enc")) or ""
    return OpenRouterClient(key, base_url=provider.get("base_url") or DEFAULT_BASE)


@router.post("/openrouter/{provider_id}/connect")
async def connect(provider_id: int, request: Request):
    """Подключить OpenRouter: проверить ключ (без инференса) и подтянуть каталог.

    invalid-ключ → 400 с чистым текстом; сеть → 502. Сырой ключ ни в ответе,
    ни в событиях не появляется. CatalogUnavailable при авто-sync не валит
    connect: ключ подтверждён, каталог можно подтянуть позже кнопкой Refresh.
    """
    svc = _svc_or_404(request)
    provider = await _openrouter_provider(svc, provider_id)
    if not svc.vault.decrypt(provider.get("api_key_enc")):
        raise HTTPException(422, {"message": "у провайдера нет api_key",
                                  "hint": "вставьте ключ и повторите Connect"})
    state, detail = await _client_for(svc, provider).validate_key()
    if state == "invalid":
        raise HTTPException(400, {"message": detail,
                                  "hint": "проверьте ключ на openrouter.ai/keys"})
    if state == "network":
        raise HTTPException(502, {"message": detail, "hint": "повторите позже"})
    try:
        sync_result = await OpenRouterCatalogService(svc.db, svc.vault).sync(provider_id)
    except LookupError:
        raise HTTPException(404, {"message": "провайдер не найден"})
    except Exception as exc:             # каталог не критичен для факта подключения
        sync_result = {"synced": 0, "cached": True, "error": type(exc).__name__}
    await svc.bus.emit("openrouter.connected", provider_id=provider_id,
                       models=sync_result.get("synced", 0))
    return {"ok": True, "models": sync_result.get("synced", 0),
            "cached": sync_result.get("cached", False),
            "last_synced_at": sync_result.get("last_synced_at")}


@router.patch("/openrouter/{provider_id}/key")
async def set_key(provider_id: int, body: ApiKeyIn, request: Request):
    """Сохранить/заменить ключ провайдера. Ключ шифруется в vault; наружу и в
    события идёт только факт обновления, не значение."""
    svc = _svc_or_404(request)
    await _openrouter_provider(svc, provider_id)          # 404, если нет такого
    key = body.api_key.strip()
    if not key:
        raise HTTPException(422, {"message": "api_key пустой"})
    async with svc.db.session() as s:
        await s.execute(sa.update(providers_t).where(
            providers_t.c.id == provider_id
        ).values(api_key_enc=svc.vault.encrypt(key)))
        await s.commit()
    await svc.bus.emit("openrouter.key_updated", provider_id=provider_id)
    return {"ok": True}


@router.get("/openrouter/{provider_id}/status")
async def status(provider_id: int, request: Request):
    """Состояние для UI: ключ есть/нет, размер каталога, последний успешный sync."""
    svc = _svc_or_404(request)
    try:
        return await OpenRouterCatalogService(svc.db, svc.vault).catalog_status(provider_id)
    except LookupError:
        raise HTTPException(404, {"message": "провайдер не найден"})


@router.post("/openrouter/{provider_id}/sync")
async def sync_catalog(provider_id: int, request: Request, force: bool = False):
    """Синхронизировать удалённый каталог OpenRouter (не активирует модели авто).

    force=True — ручной Refresh, идёт в сеть мимо TTL. При недоступности
    OpenRouter кэш остаётся нетронутым, наружу 503 с меткой последнего sync.
    """
    svc = _svc_or_404(request)
    service = OpenRouterCatalogService(svc.db, svc.vault)
    try:
        result = await service.sync(provider_id, force=force)
    except ValueError as exc:            # нет ключа
        raise HTTPException(422, {"message": str(exc),
                                  "hint": "добавьте api_key в провайдере OpenRouter"})
    except LookupError:
        raise HTTPException(404, {"message": "провайдер не найден"})
    except Exception as exc:             # сеть/HTTP — наружу человекочитаемо
        detail = getattr(exc, "last_synced_at", None)
        cached = getattr(exc, "cached_count", 0)
        raise HTTPException(503, {
            "message": f"OpenRouter недоступен: показан последний сохранённый каталог",
            "hint": "каталог в кэше не изменён; повторите позже",
            "last_synced_at": str(detail) if detail is not None else None,
            "cached_models": cached,
            "error_type": type(exc).__name__})
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


async def _advertised_for(svc, model: dict) -> dict[str, bool]:
    """Заявленные способности: каталог провайдера (источник правды) + caps реестра."""
    adv: dict[str, bool] = {}
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(catalog_t).where(
            catalog_t.c.provider_id == model["provider_id"],
            catalog_t.c.remote_id == model["name"]))).first()
    if row is not None:
        c = row._mapping
        cat = c["advertised_caps"] if isinstance(c["advertised_caps"], dict) else {}
        adv.update({k: bool(v) for k, v in cat.items()})
        if "image" in (c["input_modalities"] or []):
            adv["vision"] = True
    caps = model["caps"] if isinstance(model["caps"], dict) else {}
    for k, v in caps.items():            # ручные правки реестра не теряем
        adv[k] = bool(v) or adv.get(k, False)
    adv["chat"] = True
    return adv


@router.post("/openrouter/models/{model_id}/probe")
async def probe(model_id: int, request: Request):
    """Живые пробы модели: chat всегда + tools/structured_output/vision/streaming,
    но ТОЛЬКО там, где способность заявлена. Незаявленное пишется как
    verified=NULL («не знаем»), а не False («проверили, не умеет»).
    Пишет advertised vs verified в model_capability_checks."""
    svc = _svc_or_404(request)
    async with svc.db.session() as s:
        model = (await s.execute(sa.select(models_t).where(models_t.c.id == model_id))).first()
        if model is None:
            raise HTTPException(404, {"message": "модель не найдена"})
        m = dict(model._mapping)
        provider = (await s.execute(sa.select(providers_t).where(
            providers_t.c.id == m["provider_id"]))).first()
    if provider is None:
        raise HTTPException(404, {"message": "провайдер модели не найден"})
    p = dict(provider._mapping)
    key = svc.vault.decrypt(p.get("api_key_enc")) or "test"
    client = OpenRouterClient(key, base_url=p.get("base_url") or "https://openrouter.ai/api/v1")

    advertised = await _advertised_for(svc, m)
    results = await probe_model(client, m["name"], advertised)

    async with svc.db.session() as s:
        for r in results:
            await s.execute(sa.insert(caps_t).values(
                model_id=model_id, capability=r.capability,
                advertised=bool(advertised.get(r.capability, False)),
                verified=r.verified,          # None, если пробу не гоняли
                detail=r.detail[:500], checked_at=utcnow()))
        await s.commit()
    await svc.bus.emit("model.capabilities_probed", model_id=model_id,
                       verified=[r.capability for r in results if r.verified])
    return {"model_id": model_id,
            "probes": [{"capability": r.capability,
                        "advertised": bool(advertised.get(r.capability, False)),
                        "verified": r.verified, "skipped": r.skipped,
                        "detail": r.detail}
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


ENV_API_KEY = "BOSSMAN_OPENROUTER_API_KEY"
ENV_MODELS = "BOSSMAN_OPENROUTER_MODELS"          # конфигурация, не код: "z-ai/glm-4.5-air,qwen/qwen3-coder"
ENV_PROVIDER_NAME = "OpenRouter (env)"


def _alias_for(remote_id: str) -> str:
    return "or-" + remote_id.replace("/", "-").replace(":", "-")[:100]


async def _ensure_models(svc, provider_id: int, remote_ids: list[str]) -> list[str]:
    """Модели из конфигурации: точные ID — данные окружения, не ядро. Идемпотентно."""
    created = []
    for remote_id in remote_ids:
        alias = _alias_for(remote_id)
        async with svc.db.session() as s:
            exists = (await s.execute(sa.select(models_t.c.id).where(models_t.c.alias == alias))).first()
        if exists:
            continue
        await svc.registry.create_model(provider_id=provider_id, name=remote_id, alias=alias, kind="cloud",
                                        context_window=None)
        created.append(alias)
    return created


async def setup(svc) -> None:
    """Временный путь провайдера через окружение (BOSS-V3-PRODUCTIZATION-CLOSURE-002):
    ключ берётся ТОЛЬКО из переменной окружения, в репозитории и в логах его нет;
    при старте он один раз шифруется в vault как у любого провайдера. Без
    переменной ничего не создаётся; повторный старт не дублирует провайдера."""
    import os
    key = (os.environ.get(ENV_API_KEY) or "").strip()
    if not key:
        return
    remote_ids = [m.strip() for m in (os.environ.get(ENV_MODELS) or "").split(",") if m.strip()]
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(providers_t).where(
            providers_t.c.base_url.like("https://openrouter.ai/%")))).fetchall()
    if rows:
        row = dict(rows[0]._mapping)
        if not svc.vault.decrypt(row.get("api_key_enc")):
            async with svc.db.session() as s:
                await s.execute(sa.update(providers_t).where(providers_t.c.id == row["id"]).values(
                    api_key_enc=svc.vault.encrypt(key)))
                await s.commit()
            await svc.bus.emit("provider.key_from_env", provider_id=row["id"], source=ENV_API_KEY)
        provider_id = int(row["id"])
    else:
        created = await svc.registry.create_provider(ENV_PROVIDER_NAME, "openai_compat", DEFAULT_BASE, key)
        provider_id = int(created.get("id"))
        await svc.bus.emit("provider.bootstrapped", provider_id=provider_id, name=ENV_PROVIDER_NAME,
                           source=ENV_API_KEY)
    if remote_ids:
        aliases = await _ensure_models(svc, provider_id, remote_ids)
        if aliases:
            await svc.bus.emit("model.bootstrapped", provider_id=provider_id, aliases=aliases, source=ENV_MODELS)


FEATURE = Feature(name="openrouter", router=router, setup=setup)
