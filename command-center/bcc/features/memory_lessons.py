"""Приём уроков от Codex / Claude / Bossman в ШТАТНУЮ память (таблица `facts`).

Владелец 31.10: «штатная память с источниками и датами … Не создавай второй
backend или память. Запись урока не называй доказанным обучением.»

HTTP (монтируется под /api с токеном владельца):
  POST /api/memory/lessons                 — записать урок (идемпотентно по content hash)
  GET  /api/memory/lessons                 — список (без приватных категорий по умолчанию)
  GET  /api/memory/lessons/stale           — уроки, не сверенные с вебом дольше N дней
  GET  /api/memory/lessons/policy          — правила приёма (читает readiness-скрипт)
  GET  /api/memory/lessons/{id}            — урок + вычисленный статус + проверки
  POST /api/memory/lessons/{id}/verify     — прогнать СВЯЗАННУЮ проверку (сервер сам)
  POST /api/memory/lessons/{id}/refute     — опровергнуть с источником
  POST /api/memory/lessons/{id}/freshness  — сверить с вебом (expect_text на странице)

Инструменты: `memory.lesson.add` (ask), `memory.lesson.search` (auto, внешние данные).
"""
from __future__ import annotations

import json
import re

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from .. import business_exercises as bx
from ..business_exercise_checker import CheckerError, check_answer
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from ..v2.memory import lessons as L
from . import Feature

router = APIRouter()

INTAKE_LIMITER = L.RateLimiter(per_minute=30, per_hour=300)
VERIFY_LIMITER = L.RateLimiter(per_minute=10, per_hour=60)
FRESHNESS_LIMITER = L.RateLimiter(per_minute=10, per_hour=120)

_EVAL_RE = re.compile(r"^(business_exercise|skill_evaluation):([A-Za-z0-9_\-]{1,80})$")


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


def _remote_hint(request: Request) -> bool:
    return request.headers.get("x-bossman-writer", "").strip().lower() in {"cloud", "remote"}


async def _json_body(request: Request, limit: int = L.MAX_REQUEST_BYTES) -> dict:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, {"message": f"тело запроса больше {limit} байт", "code": "LESSON_TOO_LARGE"})
    raw = await request.body()
    if len(raw) > limit:
        raise HTTPException(413, {"message": f"тело запроса больше {limit} байт", "code": "LESSON_TOO_LARGE"})
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, {"message": "тело не JSON", "code": "LESSON_BAD_JSON"}) from None
    if not isinstance(data, dict):
        raise HTTPException(422, {"message": "тело — JSON-объект", "code": "LESSON_BAD_JSON"})
    return data


def _fail(exc: Exception):
    if isinstance(exc, L.LessonForbidden):
        return HTTPException(403, {"message": str(exc), "code": "LESSON_FORBIDDEN"})
    return HTTPException(422, {"message": str(exc), "code": "LESSON_REJECTED"})


async def _lesson_view(svc, lesson_id: int, *, stale_days: int = L.DEFAULT_STALE_DAYS) -> dict | None:
    async with svc.db.session() as session:
        row = await L.get_lesson_row(session, lesson_id)
        if row is None:
            return None
        checks = await L.check_rows(session, lesson_id)
    return L.public_lesson(row, checks, stale_days=stale_days)


async def add_lesson(svc, payload: dict, *, client_host: str | None, remote_hint: bool) -> dict:
    lesson = L.prepare_lesson(payload, client_host=client_host, remote_hint=remote_hint)
    async with svc.db.session() as session:
        result = await L.write_lesson(session, lesson)
    view = await _lesson_view(svc, int(result["lesson"]["id"]))
    try:
        await svc.bus.emit("memory.lesson.recorded", lesson_id=view["id"], author=view["author"],
                           category=view["category"], deduped=result["deduped"])
    except Exception:  # noqa: BLE001 — шина не обязана быть
        pass
    return {"lesson": view, "deduped": result["deduped"], "redacted": lesson["redacted"],
            "status": view["status"], "proven_learning": False}


@router.get("/memory/lessons/policy")
async def http_lessons_policy():
    return {
        "schema": L.SCHEMA, "authors": list(L.AUTHORS), "remote_authors": sorted(L.REMOTE_AUTHORS),
        "statuses": list(L.STATUSES), "work_categories": sorted(L.WORK_CATEGORIES),
        "owner_private_categories": sorted(L.OWNER_PRIVATE_CATEGORIES),
        "required_fields": ["title", "body", "author", "source", "observed_at"],
        "optional_fields": ["category", "valid_from", "expect_text", "tags", "session_id", "limitations"],
        "max_limitations": L.MAX_LIMITATIONS,
        "max_body_chars": L.MAX_BODY, "max_request_bytes": L.MAX_REQUEST_BYTES,
        "rate_limit": {"per_minute": INTAKE_LIMITER.per_minute, "per_hour": INTAKE_LIMITER.per_hour},
        "eval_kinds": ["business_exercise:<exercise_id> (+answer)", "skill_evaluation:<id>"],
        "secrets_redacted": True, "idempotent_by": "sha256(category,title,body,source)",
        "store": "facts (existing bi-temporal table), source_kind=lesson|lesson_check",
        "recorded_is_not_proven_learning": True,
    }


@router.post("/memory/lessons")
async def http_add_lesson(request: Request):
    payload = await _json_body(request)
    author = str(payload.get("author") or "unknown").strip().lower()[:20]
    refusal = INTAKE_LIMITER.check(f"lesson:{author}")
    if refusal:
        raise HTTPException(429, {"message": refusal, "code": "LESSON_RATE_LIMITED"})
    try:
        return await add_lesson(request.app.state.svc, payload,
                                client_host=_client_host(request), remote_hint=_remote_hint(request))
    except L.LessonError as exc:
        raise _fail(exc) from None


@router.get("/memory/lessons")
async def http_list_lessons(request: Request, category: str = "", author: str = "",
                            status: str = "", stale_days: int = L.DEFAULT_STALE_DAYS,
                            include_private: bool = False, limit: int = 100):
    if status and status not in L.STATUSES:
        raise HTTPException(422, {"message": f"status из {', '.join(L.STATUSES)}"})
    if include_private and (_remote_hint(request) or not L._is_loopback(_client_host(request))):
        raise HTTPException(403, {"message": "приватные категории — только локально владельцу",
                                  "code": "LESSON_FORBIDDEN"})
    async with request.app.state.svc.db.session() as session:
        items = await L.list_lessons(session, category=category or None, author=author or None,
                                     status=status or None, include_private=include_private,
                                     stale_days=max(1, stale_days), limit=limit)
    return {"items": items, "total": len(items), "proven_learning": False}


@router.get("/memory/lessons/stale")
async def http_stale_lessons(request: Request, max_age_days: int = L.DEFAULT_STALE_DAYS,
                             limit: int = 100):
    async with request.app.state.svc.db.session() as session:
        items = await L.list_lessons(session, stale_days=max(1, max_age_days),
                                     only_stale=True, limit=limit)
    return {"items": items, "total": len(items), "max_age_days": max(1, max_age_days)}


@router.get("/memory/lessons/{lesson_id}")
async def http_get_lesson(lesson_id: int, request: Request, stale_days: int = L.DEFAULT_STALE_DAYS):
    view = await _lesson_view(request.app.state.svc, lesson_id, stale_days=max(1, stale_days))
    if view is None:
        raise HTTPException(404, {"message": f"урока {lesson_id} нет"})
    if view["category"] in L.OWNER_PRIVATE_CATEGORIES and (
            _remote_hint(request) or not L._is_loopback(_client_host(request))):
        raise HTTPException(403, {"message": "приватная категория владельца", "code": "LESSON_FORBIDDEN"})
    return view


async def _forbidden_roots(svc) -> list:
    roots = [bx_root for bx_root in [getattr(svc.settings, "data_dir", None)] if bx_root]
    try:
        from .tools_code import _repo_root, allowed_roots
        roots += [_repo_root(), *(await allowed_roots(svc))]
    except Exception:  # noqa: BLE001 — без списка корней всё равно запрещаем репозиторий
        from pathlib import Path
        roots.append(Path(__file__).resolve().parents[3])
    return roots


async def run_linked_eval(svc, eval_ref: str, answer) -> tuple[bool, str, dict]:
    """Разрешить ссылку на проверку САМИМ сервером. Возвращает (passed, receipt_sha, detail)."""
    m = _EVAL_RE.match(str(eval_ref or "").strip())
    if not m:
        raise L.LessonError(
            "eval_ref не распознан: business_exercise:<id> (с answer) или skill_evaluation:<id>. "
            "Ссылка «тест прошёл» на слово не принимается")
    kind, ref = m.group(1), m.group(2)
    if kind == "business_exercise":
        if ref not in bx.EXERCISES:
            raise L.LessonError(f"нет упражнения {ref}")
        if answer is None:
            raise L.LessonError("для business_exercise нужен answer — сервер сам прогонит чекер")
        try:
            bx.materialize_key(ref, await _forbidden_roots(svc))
            receipt = check_answer(bx.keys_dir(), ref, answer)
        except (CheckerError, bx.KeysLocationError) as exc:
            raise L.LessonError(f"проверка не выполнена: {exc}") from None
        return bool(receipt["passed"]), receipt["receipt_sha256"], {"receipt": receipt}
    from ..db import skill_evaluations as se
    if not ref.isdigit():
        raise L.LessonError("skill_evaluation:<целый id>")
    async with svc.db.session() as session:
        row = (await session.execute(sa.select(se).where(se.c.id == int(ref)))).first()
    if row is None:
        raise L.LessonError(f"skill_evaluation {ref} не найдена")
    r = dict(row._mapping)
    if r.get("status") != "decided":
        raise L.LessonError(f"skill_evaluation {ref} ещё не решена ({r.get('status')})")
    passed = r.get("verdict") == "promote"
    digest = L.content_hash(title=f"skill_evaluation:{ref}", body=json.dumps(r.get("metrics") or {}, sort_keys=True, default=str),
                            category="eval", source=str(r.get("verdict")))
    return passed, digest, {"skill_evaluation": {"id": r["id"], "verdict": r.get("verdict")}}


@router.post("/memory/lessons/{lesson_id}/verify")
async def http_verify_lesson(lesson_id: int, request: Request):
    body = await _json_body(request)
    refusal = VERIFY_LIMITER.check(f"verify:{lesson_id}")
    if refusal:
        raise HTTPException(429, {"message": refusal, "code": "LESSON_RATE_LIMITED"})
    svc = request.app.state.svc
    if await _lesson_view(svc, lesson_id) is None:
        raise HTTPException(404, {"message": f"урока {lesson_id} нет"})
    try:
        passed, receipt_sha, detail = await run_linked_eval(svc, str(body.get("eval_ref") or ""),
                                                            body.get("answer"))
        async with svc.db.session() as session:
            await L.record_eval_result(session, lesson_id, eval_ref=str(body.get("eval_ref")),
                                       passed=passed, receipt_sha256=receipt_sha, detail=detail)
    except L.LessonError as exc:
        raise _fail(exc) from None
    return {"lesson": await _lesson_view(svc, lesson_id), "eval_passed": passed, **detail,
            "proven_learning": False,
            "note_ru": "verified_by_test = связанная проверка прошла; это не доказывает, что урок изменил поведение"}


@router.post("/memory/lessons/{lesson_id}/refute")
async def http_refute_lesson(lesson_id: int, request: Request):
    body = await _json_body(request)
    svc = request.app.state.svc
    try:
        async with svc.db.session() as session:
            await L.record_refutation(session, lesson_id, reason=str(body.get("reason") or ""),
                                      source=str(body.get("source") or ""),
                                      author=str(body.get("author") or "").strip().lower())
    except L.LessonError as exc:
        raise _fail(exc) from None
    return {"lesson": await _lesson_view(svc, lesson_id)}


@router.post("/memory/lessons/{lesson_id}/freshness")
async def http_lesson_freshness(lesson_id: int, request: Request):
    body = await _json_body(request)
    refusal = FRESHNESS_LIMITER.check("freshness")
    if refusal:
        raise HTTPException(429, {"message": refusal, "code": "LESSON_RATE_LIMITED"})
    svc = request.app.state.svc
    fetcher = getattr(svc, "_lesson_fetcher", None)   # тесты подменяют фейком; сеть по умолчанию
    try:
        async with svc.db.session() as session:
            row = await L.check_freshness(session, lesson_id, fetcher=fetcher,
                                          url=str(body.get("url") or "") or None)
    except L.LessonError as exc:
        raise _fail(exc) from None
    return {"check": {"id": row["id"], **{k: L._meta(row).get(k) for k in ("outcome", "url", "reason", "checked_at")}},
            "lesson": await _lesson_view(svc, lesson_id)}


# ------------------------------------------------------------------ инструменты

def _err(text: str) -> ToolResult:
    return ToolResult(content=text, one_line=text[:140], error=True)


async def tool_lesson_add(args: dict, ctx: ToolContext) -> ToolResult:
    payload = dict(args or {})
    payload["author"] = "bossman"                  # инструмент пишет от имени Bossman, не владельца
    payload.setdefault("source", f"bossman-run:{ctx.run_id}")
    refusal = INTAKE_LIMITER.check("lesson:bossman")
    if refusal:
        return _err(f"memory.lesson.add: {refusal}")
    try:
        # Прогон может идти на облачной модели — приватные категории закрыты всегда.
        out = await add_lesson(ctx.svc, payload, client_host="127.0.0.1", remote_hint=True)
    except L.LessonError as exc:
        return _err(f"memory.lesson.add: {exc}")
    v = out["lesson"]
    return ToolResult(content=(f"lesson#{v['id']} [{v['category']}] status={v['status']} "
                               f"(запись урока ≠ доказанное обучение)"),
                      one_line=f"memory.lesson.add: lesson#{v['id']}", data=out)


async def tool_lesson_search(args: dict, ctx: ToolContext) -> ToolResult:
    async with ctx.svc.db.session() as session:
        items = await L.list_lessons(session, category=str(args.get("category") or "") or None,
                                     status=str(args.get("status") or "") or None,
                                     limit=int(args.get("limit") or 20))
    query = str(args.get("query") or "").casefold()
    if query:
        items = [x for x in items if query in f"{x['title']} {x['body']}".casefold()]
    lines = [f"lesson#{x['id']} [{x['category']}] [source={x['source']}] [author={x['author']}] "
             f"[status={x['status']}] observed={x['observed_at']} stale={x['stale']}\n{x['title']}: {x['body'][:400]}"
             for x in items]
    return ToolResult(content="\n\n---\n\n".join(lines) or "уроки не найдены",
                      one_line=f"memory.lesson.search: {len(items)}", data={"items": items})


SPECS = [
    ToolSpec(
        name="memory.lesson.add", handler=tool_lesson_add, source="memory", category="write",
        permission="filesystem.write", default_effect="ask", idempotent=True,
        description=("Записать урок в штатную память с источником и датой. Статус всегда "
                     "lesson_recorded: запись урока не является доказанным обучением."),
        input_schema={
            "title": {"type": "string", "maxLength": L.MAX_TITLE},
            "body": {"type": "string", "maxLength": L.MAX_BODY},
            "category": {"type": "string", "enum": sorted(L.WORK_CATEGORIES)},
            "source": {"type": "string"}, "observed_at": {"type": "string"},
            "valid_from": {"type": "string"}, "expect_text": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string", "maxLength": L.MAX_LIMITATION},
                            "maxItems": L.MAX_LIMITATIONS},
        }, required=["title", "body", "observed_at"]),
    ToolSpec(
        name="memory.lesson.search", handler=tool_lesson_search, source="memory", category="read",
        default_effect="auto", external_output=True,
        description="Найти уроки в памяти с источником, датами, автором и статусом проверки.",
        input_schema={"query": {"type": "string"}, "category": {"type": "string"},
                      "status": {"type": "string", "enum": list(L.STATUSES)},
                      "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
]


async def setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="memory_lessons", router=router, setup=setup)
