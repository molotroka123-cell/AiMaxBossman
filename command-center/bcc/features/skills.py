"""Feature 10 — Skill Library + Skill Forge + MCP Hub.

Skills — переиспользуемые проверенные процессы в .agents/skills/<id>/SKILL.md
(discovery поверх готовой bcc/v2/skill_library, БЕЗ рекурсии по всей машине).
Запуск скилла реально влияет на выполнение: process+input → prompt задачи.
MCP Hub — канонический реестр MCP-серверов/инструментов с AUTO/ASK/DENY;
только назначенные инструменты попадают в контекст модели.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import settings_kv, tasks as tasks_t, utcnow
from ..v2.mcp_hub import MCPServerSpec, MCPToolView, namespaced_tool
from ..v2.skill_library import SkillLibrary, default_skill_roots
from ..v2.tables import mcp_servers as mcp_servers_t, mcp_tools as mcp_tools_t
from . import Feature

ASSIGN_KEY = "skills.assignments"
MCP_POLICY_KEY = "mcp.policy"          # {"mcp:server:tool": "auto|ask|deny"}
router = APIRouter()


def _lib(svc) -> SkillLibrary:
    if getattr(svc, "skills", None) is not None:
        return svc.skills
    # запасной путь, если менеджер не поднялся
    repo = svc.settings.ui_dir.parent.parent
    return SkillLibrary(default_skill_roots(repo), repo / ".agents" / "skills")


def _skill_dict(sk) -> dict:
    meta = sk.frontmatter.get("metadata")
    version = meta.get("version", "1.0") if isinstance(meta, dict) else "1.0"
    return {"id": sk.id, "name": sk.name, "description": sk.description,
            "source_root": str(sk.source_root), "fingerprint": sk.fingerprint[:16],
            "version": version,
            "permissions": sk.frontmatter.get("permissions", []),
            "required_tools": sk.frontmatter.get("required_tools", [])}


# ---------- Skills API ----------

@router.get("/skills")
async def list_skills(request: Request):
    svc = request.app.state.svc
    lib = _lib(svc)
    assigns = await _assignments(svc)
    out = []
    for sk in lib.discover():
        d = _skill_dict(sk)
        d["agents"] = assigns.get(sk.id, [])
        out.append(d)
    return out


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, request: Request):
    svc = request.app.state.svc
    sk = _lib(svc).by_id().get(skill_id)
    if sk is None:
        raise HTTPException(404, {"message": "скилл не найден"})
    d = _skill_dict(sk)
    d["process"] = sk.body
    d["frontmatter"] = sk.frontmatter
    return d


@router.post("/skills")
async def create_skill(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    sid = body.get("id")
    content = body.get("content")
    if not sid or not content:
        raise HTTPException(422, {"message": "нужны id и content"})
    try:
        sk = _lib(svc).create(sid, content, overwrite=bool(body.get("overwrite")))
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(409, {"message": str(exc)})
    await svc.bus.emit("skill.created", slug=sk.id)
    return _skill_dict(sk)


@router.post("/skills/{skill_id}/clone")
async def clone_skill(skill_id: str, request: Request):
    svc = request.app.state.svc
    body = await request.json()
    lib = _lib(svc)
    sk = lib.by_id().get(skill_id)
    if sk is None:
        raise HTTPException(404, {"message": "скилл не найден"})
    new_id = body.get("new_id") or f"{skill_id}-copy"
    content = sk.path.read_text(encoding="utf-8")
    try:
        clone = lib.create(new_id, content)
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(409, {"message": str(exc)})
    return _skill_dict(clone)


@router.get("/skills/{skill_id}/export")
async def export_skill(skill_id: str, request: Request):
    svc = request.app.state.svc
    sk = _lib(svc).by_id().get(skill_id)
    if sk is None:
        raise HTTPException(404, {"message": "скилл не найден"})
    return {"id": sk.id, "content": sk.path.read_text(encoding="utf-8"),
            "fingerprint": sk.fingerprint}


@router.post("/skills/import")
async def import_skill(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    if not body.get("id") or not body.get("content"):
        raise HTTPException(422, {"message": "нужны id и content"})
    try:
        sk = _lib(svc).create(body["id"], body["content"], overwrite=bool(body.get("overwrite")))
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(409, {"message": str(exc)})
    return _skill_dict(sk)


@router.post("/skills/{skill_id}/assign")
async def assign_skill(skill_id: str, request: Request):
    svc = request.app.state.svc
    body = await request.json()
    agent_id = body.get("agent_id")
    if agent_id is None:
        raise HTTPException(422, {"message": "нужен agent_id"})
    assigns = await _assignments(svc)
    lst = assigns.setdefault(skill_id, [])
    if agent_id not in lst:
        lst.append(agent_id)
    await _save_assignments(svc, assigns)
    await svc.bus.emit("skill.assigned", slug=skill_id, agent_id=agent_id)
    return {"skill_id": skill_id, "agents": lst}


@router.post("/skills/{skill_id}/run")
async def run_skill(skill_id: str, request: Request):
    """Реальное влияние на выполнение: process+input → prompt задачи и запуск."""
    svc = request.app.state.svc
    body = await request.json()
    sk = _lib(svc).by_id().get(skill_id)
    if sk is None:
        raise HTTPException(404, {"message": "скилл не найден"})
    inputs = body.get("input") or {}
    errors = _validate_input(sk.frontmatter.get("input_schema") or {}, inputs)
    if errors:
        raise HTTPException(422, {"message": "вход не по схеме", "hint": "; ".join(errors)})
    prompt = (f"{sk.body}\n\n## Входные данные\n"
              + "\n".join(f"- {k}: {v}" for k, v in inputs.items()))
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(tasks_t).values(
            title=f"Скилл: {sk.name}", prompt=prompt, agent_id=body.get("agent_id"),
            status="draft", kind="research", meta={"skill": sk.id, "skill_fp": sk.fingerprint},
            created_at=utcnow(), updated_at=utcnow()))
        task_id = int(res.inserted_primary_key[0])
        await s.commit()
    if body.get("agent_id"):
        await svc.engine.enqueue(task_id)
    return {"task_id": task_id, "skill": sk.id}


def _validate_input(schema: dict, data: dict) -> list[str]:
    """Минимальный валидатор required/type (jsonschema не тянем)."""
    errors: list[str] = []
    for field in schema.get("required", []):
        if field not in data:
            errors.append(f"нет обязательного поля {field}")
    for name, spec in (schema.get("properties") or {}).items():
        if name in data and spec.get("type") == "string" and not isinstance(data[name], str):
            errors.append(f"{name} должно быть строкой")
    return errors


async def _assignments(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == ASSIGN_KEY))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return {}


async def _save_assignments(svc, data: dict) -> None:
    enc = svc.vault.encrypt(json.dumps(data))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == ASSIGN_KEY))
        await s.execute(sa.insert(settings_kv).values(key=ASSIGN_KEY, value_enc=enc))
        await s.commit()


# ---------- MCP Hub API ----------

@router.get("/mcp/servers")
async def list_mcp(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(mcp_servers_t))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/mcp/servers")
async def add_mcp(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    spec = MCPServerSpec(id=body.get("name", ""), name=body.get("name", ""),
                         transport=body.get("transport", "stdio"),
                         command=body.get("command", []), url=body.get("url", ""))
    errs = spec.validate()
    if errs:
        raise HTTPException(422, {"message": "; ".join(errs)})
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(mcp_servers_t).values(
            name=spec.name, transport=spec.transport, command=spec.command,
            url=spec.url, enabled=True, status="unknown", created_at=utcnow()))
        sid = int(res.inserted_primary_key[0])
        await s.commit()
    return {"id": sid, "name": spec.name}


@router.delete("/mcp/servers/{server_id}")
async def del_mcp(server_id: int, request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        await s.execute(sa.delete(mcp_servers_t).where(mcp_servers_t.c.id == server_id))
        await s.commit()
    return {"ok": True}


@router.get("/mcp/tools")
async def list_mcp_tools(request: Request):
    """Инструменты в каноническом виде mcp:<server>:<tool> + политика AUTO/ASK/DENY."""
    svc = request.app.state.svc
    policy = await _mcp_policy(svc)
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(mcp_tools_t, mcp_servers_t.c.name)
                                .join(mcp_servers_t, mcp_servers_t.c.id == mcp_tools_t.c.server_id))
                ).fetchall()
    out = []
    for r in rows:
        m = r._mapping
        canonical = namespaced_tool(m["name_1"], m["name"])
        out.append({"server": m["name_1"], "tool": m["name"], "canonical": canonical,
                    "description": m["description"], "policy": policy.get(canonical, "ask")})
    return out


@router.post("/mcp/policy")
async def set_mcp_policy(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    canonical = body.get("canonical")
    decision = body.get("policy")
    if decision not in ("auto", "ask", "deny"):
        raise HTTPException(422, {"message": "policy должна быть auto|ask|deny"})
    policy = await _mcp_policy(svc)
    policy[canonical] = decision
    enc = svc.vault.encrypt(json.dumps(policy))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == MCP_POLICY_KEY))
        await s.execute(sa.insert(settings_kv).values(key=MCP_POLICY_KEY, value_enc=enc))
        await s.commit()
    return {"canonical": canonical, "policy": decision}


async def _mcp_policy(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == MCP_POLICY_KEY))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return {}


FEATURE = Feature(name="skills", router=router)
