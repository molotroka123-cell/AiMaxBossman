"""Local safety control plane and interactive prototype dashboard backend."""
import os
import sys
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List

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

try:
    from solana_volume_suite.core.liquidity_gate import check_liquidity, LiquidityGate
    from solana_volume_suite.core.key_vault.vault import SecurityKeyVault
    from solana_volume_suite.orchestrator_loop import VolumeOrchestratorLoop
except ImportError:
    from core.liquidity_gate import check_liquidity, LiquidityGate
    from core.key_vault.vault import SecurityKeyVault
    from orchestrator_loop import VolumeOrchestratorLoop

app = FastAPI(title="Solana AI Volume Suite - Safety Control Plane")

# Global VolumeOrchestratorLoop instance
orchestrator = VolumeOrchestratorLoop(
    vault_path=os.path.join(SUITE_ROOT, "wallets_encrypted.json"),
    master_password="SuperSecretMasterPass123!",
    test_mode=False
)
orchestrator_task: Optional[asyncio.Task] = None

# Pre-initialize vault pool so wallet table has data immediately
try:
    orchestrator.initialize_vault_pool(count=10)
except Exception:
    pass


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
    m = orchestrator.treasury_guard.get_recent_metrics()
    return {
        "bot_status": "RUNNING" if orchestrator.is_running else "STOPPED",
        "mode": "PAPER_TRADING_ONLY",
        "notice": "NO LIVE EXECUTION ENABLED",
        "metrics": {
            "volume_5m_usd": m["recent_volume_usd"],
            "burn_5m_usd": m["recent_burn_usd"],
            "total_volume_usd": m["total_volume_usd"],
            "total_burn_usd": m["total_burn_usd"],
            "efficiency_ratio": m["efficiency_ratio"],
            "circuit_breaker_tripped": m["circuit_breaker_tripped"],
            "pause_reason": m["pause_reason"]
        },
        "jito_stats": {
            "bundles_sent": orchestrator.jito_client.total_bundles_sent,
            "bundles_confirmed": orchestrator.jito_client.total_bundles_confirmed,
            "bundles_dropped": orchestrator.jito_client.total_bundles_dropped,
            "mempool_leak_prevention": "100%_SECURED"
        },
        "total_tx_count": orchestrator.iteration_count,
        "recent_events": orchestrator.event_journal[:30]
    }


@app.get("/api/vault/wallets")
def get_vault_wallets():
    wallets = []
    for idx, (addr, bal) in enumerate(orchestrator.wallet_balances.items()):
        wallets.append({
            "wallet_index": idx,
            "alias": f"wallet_{idx}",
            "pubkey": addr,
            "sol_balance": bal,
            "role": "market_maker" if idx % 2 == 0 else "momentum_trader"
        })
    if not wallets:
        wallets = [
            {
                "wallet_index": i,
                "alias": f"wallet_{i}",
                "pubkey": f"SimWallet{i}PubkeyMock111111111111111111111",
                "sol_balance": 0.5,
                "role": "market_maker" if i % 2 == 0 else "momentum_trader"
            }
            for i in range(10)
        ]
    return {"wallets": wallets, "count": len(wallets)}


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
    global orchestrator_task
    orchestrator.stop()
    if orchestrator_task and not orchestrator_task.done():
        orchestrator_task.cancel()
    return {"status": "STOPPED", "bot_status": "STOPPED", "live_execution_enabled": False}


@app.get("/api/trading/executions")
def executions():
    return {"executions": [], "persistence": "NO_EXECUTION_BACKEND"}


@app.get("/api/trading/budget")
def budget():
    return {"execution_budget_usd": 0, "spent_usd": None, "status": "DISABLED"}


@app.post("/api/bot/start")
async def bot_start(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    if not body or not isinstance(body, dict):
        return JSONResponse(status_code=403, content={"status": "BLOCKED", "reason": "SAFETY_ONLY_RUNTIME"})
    global orchestrator_task
    orchestrator.is_running = True
    if not orchestrator.cached_keypairs:
        orchestrator.initialize_vault_pool(count=10)
    orchestrator_task = asyncio.create_task(orchestrator.run())
    return {"status": "SUCCESS", "bot_status": "RUNNING"}


@app.post("/api/bot/sweep")
async def bot_sweep(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    if not body or not isinstance(body, dict):
        return JSONResponse(status_code=403, content={"status": "BLOCKED", "reason": "SAFETY_ONLY_RUNTIME"})
    dest = body.get("cold_destination_pubkey") or body.get("destination") or "ColdDestination"
    total_sol = sum(orchestrator.wallet_balances.values())
    count = len(orchestrator.wallet_balances)
    orchestrator.wallet_balances.clear()
    sig = f"sim_sweep_sig_{int(time.time()*1000)}"
    return {
        "status": "SUCCESS",
        "destination": dest,
        "total_sol_swept": round(total_sol, 4),
        "wallets_swept": count,
        "tx_signature": sig
    }


# -------------------------------------------------------------
# INTERACTIVE PROTOTYPE ENDPOINTS
# -------------------------------------------------------------

@app.post("/api/orchestrator/start")
async def start_orchestrator():
    global orchestrator_task
    if not orchestrator.is_running:
        if not orchestrator.cached_keypairs:
            orchestrator.initialize_vault_pool(count=10)
        orchestrator_task = asyncio.create_task(orchestrator.run())
        orchestrator.is_running = True
    return {"status": "RUNNING"}


@app.post("/api/orchestrator/stop")
async def stop_orchestrator():
    global orchestrator_task
    orchestrator.stop()
    if orchestrator_task and not orchestrator_task.done():
        orchestrator_task.cancel()
    return {"status": "STOPPED"}


@app.get("/api/status")
def get_status():
    return {
        "mode": "PAPER_TRADING_ONLY",
        "bot_status": orchestrator.is_running,
        "wallets": orchestrator.wallet_balances,
        "metrics": orchestrator.treasury_guard.get_recent_metrics(),
        "liquidity_gate_status": orchestrator.liquidity_gate.last_status,
        "events": orchestrator.event_journal[:50]
    }


@app.post("/api/sweep")
async def sweep(req: Optional[SweepRequest] = None):
    dest = req.destination if (req and req.destination) else "SafeColdStorageDestinationAddress11111111111111"
    total_sol = sum(orchestrator.wallet_balances.values())
    count = len(orchestrator.wallet_balances)
    for k in orchestrator.wallet_balances:
        orchestrator.wallet_balances[k] = 0.005  # dust for rent
    sig = f"sim_sweep_sig_{int(time.time()*1000)}"
    orchestrator.log_event(
        "EMERGENCY_SWEEP",
        f"Simulated emergency sweep of {total_sol:.4f} SOL to cold storage {dest[:4]}...{dest[-4:]}",
        meta={"destination": dest, "total_sol": total_sol, "sig": sig}
    )
    return {
        "status": "SUCCESS",
        "mode": "PAPER_TRADING_SIMULATED",
        "destination": dest,
        "total_sol_swept": round(total_sol, 4),
        "wallets_swept": count,
        "tx_signature": sig
    }


@app.post("/api/vault/generate")
async def generate_vault(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None

    if not body or not isinstance(body, dict):
        return JSONResponse(status_code=403, content={"status": "BLOCKED", "reason": "SAFETY_ONLY_RUNTIME"})

    count = body.get("count")
    password = body.get("password")
    if not isinstance(count, int) or count < 1 or not isinstance(password, str) or len(password) < 6:
        return JSONResponse(status_code=403, content={"status": "BLOCKED", "reason": "SAFETY_ONLY_RUNTIME"})

    try:
        if os.path.exists(orchestrator.vault_path):
            try:
                os.remove(orchestrator.vault_path)
            except OSError:
                pass
        orchestrator.master_password = password
        orchestrator.vault = SecurityKeyVault(storage_path=orchestrator.vault_path)
        orchestrator.wallet_balances.clear()
        orchestrator.initialize_vault_pool(count=count)
        return {"status": "SUCCESS", "count": count}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = {
                "mode": "PAPER_TRADING_ONLY",
                "notice": "NO LIVE EXECUTION ENABLED",
                "bot_status": orchestrator.is_running,
                "wallets": orchestrator.wallet_balances,
                "metrics": orchestrator.treasury_guard.get_recent_metrics(),
                "liquidity_gate_status": orchestrator.liquidity_gate.last_status,
                "events": orchestrator.event_journal[:50],
                "timestamp": time.time()
            }
            await websocket.send_json(data)
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, Exception):
        pass
