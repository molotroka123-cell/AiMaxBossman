from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import AuthManager, AuthenticatedClient, ensure_alias_allowed
from .backends import BackendError, CircuitOpenError
from .config import GatewayConfig, load_gateway_config
from .router import CloudPolicyDenied, ModelRouter, RouteNotFound
from .telemetry import GatewayMetrics


# Gateway перестаёт быть чёрным ящиком: одна строка лога на запрос с
# request_id/run_id, выбранным бэкендом, исходом и латентностью. Без тел
# запросов, промптов и ключей.
logger = logging.getLogger("bossman.gateway")


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
            # Отдельный код: Ñдро отличит «политика запретила облако» от «нечем
                        # служить» и от «модель недоступна». Данные наружу не ушли.
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
            try:
                metrics.queued += 1
                try:
                    await asyncio.wait_for(route.backend.semaphore.acquire(), timeout=cfg.queue_timeout_seconds)
                finally:
                    metrics.queued = max(0, metrics.queued - 1)
                try:
                    body, _ = await route.backend.json_request(path, forward)
                finally:
                    route.backend.semaphore.release()
                # expose alias externally so clients stay decoupled from backend model names
                if isinstance(body, dict) and "model" in body:
                    body["model"] = alias
                metrics.end(started, route.backend_name, body.get("usage") if isinstance(body, dict) else None)
                _log_outcome(request_id, run_id, c.name, alias, route.backend_name, route.model,
                             "ok", started, len(errors))
                return JSONResponse(body, headers={"x-bossman-backend": route.backend_name, "x-bossman-route-model": route.model})
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
                # на Ñортировку целей в resolve()
                route.backend.health.checked_at = time.time()
                errors.append(f"{route.backend_name}/{route.model}: {type(exc).__name__}: {exc}")
                continue
            except Exception as exc:
                route.backend.health.healthy = False
                route.backend.health.checked_at = time.time()
                errors.append(f"{route.backend_name}/{route.model}: {type(exc).__name__}: {exc}")
                continue
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
                acquired = False
                emitted = False
                try:
                    metrics.queued += 1
                    try:
                        await asyncio.wait_for(route.backend.semaphore.acquire(), timeout=cfg.queue_timeout_seconds)
                        acquired = True
                    finally:
                        metrics.queued = max(0, metrics.queued - 1)
                    async for chunk in route.backend.stream_request(path, forward):
                        emitted = True
                        yield chunk
                    metrics.end(started, route.backend_name)
                    _log_outcome(request_id, run_id, c.name, alias, route.backend_name,
                                 route.model, "ok", started, len(errors))
                    return
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
                    # даже на Ñортировку целей в resolve()
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