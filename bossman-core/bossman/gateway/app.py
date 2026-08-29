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


# Gateway Ð¿ÐµÑ€ÐµÑÑ‚Ð°Ñ‘Ñ‚ Ð±Ñ‹Ñ‚ÑŒ Ñ‡Ñ‘Ñ€Ð½Ñ‹Ð¼ ÑÑ‰Ð¸ÐºÐ¾Ð¼: Ð¾Ð´Ð½Ð° ÑÑ‚Ñ€Ð¾ÐºÐ° Ð»Ð¾Ð³Ð° Ð½Ð° Ð·Ð°Ð¿Ñ€Ð¾Ñ Ñ
# request_id/run_id, Ð²Ñ‹Ð±Ñ€Ð°Ð½Ð½Ñ‹Ð¼ Ð±ÑÐºÐµÐ½Ð´Ð¾Ð¼, Ð¸ÑÑ…Ð¾Ð´Ð¾Ð¼ Ð¸ Ð»Ð°Ñ‚ÐµÐ½Ñ‚Ð½Ð¾ÑÑ‚ÑŒÑŽ. Ð‘ÐµÐ· Ñ‚ÐµÐ»
# Ð·Ð°Ð¿Ñ€Ð¾ÑÐ¾Ð², Ð¿Ñ€Ð¾Ð¼Ð¿Ñ‚Ð¾Ð² Ð¸ ÐºÐ»ÑŽÑ‡ÐµÐ¹.
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
        # ÐšÐ¾Ñ€Ñ€ÐµÐ»ÑÑ†Ð¸Ñ: Ð²Ñ…Ð¾Ð´ÑÑ‰Ð¸Ð¹ X-Request-Id (Ð¸Ð»Ð¸ ÑÐ²ÐµÐ¶Ð¸Ð¹ uuid) Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÑ‚ÑÑ
        # заголовком и попадает в лог каждой попытки маршрутизации. X-Run-Id —
        # ÑÐºÐ²Ð¾Ð·Ð½Ð¾Ð¹ id Ð¿Ñ€Ð¾Ð³Ð¾Ð½Ð° ÑÐ´Ñ€Ð°, Ñ‚Ð¾Ð¶Ðµ Ð»Ð¾Ð³Ð¸Ñ€ÑƒÐµÑ‚ÑÑ. Ð¡Ð¾Ð´ÐµÑ€Ð¶Ð¸Ð¼Ð¾Ðµ Ð·Ð°Ð¿Ñ€Ð¾ÑÐ¾Ð² Ð² Ð»Ð¾Ð³
        # Ð½Ðµ Ð¿Ð¸ÑˆÐµÑ‚ÑÑ Ð½Ð¸ÐºÐ¾Ð³Ð´Ð°.
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
            # ÐžÑ‚Ð´ÐµÐ»ÑŒÐ½Ñ‹Ð¹ ÐºÐ¾Ð´: ÑÐ´Ñ€Ð¾ Ð¾Ñ‚Ð»Ð¸Ñ‡Ð¸Ñ‚ Â«Ð¿Ð¾Ð»Ð¸Ñ‚Ð¸ÐºÐ° Ð·Ð°Ð¿Ñ€ÐµÑ‚Ð¸Ð»Ð° Ð¾Ð±Ð»Ð°ÐºÐ¾Â» Ð¾Ñ‚ Â«Ð½ÐµÑ‡ÐµÐ¼
            # Ð¾Ð±ÑÐ»ÑƒÐ¶Ð¸Ñ‚ÑŒÂ» Ð¸ Ð¾Ñ‚ Â«Ð¼Ð¾Ð´ÐµÐ»ÑŒ Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿Ð½Ð°Â». Ð”Ð°Ð½Ð½Ñ‹Ðµ Ð½Ð°Ñ€ÑƒÐ¶Ñƒ Ð½Ðµ ÑƒÑˆÐ»Ð¸.
            _log_outcome(request_id, run_id, c.name, alias, None, None, "error", None, 0)
            return JSONResponse({"error": {"code": "POLICY_DENIED", "message": str(exc)}},
                                status_code=403)
        except RouteNotFound as exc:
            _log_outcome(request_id, run_id, c.name, alias, None, None, "error", None, 0)
            raise HTTPException(404, str(exc)) from exc
        except CircuitOpenError as exc:
            # Ð²ÑÐµ Ð¿Ð¾Ð´Ñ…Ð¾Ð´ÑÑ‰Ð¸Ðµ Ñ†ÐµÐ»Ð¸ Ñ€Ð°Ð·Ð¾Ð¼ÐºÐ½ÑƒÑ‚Ñ‹ Ð°Ð²Ñ‚Ð¾Ð¼Ð°Ñ‚Ð¾Ð¼: Ð¾Ñ‚ÐºÐ°Ð· ÑÑ€Ð°Ð·Ñƒ, Ð±ÐµÐ·
            # Ð¾Ð¶Ð¸Ð´Ð°Ð½Ð¸Ñ Ñ‚Ð°Ð¹Ð¼Ð°ÑƒÑ‚Ð¾Ð² Ð¼Ñ‘Ñ€Ñ‚Ð²Ñ‹Ñ… Ð±ÑÐºÐµÐ½Ð´Ð¾Ð²
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
                    # ÐžÑˆÐ¸Ð±ÐºÐ° ÑÐ°Ð¼Ð¾Ð³Ð¾ Ð·Ð°Ð¿Ñ€Ð¾ÑÐ°/Ð¿Ð¾Ð»Ð¸Ñ‚Ð¸ÐºÐ¸ (4xx): Ð½Ðµ Ð¿ÐµÑ€ÐµÐºÐ»ÑŽÑ‡Ð°ÐµÐ¼ÑÑ Ð½Ð°
                    # ÑÐ»ÐµÐ´ÑƒÑŽÑ‰Ð¸Ð¹ Ñ‚Ð°Ñ€Ð³ÐµÑ‚ (Ð² Ñ‚.Ñ‡. Ð¾Ð±Ð»Ð°Ñ‡Ð½Ñ‹Ð¹) Ð¸ ÐÐ• Ð³Ð°ÑÐ¸Ð¼ Ð·Ð´Ð¾Ñ€Ð¾Ð²ÑŒÐµ
                    # Ð±ÑÐºÐµÐ½Ð´Ð° â€” Ñ‚Ð¾Ñ‚ Ð¶Ðµ Ð¾Ñ‚Ð²ÐµÑ‚ Ð´Ð°Ð» Ð±Ñ‹ Ð»ÑŽÐ±Ð¾Ð¹. Ð’Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÐ¼ ÐºÐ°Ðº ÐµÑÑ‚ÑŒ.
                    metrics.end(started, route.backend_name, error=True)
                    _log_outcome(request_id, run_id, c.name, alias, route.backend_name,
                                 route.model, "error", started, len(errors))
                    raise HTTPException(exc.status_code or 502,
                                        {"message": str(exc), "backend": route.backend_name}) from exc
                route.backend.health.healthy = False
                # checked_at Ð¾Ð±ÑÐ·Ð°Ñ‚ÐµÐ»ÐµÐ½: Ð±ÐµÐ· Ð½ÐµÐ³Ð¾ unhealthy-Ñ„Ð»Ð°Ð³ Ð½Ðµ Ð²Ð»Ð¸ÑÐµÑ‚ Ð´Ð°Ð¶Ðµ
                # Ð½Ð° ÑÐ¾Ñ€Ñ‚Ð¸Ñ€Ð¾Ð²ÐºÑƒ Ñ†ÐµÐ»ÐµÐ¹ Ð² resolve()
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
                        # 4xx Ð·Ð°Ð¿Ñ€Ð¾ÑÐ°/Ð¿Ð¾Ð»Ð¸Ñ‚Ð¸ÐºÐ¸: Ð½Ðµ Ð¿ÐµÑ€ÐµÐºÐ»ÑŽÑ‡Ð°ÐµÐ¼ÑÑ Ð½Ð° ÑÐ»ÐµÐ´ÑƒÑŽÑ‰Ð¸Ð¹
                        # (Ð² Ñ‚.Ñ‡. Ð¾Ð±Ð»Ð°Ñ‡Ð½Ñ‹Ð¹) Ñ‚Ð°Ñ€Ð³ÐµÑ‚ Ð¸ Ð½Ðµ Ð³Ð°ÑÐ¸Ð¼ Ð·Ð´Ð¾Ñ€Ð¾Ð²ÑŒÐµ Ð±ÑÐºÐµÐ½Ð´Ð°.
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
                    # checked_at Ð¾Ð±ÑÐ·Ð°Ñ‚ÐµÐ»ÐµÐ½: Ð±ÐµÐ· Ð½ÐµÐ³Ð¾ unhealthy-Ñ„Ð»Ð°Ð³ Ð½Ðµ Ð²Ð»Ð¸ÑÐµÑ‚
                    # Ð´Ð°Ð¶Ðµ Ð½Ð° ÑÐ¾Ñ€Ñ‚Ð¸Ñ€Ð¾Ð²ÐºÑƒ Ñ†ÐµÐ»ÐµÐ¹ Ð² resolve()
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
        # Ð¯Ð´Ñ€Ð¾ ÑÐ¾Ð¾Ð±Ñ‰Ð°ÐµÑ‚ Ð¾Ð±Ð»Ð°Ñ‡Ð½ÑƒÑŽ Ð¿Ð¾Ð»Ð¸Ñ‚Ð¸ÐºÑƒ Ð°Ð³ÐµÐ½Ñ‚Ð° Ð·Ð°Ð³Ð¾Ð»Ð¾Ð²ÐºÐ¾Ð¼. ÐžÑ‚ÑÑƒÑ‚ÑÑ‚Ð²Ð¸Ðµ = Ñ€Ð°Ð·Ñ€ÐµÑˆÐµÐ½Ð¾
        # (Ð¿Ñ€ÑÐ¼Ð¾Ð¹ ÑÑ‚Ð¾Ñ€Ð¾Ð½Ð½Ð¸Ð¹ ÐºÐ»Ð¸ÐµÐ½Ñ‚); ÑÐ´Ñ€Ð¾ BOSSMAN Ð²ÑÐµÐ³Ð´Ð° Ð¿Ñ€Ð¾ÑÑ‚Ð°Ð²Ð»ÑÐµÑ‚ ÑÐ²Ð½Ð¾.
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
