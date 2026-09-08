"""§20/§21 — типизированная способность File Intelligence и её эндпоинты.

Модель НЕ получает сюда произвольную командную строку. Она называет класс
задачи (§7) и цели; операция сайдкара берётся из перечисления, пути проходят
ScopePolicy, argv собирается списком. «Дать инструмент, принимающий строку
флагов» и «дать shell» — на этой границе одно и то же.

`apply` — эффектный, неидемпотентный вызов: он требует явного разрешения,
перепроверяет полномочия во время эффекта, пишет намерение в журнал ДО запуска
и разбирает неоднозначный исход отдельно (§22).

Фича по умолчанию ВЫКЛЮЧЕНА (§31). Выключенная — не поднимает ни одной петли,
не запускает моделей и отвечает 404-подобным отказом; её отсутствие ничего не
ломает, и её поломка не имеет права уронить старт Command Center (§30/§31).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import file_intelligence as fi
from ..file_intelligence import discovery as fi_discovery
from ..file_intelligence.models import Denied, JobState, Refusal
from ..file_intelligence.privacy import read_backend_mode
from ..file_intelligence.scope import ScopePolicy, default_protected_paths
from ..file_intelligence.service import FileIntelligenceService
from . import Feature

log = logging.getLogger(__name__)
router = APIRouter(prefix="/file-intelligence", tags=["file-intelligence"])

#: Где владелец разрешает работать. Пусто — File Intelligence не может никуда,
#: и это правильное умолчание для способности, которая двигает файлы.
ROOTS_ENV = "AIFS_ROOTS"


def _service(svc: Any) -> FileIntelligenceService:
    """Сервис создаётся ЛЕНИВО, при первом обращении (§30).

    На старте Bossman'а не создаётся ничего: ни процесса, ни модели, ни каталога.
    Выключенная фича обязана стоить ноль.
    """
    existing = getattr(svc, "_file_intelligence", None)
    if existing is not None:
        return existing
    import os
    roots = [r for r in os.environ.get(ROOTS_ENV, "").split(os.pathsep) if r.strip()]
    data_dir = Path(getattr(svc.settings, "data_dir", ".")).resolve()
    policy = ScopePolicy(
        authorized_roots=roots,
        protected_paths=default_protected_paths(data_dir),
        repository_root=Path(__file__).resolve().parents[3],
    )
    service = FileIntelligenceService(
        state_dir=data_dir / "file-intelligence", policy=policy)
    svc._file_intelligence = service
    return service


def _require_enabled() -> None:
    if not fi.enabled():
        raise Denied(Refusal.FEATURE_DISABLED,
                     f"{fi.FLAG} is off; the owner enables it explicitly "
                     f"({fi.FLAG_ENV}=1)")


def _fail(denied: Denied) -> dict[str, Any]:
    """Отказ отдаётся как ДАННЫЕ с названной причиной, а не как «ошибка».

    Владельцу и вызывающему нужна причина: STALE_REVIEW_PLAN и
    PROTECTED_REPOSITORY чинятся по-разному, и схлопывать их в 500 значит
    отобрать у них разницу.
    """
    return {"ok": False, **denied.as_dict()}


class AnalyzeRequest(BaseModel):
    """Типизированный вход. Ни одного поля, которое доедет до argv как флаг."""
    task_class: str = Field(default="file.categorize")
    targets: list[str] = Field(default_factory=list)
    remote_approved: bool = False


class ApplyRequest(BaseModel):
    job_id: str
    selected: list[str] = Field(default_factory=list)
    remote_approved: bool = False


@router.get("/status")
async def status(svc: Any = Depends(lambda: None)) -> dict[str, Any]:
    """Доктор (§18) — работает даже при выключенной фиче, чтобы владелец мог
    увидеть, ЧТО именно не готово, прежде чем включать."""
    found = fi_discovery.discover(probe=fi.enabled())
    mode, raw = read_backend_mode(fi_discovery.default_config_path())
    return {
        "flag": fi.FLAG,
        "enabled": fi.enabled(),
        "binary": found.as_dict(),
        "backend_mode": mode.value,
        "backend_setting": raw,
        "undo_headless": fi.UNDO_HEADLESS,
        "pinned_upstream_sha": fi_discovery.pinned_sha(),
        "task_classes": sorted(fi.TASK_CLASSES),
    }


def attach(svc: Any) -> None:
    """Довесить эндпоинты, которым нужен Services. Вызывается из setup()."""

    @router.post("/analyze")
    async def analyze(body: AnalyzeRequest) -> dict[str, Any]:
        try:
            _require_enabled()
            operation = fi.operation_for(body.task_class)
            job = await _service(svc).analyze(
                body.targets, operation=operation,
                remote_approved=body.remote_approved)
        except Denied as denied:
            return _fail(denied)
        return {"ok": job.state != JobState.DENIED.value, "job": job.as_dict()}

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        try:
            _require_enabled()
        except Denied as denied:
            return _fail(denied)
        job = _service(svc).load(job_id)
        if job is None:
            return {"ok": False, "refused": Refusal.PATH_DOES_NOT_EXIST.value}
        return {"ok": True, "job": job.as_dict()}

    @router.get("/jobs")
    async def list_jobs() -> dict[str, Any]:
        try:
            _require_enabled()
        except Denied as denied:
            return _fail(denied)
        return {"ok": True, "jobs": [j.as_dict() for j in _service(svc).list_jobs()]}

    @router.post("/apply")
    async def apply(body: ApplyRequest) -> dict[str, Any]:
        try:
            _require_enabled()
            job = await _service(svc).apply(
                body.job_id, body.selected, remote_approved=body.remote_approved)
        except Denied as denied:
            return _fail(denied)
        return {"ok": job.state == JobState.VERIFIED.value, "job": job.as_dict()}

    @router.post("/jobs/{job_id}/cancel")
    async def cancel(job_id: str) -> dict[str, Any]:
        try:
            _require_enabled()
        except Denied as denied:
            return _fail(denied)
        job = _service(svc).stop(job_id)
        return {"ok": job is not None, "job": job.as_dict() if job else None}

    @router.post("/jobs/{job_id}/reconcile")
    async def reconcile(job_id: str) -> dict[str, Any]:
        try:
            _require_enabled()
            job = _service(svc).reconcile(job_id)
        except Denied as denied:
            return _fail(denied)
        return {"ok": job.state == JobState.VERIFIED.value, "job": job.as_dict()}


async def setup(svc: Any) -> None:
    """Старт фичи. Выключенная — не делает НИЧЕГО.

    Ошибка здесь ловится и записывается: File Intelligence не имеет права
    уронить старт Command Center (§31).
    """
    try:
        attach(svc)
        if not fi.enabled():
            log.info("file_intelligence is off (%s=0); no runtime was created",
                     fi.FLAG_ENV)
            return
        # §22 — рестарт: разобрать работы, застигнутые падением. Это ЧТЕНИЕ и
        # переклассификация, а не переигрывание эффекта.
        recovered = _service(svc).recover_all()
        ambiguous = [j.job_id for j in recovered
                     if j.state == JobState.AMBIGUOUS.value]
        if ambiguous:
            log.warning("file_intelligence: %d job(s) need reconciliation: %s",
                        len(ambiguous), ", ".join(ambiguous))
    except Exception as exc:                       # pragma: no cover - защитный
        log.error("file_intelligence setup failed (feature stays off): %r", exc)


#: tick_seconds=0 — никакой фоновой петли (§30). Неиспользуемая фича не опрашивает
#: ничего и ничего не индексирует.
FEATURE = Feature(name="file_intelligence", router=router, setup=setup,
                  tick_seconds=0.0)
