"""Local safety control plane and interactive prototype dashboard backend."""
import os
import sys
import time
import asyncio
from pathlib import Path
from typing import Optional

# Ensure solana_volume_suite and workspace root are importable
SUITE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SUITE_ROOT not in sys.path:
    sys.path.insert(0, SUITE_ROOT)
WORKSPACE_ROOT = os.path.dirname(SUITE_ROOT)
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

# Safety inspection deliberately has no dependency on key vaults, SDKs,
# transaction clients or strategy loops. Importing this app cannot create keys.
from solana_volume_suite.core.liquidity_gate import check_liquidity

app = FastAPI(title="Solana Safety Control Plane — execution disabled")


def _execution_blocked():
    return JSONResponse(status_code=403, content={
        "status": "BLOCKED", "reason": "SAFETY_ONLY_RUNTIME",
        "execution_allowed": False, "verified_side_effect": False,
    })


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_lamports: int = Field(strict=True, gt=0, le=2**64 - 1)
    reserve_in: int = Field(strict=True, gt=0, le=2**64 - 1)
    reserve_out: int = Field(strict=True, gt=0, le=2**64 - 1)
    fee_bps: int = Field(default=25, strict=True, ge=0, lt=10000)


class SweepRequest(BaseModel):
    destination: Optional[str] = "SafeColdStorageDestinationAddress11111111111111"


def telemetry():
    return {
        "mode": "PAPER_TRADING", "live_execution_enabled": False,
        "notice": "NO LIVE EXECUTION ENABLED", "bot_status": "STOPPED",
        "jito_status": "DISABLED", "confirmed_transactions": 0,
        "volume_5m_usd": None, "volume_1h_usd": None, "burn_rate": None,
        "wallets": [], "balances_status": "NOT_FETCHED",
        "liquidity": check_liquidity(1, {}, {}),
    }


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


# Safety-only telemetry endpoints preserved for test compatibility
@app.get("/api/trading/telemetry")
def get_trading_telemetry():
    return telemetry()


@app.get("/api/telemetry")
def get_suite_telemetry():
    return {**telemetry(), "mode": "PAPER_TRADING_ONLY",
            "metrics": {"volume_5m_usd": None, "burn_5m_usd": None,
                        "total_volume_usd": None, "total_burn_usd": None,
                        "efficiency_ratio": None, "circuit_breaker_tripped": True,
                        "pause_reason": "SAFETY_ONLY_RUNTIME"},
            "jito_stats": {"bundles_sent": 0, "bundles_confirmed": 0,
                           "bundles_dropped": 0, "mempool_leak_prevention": "NOT_MEASURED"},
            "total_tx_count": 0, "recent_events": []}


@app.get("/api/vault/wallets")
def get_vault_wallets():
    return {"wallets": [], "count": 0, "balances_status": "NOT_FETCHED"}


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
        "execution_allowed": False, "verified_side_effect": False,
        "liquidity_gate_status": "UNKNOWN",
    })


@app.post("/api/trading/kill-switch")
@app.post("/api/bot/stop")
def kill_switch():
    return {"status": "STOPPED", "bot_status": "STOPPED", "live_execution_enabled": False}


@app.get("/api/trading/executions")
def executions():
    return {"executions": [], "persistence": "NO_EXECUTION_BACKEND"}


@app.get("/api/trading/budget")
def budget():
    return {"execution_budget_usd": 0, "spent_usd": None, "status": "DISABLED"}


@app.post("/api/bot/start")
async def bot_start(request: Request):
    return _execution_blocked()


@app.post("/api/bot/sweep")
async def bot_sweep(request: Request):
    return _execution_blocked()


# -------------------------------------------------------------
# LEGACY CONTROL ROUTES — fail closed for every request
# -------------------------------------------------------------

@app.post("/api/orchestrator/start")
async def start_orchestrator():
    return _execution_blocked()


@app.post("/api/orchestrator/stop")
async def stop_orchestrator():
    return kill_switch()


@app.get("/api/status")
def get_status():
    return {"mode": "PAPER_TRADING_ONLY", "bot_status": False,
            "live_execution_enabled": False, "notice": "NO LIVE EXECUTION ENABLED",
            "wallets": {}, "metrics": get_suite_telemetry()["metrics"],
            "liquidity_gate_status": "UNKNOWN", "events": []}


@app.post("/api/sweep")
async def sweep(req: Optional[SweepRequest] = None):
    return _execution_blocked()


@app.post("/api/vault/generate")
async def generate_vault(request: Request):
    return _execution_blocked()


@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({**get_status(), "timestamp": time.time()})
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
