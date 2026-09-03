"""HTTP-поверхность модуля. Только чтение и только симуляция.

Ручек, создающих ордер, здесь нет и быть не может: единственный «исполнитель» —
бумажный брокер в памяти процесса, а он вызывается из бэктеста и бенчмарка, а
не из HTTP. Приём источника требует одобрения владельца, поэтому ручка ingest
сделана намеренно отказной: одобрение приходит через существующую очередь
подтверждений (bossman.approvals), а не через тело запроса.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..perimeter import SCOPE_ADMIN, SCOPE_CHAT, require_scope
from .adapters import probe_all
from .safety import (EXTERNAL_WRITE_ACTIONS, OWNER_APPROVAL_REQUIRED,
                     PAPER_TRADING_ONLY, TRADING_EXECUTION, env_requested_live)

router = APIRouter(prefix="/trading-lab", tags=["trading-learning"])


def pipeline_status() -> dict:
    """Состояние пайплайна: что реально работает, а что заблокировано.

    Это же значение показывает экран. Оно строится ПРОВЕРКОЙ окружения, а не
    константой, иначе «BLOCKED» превратится в устаревшую надпись.
    """
    caps = probe_all()
    steps = [
        {"step": "ingest_video", "status": "OK", "evidence_class": "REAL_SANDBOX",
         "detail": "sha256 источника и манифест; URL регистрируется без скачивания"},
        {"step": "extract_audio", "status": "OK" if caps["extract_audio"].available else "BLOCKED",
         "evidence_class": "REAL_SANDBOX" if caps["extract_audio"].available else "BLOCKED",
         "detail": caps["extract_audio"].detail, "missing": list(caps["extract_audio"].missing)},
        {"step": "transcribe", "status": "OK" if caps["transcribe"].available else "BLOCKED",
         "evidence_class": "REAL_SANDBOX" if caps["transcribe"].available else "BLOCKED",
         "detail": caps["transcribe"].detail, "missing": list(caps["transcribe"].missing)},
        {"step": "extract_frames", "status": "OK" if caps["extract_frames"].available else "BLOCKED",
         "evidence_class": "REAL_SANDBOX" if caps["extract_frames"].available else "BLOCKED",
         "detail": caps["extract_frames"].detail, "missing": list(caps["extract_frames"].missing)},
        {"step": "chart_ocr", "status": "OK" if caps["chart_ocr"].available else "BLOCKED",
         "evidence_class": "REAL_SANDBOX" if caps["chart_ocr"].available else "BLOCKED",
         "detail": caps["chart_ocr"].detail, "missing": list(caps["chart_ocr"].missing)},
        {"step": "extract_claims", "status": "OK", "evidence_class": "REAL_SANDBOX",
         "detail": "детерминированная типизация + карантин недоверенного входа"},
        {"step": "normalize_strategy", "status": "OK", "evidence_class": "REAL_SANDBOX",
         "detail": "правило без стопа и без лимита времени не принимается"},
        {"step": "verify_claims", "status": "OK", "evidence_class": "HISTORICAL_REPLAY",
         "detail": "независимый верификатор против свечей"},
        {"step": "compile_backtest", "status": "OK", "evidence_class": "HISTORICAL_REPLAY",
         "detail": "замороженный план с plan_hash"},
        {"step": "run_backtest", "status": "OK", "evidence_class": "HISTORICAL_REPLAY",
         "detail": "решения строятся только из прошлого (анти-lookahead)"},
        {"step": "paper_trade", "status": "OK", "evidence_class": "SIMULATED",
         "detail": "комиссии, funding, проскальзывание, задержка, запрет идеального исполнения"},
        {"step": "lesson_builder", "status": "OK", "evidence_class": "SIMULATED",
         "detail": "урок рождается в карантине; прямой записи в процедурную память нет"},
        {"step": "trading_benchmark", "status": "OK", "evidence_class": "HISTORICAL_REPLAY",
         "detail": "DEVELOPMENT / SEALED_HOLDOUT / ADVERSARIAL / PAPER_REPLAY"},
    ]
    blocked = [s["step"] for s in steps if s["status"] == "BLOCKED"]
    return {
        "safety": {"trading_execution": TRADING_EXECUTION,
                   "paper_trading_only": PAPER_TRADING_ONLY,
                   "owner_approval_required": OWNER_APPROVAL_REQUIRED,
                   "external_write_actions": EXTERNAL_WRITE_ACTIONS,
                   "env_requested_live_ignored": env_requested_live()},
        "steps": steps, "blocked_steps": blocked,
        "pipeline_complete": not blocked,
        "badge": "PAPER" if not blocked else "BLOCKED",
    }


@router.get("/status", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def status() -> dict:
    return pipeline_status()


@router.get("/capabilities", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def capabilities() -> dict:
    return {name: {"available": c.available, "detail": c.detail, "missing": list(c.missing)}
            for name, c in probe_all().items()}


@router.get("/seed", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def seed() -> dict:
    from .seed import seed_report
    return seed_report()


@router.get("/benchmark", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def benchmark() -> dict:
    from .benchmark import run_benchmark
    return run_benchmark().as_dict()


@router.post("/ingest", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def ingest() -> dict:
    """Приём источника через HTTP запрещён намеренно.

    Одобрение владельца — это действие человека в очереди подтверждений, а не
    поле JSON, которое может прислать кто угодно с админ-токеном.
    """
    raise HTTPException(status_code=409, detail={
        "message": "ingest requires an owner approval from the approvals queue",
        "how": "bossman.approvals.create(kind='trading_lab.ingest', ...) then CLI ingest_video",
        "trading_execution": TRADING_EXECUTION})
