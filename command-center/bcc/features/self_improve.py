"""Self-improvement: READ-ONLY view for the Command Center.

GET /api/self-improve/state   observations, hypotheses, experiments, results, cost, rollback, pending owner decision
GET /api/self-improve/botmap  the generated bot map (BOT_MAP.json) or NOT_RUN when it was never generated

There is deliberately no POST/PUT/PATCH/DELETE route: proposing runs offline from the CLI, applying needs the
owner's token on the CLI. Missing data is returned as the literal N/A / NOT_RUN.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request

from ..self_improve.limits import NA, NOT_RUN
from ..self_improve.store import Store, default_root
from . import Feature

router = APIRouter(prefix="/self-improve", tags=["self-improve"])


def _root(request: Request) -> Path:
    return default_root(request.app.state.svc.settings.data_dir)


@router.get("/state")
async def state(request: Request):
    root = _root(request)
    if not (root / "journal" / "journal.jsonl").exists():
        return {"status": NOT_RUN, "reason": "цикл самоулучшения ещё не запускался (журнала нет)",
                "journal": {"ok": NA, "entries": 0}, "observations": {"total": 0, "failures": 0, "recent": []},
                "hypotheses": [], "experiments": [], "results": [], "cost": {"total_usd": 0.0, "cap_usd": 0.0},
                "rollback": [], "refusals": [], "owner_decision_required": [],
                "gain_claim": f"{NOT_RUN}: эксперименты не запускались, улучшение не заявляется"}
    return {"status": "OK", **Store(root).state()}


@router.get("/botmap")
async def botmap(request: Request):
    path = _root(request) / "BOT_MAP.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": NOT_RUN, "reason": "карта ботов не сгенерирована (python -m bcc.self_improve botmap)",
                "agents": [], "components": []}


FEATURE = Feature(name="self_improve", router=router)
