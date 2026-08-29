"""HTTP-эндпоинты Video Factory. Ошибки поднимаются как BossmanError и
рендерятся глобальным обработчиком (`errors.install_error_handlers`).

- POST /video/jobs   — создать + поставить в очередь (может дать QueueFull/
                       ResourceExhausted);
- GET  /video/jobs   — список джоб;
- GET  /video/jobs/{id} — состояние джобы (NotFound → 404).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..perimeter import SCOPE_CHAT, require_scope
from pydantic import BaseModel, Field

# Периметр: видео-задания — пользовательская операция уровня задач (chat).
router = APIRouter(prefix="/video", tags=["video"],
                   dependencies=[Depends(require_scope(SCOPE_CHAT))])


class CreateJobBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    prompts: list[str] = Field(..., min_length=1)
    duration_s: float = Field(default=5.0, gt=0.0, le=120.0)


def _service():
    # Ленивый доступ к синглтону пакета — избегаем цикла импорта с __init__.
    from . import FACTORY

    return FACTORY


@router.post("/jobs")
async def create_job(body: CreateJobBody) -> dict:
    """Создать джобу и поставить в ограниченную очередь. Переполнение очереди →
    QueueFull (503); отказ допуска на генерации всплывёт из воркера."""
    svc = _service()
    job = svc.create_and_enqueue(body.title, list(body.prompts), duration_s=body.duration_s)
    return job.to_public()


@router.get("/jobs")
async def list_jobs() -> dict:
    """Список джоб на диске (id/title/state/scenes)."""
    return {"jobs": _service().factory.list_jobs()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    """Состояние одной джобы. Нет такой → errors.NotFound (404)."""
    return _service().factory.load(job_id).to_public()
