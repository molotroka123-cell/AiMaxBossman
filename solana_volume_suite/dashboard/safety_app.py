"""Authenticated, single-process, offline control plane."""
import asyncio
import os
import json
import time
from collections import deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from solana_volume_suite.core.liquidity_gate import check_liquidity
from solana_volume_suite.core.security import audit, require_virtual_mode, valid_bearer, validate_password
from solana_volume_suite.orchestrator_loop import VolumeOrchestratorLoop
from solana_volume_suite.tools.github_hygiene import GitHubHygieneSearcher, GitHubSearchError, GitHubRateLimitError

SUITE_ROOT = Path(__file__).resolve().parents[1]
orchestrator = None
orchestrator_task = None
sockets = set()
searcher = GitHubHygieneSearcher()
github_results = []
github_searched_at = None
control_lock = asyncio.Lock()
control_epoch = 0
BODY_TIMEOUT_SECONDS = 3.0


async def stop_runner():
    async with control_lock:
        await _stop_runner_locked()


async def _stop_runner_locked():
    global orchestrator_task, control_epoch
    control_epoch += 1
    if orchestrator is not None:
        orchestrator.stop()
    if orchestrator_task is not None:
        if not orchestrator_task.done():
            orchestrator_task.cancel()
        try:
            await orchestrator_task
        except asyncio.CancelledError:
            pass
        except Exception:
            audit("RUNNER_FAILURE", reason="TASK_FAILED")
        orchestrator_task = None
    if orchestrator is not None:
        orchestrator.save_state()


@asynccontextmanager
async def lifespan(app):
    global orchestrator, control_lock, control_epoch
    control_lock = asyncio.Lock()
    control_epoch = 0
    require_virtual_mode()
    validate_password(os.getenv("DASHBOARD_API_TOKEN", ""))
    orchestrator = VolumeOrchestratorLoop(
        max_allowed_loss_usd=float(os.getenv("MAX_ALLOWED_LOSS_USD", "40")),
        test_mode=False, state_path=SUITE_ROOT / "runtime" / "state.json")
    orchestrator.initialize_vault_pool()
    try:
        yield
    finally:
        await stop_runner()
        for ws in tuple(sockets):
            with suppress(RuntimeError, WebSocketDisconnect):
                await ws.close(code=1001)
        sockets.clear()


app = FastAPI(title="Virtual Bot Safety Control Plane", lifespan=lifespan)


class APIProtection:
    """Authenticate before reading bounded bodies. Never trust forwarded IP headers.

    A single shared dashboard token has one quota per operation across all IPs,
    stronger than per-IP limiting and constant-memory. Deploy one worker locally.
    """
    def __init__(self, app):
        self.app = app
        self.buckets = {}
        self.last_rate_log = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        raw_headers = scope.get("headers", [])
        hosts = [value.decode("latin-1") for key, value in raw_headers if key == b"host"]
        origins = [value.decode("latin-1") for key, value in raw_headers if key == b"origin"]
        status = None
        try:
            parsed_host = urlsplit("http://" + hosts[0]) if len(hosts) == 1 else None
            if (parsed_host is None or parsed_host.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed_host.username or parsed_host.password or parsed_host.path
                    or parsed_host.query or parsed_host.fragment):
                status = 400
            elif parsed_host.port is not None and not 1 <= parsed_host.port <= 65535:
                status = 400
            elif origins:
                scheme = {"ws": "http", "wss": "https"}.get(scope.get("scheme"), scope.get("scheme", "http"))
                if len(origins) != 1 or origins[0].lower() != scheme + "://" + hosts[0].lower():
                    status = 403
        except ValueError:
            status = 400
        if status is not None:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await JSONResponse({"detail": "Untrusted host or origin"}, status_code=status)(scope, receive, send)
        auth_headers = [value.decode("latin-1") for key, value in raw_headers if key == b"authorization"]
        if scope["type"] == "websocket":
            if len(auth_headers) > 1:
                return await send({"type": "websocket.close", "code": 1008})
            return await self.app(scope, receive, send)
        if not scope["path"].startswith("/api"):
            return await self.app(scope, receive, send)
        auth = auth_headers[0] if len(auth_headers) == 1 else ""
        if not valid_bearer(auth):
            await JSONResponse({"detail": "Unauthorized"}, status_code=401,
                               headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
            return
        scope.setdefault("state", {})["control_epoch"] = control_epoch
        path = scope["path"].rstrip("/")
        stops = {"/api/orchestrator/stop", "/api/bot/stop", "/api/trading/kill-switch"}
        if path not in stops:
            try:
                require_virtual_mode()
            except PermissionError:
                await JSONResponse({"detail": "VIRTUAL_ONLY"}, status_code=403)(scope, receive, send)
                return
        group = ("start" if path in {"/api/orchestrator/start", "/api/bot/start"}
                 else "vault" if path == "/api/vault/generate"
                 else "github" if path == "/api/github/search" else "other")
        if path not in stops:
            now = time.monotonic()
            bucket = self.buckets.setdefault(group, deque())
            while bucket and now - bucket[0] >= 60:
                bucket.popleft()
            limit = 120 if group == "other" else 5
            if len(bucket) >= limit:
                # Bound log volume too: one event per bucket window.
                if now - self.last_rate_log.get(group, float("-inf")) >= 60:
                    audit("security.rate_limit_exceeded", operation=group)
                    self.last_rate_log[group] = now
                await JSONResponse({"detail": "Rate limit exceeded"}, status_code=429,
                                   headers={"Retry-After": "60"})(scope, receive, send)
                return
            bucket.append(now)
        # STOP is body-independent and cannot be delayed by chunked input.
        body = bytearray()
        if path not in stops:
            try:
                async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        body.extend(message.get("body", b""))
                        if len(body) > 8192:
                            return await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await JSONResponse({"detail": "Request body deadline exceeded"}, status_code=408)(scope, receive, send)
            if body:
                try:
                    value = json.loads(body)
                    pending = [(value, 0)]
                    while pending:
                        item, depth = pending.pop()
                        if depth > 32:
                            raise ValueError("JSON nesting limit")
                        if isinstance(item, dict):
                            pending.extend((v, depth + 1) for v in item.values())
                        elif isinstance(item, list):
                            pending.extend((v, depth + 1) for v in item)
                except (ValueError, RecursionError):
                    return await JSONResponse({"detail": "Invalid or excessively nested JSON"}, status_code=400)(scope, receive, send)
        replayed = False
        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}
        await self.app(scope, replay, send)


app.add_middleware(APIProtection)


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_lamports: int = Field(strict=True, gt=0, le=2**64 - 1)
    reserve_in: int = Field(strict=True, gt=0, le=2**64 - 1)
    reserve_out: int = Field(strict=True, gt=0, le=2**64 - 1)
    fee_bps: int = Field(default=25, strict=True, ge=0, lt=10000)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    min_stars: int = Field(default=0, strict=True, ge=0, le=1_000_000_000)
    language: str = Field(default="Python", min_length=1, max_length=40)


def telemetry():
    return {"mode": "PAPER_TRADING", "live_execution_enabled": False, "paper_trading": True,
            "gemini_real_money_ready": False, "notice": "NO LIVE EXECUTION ENABLED",
            "bot_status": "RUNNING" if orchestrator and orchestrator.is_running else "STOPPED",
            "jito_status": "DISABLED", "confirmed_transactions": 0,
            "volume_5m_usd": None, "volume_1h_usd": None, "burn_rate": None,
            "wallets": [], "balances_status": "MOCK_ONLY", "liquidity": check_liquidity(1, {}, {})}


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})


@app.get("/api/trading/telemetry")
def get_trading_telemetry():
    return telemetry()


@app.get("/api/status")
@app.get("/api/telemetry")
def get_status():
    return {**telemetry(), "mode": "PAPER_TRADING_ONLY",
            "wallets": orchestrator.wallet_balances,
            "metrics": orchestrator.treasury_guard.get_recent_metrics(),
            "liquidity_gate_status": orchestrator.liquidity_gate.last_status,
            "events": orchestrator.event_journal[:50]}


@app.get("/api/vault/wallets")
def wallets():
    items = [{"wallet_index": i, "pubkey": addr, "sol_balance": balance, "source": "MOCK"}
             for i, (addr, balance) in enumerate(orchestrator.wallet_balances.items())]
    return {"wallets": items, "count": len(items)}


@app.get("/api/liquidity/status")
def liquidity_status():
    return telemetry()["liquidity"]


@app.post("/api/liquidity/assess")
def assess(req: Assessment):
    reserves = req.model_dump(exclude={"amount_lamports"})
    reserves.update(model="CONSTANT_PRODUCT", input_asset="SOL")
    return check_liquidity(req.amount_lamports, reserves, {})


@app.post("/api/trading/simulate")
def simulate():
    return JSONResponse(status_code=409, content={
        "state": "FAILED_OR_UNKNOWN", "reason": "VERIFIED_POOL_ADAPTER_UNAVAILABLE",
        "execution_allowed": False, "verified_side_effect": False, "liquidity_gate_status": "UNKNOWN"})


@app.post("/api/trading/kill-switch")
@app.post("/api/bot/stop")
@app.post("/api/orchestrator/stop")
async def kill_switch():
    await stop_runner()
    return {"status": "STOPPED", "bot_status": "STOPPED", "live_execution_enabled": False}


@app.get("/api/trading/executions")
def executions():
    return {"executions": [], "persistence": "NO_EXECUTION_BACKEND"}


@app.get("/api/trading/budget")
def budget():
    return {"execution_budget_usd": 0, "spent_usd": None, "status": "DISABLED"}


async def start_runner(expected_epoch=None):
    global orchestrator_task
    async with control_lock:
        if expected_epoch is not None and expected_epoch != control_epoch:
            return JSONResponse({"detail": "Start invalidated by a later stop; send a new request"}, status_code=409)
        require_virtual_mode()
        if orchestrator_task is None or orchestrator_task.done():
            if orchestrator_task is not None:
                try:
                    await orchestrator_task
                except (asyncio.CancelledError, Exception):
                    audit("RUNNER_FAILURE", reason="PREVIOUS_TASK_FAILED")
            orchestrator_task = asyncio.create_task(orchestrator.run())
            await asyncio.sleep(0)
        if orchestrator_task.done() or not orchestrator.is_running:
            return JSONResponse({"detail": "Runner did not start"}, status_code=503)
        return {"status": "RUNNING", "bot_status": "RUNNING", "mode": "PAPER_TRADING_ONLY"}


@app.post("/api/orchestrator/start")
async def start_orchestrator(request: Request):
    body = await request.body()
    if body not in (b"", b"{}"):
        audit("SECURITY_VIOLATION", reason="UNSUPPORTED_START_CONFIGURATION")
        return JSONResponse({"detail": "Start accepts no configuration or credentials"}, status_code=403)
    return await start_runner(request.state.control_epoch)


@app.post("/api/bot/start")
async def bot_start(request: Request):
    # Legacy callers must explicitly request simulation; no passwords or RPC URLs.
    try:
        body = await request.json()
    except ValueError:
        body = None
    if body != {"mode": "simulation"}:
        audit("SECURITY_VIOLATION", reason="UNSUPPORTED_START_CONFIGURATION")
        return JSONResponse({"detail": "Use mode=simulation only"}, status_code=403)
    return await start_runner(request.state.control_epoch)


@app.post("/api/vault/generate")
async def generate_vault(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or set(body) != {"count"}:
        return JSONResponse({"detail": "Only count is accepted; keys and passwords cannot be imported"}, status_code=403)
    count = body["count"]
    if type(count) is not int or not 1 <= count <= 100:
        return JSONResponse({"detail": "count must be an integer between 1 and 100"}, status_code=422)
    async with control_lock:
        if orchestrator_task is not None and not orchestrator_task.done():
            return JSONResponse({"detail": "Stop the simulation before resetting mock wallets"}, status_code=409)
        orchestrator.initialize_vault_pool(count)
        return {"status": "SUCCESS", "count": count, "mode": "MOCK_ONLY"}


@app.post("/api/bot/sweep")
@app.post("/api/sweep")
async def sweep(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or set(body) != {"destination"} or body["destination"] != "mock:cold":
        audit("SECURITY_VIOLATION", reason="NON_MOCK_SWEEP")
        return JSONResponse({"detail": "Only destination=mock:cold is accepted"}, status_code=403)
    async with control_lock:
        await _stop_runner_locked()
        total = sum(orchestrator.wallet_balances.values())
        orchestrator.wallet_balances = {key: 0.0 for key in orchestrator.wallet_balances}
        orchestrator.log_event("SIMULATED_SWEEP", "Reset fictitious balances")
        orchestrator.save_state()
        return {"status": "SUCCESS", "mode": "PAPER_TRADING_SIMULATED", "destination": "mock:cold",
                "total_sol_swept": total, "confirmed_onchain": False, "tx_signature": None}


@app.post("/api/github/search")
async def github_search(req: SearchRequest):
    global github_results, github_searched_at
    try:
        repos = await asyncio.to_thread(searcher.search_repositories, req.query, req.min_stars, req.language)
        filtered = searcher.filter_garbage(repos)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except GitHubRateLimitError as exc:
        audit("security.rate_limit_exceeded", operation="github_upstream")
        return JSONResponse({"detail": str(exc)}, status_code=429,
                            headers={"Retry-After": str(exc.retry_after)})
    except GitHubSearchError:
        return JSONResponse({"detail": "GitHub search unavailable; previous results retained"}, status_code=502)
    github_results = filtered
    github_searched_at = time.time()
    return github_results


@app.get("/api/github/results")
def results():
    return github_results


@app.websocket("/ws/telemetry")
async def websocket_telemetry(ws: WebSocket):
    if len(sockets) >= 4:
        await ws.close(code=1013)
        return
    sockets.add(ws)
    try:
        await ws.accept()
        auth = ws.headers.get("authorization")
        if not auth:
            first = await asyncio.wait_for(ws.receive_text(), timeout=3)
            if len(first) > 512:
                await ws.close(code=1008)
                return
            auth = "Bearer " + first
        if not valid_bearer(auth):
            await ws.close(code=1008)
            return
        while True:
            if not valid_bearer(auth):
                await ws.close(code=1008)
                return
            require_virtual_mode()
            await ws.send_json(get_status())
            await asyncio.sleep(1)
    except (WebSocketDisconnect, asyncio.TimeoutError, PermissionError):
        with suppress(RuntimeError, WebSocketDisconnect):
            await ws.close(code=1008)
    finally:
        sockets.discard(ws)
