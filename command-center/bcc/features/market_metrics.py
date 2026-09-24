"""Market metrics (Twitch OI/CVD collector) — read-only surface of the SAME Bossman.

The collector itself runs as its own process (`python -m bcc.market.collector
run`) so a browser/player crash never touches the backend. Bossman exposes
what it recorded, under the owner's token:

    GET  /api/market/status   ledger counts + collector heartbeat (market.metrics.status)
    GET  /api/market/export   CSV of the index (market.metrics.export)
    POST /api/market/stop     write the STOP file (a stop is always allowed)

There is no start, trade, order or exchange endpoint — data collection only.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..market import CAPABILITIES, schema
from ..market.ledger import Ledger
from . import Feature

router = APIRouter()
CHANNEL = "k1m6a"


def _root(request: Request) -> Path:
    return Path(request.app.state.svc.settings.data_dir) / "market-data" / "twitch" / CHANNEL


@router.get("/market/status")
async def status(request: Request):
    root = _root(request)
    out = {"channel": CHANNEL, "root": str(root), "capabilities": list(CAPABILITIES),
           "trading": "NONE — data collection only", "dataset_status": "DATA_COLLECTION",
           "collector": None, "ledger": None}
    hb = root / "reports" / "collector-status.json"
    if hb.exists():
        try:
            out["collector"] = json.loads(hb.read_text(encoding="utf-8"))
        except ValueError:
            out["collector"] = {"error": "unreadable heartbeat"}
    if (root / "market-observations.sqlite").exists():
        led = Ledger(root)
        try:
            out["ledger"] = led.counts()
        finally:
            led.close()
    return out


@router.get("/market/export")
async def export(request: Request, day: str | None = None):
    root = _root(request)
    if not (root / "market-observations.sqlite").exists():
        return PlainTextResponse("", media_type="text/csv")
    led = Ledger(root)
    try:
        path = led.export_csv(day)
    finally:
        led.close()
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/csv")


@router.post("/market/stop")
async def stop(request: Request):
    root = _root(request)
    root.mkdir(parents=True, exist_ok=True)
    (root / "STOP").write_text(schema.utc_now(), encoding="utf-8")
    return {"stop": True, "path": str(root / "STOP")}


FEATURE = Feature(name="market_metrics", router=router)
