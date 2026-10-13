"""Local numeric forecast (TimesFM 2.5, opt-in) — same Bossman backend, under the owner's token.

    GET  /api/forecast/status   available? reason, model id/revision/licence, limits, loaded?
    POST /api/forecast          {"series": [numbers], "horizon": int}
                                -> point forecast + q0.1..q0.9 + model id/version

Disabled unless BOSSMAN_TIMESFM=1 and the model directory exists (bcc.forecast.timesfm_local).
When unavailable the POST answers 503 with ``unavailable: <reason>``; the model loads lazily
on the first forecast and always runs off the event loop.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..forecast.timesfm_local import ForecastError, TimesFMService
from . import Feature

router = APIRouter()


def _service(request: Request) -> TimesFMService:
    state = request.app.state
    svc = getattr(state, "timesfm", None)
    if svc is None:
        svc = TimesFMService(state.svc.settings.data_dir)
        state.timesfm = svc
    return svc


@router.get("/forecast/status")
async def forecast_status(request: Request):
    return _service(request).status()


@router.post("/forecast")
async def forecast(request: Request):
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        return JSONResponse({"error": "body must be JSON"}, status_code=422)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=422)
    service = _service(request)
    try:
        return await service.forecast(body.get("series"), body.get("horizon"))
    except ForecastError as exc:
        payload = {"error": exc.message}
        if exc.status == 503:
            payload["available"] = False
        return JSONResponse(payload, status_code=exc.status)


FEATURE = Feature(name="forecast", router=router)
