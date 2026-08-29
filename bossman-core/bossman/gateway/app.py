from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import AuthManager, AuthenticatedClient, ensure_alias_allowed
from .backends import BackendError
from .config import GatewayConfig, load_gateway_config
from .router import ModelRouter, RouteNotFound
from .telemetry import GatewayMetrics


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

    async def run_json(path: str, payload: dict[str, Any], c: AuthenticatedClient) -> JSONResponse:
        alias = str(payload.get("model") or "")
        if not alias:
            raise HTTPException(400, "model is required")
        ensure_alias_allowed(c, alias)
        capabilities = set()
        if any(isinstance(m.get("content"), list) for m in payload.get("messages", []) if isinstance(m, dict)):
            capabilities.add("vision")
        try:
            routes = owned_router.resolve(alias, capabilities)
        except RouteNotFound as exc:
            raise HTTPException(404, str(exc)) from exc

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
                return JSONResponse(body, headers={"x-bossman-backend": route.backend_name, "x-bossman-route-model": route.model})
            except Exception as exc:
                route.backend.health.healthy = False
                errors.append(f"{route.backend_name}/{route.model}: {type(exc).__name__}: {exc}")
                continue
        metrics.end(started, None, error=True)
        raise HTTPException(502, {"message": "All model routes failed", "attempts": errors})

    async def run_stream(path: str, payload: dict[str, Any], c: AuthenticatedClient):
        alias = str(payload.get("model") or "")
        if not alias:
            raise HTTPException(400, "model is required")
        ensure_alias_allowed(c, alias)
        capabilities = set()
        if any(isinstance(m.get("content"), list) for m in payload.get("messages", []) if isinstance(m, dict)):
            capabilities.add("vision")
        try:
            routes = owned_router.resolve(alias, capabilities)
        except RouteNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
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
                    return
                except Exception as exc:
                    route.backend.health.healthy = False
                    errors.append(f"{route.backend_name}/{route.model}: {type(exc).__name__}: {exc}")
                    if emitted:
                        metrics.end(started, route.backend_name, error=True)
                        yield b'\ndata: {"error":{"message":"upstream stream failed"}}\n\n'
                        return
                finally:
                    if acquired:
                        route.backend.semaphore.release()
            metrics.end(started, None, error=True)
            yield ("data: " + json.dumps({"error":{"message":"All model routes failed","attempts":errors}}) + "\n\n").encode()
        return StreamingResponse(generator(), media_type="text/event-stream", headers={"x-accel-buffering":"no"})

    @app.post("/v1/chat/completions")
    async def chat(request: Request, c: AuthenticatedClient = Depends(client)):
        payload = await request.json()
        return await run_stream("/v1/chat/completions", payload, c) if payload.get("stream") else await run_json("/v1/chat/completions", payload, c)

    @app.post("/v1/responses")
    async def responses(request: Request, c: AuthenticatedClient = Depends(client)):
        payload = await request.json()
        return await run_stream("/v1/responses", payload, c) if payload.get("stream") else await run_json("/v1/responses", payload, c)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request, c: AuthenticatedClient = Depends(client)):
        payload = await request.json()
        return await run_json("/v1/embeddings", payload, c)

    return app
