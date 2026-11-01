"""Учебные бизнес-упражнения (синтетика) для сессий обучения Codex / Bossman.

  GET  /api/learning/business-exercises              — каталог
  GET  /api/learning/business-exercises/{id}         — задание с входными CSV (без правды)
  POST /api/learning/business-exercises/{id}/check   — независимый чекер → квитанция

Ничего не платит, никому не пишет, кошельков не трогает: модуль упражнений не
делает сетевых вызовов. Правда лежит вне корней агента (см. bcc.business_exercises).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import business_exercises as bx
from ..business_exercise_checker import MAX_ANSWER_BYTES, CheckerError, check_answer
from . import Feature
from .memory_lessons import VERIFY_LIMITER, _forbidden_roots, _json_body

router = APIRouter()


@router.get("/learning/business-exercises")
async def http_catalog():
    return {"items": bx.catalog(), "simulated": True, "not_done_ru": list(bx.NOT_DONE_RU)}


@router.get("/learning/business-exercises/{exercise_id}")
async def http_task(exercise_id: str):
    if exercise_id not in bx.EXERCISES:
        raise HTTPException(404, {"message": f"нет упражнения {exercise_id}"})
    return bx.public_task(exercise_id)


@router.post("/learning/business-exercises/{exercise_id}/check")
async def http_check(exercise_id: str, request: Request):
    if exercise_id not in bx.EXERCISES:
        raise HTTPException(404, {"message": f"нет упражнения {exercise_id}"})
    body = await _json_body(request, limit=MAX_ANSWER_BYTES + 1024)
    refusal = VERIFY_LIMITER.check(f"exercise:{exercise_id}")
    if refusal:
        raise HTTPException(429, {"message": refusal, "code": "EXERCISE_RATE_LIMITED"})
    try:
        bx.materialize_key(exercise_id, await _forbidden_roots(request.app.state.svc))
        return check_answer(bx.keys_dir(), exercise_id, body.get("answer"))
    except bx.KeysLocationError as exc:
        raise HTTPException(409, {"message": str(exc), "code": "EXERCISE_KEYS_INSIDE_AGENT_ROOT"}) from None
    except CheckerError as exc:
        raise HTTPException(422, {"message": str(exc)}) from None


FEATURE = Feature(name="learning_exercises", router=router)
