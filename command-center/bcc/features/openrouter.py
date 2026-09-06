"""Feature 02/04 (часть) — OpenRouter как first-class провайдер.

Поверх готовых bcc/v2/openrouter_catalog_service (sync каталога, stale-пометка,
pin в реестр) и capability_probe (chat/tools/structured/vision пробы). BOSSMAN
остаётся верхним роутером; каталог ≠ активный реестр; алиасы/история переживают
refresh. Ключ хранится шифрованным (как у всех провайдеров).

Путь владельца («дал ключ — увидел список моделей») держат три ручки:
POST /openrouter/connect создаёт или обновляет провайдера по одному ключу,
GET  /openrouter/{id}/catalog отдаёт СТРАНИЦУ с честными счётчиками,
POST /openrouter/{id}/pin переносит выбранную модель в активный реестр.
Все три обязаны называть причину отказа: пустой список без причины — дефект,
из-за которого владелец искал проблему не там (audit-11, OR-001…OR-003).
"""
from __future__ import annotations
from bcc.v2.openrouter_ext import catalog_price_values
from bcc.provider_governance import known_prices

import asyncio

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import models as models_t, providers as providers_t, utcnow
from ..v2 import openrouter_identity as identity
from ..v2.capability_probe import probe_model
from ..v2.openrouter_catalog_service import CatalogUnavailable, OpenRouterCatalogService
from ..v2.openrouter_ext import DEFAULT_BASE, OpenRouterClient, normalize_base_url
from ..v2.tables import model_capability_checks as caps_t, provider_catalog_models as catalog_t
from . import Feature

router = APIRouter()


class ApiKeyIn(BaseModel):
    api_key: str


class ConnectIn(BaseModel):
    """Всё, что владелец вводит на странице OpenRouter: ключ и (редко) адрес."""
    api_key: str
    base_url: str | None = None


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


def _catalog_failure(exc: Exception) -> dict:
    """Почему каталог не загрузился — словами, которые владельцу что-то говорят.

    Пустой список без причины неотличим от «у провайдера нет моделей», и
    владелец начинает искать проблему не там (живой прогон 20260906: истёкший
    ключ выглядел как исчерпанный бюджет планировщика). Причина всегда едет
    вместе с ответом, а сырой ключ в неё не попадает.
    """
    if isinstance(exc, CatalogUnavailable):
        return {"catalog_error": str(exc), "catalog_hint": exc.hint,
                "catalog_status": exc.status_code}
    if isinstance(exc, PermissionError):     # приватность/офлайн-политика
        return {"catalog_error": f"каталог не запрошен: {exc}",
                "catalog_hint": "разрешите обращение к облаку или работайте на локальных моделях",
                "catalog_status": None}
    return {"catalog_error": f"каталог не загрузился: {type(exc).__name__}",
            "catalog_hint": "нажмите «Обновить список» ещё раз", "catalog_status": None}


async def _validate_or_raise(svc, provider: dict) -> None:
    """Проверить ключ и превратить любой отказ в понятное владельцу действие."""
    try:
        state, detail = await _client_for(svc, provider).validate_key()
    except PermissionError as exc:       # приватный контекст: наружу нельзя
        raise HTTPException(403, {"message": f"политика запрещает обращение к OpenRouter: {exc}",
                                  "hint": "снимите приватный режим или используйте локальные модели"})
    if state == "invalid":
        raise HTTPException(400, {"message": detail,
                                  "hint": "проверьте ключ на openrouter.ai/keys"})
    if state == "address":
        effective = normalize_base_url(provider.get("base_url") or DEFAULT_BASE)
        raise HTTPException(400, {"message": detail,
                                  "hint": f"запрос уходил на {effective}; "
                                          f"рабочий адрес — {DEFAULT_BASE}"})
    if state == "network":
        raise HTTPException(502, {"message": detail, "hint": "повторите позже"})


async def _sync_catalog(svc, provider_id: int) -> tuple[dict, dict]:
    """(результат sync, причина отказа). Сбой каталога не отменяет факт подключения."""
    try:
        return await OpenRouterCatalogService(svc.db, svc.vault).sync(provider_id), {}
    except LookupError:
        raise HTTPException(404, {"message": "провайдер не найден"})
    except Exception as exc:             # каталог не критичен для факта подключения
        return ({"synced": getattr(exc, "cached_count", 0), "cached": True,
                 "last_synced_at": getattr(exc, "last_synced_at", None)},
                _catalog_failure(exc))


def _connect_lock(request: Request) -> asyncio.Lock:
    """Один Connect за раз на приложение: двойной клик не создаёт двух провайдеров."""
    lock = getattr(request.app.state, "openrouter_connect_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        request.app.state.openrouter_connect_lock = lock
    return lock


@router.post("/openrouter/connect")
async def connect_with_key(body: ConnectIn, request: Request):
    """Путь владельца целиком: ключ → провайдер → проверка → каталог.

    До этого провайдера OpenRouter могла создать ТОЛЬКО переменная окружения при
    старте (audit-11, OR-001): на чистой установке страница показывала пустоту и
    отправляла владельца на другую страницу за визардом провайдера. Здесь тот же
    существующий провайдер-API и тот же vault, просто вызванные оттуда, куда
    владелец приходит с ключом.

    Идемпотентно: повторный Connect (и двойной клик) обновляет ключ у того же
    провайдера, а не заводит второго. Ключ не возвращается наружу, не пишется в
    URL и не попадает в события — только шифрованным в vault.
    """
    svc = _svc_or_404(request)
    key = body.api_key.strip()
    if not key:
        raise HTTPException(422, {"message": "api_key пустой",
                                  "hint": "вставьте ключ с openrouter.ai/keys"})
    base_url = normalize_base_url(body.base_url or DEFAULT_BASE)
    async with _connect_lock(request):
        row = await identity.provider_row(svc.db, svc.vault)
        created = row is None
        if created:
            public = await svc.registry.create_provider(
                identity.PROVIDER_NAME, identity.PROVIDER_KIND, base_url, key)
            provider_id = int(public["id"])
            await identity.remember_provider(svc.db, svc.vault, provider_id)
        else:
            provider_id = int(row["id"])
            async with svc.db.session() as s:
                await s.execute(sa.update(providers_t).where(
                    providers_t.c.id == provider_id
                ).values(api_key_enc=svc.vault.encrypt(key), base_url=base_url))
                await s.commit()
            await svc.bus.emit("openrouter.key_updated", provider_id=provider_id)
        provider = await _openrouter_provider(svc, provider_id)
        await _validate_or_raise(svc, provider)
        sync_result, failure = await _sync_catalog(svc, provider_id)
    await svc.bus.emit("openrouter.connected", provider_id=provider_id,
                       models=sync_result.get("synced", 0),
                       catalog_error=failure.get("catalog_error"))
    return {"ok": True, "provider_id": provider_id, "created": created,
            "models": sync_result.get("synced", 0),
            "cached": sync_result.get("cached", False),
            "last_synced_at": sync_result.get("last_synced_at"), **failure}


@router.post("/openrouter/{provider_id}/connect")
async def connect(provider_id: int, request: Request):
    """Подключить OpenRouter: проверить ключ (без инференса) и подтянуть каталог.

    invalid-ключ → 400 с чистым текстом; неверный адрес → 400 с адресом,
    который реально использовался; сеть → 502. Сырой ключ ни в ответе, ни в
    событиях не появляется. Сбой каталога при авто-sync не валит connect (ключ
    подтверждён), но и не молчит: причина уезжает в catalog_error/catalog_hint.
    """
    svc = _svc_or_404(request)
    provider = await _openrouter_provider(svc, provider_id)
    if not svc.vault.decrypt(provider.get("api_key_enc")):
        raise HTTPException(422, {"message": "у провайдера нет api_key",
                                  "hint": "вставьте ключ и повторите Connect"})
    await _validate_or_raise(svc, provider)
    sync_result, failure = await _sync_catalog(svc, provider_id)
    await svc.bus.emit("openrouter.connected", provider_id=provider_id,
                       models=sync_result.get("synced", 0),
                       catalog_error=failure.get("catalog_error"))
    return {"ok": True, "models": sync_result.get("synced", 0),
            "cached": sync_result.get("cached", False),
            "last_synced_at": sync_result.get("last_synced_at"), **failure}


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
        failure = _catalog_failure(exc)
        # Настоящая причина в message: «недоступен» вместо 401 отправляет
        # владельца ждать сеть, когда надо заменить ключ.
        raise HTTPException(503, {
            "message": failure["catalog_error"],
            "hint": failure["catalog_hint"] + "; каталог в кэше не изменён",
            "last_synced_at": str(detail) if detail is not None else None,
            "cached_models": cached,
            "status_code": failure["catalog_status"],
            "error_type": type(exc).__name__})
    return result


CATALOG_PAGE_MAX = 200            # столько строк отдаём за один запрос


@router.get("/openrouter/{provider_id}/catalog")
async def catalog(provider_id: int, request: Request, q: str | None = None,
                  limit: int = 50, offset: int = 0, include_stale: bool = False):
    """Страница каталога + ЧЕСТНЫЕ счётчики: {items, total, returned, has_more}.

    Раньше ручка отдавала голый список с потолком в 200 строк и без offset, а
    сортировка шла по remote_id: у каталога OpenRouter (430+ моделей) весь конец
    алфавита — включая `z-ai/*`, то есть искомую GLM 5.3, — был недостижим ничем,
    кроме поиска, о котором интерфейс не подсказывал (audit-11, OR-002). Потолок
    не поднят до огромного числа: это лечит один каталог и ломает следующий.
    Поиск (q) фильтрует ВЕСЬ каталог в БД до нарезки страницы, поэтому total
    относится ровно к тому, что владелец сейчас ищет.
    """
    svc = _svc_or_404(request)
    limit = max(1, min(limit, CATALOG_PAGE_MAX))
    offset = max(0, offset)

    def _filtered(stmt):
        stmt = stmt.where(catalog_t.c.provider_id == provider_id)
        if not include_stale:
            stmt = stmt.where(catalog_t.c.stale.is_(False))
        if q:
            stmt = stmt.where(sa.or_(catalog_t.c.remote_id.ilike(f"%{q}%"),
                                     catalog_t.c.display_name.ilike(f"%{q}%")))
        return stmt

    async with svc.db.session() as s:
        total = int((await s.execute(
            _filtered(sa.select(sa.func.count()).select_from(catalog_t)))).scalar_one() or 0)
        rows = (await s.execute(_filtered(sa.select(catalog_t))
                                .order_by(catalog_t.c.remote_id)
                                .limit(limit).offset(offset))).fetchall()
    items = [dict(r._mapping) for r in rows]
    return {"items": items, "total": total, "returned": len(items),
            "offset": offset, "limit": limit,
            "has_more": offset + len(items) < total,
            "query": q or ""}


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
            **catalog_price_values(c), pricing_known=known_prices(catalog_price_values(c)),
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


ENV_API_KEY = identity.ENV_API_KEY                # каноническое имя живёт в одном месте
ENV_MODELS = "BOSSMAN_OPENROUTER_MODELS"          # конфигурация, не код: "z-ai/glm-4.5-air,qwen/qwen3-coder"
ENV_PROVIDER_NAME = identity.PROVIDER_NAME


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
    """Путь провайдера через окружение — теперь ВТОРОЙ, а не единственный.

    Ключ по-прежнему берётся только из переменной (в репозитории и логах его
    нет) и один раз шифруется в vault. Изменилось два: читаются оба исторических
    имени переменной (audit-11, OR-003 — владелец не должен угадывать, какое из
    трёх верное), а существующий провайдер ищется указателем, а не подстрокой
    «openrouter.ai» в адресе: подстрока делает своим любой чужой прокси.
    """
    import os
    cred = identity.env_credential()
    if not cred.configured:
        return
    if cred.conflicts:
        await svc.bus.emit("openrouter.credential_conflict", detail=cred.conflict_message)
    key = cred.key
    remote_ids = [m.strip() for m in (os.environ.get(ENV_MODELS) or "").split(",") if m.strip()]
    row = await identity.provider_row(svc.db, svc.vault)
    if row is not None:
        if not svc.vault.decrypt(row.get("api_key_enc")):
            async with svc.db.session() as s:
                await s.execute(sa.update(providers_t).where(providers_t.c.id == row["id"]).values(
                    api_key_enc=svc.vault.encrypt(key)))
                await s.commit()
            await svc.bus.emit("provider.key_from_env", provider_id=row["id"], source=cred.source)
        provider_id = int(row["id"])
    else:
        created = await svc.registry.create_provider(ENV_PROVIDER_NAME, identity.PROVIDER_KIND,
                                                     DEFAULT_BASE, key)
        provider_id = int(created.get("id"))
        await svc.bus.emit("provider.bootstrapped", provider_id=provider_id, name=ENV_PROVIDER_NAME,
                           source=cred.source)
    await identity.remember_provider(svc.db, svc.vault, provider_id)
    if remote_ids:
        aliases = await _ensure_models(svc, provider_id, remote_ids)
        if aliases:
            await svc.bus.emit("model.bootstrapped", provider_id=provider_id, aliases=aliases, source=ENV_MODELS)


FEATURE = Feature(name="openrouter", router=router, setup=setup)
