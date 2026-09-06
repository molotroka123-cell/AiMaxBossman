"""V5 Bossman Steward — рабочее место владельца для стоячих целей.

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

from bossman_shared.objective_spec import ObjectiveSpec, ObjectiveValidationError
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
    return Path(svc.settings.data_dir) / "v5_objectives.sqlite3"


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


def _spec_summary(spec: ObjectiveSpec) -> dict[str, Any]:
    """Что цель СОБИРАЕТСЯ делать, словами полей, а не прозой модели.

    Это же тело показывается владельцу в предпросмотре ДО создания: согласие
    даётся на конкретные полномочия, бюджет и область, а не на название.
    """
    raw = spec.to_dict()
    limits = raw["limits"]
    return {
        "objective_id": raw["objective_id"],
        "owner_id": raw["owner_id"],
        "scope_id": raw["scope_id"],
        "revision": raw["revision"],
        "spec_digest": spec.digest,
        # Условие успеха — предикаты по объявленным источникам, и ничего кроме.
        "success_condition": [
            {"predicate_id": p["predicate_id"], "source_ref": p["source_ref"],
             "field": p["field"], "operator": p["operator"], "expected": p["expected"]}
            for p in raw["predicates"]],
        # Ритм: чем цель будится и как часто ей можно.
        "cadence": {"allowed_triggers": list(raw["allowed_triggers"]),
                    "cooldown_seconds": raw["cooldown_seconds"],
                    "sources": [{"source_ref": s["source_ref"],
                                 "max_age_seconds": s["max_age_seconds"]}
                                for s in raw["sources"]],
                    "expires_at": raw["expires_at"]},
        "budget": {"max_missions": limits["max_missions"],
                   "max_observations": limits["max_observations"],
                   "max_wall_seconds": limits["max_wall_seconds"],
                   "max_cost_usd": limits["max_cost_usd"]},
        # Полномочия перечисляются поимённо: «доступ к проекту» — не полномочие.
        "capabilities": list(raw["permission_refs"]),
        "conflict_keys": list(raw["conflict_keys"]),
        "stop_conditions": list(raw["stop_conditions"]),
        "priority": raw["priority"],
    }


def _parse_spec(body: Any) -> ObjectiveSpec:
    spec_raw = (body or {}).get("spec")
    if not isinstance(spec_raw, dict):
        raise HTTPException(status_code=400, detail="spec object required")
    try:
        return ObjectiveSpec.from_dict(spec_raw)
    except ObjectiveValidationError as exc:
        # Причина отказа принадлежит владельцу: мастер обязан показать, КАКОЕ
        # поле не годится, а не «не удалось создать цель».
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/objectives/preview")
async def preview_objective(request: Request) -> dict[str, Any]:
    """Предпросмотр согласия. Ничего не создаёт и ничего не пишет.

    Последний шаг мастера: владелец видит ровно те полномочия, бюджет, область
    и ритм, которые получит цель, и цифру спецификации, по которой потом можно
    сверить, что согласовано было именно это.
    """
    spec = _parse_spec(await request.json())
    return {"preview": _spec_summary(spec), "created": False,
            "note": ("Предпросмотр: цель не создана. После создания она попадает в "
                     "DRAFT и не следит ни за чем, пока владелец не включит её.")}


@router.post("/objectives")
async def create_objective(request: Request) -> dict[str, Any]:
    """Создать цель по спецификации мастера.

    Цель заводится в DRAFT — хранилище иначе и не умеет. Создание не является
    включением: подписка на источники и переход в ACTIVE остаются отдельными
    явными действиями владельца.
    """
    body = await request.json()
    spec = _parse_spec(body)
    owner = _owner(request)
    if spec.to_dict()["owner_id"] != owner:
        # Владелец в теле запроса — заявление клиента, а не личность.
        raise HTTPException(status_code=403, detail="spec owner_id does not match the session")
    store = _store(request)
    try:
        state = store.create(spec)
    except ObjectiveStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**_view(state), "spec": spec.to_dict(), "preview": _spec_summary(spec)}


@router.get("/objectives/{objective_id}/evidence")
async def objective_evidence(objective_id: str, request: Request) -> dict[str, Any]:
    """Вкладка «Улики»: на чём основано текущее состояние цели.

    Зелёное состояние без ссылки на улику здесь не бывает: если ссылки нет,
    так и написано, и `verified` равно false. Незакрытые резервы показываются
    рядом — это неопределённость после падения, а не подтверждение.
    """
    store = _store(request)
    try:
        state = store.get(objective_id)
        journal = store.journal(objective_id)
        reservations = store.open_reservations(objective_id)
    except ObjectiveStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    verified = bool(state.last_verified_evidence_ref) and state.condition != "UNKNOWN"
    return {
        "objective_id": objective_id,
        "condition": state.condition,
        "verified": verified,
        "last_verified_evidence_ref": state.last_verified_evidence_ref,
        "last_observation_at": state.last_observation_at,
        "last_proposal_at": state.last_proposal_at,
        "usage": {"observations": state.observations_used, "missions": state.missions_used,
                  "wall_seconds": state.wall_seconds_used, "cost_usd": state.cost_usd_used},
        "open_reservations": reservations,
        "journal": journal,
        "note": ("Условие считается подтверждённым только при свежем проверенном "
                 "наблюдении со ссылкой на улику."
                 if verified else
                 "Проверенной улики нет: состояние показывается как есть и не "
                 "выдаётся за подтверждённое."),
    }


@router.get("/objectives/{objective_id}/revisions")
async def objective_revisions(objective_id: str, request: Request) -> dict[str, Any]:
    """Вкладка «Ревизии»: что менялось в цели и когда.

    Тела вытесненных редакций берутся из истории; для целей, переживших апгрейд
    до неё, история пуста, и это сказано прямо — журнал всё равно помнит сам
    факт ревизии, и выдавать отсутствие истории за отсутствие изменений нельзя.
    """
    store = _store(request)
    try:
        state = store.get(objective_id)
        current = store.get_spec(objective_id)
        history = store.spec_history(objective_id)
        journal = store.journal(objective_id)
    except ObjectiveStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    revised = [row for row in journal if row.get("event") == "revised"]
    return {
        "objective_id": objective_id,
        "current": {"revision": state.revision, "spec_digest": state.spec_digest,
                    "spec": current.to_dict(), "summary": _spec_summary(current)},
        "superseded": history,
        "revision_events": revised,
        "history_complete": len(history) >= len(revised),
        "note": ("" if len(history) >= len(revised) else
                 "Часть прежних редакций не сохранена: цель старше журнала редакций. "
                 "Сами ревизии видны в событиях ниже."),
    }


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
    except (ObjectiveStoreError, ObjectiveValidationError) as exc:
        # Запрещённый переход (например, воскрешение отозванной цели) — это
        # отказ, а не сбой: раньше он проходил мимо обработчика и владелец
        # видел «внутреннюю ошибку» вместо объяснимой причины.
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
    except (ObjectiveStoreError, ObjectiveValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(state)


# tick отсутствует намеренно: фоновой работы у V5 в этой сборке нет.
FEATURE = Feature(name="objectives", router=router)
