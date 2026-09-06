"""V5 Bossman Steward — owner-facing objective workspace (read-only inspector).

Мост между Command Center и каноническим хранилищем стоячих целей
`bossman_shared.objective_store`. Модуль сознательно НЕ активирует постоянную
автономию: `tick` отсутствует, наблюдателей здесь нет, приёмного контура
(admission) нет и ни одна миссия отсюда не отправляется. Это шаг 5 плана
миграции — инспектор только для чтения плюс явные действия владельца.

Что можно: смотреть цели, их проверенное состояние, журнал и незакрытые резервы;
явно включать/ставить на паузу/отзывать цель; явно подписывать источники.
Чего нельзя: включить автономию, расширить полномочия или выставить «зелёный» —
условие SATISFIED пишет только рантайм после свежего проверенного наблюдения,
и хранилище само откажет без ссылки на улику.

Ответы честны про неизвестность: UNKNOWN отдаётся как UNKNOWN и никогда не
подменяется «здорова» или «сломана».
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from bossman_shared.objective_store import (
    CompareAndSwapError,
    ObjectiveRuntimeState,
    ObjectiveStore,
    ObjectiveStoreError,
)

from . import Feature

router = APIRouter()

# Автономия V5 включается отдельным принятым решением (N0/V4), не флагом в UI и
# не текстом модели. Пока приёмный контур не принят, инспектор говорит об этом
# прямо, а не делает вид, что цели исполняются.
STANDING_AUTONOMY_ENABLED = False


def _store_path(svc: Any) -> Path:
    override = os.environ.get("BOSSMAN_V5_STORE")
    if override:
        return Path(override).expanduser()
    return Path(svc.config.data_dir) / "v5_objectives.sqlite3"


def _store(request: Request) -> ObjectiveStore:
    try:
        return ObjectiveStore(_store_path(request.app.state.svc))
    except (ObjectiveStoreError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _owner(request: Request) -> str:
    """Владелец берётся из аутентифицированной сессии, а не из тела запроса.

    Идентификатор в JSON — это заявление клиента, а не аутентификация; хранилище
    сверяет его с записью, но доверять ему как личности здесь нельзя.
    """
    owner = getattr(getattr(request.state, "principal", None), "owner_id", None)
    return owner or getattr(request.app.state.svc, "owner_id", "owner")


def _view(state: ObjectiveRuntimeState) -> dict[str, Any]:
    """Проекция для UI. Цвет карточки выводится из фактов, а не из прозы."""
    return {
        "objective_id": state.objective_id,
        "owner_id": state.owner_id,
        "scope_id": state.scope_id,
        "spec_digest": state.spec_digest,
        "revision": state.revision,
        "lifecycle": state.lifecycle,
        "condition": state.condition,
        "enrolled_sources": list(state.enrolled_sources),
        "observations_used": state.observations_used,
        "missions_used": state.missions_used,
        "wall_seconds_used": state.wall_seconds_used,
        "cost_usd_used": state.cost_usd_used,
        "last_observation_at": state.last_observation_at,
        "last_proposal_at": state.last_proposal_at,
        "last_verified_evidence_ref": state.last_verified_evidence_ref,
        "stopped": state.stopped,
        "version": state.version,
        # Зелёным цель бывает только при свежем проверенном SATISFIED с уликой.
        "tone": ("ok" if state.lifecycle == "ACTIVE" and state.condition == "SATISFIED"
                 and state.last_verified_evidence_ref else
                 "err" if state.lifecycle == "ACTIVE" and state.condition == "DEVIATED" else
                 "warn" if state.lifecycle in {"DRAFT", "ACTIVE"} else "idle"),
    }


@router.get("/objectives/status")
async def status(request: Request) -> dict[str, Any]:
    """Честная готовность рантайма, а не бодрый отчёт."""
    store = _store(request)
    objectives = store.list_objectives()
    return {
        "standing_autonomy_enabled": STANDING_AUTONOMY_ENABLED,
        "observers_running": False,
        "admission_enabled": False,
        "store_path": str(_store_path(request.app.state.svc)),
        "counts": {
            "total": len(objectives),
            "active": sum(1 for o in objectives if o.lifecycle == "ACTIVE"),
            "unknown": sum(1 for o in objectives if o.condition == "UNKNOWN"),
            "deviated": sum(1 for o in objectives if o.condition == "DEVIATED"),
        },
        "note": ("Инспектор только для чтения: наблюдатели и приёмный контур не "
                 "включены, миссии по целям не отправляются."),
    }


@router.get("/objectives")
async def list_objectives(request: Request, lifecycle: str | None = None) -> list[dict[str, Any]]:
    store = _store(request)
    try:
        return [_view(s) for s in store.list_objectives(lifecycle=lifecycle)]
    except ObjectiveStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/objectives/{objective_id}")
async def get_objective(objective_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    try:
        state = store.get(objective_id)
        spec = store.get_spec(objective_id)
        return {
            **_view(state),
            "spec": spec.to_dict(),
            "journal": store.journal(objective_id),
            # Незакрытый резерв — это неопределённость после падения, а не
            # разрешение повторить необратимый эффект.
            "open_reservations": store.open_reservations(objective_id),
        }
    except ObjectiveStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/objectives/{objective_id}/lifecycle")
async def set_lifecycle(objective_id: str, request: Request) -> dict[str, Any]:
    """Явное действие владельца. Отзыв необратим, истечение не воскрешает."""
    import time

    body = await request.json()
    requested = (body or {}).get("lifecycle")
    version = (body or {}).get("version")
    if not isinstance(requested, str) or not isinstance(version, int):
        raise HTTPException(status_code=400, detail="lifecycle and version required")
    store = _store(request)
    try:
        state = store.transition(objective_id, requested, now=time.time(),
                                 owner_id=_owner(request), expected_version=version)
    except CompareAndSwapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ObjectiveStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(state)


@router.post("/objectives/{objective_id}/enrollment")
async def set_enrollment(objective_id: str, request: Request) -> dict[str, Any]:
    """Подписка на источники — только явная и только на объявленные целью."""
    body = await request.json()
    sources = (body or {}).get("sources")
    version = (body or {}).get("version")
    if not isinstance(sources, list) or not isinstance(version, int):
        raise HTTPException(status_code=400, detail="sources and version required")
    if any(not isinstance(s, str) for s in sources):
        raise HTTPException(status_code=400, detail="sources must be strings")
    store = _store(request)
    try:
        state = store.enroll_sources(objective_id, tuple(sources),
                                     owner_id=_owner(request), expected_version=version)
    except CompareAndSwapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ObjectiveStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(state)


# tick отсутствует намеренно: фоновой работы у V5 в этой сборке нет.
FEATURE = Feature(name="objectives", router=router)
