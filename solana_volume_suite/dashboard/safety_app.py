"""Local safety control plane with no signing, wallet or strategy imports."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from solana_volume_suite.core.liquidity_gate import check_liquidity

app = FastAPI(title="Solana Safety Assessment")


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_lamports: int = Field(strict=True, gt=0, le=2**64 - 1)
    reserve_in: int = Field(strict=True, gt=0, le=2**64 - 1)
    reserve_out: int = Field(strict=True, gt=0, le=2**64 - 1)
    fee_bps: int = Field(default=25, strict=True, ge=0, lt=10000)


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
    return FileResponse(Path(__file__).parent / "static" / "safety.html")


@app.get("/api/telemetry")
@app.get("/api/trading/telemetry")
def get_telemetry():
    return telemetry()


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
    return {"status": "STOPPED", "live_execution_enabled": False}


@app.get("/api/trading/executions")
def executions():
    return {"executions": [], "persistence": "NO_EXECUTION_BACKEND"}


@app.get("/api/trading/budget")
def budget():
    return {"execution_budget_usd": 0, "spent_usd": None, "status": "DISABLED"}


@app.post("/api/bot/start")
@app.post("/api/bot/sweep")
@app.post("/api/vault/generate")
def disabled():
    return JSONResponse(status_code=403, content={"status": "BLOCKED", "reason": "SAFETY_ONLY_RUNTIME"})
