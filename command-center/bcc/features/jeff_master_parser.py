"""Owner button for the Jeff Master Parser (page «Jeff · паспорта»).

Endpoints (mounted under /api with the owner token/session auth like every feature):
  GET  /pit/master-parse          — configured?, running?, live progress, last result summary
  POST /pit/master-parse          — start a background run {participant?, since?, dry_run?, use_llm?}
  GET  /pit/master-parse/report   — the full report of the latest (or ?run_id=) run

The run itself is ``bcc.pit.master_parser`` — the same code as
``bossman pit master-parse`` and the пульт ``/parse``; one cross-process lock
means only one run at a time whichever surface started it. The data root is the
one this Command Center serves (``svc.settings.data_dir``), never another.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature

router = APIRouter()
_JOB: dict = {"thread": None, "error": None}
_JOB_LOCK = threading.Lock()


def _data_dir(request: Request) -> Path:
    return Path(request.app.state.svc.settings.data_dir)


def _summary(report: dict | None) -> dict | None:
    if not report:
        return None
    return {"run_id": report.get("run_id"), "dry_run": report.get("dry_run"),
            "finished_at": report.get("finished_at"), "duration_seconds": report.get("duration_seconds"),
            "corpus_messages": report.get("corpus_messages"), "totals": report.get("totals", {}),
            "revert": report.get("revert"),
            "participants": [{"label": p.get("label"), "status": p.get("status"),
                              "messages_total": p.get("messages_total"),
                              "facts_added": len(p.get("facts_added", [])),
                              "conflicts": len(p.get("conflicts", []))}
                             for p in report.get("participants", [])]}


def _run_in_thread(settings, options) -> None:
    from bcc.pit.master_parser import AlreadyRunning, run_master_parse
    try:
        asyncio.run(run_master_parse(settings, options))
        _JOB["error"] = None
    except AlreadyRunning:
        _JOB["error"] = "ALREADY_RUNNING"
    except Exception as exc:  # noqa: BLE001 — shown to the owner as a stable code
        _JOB["error"] = type(exc).__name__


class StartBody(BaseModel):
    participant: str = Field(default="", max_length=80)
    since: str = Field(default="", max_length=40)
    dry_run: bool = False
    use_llm: bool = True


@router.get("/pit/master-parse")
async def master_parse_status(request: Request):
    from bcc.pit.config import config_path
    from bcc.pit.master_parser import is_running, read_report, read_status
    data_dir = _data_dir(request)
    running = is_running(data_dir)
    return {"configured": config_path(data_dir).is_file(), "running": running,
            "status": read_status(data_dir), "last_error": _JOB.get("error"),
            "report": _summary(read_report(data_dir))}


@router.post("/pit/master-parse", status_code=202)
async def master_parse_start(body: StartBody, request: Request):
    from bcc.pit.config import config_path
    from bcc.pit.master_parser import Options, is_running, parse_since, resolve_settings
    data_dir = _data_dir(request)
    config = config_path(data_dir)
    if not config.is_file():
        raise HTTPException(409, detail={"code": "PIT_NOT_CONFIGURED",
                                         "message": "Jeff (PIT) не настроен в этих данных Bossman"})
    try:
        settings = resolve_settings(config)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, detail={"code": "PIT_CONFIG_INVALID",
                                         "message": type(exc).__name__}) from None
    try:
        since = parse_since(body.since)
    except ValueError:
        raise HTTPException(400, detail={"code": "BAD_SINCE",
                                         "message": "since: 7d, 24h, 30m или ISO-дата"}) from None
    options = Options(participant=body.participant.strip(), since=since, dry_run=body.dry_run,
                      use_llm=body.use_llm)
    with _JOB_LOCK:
        thread = _JOB.get("thread")
        if (thread is not None and thread.is_alive()) or is_running(data_dir):
            raise HTTPException(409, detail={"code": "ALREADY_RUNNING",
                                             "message": "Master Parser уже работает"})
        _JOB["error"] = None
        thread = threading.Thread(target=_run_in_thread, args=(settings, options), daemon=True,
                                  name="jeff-master-parse")
        _JOB["thread"] = thread
        thread.start()
    return {"started": True, "dry_run": body.dry_run}


@router.get("/pit/master-parse/report")
async def master_parse_report(request: Request, run_id: str = "latest"):
    from bcc.pit.master_parser import read_report
    report = read_report(_data_dir(request), run_id)
    if report is None:
        raise HTTPException(404, detail={"code": "NO_REPORT", "message": "Отчёта ещё нет"})
    return report


FEATURE = Feature(name="jeff_master_parser", router=router)
