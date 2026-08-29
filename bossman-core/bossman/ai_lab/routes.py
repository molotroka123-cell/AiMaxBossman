"""Stage 11 — AI Lab: REST-роутер поверх CandidateStore/EvalRunner/Exporter.

Периметр: ВСЕ маршруты — только admin-устройству Stage 6. Траектории, кандидаты
и экспорт — это сырьё для обучения; ни chat-, ни events-устройству они не нужны.

Containment: клиент НИКОГДА не передаёт путь файловой системы. Только
sandbox_id; путь к trajectory.jsonl сервер вычисляет сам внутри workspace
песочницы и проверяет, что итог не выходит за её пределы (symlink/../ и т.п.).
В ошибках реальный путь хоста не фигурирует.

Жизненный цикл: роутер STATELESS — хранилища открываются на запрос, воркеров и
долгоживущих соединений нет, поэтому подсистема жизненного цикла ему не нужна.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..config import settings
from ..perimeter import SCOPE_ADMIN, require_scope
from .candidates import CandidateStore, load_trajectory, sandbox_trajectory_path
from .export import EvalRunner, Exporter

router = APIRouter(prefix="/api/lab", tags=["ai-lab"],
                   dependencies=[Depends(require_scope(SCOPE_ADMIN))])


def _lab_root() -> Path:
    return Path(settings.workspace_dir) / "_ai_lab"


def _store() -> CandidateStore:
    return CandidateStore(_lab_root())


def _exporter() -> Exporter:
    return Exporter(_store(), _lab_root() / "exports")


def _sandbox_workspace() -> Path:
    from ..sandbox.subsystem import MANAGER
    return Path(MANAGER.workspace_root)


def _trajectory_path(sandbox_id: str) -> Path:
    """sandbox_id → путь к trajectory.jsonl СТРОГО внутри workspace песочницы.

    Сдерживание живёт в candidates.sandbox_trajectory_path (единая батарея
    отказов для store и роутов). Отказ единообразный (NOT_FOUND без пути
    хоста): не различаем «плохой id» и «нет файла», чтобы не превращать ручку
    в зонд файловой системы.
    """
    return sandbox_trajectory_path(sandbox_id, _sandbox_workspace())


class CreateCandidateIn(BaseModel):
    sandbox_id: str


class DecideIn(BaseModel):
    approve: bool
    by: str = "owner"


class EvalIn(BaseModel):
    cases: list[dict] = Field(default_factory=list)
    model_alias: str = "bossman-fast"
    max_cases: int = 5


@router.get("/trajectories/{sandbox_id}")
async def inspect_trajectory(sandbox_id: str):
    """Read-only инспекция raw-траектории из workspace песочницы."""
    path = _trajectory_path(sandbox_id)
    events, sha, meta = load_trajectory(path)
    return {"sandbox_id": sandbox_id, "sha256": sha, **meta,
            "events_preview": events[:20]}     # превью; сырьё уходит только в candidates


@router.get("/candidates")
async def list_candidates():
    # trajectory_path — путь хоста: клиенту не нужен (только sandbox_id).
    return [{k: v for k, v in row.items() if k not in ("samples", "trajectory_path")}
            for row in _store().list()]


@router.post("/candidates")
async def create_candidate(body: CreateCandidateIn):
    """Кандидат создаётся ТОЛЬКО из sandbox_id — произвольный путь хоста этой
    ручке недоступен: путь считает CandidateStore.create_from_sandbox с полным
    сдерживанием (reject ../, абсолютных, дисков, UNC, NUL, %2e%2e, symlink-
    побега) и перепроверкой после resolve()."""
    cand = _store().create_from_sandbox(
        body.sandbox_id, workspace_root=_sandbox_workspace(),
        sandbox_id_verified=True)
    return {"id": cand.id, "state": cand.state, "samples": len(cand.samples),
            "reasons": list(cand.reasons)}


@router.post("/candidates/{candidate_id}/decide")
async def decide_candidate(candidate_id: str, body: DecideIn):
    """Человеческий гейт обучающего набора — такое же консеквентное решение,
    как approvals ядра; скоуп admin уже проверен на роутере."""
    cand = _store().decide(candidate_id, approve=body.approve, by=body.by)
    return {"id": cand.id, "state": cand.state, "decided_by": cand.decided_by}


@router.post("/evals/run")
async def run_eval(body: EvalIn):
    from ..resource_brain import BRAIN
    runner = EvalRunner(chat_fn=None, brain=BRAIN)
    return runner.run(body.cases, model_alias=body.model_alias,
                      max_cases=max(0, body.max_cases))


@router.post("/exports/{candidate_id}/sft")
async def export_sft(candidate_id: str):
    # Только имя файла, не путь хоста.
    return {"path": _exporter().export_sft(candidate_id).name}


@router.post("/exports/{candidate_id}/dpo")
async def export_dpo(candidate_id: str):
    return {"path": _exporter().export_dpo(candidate_id).name}


@router.post("/exports/{candidate_id}/launch_training")
async def launch_training(candidate_id: str):
    """Демонстративно отказной путь: адаптер по умолчанию не сконфигурирован."""
    path = _exporter().launch_training(Path(f"{candidate_id}.sft.jsonl"))
    return {"armed": path}
