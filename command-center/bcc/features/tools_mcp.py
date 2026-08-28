"""Фаза D — MCP как настоящие инструменты модели.

Что делает модуль:
  * поднимает `MCPRuntime` (официальный SDK `mcp`, stdio) на `svc.mcp`;
  * discovery инструментов сервера → таблица `mcp_tools` (owner схемы не трогаем);
  * регистрирует каждый найденный инструмент в КАНОНИЧЕСКОМ `bcc.tools.REGISTRY`
    под именем `mcp:<server>:<tool>` со схемой сервера и `external=True`;
  * HTTP `/api/mcp/runtime/*` — connect/disconnect/health/refresh/call.

Права — только канонический слой (`decide_effect` + `agents.permissions.tool_rules`).
Параллельной системы согласований здесь нет: `default_effect` инструмента берётся
из уже существующего ключа настроек `mcp.policy` (его пишет `POST /api/mcp/policy`
из `features/skills.py`), всё остальное решает движок.

Контекст-гигиена: реестр — это КАТАЛОГ. Модель видит только то, что выдано
агенту/задаче (`bcc.tools.allowed_tools_for` → `REGISTRY.resolve`), поэтому
неназначенный MCP-инструмент в схемы провайдера не попадает никогда.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import settings_kv, utcnow
from ..tools import REGISTRY, ToolResult, ToolSpec
from ..v2.mcp_hub import MCPServerSpec, namespaced_tool
from ..v2.mcp_runtime import (MCPCallError, MCPRuntime, MCPUnavailable, sdk_available,
                              sdk_version)
from ..v2.tables import mcp_servers as mcp_servers_t, mcp_tools as mcp_tools_t
from . import Feature

MCP_POLICY_KEY = "mcp.policy"          # общий с features/skills.py
router = APIRouter()


# ---------------------------------------------------------------- инфраструктура

def runtime_of(svc) -> MCPRuntime:
    """Рантайм живёт на Services; создаётся лениво — SDK не нужен для старта."""
    rt = getattr(svc, "mcp", None)
    if rt is None:
        async def on_event(kind: str, data: dict) -> None:
            await svc.bus.emit(kind, **data)
        rt = MCPRuntime(on_event=on_event)
        svc.mcp = rt
    return rt


def _spec_from_row(row: dict) -> MCPServerSpec:
    return MCPServerSpec(id=str(row["name"]), name=str(row["name"]),
                         transport=str(row["transport"] or "stdio"),
                         command=list(row.get("command") or []),
                         url=str(row.get("url") or ""),
                         cwd=str(row.get("cwd") or ""),
                         env_keys=list(row.get("env_keys") or []),
                         enabled=bool(row.get("enabled", True)))


async def _server_row(svc, ref: str | int) -> dict:
    """Сервер по числовому id или по имени."""
    async with svc.db.session() as s:
        cond = (mcp_servers_t.c.id == int(ref)) if str(ref).isdigit() \
            else (mcp_servers_t.c.name == str(ref))
        row = (await s.execute(sa.select(mcp_servers_t).where(cond))).first()
    if row is None:
        raise HTTPException(404, {"message": f"MCP-сервер {ref} не найден"})
    return dict(row._mapping)


async def _policy(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == MCP_POLICY_KEY))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return {}


async def _mark(svc, server_id: int, status: str, detail: str = "") -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(mcp_servers_t)
                        .where(mcp_servers_t.c.id == server_id)
                        .values(status=status, status_detail=detail[:500],
                                last_check=utcnow()))
        await s.commit()


# ---------------------------------------------------------------- регистрация

def _handler_for(svc, spec: MCPServerSpec, tool_name: str):
    """Хэндлер инструмента: ленивый connect → call → ToolResult(external=True)."""
    async def handler(args: dict, ctx) -> ToolResult:
        rt = runtime_of(getattr(ctx, "svc", None) or svc)
        try:
            await rt.ensure(spec)
        except MCPUnavailable as exc:
            return ToolResult(content=f"MCP-сервер {spec.id} недоступен: {exc}",
                              one_line=f"mcp:{spec.id}: недоступен", error=True)
        try:
            res = await rt.call_tool(spec.id, tool_name, dict(args or {}))
        except (MCPCallError, MCPUnavailable) as exc:
            await _emit_failure(getattr(ctx, "svc", None) or svc, spec.id, tool_name, str(exc))
            return ToolResult(content=f"ошибка MCP {spec.id}.{tool_name}: {exc}",
                              one_line=f"mcp:{spec.id}:{tool_name}: ошибка",
                              error=True, external=True)
        line = f"mcp:{spec.id}:{tool_name}: " + ("ошибка сервера" if res.is_error else "ок")
        return ToolResult(content=res.text or "(пустой ответ MCP)", one_line=line,
                          error=bool(res.is_error), external=True,
                          data={"server": spec.id, "tool": tool_name,
                                "structured": res.structured})
    return handler


async def _emit_failure(svc, server_id: str, tool: str, detail: str) -> None:
    """Self-Healing/Governor видят провал инструмента как обычное событие."""
    try:
        await svc.bus.emit("mcp.call_failed", server=server_id, tool=tool,
                           message=detail[:400])
    except Exception:
        pass


def register_tool(svc, spec: MCPServerSpec, view, policy: dict) -> ToolSpec:
    canonical = namespaced_tool(spec.id, view.name)
    schema = view.input_schema if isinstance(view.input_schema, dict) else {}
    effect = str(policy.get(canonical) or "ask")
    if effect not in ("auto", "ask", "deny"):
        effect = "ask"
    return REGISTRY.register(ToolSpec(
        name=canonical,
        description=(view.description or f"MCP-инструмент {view.name} сервера {spec.id}")[:900],
        handler=_handler_for(svc, spec, view.name),
        input_schema=schema.get("properties") or {},
        required=list(schema.get("required") or []),
        category="exec", source="mcp", default_effect=effect,
        timeout_seconds=float(spec.timeout_seconds or 30),
        idempotent=False, external_output=True))


def unregister_server_tools(server_id: str) -> int:
    prefix = namespaced_tool(server_id, "x").rsplit(":", 1)[0] + ":"
    names = [n for n in REGISTRY.names() if n.startswith(prefix)]
    for name in names:
        REGISTRY.unregister(name)
    return len(names)


async def refresh_server(svc, row: dict, *, connect: bool = True) -> list[dict]:
    """Discovery: подключиться → tools/list → persist в `mcp_tools` → в реестр."""
    spec = _spec_from_row(row)
    rt = runtime_of(svc)
    if connect:
        await rt.connect(spec)
    views = await rt.list_tools(spec.id, refresh=True)

    async with svc.db.session() as s:
        await s.execute(sa.delete(mcp_tools_t)
                        .where(mcp_tools_t.c.server_id == int(row["id"])))
        for v in views:
            await s.execute(sa.insert(mcp_tools_t).values(
                server_id=int(row["id"]), name=v.name, description=v.description,
                input_schema=v.input_schema or {}, enabled=True))
        await s.commit()
    await _mark(svc, int(row["id"]), "healthy", f"инструментов: {len(views)}")

    policy = await _policy(svc)
    unregister_server_tools(spec.id)
    out = []
    for v in views:
        tool_spec = register_tool(svc, spec, v, policy)
        out.append({"server": spec.id, "tool": v.name, "canonical": tool_spec.name,
                    "api_name": tool_spec.api_name, "effect": tool_spec.default_effect})
    await svc.bus.emit("mcp.tools_discovered", server=spec.id, count=len(views))
    return out


async def restore_registry(svc) -> int:
    """На старте: поднять инструменты из БД в реестр БЕЗ запуска процессов серверов.

    Реальное соединение открывается лениво при первом вызове инструмента —
    так рестарт процесса не тянет за собой N дочерних процессов."""
    async with svc.db.session() as s:
        servers = (await s.execute(sa.select(mcp_servers_t)
                                   .where(mcp_servers_t.c.enabled.is_(True)))).fetchall()
        tools = (await s.execute(sa.select(mcp_tools_t)
                                 .where(mcp_tools_t.c.enabled.is_(True)))).fetchall()
    by_server: dict[int, list] = {}
    for t in tools:
        by_server.setdefault(int(t._mapping["server_id"]), []).append(dict(t._mapping))
    policy = await _policy(svc)
    rt = runtime_of(svc)
    total = 0
    for row in servers:
        srow = dict(row._mapping)
        spec = _spec_from_row(srow)
        rt.remember(spec)
        for t in by_server.get(int(srow["id"]), []):
            schema = t.get("input_schema") if isinstance(t.get("input_schema"), dict) else {}
            view = type("V", (), {"name": t["name"], "description": t.get("description") or "",
                                  "input_schema": schema})()
            register_tool(svc, spec, view, policy)
            total += 1
    return total


# ---------------------------------------------------------------- HTTP API
# Пути НЕ пересекаются с features/skills.py (там /mcp/servers, /mcp/tools,
# /mcp/policy) — здесь всё под /mcp/runtime/*.

@router.get("/mcp/runtime")
async def runtime_status(request: Request):
    svc = request.app.state.svc
    rt = runtime_of(svc)
    return {"sdk": "mcp", "sdk_available": sdk_available(), "sdk_version": sdk_version(),
            "servers": rt.statuses(),
            "registered_tools": [n for n in REGISTRY.names() if n.startswith("mcp:")]}


@router.post("/mcp/runtime/servers/{ref}/connect")
async def connect_server(ref: str, request: Request):
    svc = request.app.state.svc
    row = await _server_row(svc, ref)
    try:
        health = await runtime_of(svc).connect(_spec_from_row(row))
    except MCPUnavailable as exc:
        await _mark(svc, int(row["id"]), "unhealthy", str(exc))
        await svc.bus.emit("mcp.unhealthy", server=row["name"], detail=str(exc)[:400])
        raise HTTPException(503, {"message": str(exc)})
    await _mark(svc, int(row["id"]), health.status, health.detail)
    await svc.bus.emit("mcp.connected", server=row["name"])
    return health.as_dict()


@router.post("/mcp/runtime/servers/{ref}/disconnect")
async def disconnect_server(ref: str, request: Request):
    svc = request.app.state.svc
    row = await _server_row(svc, ref)
    await runtime_of(svc).disconnect(row["name"])
    await _mark(svc, int(row["id"]), "stopped", "отключён вручную")
    await svc.bus.emit("mcp.disconnected", server=row["name"])
    return {"server": row["name"], "status": "stopped"}


@router.get("/mcp/runtime/servers/{ref}/health")
async def server_health(ref: str, request: Request):
    svc = request.app.state.svc
    row = await _server_row(svc, ref)
    health = await runtime_of(svc).probe(row["name"])
    await _mark(svc, int(row["id"]), health.status, health.detail)
    return health.as_dict()


@router.post("/mcp/runtime/servers/{ref}/refresh")
async def refresh(ref: str, request: Request):
    svc = request.app.state.svc
    row = await _server_row(svc, ref)
    try:
        tools = await refresh_server(svc, row)
    except (MCPUnavailable, MCPCallError) as exc:
        await _mark(svc, int(row["id"]), "unhealthy", str(exc))
        await svc.bus.emit("mcp.unhealthy", server=row["name"], detail=str(exc)[:400])
        raise HTTPException(503, {"message": str(exc)})
    return {"server": row["name"], "tools": tools}


@router.get("/mcp/runtime/servers/{ref}/tools")
async def server_tools(ref: str, request: Request):
    svc = request.app.state.svc
    row = await _server_row(svc, ref)
    try:
        views = await runtime_of(svc).list_tools(row["name"])
    except (MCPUnavailable, MCPCallError) as exc:
        raise HTTPException(503, {"message": str(exc)})
    return [{"name": v.name, "description": v.description, "canonical": v.bossman_name,
             "input_schema": v.input_schema} for v in views]


@router.get("/mcp/runtime/servers/{ref}/resources")
async def server_resources(ref: str, request: Request):
    svc = request.app.state.svc
    row = await _server_row(svc, ref)
    try:
        return await runtime_of(svc).list_resources(row["name"])
    except (MCPUnavailable, MCPCallError) as exc:
        raise HTTPException(503, {"message": str(exc)})


@router.get("/mcp/runtime/servers/{ref}/prompts")
async def server_prompts(ref: str, request: Request):
    svc = request.app.state.svc
    row = await _server_row(svc, ref)
    try:
        return await runtime_of(svc).list_prompts(row["name"])
    except (MCPUnavailable, MCPCallError) as exc:
        raise HTTPException(503, {"message": str(exc)})


@router.post("/mcp/runtime/servers/{ref}/call")
async def call(ref: str, request: Request):
    """Ручной вызов оператором. Модель ходит НЕ сюда, а через tool-loop движка."""
    svc = request.app.state.svc
    body = await request.json()
    tool = body.get("tool")
    if not tool:
        raise HTTPException(422, {"message": "нужен tool"})
    row = await _server_row(svc, ref)
    rt = runtime_of(svc)
    try:
        await rt.ensure(_spec_from_row(row))
        res = await rt.call_tool(row["name"], tool, body.get("arguments") or {},
                                 timeout=float(body.get("timeout") or 30))
    except MCPUnavailable as exc:
        raise HTTPException(503, {"message": str(exc)})
    except MCPCallError as exc:
        await _emit_failure(svc, row["name"], tool, str(exc))
        raise HTTPException(502, {"message": str(exc)})
    return {"server": row["name"], "tool": tool, "is_error": res.is_error,
            "text": res.text, "structured": res.structured}


# ---------------------------------------------------------------- Feature

async def setup(svc) -> None:
    runtime_of(svc)
    try:
        await restore_registry(svc)
    except Exception as exc:      # реестр не должен ронять старт приложения
        await svc.bus.emit("mcp.unhealthy", server="*", detail=f"restore: {exc}"[:400])


async def tick(svc) -> None:
    """Периодический health: упавший сервер помечается и попадает в Activity."""
    rt = getattr(svc, "mcp", None)
    if rt is None:
        return
    for status in rt.statuses():
        if not status.get("connected"):
            continue
        health = await rt.probe(status["server"])
        if health.status != "healthy":
            try:
                row = await _server_row(svc, health.server_id)
                await _mark(svc, int(row["id"]), health.status, health.detail)
            except HTTPException:
                pass


FEATURE = Feature(name="tools_mcp", router=router, setup=setup, tick=tick,
                  tick_seconds=30.0)
