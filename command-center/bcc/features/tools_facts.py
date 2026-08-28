"""V2.2 — structured temporal fact tools."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from ..v2.memory.facts import FactStore, parse_time, public_fact
from . import Feature

router = APIRouter()
OUTPUT_LIMIT = 10000


class FactIn(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1)
    object: str = ""
    valid_at: str | None = None
    mode: str = "append"
    source_kind: str = "human"
    source_note: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    meta: dict = Field(default_factory=dict)


def _render(rows: list[dict]) -> str:
    if not rows:
        return "факты не найдены"
    lines = []
    for row in rows:
        item = public_fact(row)
        lines.append(
            f"fact#{item['id']} | {item['subject']} :: {item['predicate']}\n"
            f"{item['statement']}\n"
            f"world=[{item.get('valid_at')} .. {item.get('invalid_at') or '∞'}] "
            f"knowledge=[{item.get('created_at')} .. {item.get('expired_at') or '∞'}] "
            f"confidence={item.get('confidence')}"
        )
    return "\n\n---\n\n".join(lines)[:OUTPUT_LIMIT]


@router.post("/memory/facts")
async def http_add_fact(body: FactIn, request: Request):
    try:
        row = await FactStore(request.app.state.svc).add(
            subject=body.subject, predicate=body.predicate, statement=body.statement,
            object_value=body.object,
            valid_at=parse_time(body.valid_at) if body.valid_at else None,
            mode=body.mode, source_kind=body.source_kind,
            source_note=body.source_note, confidence=body.confidence, meta=body.meta,
        )
    except ValueError as exc:
        raise HTTPException(422, {"message": str(exc)})
    return public_fact(row)


@router.get("/memory/facts")
async def http_search_facts(request: Request, query: str = "", subject: str = "",
                            predicate: str = "", current_only: bool = True,
                            limit: int = 50):
    rows = await FactStore(request.app.state.svc).search(
        query=query, subject=subject, predicate=predicate,
        current_only=current_only, limit=limit)
    return {"items": [public_fact(x) for x in rows], "total": len(rows)}


@router.get("/memory/facts/as-of")
async def http_facts_as_of(request: Request, world_at: str, known_at: str = "",
                           subject: str = "", predicate: str = "", query: str = "",
                           limit: int = 50):
    try:
        world = parse_time(world_at)
        known = parse_time(known_at) if known_at else None
    except ValueError as exc:
        raise HTTPException(422, {"message": str(exc)})
    rows = await FactStore(request.app.state.svc).as_of(
        world_at=world, known_at=known, subject=subject,
        predicate=predicate, query=query, limit=limit)
    return {"items": [public_fact(x) for x in rows], "total": len(rows)}


@router.get("/memory/facts/history")
async def http_fact_history(request: Request, subject: str, predicate: str = "",
                            limit: int = 100):
    try:
        rows = await FactStore(request.app.state.svc).history(
            subject=subject, predicate=predicate, limit=limit)
    except ValueError as exc:
        raise HTTPException(422, {"message": str(exc)})
    return {"items": [public_fact(x) for x in rows], "total": len(rows)}


def _err(text: str) -> ToolResult:
    return ToolResult(content=text, one_line=text[:140], error=True)


async def tool_fact_add(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        row = await FactStore(ctx.svc).add(
            subject=str(args.get("subject") or ""),
            predicate=str(args.get("predicate") or ""),
            statement=str(args.get("statement") or ""),
            object_value=str(args.get("object") or ""),
            valid_at=parse_time(args.get("valid_at")) if args.get("valid_at") else None,
            mode=str(args.get("mode") or "append"), source_kind="run",
            source_run_id=ctx.run_id, source_note=str(args.get("source_note") or ""),
            confidence=float(args.get("confidence") if args.get("confidence") is not None else 1.0),
            meta=dict(args.get("meta") or {}),
        )
    except (ValueError, TypeError) as exc:
        return _err(f"memory.fact.add: {exc}")
    item = public_fact(row)
    await ctx.svc.bus.emit("memory.fact.added", fact_id=item["id"], run_id=ctx.run_id,
                           subject=item["subject"], predicate=item["predicate"],
                           mode=str(args.get("mode") or "append"))
    return ToolResult(content=_render([row]), one_line=f"memory.fact.add: fact#{item['id']}",
                      data=item)


async def tool_fact_search(args: dict, ctx: ToolContext) -> ToolResult:
    rows = await FactStore(ctx.svc).search(
        query=str(args.get("query") or ""), subject=str(args.get("subject") or ""),
        predicate=str(args.get("predicate") or ""),
        current_only=bool(args.get("current_only", True)),
        limit=int(args.get("limit") or 30))
    return ToolResult(content=_render(rows), one_line=f"memory.fact.search: {len(rows)}",
                      data={"items": [public_fact(x) for x in rows]})


async def tool_fact_at_time(args: dict, ctx: ToolContext) -> ToolResult:
    if not args.get("world_at"):
        return _err("memory.fact.at_time: нужен world_at")
    try:
        world = parse_time(args.get("world_at"))
        known = parse_time(args.get("known_at")) if args.get("known_at") else None
    except ValueError as exc:
        return _err(f"memory.fact.at_time: {exc}")
    rows = await FactStore(ctx.svc).as_of(
        world_at=world, known_at=known, query=str(args.get("query") or ""),
        subject=str(args.get("subject") or ""),
        predicate=str(args.get("predicate") or ""), limit=int(args.get("limit") or 30))
    return ToolResult(content=_render(rows), one_line=f"memory.fact.at_time: {len(rows)}",
                      data={"items": [public_fact(x) for x in rows]})


async def tool_fact_history(args: dict, ctx: ToolContext) -> ToolResult:
    subject = str(args.get("subject") or "").strip()
    if not subject:
        return _err("memory.fact.history: нужен subject")
    rows = await FactStore(ctx.svc).history(
        subject=subject, predicate=str(args.get("predicate") or ""),
        limit=int(args.get("limit") or 100))
    return ToolResult(content=_render(rows), one_line=f"memory.fact.history: {len(rows)}",
                      data={"items": [public_fact(x) for x in rows]})


SPECS = [
    ToolSpec(
        name="memory.fact.add", handler=tool_fact_add, source="memory", category="write",
        permission="filesystem.write", default_effect="ask", idempotent=False,
        description=("Сохранить bi-temporal факт. append ничего не инвалидирует; "
                     "replace-current использовать только при явной замене значения."),
        input_schema={
            "subject": {"type": "string"}, "predicate": {"type": "string"},
            "statement": {"type": "string"}, "object": {"type": "string"},
            "valid_at": {"type": "string"},
            "mode": {"type": "string", "enum": ["append", "replace-current"]},
            "source_note": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "meta": {"type": "object"},
        }, required=["subject", "predicate", "statement"]),
    ToolSpec(
        name="memory.fact.search", handler=tool_fact_search, source="memory", category="read",
        default_effect="auto", description="Искать текущие/исторические структурированные факты.",
        input_schema={
            "query": {"type": "string"}, "subject": {"type": "string"},
            "predicate": {"type": "string"}, "current_only": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
    ToolSpec(
        name="memory.fact.at_time", handler=tool_fact_at_time, source="memory", category="read",
        default_effect="auto", description="Bi-temporal query по world_at и known_at.",
        input_schema={
            "world_at": {"type": "string"}, "known_at": {"type": "string"},
            "query": {"type": "string"}, "subject": {"type": "string"},
            "predicate": {"type": "string"}, "limit": {"type": "integer"}},
        required=["world_at"]),
    ToolSpec(
        name="memory.fact.history", handler=tool_fact_history, source="memory", category="read",
        default_effect="auto", description="Полная temporal history для subject/predicate.",
        input_schema={"subject": {"type": "string"}, "predicate": {"type": "string"},
                      "limit": {"type": "integer"}}, required=["subject"]),
]


async def setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_facts", router=router, setup=setup)
