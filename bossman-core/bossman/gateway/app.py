from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import AuthManager, AuthenticatedClient, ensure_alias_allowed
from .backends import BackendError, CircuitOpenError
from .config import GatewayConfig, ModelTarget, load_gateway_config
from .prompt_cache import (
    SSEUsageCollector,
    cache_metadata_rejected,
    extract_cache_usage,
    prepare_provider_payload,
)
from .router import CloudPolicyDenied, ModelRouter, RouteNotFound
from .telemetry import GatewayMetrics
from ..cost_control.enforcer import BudgetApprovalRejected as _BudgetApprovalRejected
from ..cost_control.enforcer import BudgetHardStop as _BudgetHardStop


# Gateway перестаёт быть чёрным ящиком: одна строка лога на запрос с
# request_id/run_id, выбранным бэкендом, исходом и латентностью. Без тел
# запросов, промптов и ключей.
logger = logging.getLogger("bossman.gateway")


class BudgetPricingUnknown(RuntimeError):
    """Нельзя безопасно оценить верхнюю границу расхода — cloud-попытка отклонена
    (fail closed), а не «наверное дёшево»."""


def _prompt_tokens_upper(payload: dict[str, Any]) -> int:
    """Консервативная оценка prompt-токенов ТЕМ ЖЕ методом, что и остальное
    ядро (bossman.context.estimate_tokens) — не второй алгоритм подсчёта."""
    from ..context import estimate_tokens
    billable_prefix = {
        "messages_or_input": payload.get("messages") or payload.get("input") or "",
        "tools": payload.get("tools") or [],
    }
    text = json.dumps(billable_prefix, ensure_ascii=False)
    return estimate_tokens(text)


def _completion_tokens_upper(payload: dict[str, Any], target: ModelTarget) -> int | None:
    mt = payload.get("max_tokens")
    if isinstance(mt, int) and mt > 0:
        return mt
    if target.max_output_tokens:
        return int(target.max_output_tokens)
    return None  # верхней границы нет — оценивать небезопасно


def _route_price(target: ModelTarget) -> tuple[Decimal, Decimal, Decimal] | None:
    """USD/token из конфигурации, объявленной как USD/МИЛЛИОН токенов (см.
    комментарий у полей ModelTarget) — единицы не перепутать. Нет цены на
    ОБА направления → цена неизвестна целиком, половинчатых оценок не бывает."""
    if not (target.price_usd_per_million_input_tokens and target.price_usd_per_million_output_tokens):
        return None
    try:
        million = Decimal("1000000")
        p_in = Decimal(str(target.price_usd_per_million_input_tokens)) / million
        p_out = Decimal(str(target.price_usd_per_million_output_tokens)) / million
        fixed = Decimal(str(target.fixed_request_usd)) if target.fixed_request_usd else Decimal("0")
        return p_in, p_out, fixed
    except Exception:
        return None


def _per_token(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value)) / Decimal("1000000")
    except Exception:
        return None
    return result if result.is_finite() and result >= 0 else None


def _cache_prices(route, p_in: Decimal, cache_meta: dict[str, Any]) -> tuple[Decimal, Decimal]:
    """Cache read/write prices per token, with documented Anthropic ratios as fallback."""
    target = route.target
    read = _per_token(target.price_usd_per_million_cache_read_tokens)
    write_field = (target.price_usd_per_million_cache_write_tokens_1h
                   if cache_meta.get("ttl") == "1h"
                   else target.price_usd_per_million_cache_write_tokens_5m)
    write = _per_token(write_field)
    anthropic = (route.model or "").lower().lstrip("~").startswith("anthropic/")
    if read is None:
        read = p_in * (Decimal("0.1") if anthropic else Decimal("1"))
    if write is None:
        multiplier = Decimal("2") if cache_meta.get("ttl") == "1h" else Decimal("1.25")
        write = p_in * (multiplier if anthropic else Decimal("1"))
    return read, write


async def _cost_reserve(route, payload: dict[str, Any], *, cloud_allowed: bool,
                        request_id: str, attempt_index: int, run_id: str | None, client_name: str,
                        cache_meta: dict[str, Any] | None = None):
    """Immediately-before-cloud-upstream гейт (см. integration/GATEWAY_COST_HOOK.md
    пакета). Только для облачных целей; для локальных — no-op (None, None).

    Неизвестная цена/потолок токенов при ВКЛЮЧЁННЫХ бюджетах → BudgetPricingUnknown
    (fail closed), а не тихая отправка. Если ни одна политика бюджета не настроена
    вовсе — не изобретаем лимит и пропускаем губернатора целиком (пустые env-
    переменные бюджета НЕ создают лимит, см. integration/CONFIG.md)."""
    if not route.is_cloud:
        return None, None
    from ..cost_control.enforcer import BudgetEnforcer
    from ..cost_control.models import BudgetContext
    from ..cost_control.pricing import estimate_usd
    from ..cost_control.runtime import GOVERNOR, STORE as cost_store

    price = _route_price(route.target)
    completion_cap = _completion_tokens_upper(payload, route.target)
    if price is None or completion_cap is None:
        if cost_store.has_enabled_policies():
            raise BudgetPricingUnknown(
                f"{route.backend_name}/{route.model}: неизвестна точная цена или "
                f"потолок completion-токенов, а бюджетная политика включена")
        return None, None
    p_in, p_out, fixed = price
    cache_meta = cache_meta or {}
    cache_read_price, cache_write_price = _cache_prices(route, p_in, cache_meta)
    # A cold Anthropic write is more expensive than ordinary input.  Reserve
    # against that upper bound; a warm read is reconciled from provider usage.
    reserve_prompt_price = max(p_in, cache_write_price) if cache_meta.get("cache_control_applied") else p_in
    estimated = estimate_usd(prompt_tokens_upper=_prompt_tokens_upper(payload),
                             completion_tokens_upper=completion_cap,
                             prompt_price_per_token=reserve_prompt_price, completion_price_per_token=p_out,
                             fixed_request_usd=fixed)
    context = BudgetContext(run_id=run_id, owner_device_id=client_name)
    idempotency_key = f"{request_id}:{attempt_index}:{route.backend_name}:{route.model}"

    async def _approval_create(kind: str, preview: str, **kw):
        from .. import approvals
        return await approvals.create(kind, preview, **kw)

    async def _approval_wait(approval_id):
        from .. import approvals
        return await approvals.wait(approval_id)

    enforcer = BudgetEnforcer(GOVERNOR, _approval_create, _approval_wait)
    reservation = await enforcer.reserve(context, estimated, idempotency_key=idempotency_key,
                                         cloud_allowed=cloud_allowed)
    return reservation, (enforcer, p_in, p_out, cache_read_price, cache_write_price, fixed)


async def _cost_settle(enforcer, reservation, usage_body: Any, p_in: Decimal, p_out: Decimal,
                       cache_read_price: Decimal, cache_write_price: Decimal,
                       fixed: Decimal) -> Decimal | None:
    """reserve → commit по факту usage. Стрим/бэкенд без usage → коммитим саму
    бронь (верхнюю границу): расход никогда не занижается, лишь изредка
    (честно) переоценивается.

    Учёт расхода никогда не превращает уже успешный (и уже оплаченный у
    провайдера) ответ в ошибку для клиента — все сбои здесь только логируются."""
    from .. import events
    from ..cost_control.models import money
    from ..cost_control.pricing import cache_aware_actual_usd
    from ..cost_control.store import BudgetExtensionRequired
    try:
        if isinstance(usage_body, dict) and usage_body.get("usage"):
            u = extract_cache_usage(usage_body)
            if u.get("provider_cost") is not None:
                actual = money(u["provider_cost"])
            else:
                actual = cache_aware_actual_usd(
                    prompt_tokens=int(u.get("prompt_tokens") or 0),
                    completion_tokens=int(u.get("completion_tokens") or 0),
                    cached_tokens=int(u.get("cached_tokens") or 0),
                    cache_write_tokens=int(u.get("cache_write_tokens") or 0),
                    prompt_price_per_token=p_in,
                    completion_price_per_token=p_out,
                    cache_read_price_per_token=cache_read_price,
                    cache_write_price_per_token=cache_write_price,
                    fixed_request_usd=fixed,
                )
        else:
            actual = reservation.estimated_usd
        try:
            enforcer.commit(reservation.id, actual)
        except BudgetExtensionRequired as exc:
            ext = enforcer.governor.extend(reservation.id, exc.delta_usd)
            if not ext.allowed:
                # Провайдер уже выполнил и, вероятно, выставит счёт — отменить
                # это нельзя. Громко сигналим (телефон получит CRITICAL) и
                # оставляем бронь как есть для ручной сверки: TTL-уборщик
                # (cost_control.subsystem) со временем её освободит сам. Тихого
                # перерасхода СВЕРХ лимита при этом не происходит.
                events.emit("budget.exceeded",
                            reason="actual cost exceeds reservation and extension denied — "
                                   "needs manual reconciliation",
                            reservation_id=reservation.id, delta_usd=str(exc.delta_usd))
            else:
                enforcer.commit(reservation.id, actual)
        return actual
    except Exception as exc:  # noqa: BLE001 — учёт расхода не должен ронять успешный ответ
        events.emit("budget.exceeded",
                    reason=f"cost settlement failed: {type(exc).__name__}: {exc}",
                    reservation_id=getattr(reservation, "id", None))
        # Usage can be incomplete or internally inconsistent.  The pre-call
        # reservation is deliberately conservative, so commit that bound as
        # billing evidence instead of leaving it ACTIVE until TTL cleanup.
        try:
            enforcer.commit(reservation.id, reservation.estimated_usd)
            return reservation.estimated_usd
        except Exception as fallback_exc:  # DB failure: keep ACTIVE for reconciliation
            events.emit("budget.exceeded",
                        reason=f"fallback cost settlement failed: {type(fallback_exc).__name__}: {fallback_exc}",
                        reservation_id=getattr(reservation, "id", None))
            return None


def _cache_economics(route, usage_body: Any, cache_meta: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    usage = extract_cache_usage(usage_body if isinstance(usage_body, dict) else None)
    provider_actual = usage.get("provider_cost")
    price = _route_price(route.target)
    if price is None:
        return provider_actual, None
    from ..cost_control.models import money
    from ..cost_control.pricing import actual_usd, cache_aware_actual_usd
    p_in, p_out, fixed = price
    read_price, write_price = _cache_prices(route, p_in, cache_meta)
    baseline = actual_usd(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        prompt_price_per_token=p_in,
        completion_price_per_token=p_out,
        fixed_request_usd=fixed,
    )
    if provider_actual is not None:
        return money(provider_actual), baseline
    try:
        actual = cache_aware_actual_usd(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int(usage.get("cached_tokens") or 0),
            cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
            prompt_price_per_token=p_in,
            completion_price_per_token=p_out,
            cache_read_price_per_token=read_price,
            cache_write_price_per_token=write_price,
            fixed_request_usd=fixed,
        )
    except ValueError:
        actual = None
    return actual, baseline


def _upstream_provider(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("provider"), str):
        return body["provider"]
    metadata = body.get("openrouter_metadata")
    if isinstance(metadata, dict):
        for key in ("provider_name", "provider"):
            if isinstance(metadata.get(key), str):
                return metadata[key]
    return None


def create_gateway_app(config: GatewayConfig | None = None, router: ModelRouter | None = None) -> FastAPI:
    cfg = config or load_gateway_config()
    owned_router = router or ModelRouter(cfg)
    auth = AuthManager(cfg)
    metrics = GatewayMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.gateway_config = cfg
        app.state.gateway_router = owned_router
        app.state.gateway_metrics = metrics
        yield
        await owned_router.close()

    app = FastAPI(title="BOSSMAN AI Gateway", version="3.0.0", lifespan=lifespan)

    async def client(request: Request) -> AuthenticatedClient:
        return auth.authenticate(request)

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and int(length) > cfg.request_body_limit_bytes:
            return JSONResponse({"error": {"message": "Request body too large", "type": "request_too_large"}}, status_code=413)
        return await call_next(request)

    @app.middleware("http")
    async def correlation(request: Request, call_next):
                # Корреляция: входящий X-Request-Id (или свежий uuid) возвращается
        # заголовком и попадает в лог каждой попытки маршрутизации. X-Run-Id —
                # сквозной id прогона ядра, тоже логируется. Содержимое запросов в лог
                # не пишется никогда.
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        run_id = request.headers.get("x-run-id") or request.headers.get("x-bossman-run-id")
        request.state.request_id = request_id
        request.state.run_id = run_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    def _correlation(request: Request | None) -> tuple[str | None, str | None]:
        if request is None:
            return None, None
        return getattr(request.state, "request_id", None), getattr(request.state, "run_id", None)

    def _prepare_cache(route, forward: dict[str, Any], path: str,
                       request: Request | None) -> tuple[dict[str, Any], dict[str, Any]]:
        backend_cfg = route.backend.config
        session_id = request.headers.get("x-bossman-session-id", "") if request else ""
        requested_ttl = request.headers.get("x-bossman-cache-ttl") if request else None
        try:
            return prepare_provider_payload(
                forward,
                provider_kind=backend_cfg.kind,
                provider_model=route.model,
                session_id=session_id,
                requested_ttl=requested_ttl,
                default_ttl=backend_cfg.prompt_cache_ttl,
                enabled=backend_cfg.prompt_cache_enabled,
                session_affinity=backend_cfg.session_affinity_enabled,
                endpoint=path,
            )
        except Exception:  # fail-open: request semantics survive cache shaping failure
            return dict(forward), {
                "provider": str(backend_cfg.kind or "openai").lower(),
                "model": route.model,
                "enabled": bool(backend_cfg.prompt_cache_enabled),
                "ttl": backend_cfg.prompt_cache_ttl if backend_cfg.prompt_cache_ttl in {"5m", "1h"} else "5m",
                "mode": "none",
                "session_affinity": False,
                "session_id_hash": None,
                "prefix_hash": None,
                "prefix_tokens": 0,
                "cache_control_applied": False,
                "state": "DEGRADED",
                "miss_reason": "invalid metadata",
                "degraded_reason": "invalid metadata",
            }

    def _log_outcome(request_id: str | None, run_id: str | None, client_name: str,
                     alias: str, backend: str | None, model: str | None,
                     outcome: str, started: float | None, fallbacks: int) -> None:
        latency_ms = round((time.perf_counter() - started) * 1000, 1) if started is not None else None
        logger.info(
            "request_id=%s run_id=%s client=%s alias=%s backend=%s model=%s "
            "outcome=%s latency_ms=%s fallbacks=%d",
            request_id or "-", run_id or "-", client_name, alias or "-",
            backend or "-", model or "-", outcome,
            latency_ms if latency_ms is not None else "-", fallbacks,
        )

    @app.get("/health")
    async def health():
        backend_health = await owned_router.refresh_health(force=False)
        usable = any(v["healthy"] for v in backend_health.values()) if backend_health else False
        return {"status": "ok" if usable else "degraded", "backends": backend_health}

    @app.get("/metrics")
    async def metric_snapshot(_: AuthenticatedClient = Depends(client)):
        if not cfg.metrics_enabled:
            raise HTTPException(404, "Metrics disabled")
        return metrics.snapshot()

    @app.get("/v1/models")
    async def models(c: AuthenticatedClient = Depends(client)):
        rows = [m for m in owned_router.list_models() if "*" in c.config.allowed_aliases or m["id"] in c.config.allowed_aliases]
        return {"object": "list", "data": rows}

    async def run_json(path: str, payload: dict[str, Any], c: AuthenticatedClient,
                       cloud_allowed: bool = True, request: Request | None = None) -> JSONResponse:
        request_id, run_id = _correlation(request)
        alias = str(payload.get("model") or "")
        if not alias:
            raise HTTPException(400, "model is required")
        ensure_alias_allowed(c, alias)
        capabilities = set()
        if any(isinstance(m.get("content"), list) for m in payload.get("messages", []) if isinstance(m, dict)):
            capabilities.add("vision")
        try:
            routes = owned_router.resolve(alias, capabilities, cloud_allowed=cloud_allowed)
        except CloudPolicyDenied as exc:
            # Отдельный код: ядро отличит «политика запретила облако» от «нечем
                        #   обслужить» и от «модель недоступна». Данные наружу не ушли.
            _log_outcome(request_id, run_id, c.name, alias, None, None, "error", None, 0)
            return JSONResponse({"error": {"code": "POLICY_DENIED", "message": str(exc)}},
                                status_code=403)
        except RouteNotFound as exc:
            _log_outcome(request_id, run_id, c.name, alias, None, None, "error", None, 0)
            raise HTTPException(404, str(exc)) from exc
        except CircuitOpenError as exc:
                        # все подходящие цели разомкнуты автоматом: отказ сразу, без
                        # ожидания таймаутов мёртвых бэкендов
            _log_outcome(request_id, run_id, c.name, alias, None, None, "error", None, 0)
            return JSONResponse({"error": {"code": "NO_BACKENDS_AVAILABLE", "message": str(exc)}},
                                status_code=503)

        started = metrics.begin(alias)
        errors = []
        for route in routes:
            forward = dict(payload)
            forward["model"] = route.model
            prepared, cache_meta = _prepare_cache(route, forward, path, request)
            cache_degraded = None
            reservation = None
            cost_state = None
            settled = False
            try:
                # Cost Governor: непосредственно перед РЕАЛЬНОЙ облачной попыткой,
                # для каждой цели своей — см. integration/GATEWAY_COST_HOOK.md.
                # Локальные цели проходят мимо (reservation остаётся None).
                reservation, cost_state = await _cost_reserve(
                    route, prepared, cloud_allowed=cloud_allowed, request_id=request_id,
                    attempt_index=len(errors), run_id=run_id, client_name=c.name,
                    cache_meta=cache_meta)
                metrics.queued += 1
                try:
                    await asyncio.wait_for(route.backend.semaphore.acquire(), timeout=cfg.queue_timeout_seconds)
                finally:
                    metrics.queued = max(0, metrics.queued - 1)
                try:
                    try:
                        body, _ = await route.backend.json_request(path, prepared)
                    except BackendError as exc:
                        if prepared != forward and cache_metadata_rejected(str(exc), exc.status_code):
                            # Cache metadata is optional.  A provider contract
                            # mismatch retries once without it, under the same
                            # Cost Governor reservation and policy decision.
                            cache_degraded = "invalid metadata"
                            body, _ = await route.backend.json_request(path, forward)
                        else:
                            raise
                finally:
                    route.backend.semaphore.release()
                if reservation is not None:
                    enforcer, p_in, p_out, p_cache_read, p_cache_write, fixed = cost_state
                    await _cost_settle(enforcer, reservation, body, p_in, p_out,
                                       p_cache_read, p_cache_write, fixed)
                    settled = True
                # expose alias externally so clients stay decoupled from backend model names
                if isinstance(body, dict) and "model" in body:
                    body["model"] = alias
                usage_body = body if isinstance(body, dict) else None
                usage = extract_cache_usage(usage_body)
                actual_cost, baseline_cost = _cache_economics(route, usage_body, cache_meta)
                metrics.end(started, route.backend_name,
                            body.get("usage") if isinstance(body, dict) else None)
                metrics.end_cache(
                    cache_meta, usage, backend=route.backend_name, model=route.model,
                    actual_cost=actual_cost, baseline_cost=baseline_cost,
                    upstream_provider=_upstream_provider(body), degraded_reason=cache_degraded,
                )
                _log_outcome(request_id, run_id, c.name, alias, route.backend_name, route.model,
                             "ok", started, len(errors))
                return JSONResponse(body, headers={"x-bossman-backend": route.backend_name, "x-bossman-route-model": route.model})
            except BudgetPricingUnknown as exc:
                # Неизвестная цена при включённом бюджете: fail closed, к сети
                # даже не подступались — не сигнал здоровья бэкенда.
                _log_outcome(request_id, run_id, c.name, alias, route.backend_name,
                             route.model, "error", started, len(errors))
                errors.append(f"{route.backend_name}/{route.model}: BudgetPricingUnknown: {exc}")
                continue
            except (_BudgetHardStop, _BudgetApprovalRejected) as exc:
                # Бюджет отказал (STOP) или человек отклонил (ASK): облачный
                # запрос физически не ушёл — цель просто пропускается дальше,
                # как и остальные исчерпанные/недоступные цели.
                _log_outcome(request_id, run_id, c.name, alias, route.backend_name,
                             route.model, "error", started, len(errors))
                errors.append(f"{route.backend_name}/{route.model}: {type(exc).__name__}: {exc}")
                continue
            except BackendError as exc:
                if not exc.failover:
                                        # Ошибка самого запроса/политики (4xx): не переключаемся на
                                        # следующий таргет (в т.ч. облачный) и НЕ гасим здоровье
                                        # бэкенда — тот же ответ дал бы любой. Возвращаем как есть.
                    metrics.end(started, route.backend_name, error=True)
                    _log_outcome(request_id, run_id, c.name, alias, route.backend_name,
                                 route.model, "error", started, len(errors))
                    raise HTTPException(exc.status_code or 502,
                                        {"message": str(exc), "backend": route.backend_name}) from exc
                route.backend.health.healthy = False
                                # checked_at обязателен: без него unhealthy-флаг не влияет даже
                # на сортировку целей в resolve()
                route.backend.health.checked_at = time.time()
                errors.append(f"{route.backend_name}/{route.model}: {type(exc).__name__}: {exc}")
                continue
            except Exception as exc:
                route.backend.health.healthy = False
                route.backend.health.checked_at = time.time()
                errors.append(f"{route.backend_name}/{route.model}: {type(exc).__name__}: {exc}")
                continue
            finally:
                # Бронь, которую не довели до commit() (любой выход, кроме
                # успешного _cost_settle выше) — вернуть деньги в пул. release()
                # идемпотентен и на уже закоммиченной/освобождённой брони — no-op,
                # так что settled нужен только для отличия «коммит не удался,
                # оставили для ручной сверки» (см. _cost_settle) от прочих путей.
                if reservation is not None and not settled:
                    cost_state[0].release(reservation.id)
        metrics.end(started, None, error=True)
        _log_outcome(request_id, run_id, c.name, alias, None, None, "error", started, len(errors))
        raise HTTPException(502, {"message": "All model routes failed", "attempts": errors})

    async def run_stream(path: str, payload: dict[str, Any], c: AuthenticatedClient,
                         cloud_allowed: bool = True, request: Request | None = None):
        request_id, run_id = _correlation(request)
        alias = str(payload.get("model") or "")
        if not alias:
            raise HTTPException(400, "model is required")
        ensure_alias_allowed(c, alias)
        capabilities = set()
        if any(isinstance(m.get("content"), list) for m in payload.get("messages", []) if isinstance(m, dict)):
            capabilities.add("vision")
        try:
            routes = owned_router.resolve(alias, capabilities, cloud_allowed=cloud_allowed)
        except CloudPolicyDenied as exc:
            _log_outcome(request_id, run_id, c.name, alias, None, None, "error", None, 0)
            return JSONResponse({"error": {"code": "POLICY_DENIED", "message": str(exc)}},
                                status_code=403)
        except RouteNotFound as exc:
            _log_outcome(request_id, run_id, c.name, alias, None, None, "error", None, 0)
            raise HTTPException(404, str(exc)) from exc
        except CircuitOpenError as exc:
            _log_outcome(request_id, run_id, c.name, alias, None, None, "error", None, 0)
            return JSONResponse({"error": {"code": "NO_BACKENDS_AVAILABLE", "message": str(exc)}},
                                status_code=503)
        # Streaming cannot transparently fall back after bytes are emitted. We fall back only before first byte.
        async def generator():
            started = metrics.begin(alias)
            errors = []
            for route in routes:
                forward = dict(payload); forward["model"] = route.model
                prepared, cache_meta = _prepare_cache(route, forward, path, request)
                cache_degraded = None
                collector = SSEUsageCollector()
                acquired = False
                emitted = False
                reservation = None
                cost_state = None
                settled = False
                try:
                    # Cost Governor: как и в run_json, до сети для КАЖДОЙ облачной
                    # цели своя проверка. Стрим не даёт точный usage до конца
                    # потока — коммитим по факту саму бронь, см. _cost_settle.
                    reservation, cost_state = await _cost_reserve(
                        route, prepared, cloud_allowed=cloud_allowed, request_id=request_id,
                        attempt_index=len(errors), run_id=run_id, client_name=c.name,
                        cache_meta=cache_meta)
                    metrics.queued += 1
                    try:
                        await asyncio.wait_for(route.backend.semaphore.acquire(), timeout=cfg.queue_timeout_seconds)
                        acquired = True
                    finally:
                        metrics.queued = max(0, metrics.queued - 1)
                    try:
                        async for chunk in route.backend.stream_request(path, prepared):
                            emitted = True
                            collector.feed(chunk)
                            yield chunk
                    except BackendError as exc:
                        if not emitted and prepared != forward and \
                                cache_metadata_rejected(str(exc), exc.status_code):
                            cache_degraded = "invalid metadata"
                            async for chunk in route.backend.stream_request(path, forward):
                                emitted = True
                                collector.feed(chunk)
                                yield chunk
                        else:
                            raise
                    collector.finish()
                    if reservation is not None:
                        enforcer, p_in, p_out, p_cache_read, p_cache_write, fixed = cost_state
                        await _cost_settle(enforcer, reservation, collector.body, p_in, p_out,
                                           p_cache_read, p_cache_write, fixed)
                        settled = True
                    usage = extract_cache_usage(collector.body)
                    actual_cost, baseline_cost = _cache_economics(route, collector.body, cache_meta)
                    metrics.end(started, route.backend_name,
                                (collector.body or {}).get("usage"))
                    metrics.end_cache(
                        cache_meta, usage, backend=route.backend_name, model=route.model,
                        actual_cost=actual_cost, baseline_cost=baseline_cost,
                        degraded_reason=cache_degraded,
                    )
                    _log_outcome(request_id, run_id, c.name, alias, route.backend_name,
                                 route.model, "ok", started, len(errors))
                    return
                except (BudgetPricingUnknown, _BudgetHardStop, _BudgetApprovalRejected) as exc:
                    # Пре-сетевой отказ бюджета: как и обычный локальный сбой —
                    # байты ещё не пошли, пробуем следующую цель.
                    errors.append(f"{route.backend_name}/{route.model}: {type(exc).__name__}: {exc}")
                    _log_outcome(request_id, run_id, c.name, alias, route.backend_name,
                                 route.model, "error", started, len(errors))
                    continue
                except BackendError as exc:
                    if not exc.failover:
                                                # 4xx запроса/политики: не переключаемся на следующий
                                                # (в т.ч. облачный) таргет и не гасим здоровье бэкенда.
                        metrics.end(started, route.backend_name, error=True)
                        _log_outcome(request_id, run_id, c.name, alias, route.backend_name,
                                     route.model, "error", started, len(errors))
                        if not emitted:
                            yield ("data: " + json.dumps({"error": {"code": "REQUEST_REJECTED",
                                    "message": str(exc), "status": exc.status_code}}) + "\n\n").encode()
                        else:
                            yield b'\ndata: {"error":{"message":"upstream stream failed"}}\n\n'
                        return
                    route.backend.health.healthy = False
                                    # checked_at обязателен: без него unhealthy-флаг не влияет
                    # даже на сортировку целей в resolve()
                    route.backend.health.checked_at = time.time()
                    errors.append(f"{route.backend_name}/{route.model}: {type(exc).__name__}: {exc}")
                    if emitted:
                        metrics.end(started, route.backend_name, error=True)
                        _log_outcome(request_id, run_id, c.name, alias, route.backend_name,
                                     route.model, "error", started, len(errors))
                        yield b'\ndata: {"error":{"message":"upstream stream failed"}}\n\n'
                        return
                except Exception as exc:
                    route.backend.health.healthy = False
                    route.backend.health.checked_at = time.time()
                    errors.append(f"{route.backend_name}/{route.model}: {type(exc).__name__}: {exc}")
                    if emitted:
                        metrics.end(started, route.backend_name, error=True)
                        _log_outcome(request_id, run_id, c.name, alias, route.backend_name,
                                     route.model, "error", started, len(errors))
                        yield b'\ndata: {"error":{"message":"upstream stream failed"}}\n\n'
                        return
                finally:
                    if acquired:
                        route.backend.semaphore.release()
                    if reservation is not None and not settled:
                        enforcer, p_in, p_out, p_cache_read, p_cache_write, fixed = cost_state
                        if emitted:
                            # Часть потока уже ушла клиенту — провайдер, вероятно,
                            # принял к оплате сгенерированное. Коммитим бронь
                            # целиком (верхнюю границу), как и в успешном случае
                            # выше: расход никогда не занижается.
                            try:
                                collector.finish()
                                await _cost_settle(enforcer, reservation, collector.body, p_in, p_out,
                                                   p_cache_read, p_cache_write, fixed)
                            except Exception:  # noqa: BLE001 — сбой учёта не должен рвать генератор
                                pass
                        else:
                            enforcer.release(reservation.id)
            metrics.end(started, None, error=True)
            _log_outcome(request_id, run_id, c.name, alias, None, None, "error", started, len(errors))
            yield ("data: " + json.dumps({"error":{"message":"All model routes failed","attempts":errors}}) + "\n\n").encode()
        return StreamingResponse(generator(), media_type="text/event-stream", headers={"x-accel-buffering":"no"})

    def _cloud_allowed(request: Request) -> bool:
                # Ядро сообщает облачную политику агента заголовком. Отсутствие = разрешено
                # (прямой сторонний клиент); ядро BOSSMAN всегда проставляет явно.
        return request.headers.get("x-bossman-cloud-allowed", "1").strip() not in ("0", "false", "no")

    @app.post("/v1/chat/completions")
    async def chat(request: Request, c: AuthenticatedClient = Depends(client)):
        try:
            payload = await request.json()
        except ValueError as exc:  # битый JSON — ошибка клиента, не сервера
            raise HTTPException(400, "invalid JSON body") from exc
        ca = _cloud_allowed(request)
        return await (run_stream if payload.get("stream") else run_json)(
            "/v1/chat/completions", payload, c, cloud_allowed=ca, request=request)

    @app.post("/v1/responses")
    async def responses(request: Request, c: AuthenticatedClient = Depends(client)):
        try:
            payload = await request.json()
        except ValueError as exc:  # битый JSON — ошибка клиента, не сервера
            raise HTTPException(400, "invalid JSON body") from exc
        ca = _cloud_allowed(request)
        return await (run_stream if payload.get("stream") else run_json)(
            "/v1/responses", payload, c, cloud_allowed=ca, request=request)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request, c: AuthenticatedClient = Depends(client)):
        try:
            payload = await request.json()
        except ValueError as exc:  # битый JSON — ошибка клиента, не сервера
            raise HTTPException(400, "invalid JSON body") from exc
        return await run_json("/v1/embeddings", payload, c, request=request)

    return app
