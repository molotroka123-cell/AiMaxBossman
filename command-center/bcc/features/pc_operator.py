"""The ONE PC operator behind the phone pult (owner redirect 11.11.2026, constitution v3).

    GET  /api/operator        -> {agent, model, candidates, total_control, ready, reason}
    PUT  /api/operator        {model_id?, total_control?} -> same shape
    POST /api/operator/warm   -> {ok, loaded, load_ms, error}

One agent row, role ``pc-operator``, named «Управленец ПК». Its model is swappable by one setting and only LOCAL models
(provider on loopback) are offered. «Total control» is the existing permission mechanism: the operator gets the
dangerous PC permissions, so those tools run without a per-step approval. Hook floors and DENY of every tool stay as
they are (tools.decide_effect is monotone), and money, mail, deploys, settings and cli.bypass are never granted here:
under constitution v3 they remain the owner's decision.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..db import agents as agents_t, models as models_t, providers as providers_t
from . import Feature

log = logging.getLogger("bcc.pc_operator")
router = APIRouter()

ROLE = "pc-operator"
NAME = "Управленец ПК"
MIN_STEPS = 30
#: what «Полный доступ к ПК» grants. Never: email.send, channel.send, invoice.create, deploy.*, settings.write,
#: cli.bypass, model.unload (constitution v3: third parties, money, the pult itself stay the owner's decision).
TOTAL_CONTROL = ("filesystem.write", "terminal.run", "git.write", "browser.control",
                 "computer.observe", "computer.control", "desktop.control")
NON_CHAT_KINDS = {"embedding", "embed", "image", "video", "tts", "stt", "audio", "rerank"}
#: owner 11.11 night «по пути создавая tools and skills»: the operator reuses and saves its procedures
#: (bcc.features.tools_selfext). Granted to this agent only; other agents get them only by an explicit grant.
SELFEXT_TOOLS = ("skill.find", "skill.read", "skill.save", "tool.create", "tool.list_custom", "tool.run_custom")
TOOL_NAME = "filesystem.read_any"
SELFEXT_MARK = "[навыки v1]"
SELFEXT_RULE = (f"{SELFEXT_MARK} Перед задачей вызови skill.find; нашёл навык — skill.read и следуй ему. "
                "Решил задачу в несколько шагов и проверил результат — сохрани skill.save (точные шаги). "
                "Повторяешь одну и ту же команду/скрипт — tool.create, дальше tool.run_custom.")

#: owner 12.11 «тренировка на управление компом»: measured on tools/pc_operator_bench.py — the model took the
#: system-disk tool for D:, and PowerShell `$_` broke inside terminal.run's `sh -lc`. Executable lessons = the seed
#: custom tools (tools/pc_operator_seed_tools, installed into the operator tool store) named here once.
RECIPES_MARK = "[ПК-рецепты v1]"
RECIPES_RULE = (f"{RECIPES_MARK} Факты о ПК бери из инструмента, не по памяти, и сверяй ответ с выводом. Готовые "
                "инструменты (tool.run_custom name=<имя> args={...}): disk_free {drive} — место на ЛЮБОМ диске "
                "(pc.system_info видит только C:; нет диска — так и скажи); find_files {root, ext или name}; "
                "json_get {path, key}; count_lines {path, contains?, skip_header?}; proc_count {name}; "
                "port_check {port}; git_info {repo}; active_window; list_windows {query?}; open_in_notepad {path}; "
                "notepad_write {path, text}; close_window {title} — закрывает только окна с этой частью заголовка "
                "(никогда Stop-Process -Name). Файлы меняй только в папке из задачи: один файл через "
                "Set-Content / Rename-Item / Remove-Item -LiteralPath. Оболочка terminal.run — sh: PowerShell "
                "пиши как powershell -NoProfile -Command '...' (одинарные кавычки). Страница в браузере: "
                "browser.open url, заголовок — из снимка.")

#: owner 11.11 «доступ ко всем файлам, read-only, чаты не читаем»: tool filesystem.read_any (features/tools_fs_read),
#: DENY list in features/fs_read_policy. Default ON for the operator; PUT read_everything toggles it for the operator
#: and for every existing local-model agent (permission ``filesystem.read_any`` + tool name).
READ_PERM = "filesystem.read_any"
READ_MARK = "[чтение ПК v1]"
READ_RULE = (f"{READ_MARK} Любые файлы ПК читай ТОЛЬКО через filesystem.read_any (read-only: read/list/find). "
             "Чаты, браузерные профили, ключи/токены/vault, фото и видео закрыты политикой: не пытайся обойти "
             "их другими инструментами, а отвечай, что это закрыто.")


class OperatorPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: int | None = Field(default=None, ge=1)
    total_control: bool | None = None
    read_everything: bool | None = None


def _loopback(url: Any) -> bool:
    from .agent_opencode import provider_url_is_loopback
    return provider_url_is_loopback(url)


def _perms(agent: dict) -> dict:
    p = agent.get("permissions") or {}
    if isinstance(p, list):
        return {k: True for k in p if isinstance(k, str)}
    return dict(p) if isinstance(p, dict) else {}


def _tools(agent: dict) -> list:
    t = agent.get("tools") or []
    if isinstance(t, str):
        try:
            t = json.loads(t)
        except ValueError:
            t = []
    return list(t) if isinstance(t, list) else []


async def _rows(s, table, *where) -> list[dict]:
    q = sa.select(table)
    for w in where:
        q = q.where(w)
    return [dict(r._mapping) for r in (await s.execute(q)).fetchall()]


async def _local_models(s) -> list[dict]:
    provs = {p["id"]: p for p in await _rows(s, providers_t)}
    out = []
    for m in await _rows(s, models_t):
        p = provs.get(m.get("provider_id"))
        if not p or not _loopback(p.get("base_url")):
            continue
        if str(m.get("name") or "").endswith(":cloud") or str(m.get("kind") or "").lower() in NON_CHAT_KINDS:
            continue
        if str(m.get("status") or "").lower() in {"disabled", "removed", "deleted"}:
            continue
        out.append({**m, "_provider": p})
    return out


async def _with_selfext(s, agent: dict) -> dict:
    """Idempotently give the operator its self-extension tools and the one-paragraph rule (no other change)."""
    tools = _tools(agent)
    prompt = str(agent.get("system_prompt") or "")
    values: dict = {}
    missing = [t for t in SELFEXT_TOOLS if t not in tools]
    if missing:
        values["tools"] = tools + missing
    if SELFEXT_MARK not in prompt:
        prompt = (prompt.rstrip() + "\n\n" + SELFEXT_RULE).strip()
        values["system_prompt"] = prompt
    if RECIPES_MARK not in prompt:
        values["system_prompt"] = (prompt.rstrip() + "\n\n" + RECIPES_RULE).strip()
    if not values:
        return agent
    await s.execute(sa.update(agents_t).where(agents_t.c.id == agent["id"]).values(**values))
    await s.commit()
    log.info("pc_operator: agent %s self-extension tools/rule added (%s)", agent["id"], ",".join(sorted(values)))
    return {**agent, **values}


async def _set_read(s, agent: dict, on: bool, prompt: bool = False) -> dict:
    """Grant/revoke filesystem.read_any on one agent (permission + tool name; monotone: nothing else changes)."""
    perms, tools = _perms(agent), _tools(agent)
    if on:
        perms[READ_PERM] = True
        if TOOL_NAME not in tools:
            tools.append(TOOL_NAME)
    else:
        perms[READ_PERM] = False
        tools = [t for t in tools if t != TOOL_NAME]
    values: dict = {"permissions": perms, "tools": tools}
    prompt_s = str(agent.get("system_prompt") or "")
    if prompt and on and READ_MARK not in prompt_s:
        values["system_prompt"] = f"{prompt_s.rstrip()}{chr(10)}{chr(10)}{READ_RULE}".strip()
    await s.execute(sa.update(agents_t).where(agents_t.c.id == agent["id"]).values(**values))
    await s.commit()
    return {**agent, **values}


async def _with_read(s, agent: dict) -> dict:
    """Default ON for the operator unless the owner switched it off explicitly (permission stored as False)."""
    perms = _perms(agent)
    if perms.get(READ_PERM) is False:
        return agent
    if perms.get(READ_PERM) is True and TOOL_NAME in _tools(agent) and READ_MARK in str(agent.get("system_prompt") or ""):
        return agent
    return await _set_read(s, agent, True, prompt=True)


async def _ensure_agent(s) -> dict | None:
    """The operator row; created once from the best existing local PC agent (the one with computer tools)."""
    rows = await _rows(s, agents_t, agents_t.c.role == ROLE)
    if rows:
        return await _with_read(s, await _with_selfext(s, sorted(rows, key=lambda a: a["id"])[0]))
    local_ids = {m["id"] for m in await _local_models(s)}
    if not local_ids:
        return None
    pool = [a for a in await _rows(s, agents_t) if a.get("enabled") and a.get("model_id") in local_ids]
    pool.sort(key=lambda a: ("computer.observe" not in _tools(a), "terminal.run" not in _tools(a), a["id"]))
    if not pool:
        return None
    tpl = pool[0]
    values = {"name": NAME, "role": ROLE, "system_prompt": tpl.get("system_prompt") or "",
              "model_id": tpl["model_id"], "fallback_model_id": None, "tools": _tools(tpl),
              "max_steps": max(int(tpl.get("max_steps") or 0), MIN_STEPS), "max_tokens": tpl.get("max_tokens"),
              "budget_usd": 0.0, "permissions": {k: v for k, v in _perms(tpl).items() if k not in TOTAL_CONTROL},
              "enabled": True}
    res = await s.execute(sa.insert(agents_t).values(**values))
    await s.commit()
    new_id = res.inserted_primary_key[0]
    log.info("pc_operator: created agent %s from template %s", new_id, tpl["id"])
    return await _with_read(s, await _with_selfext(s, (await _rows(s, agents_t, agents_t.c.id == new_id))[0]))


def _ollama_root(provider: dict) -> str:
    root = str(provider.get("base_url") or "").rstrip("/")
    return root[:-3] if root.endswith("/v1") else root


async def _resident(model: dict) -> bool | None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as c:
            r = await c.get(_ollama_root(model["_provider"]) + "/api/ps")
            r.raise_for_status()
            names = {str(x.get(k) or "") for x in (r.json().get("models") or []) for k in ("name", "model")}
    except Exception:  # noqa: BLE001 - unknown, never a guess
        return None
    n = str(model.get("name") or "")
    return n in names or f"{n}:latest" in names or n.removesuffix(":latest") in names


async def _view(svc) -> dict:
    async with svc.db.session() as s:
        agent = await _ensure_agent(s)
        cands = await _local_models(s)
    if agent is None:
        return {"agent": None, "model": None, "candidates": [], "total_control": False, "ready": False,
                "reason": "на ПК нет локальной модели с агентом — добавьте модель Ollama"}
    model = {m["id"]: m for m in cands}.get(agent.get("model_id"))
    perms = _perms(agent)
    view = None
    if model is not None:
        view = {"id": model["id"], "alias": model.get("alias"), "name": model.get("name"),
                "context_window": model.get("context_window"), "health": model.get("status"),
                "loaded": await _resident(model)}
    reason = None
    if not agent.get("enabled"):
        reason = "агент выключен"
    elif model is None:
        reason = "у управленца не локальная модель — выберите локальную"
    return {"agent": {"id": agent["id"], "name": agent.get("name")}, "model": view,
            "candidates": [{"id": m["id"], "alias": m.get("alias"), "name": m.get("name")} for m in cands][:50],
            "total_control": all(perms.get(k) is True for k in TOTAL_CONTROL),
            "read_everything": perms.get(READ_PERM) is True and TOOL_NAME in _tools(agent),
            "ready": reason is None, "reason": reason}


@router.get("/operator")
async def get_operator(request: Request):
    return await _view(request.app.state.svc)


@router.put("/operator")
async def put_operator(body: OperatorPut, request: Request):
    svc = request.app.state.svc
    if body.model_id is None and body.total_control is None and body.read_everything is None:
        raise HTTPException(422, {"code": "BM_OPERATOR_EMPTY", "message": "нечего менять"})
    async with svc.db.session() as s:
        agent = await _ensure_agent(s)
        if agent is None:
            raise HTTPException(409, {"code": "BM_OPERATOR_NO_AGENT", "message": "нет локального агента"})
        values: dict = {}
        if body.model_id is not None:
            if body.model_id not in {m["id"] for m in await _local_models(s)}:
                raise HTTPException(422, {"code": "BM_OPERATOR_NOT_LOCAL",
                                          "message": "управленец работает только на локальной модели этого ПК"})
            values["model_id"] = body.model_id
        if body.total_control is not None:
            perms = _perms(agent)
            for k in TOTAL_CONTROL:
                if body.total_control:
                    perms[k] = True
                else:
                    perms.pop(k, None)
            values["permissions"] = perms
        if values:
            await s.execute(sa.update(agents_t).where(agents_t.c.id == agent["id"]).values(**values))
            await s.commit()
        if body.read_everything is not None:
            agent = {**agent, **values}
            await _set_read(s, agent if "permissions" in values else (await _rows(s, agents_t, agents_t.c.id == agent["id"]))[0],
                            body.read_everything, prompt=True)
            local_ids = {m["id"] for m in await _local_models(s)}
            for other in await _rows(s, agents_t):
                if other["id"] == agent["id"]:
                    continue
                if body.read_everything and other.get("model_id") in local_ids:
                    await _set_read(s, other, True)
                elif not body.read_everything and (_perms(other).get(READ_PERM) or TOOL_NAME in _tools(other)):
                    await _set_read(s, other, False)
            log.warning("operator_read_everything agent=%s value=%s", agent["id"], body.read_everything)
    if body.total_control is not None:
        log.warning("operator_total_control agent=%s value=%s", agent["id"], body.total_control)
    return await _view(svc)


@router.post("/operator/warm")
async def warm_operator(request: Request):
    """Load the operator's model into memory so the first message does not wait for a cold load."""
    import httpx
    svc = request.app.state.svc
    async with svc.db.session() as s:
        agent = await _ensure_agent(s)
        model = {m["id"]: m for m in await _local_models(s)}.get((agent or {}).get("model_id"))
    if model is None:
        return {"ok": False, "loaded": False, "load_ms": 0, "error": "нет локальной модели у управленца"}
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=300.0, trust_env=False) as c:
            r = await c.post(_ollama_root(model["_provider"]) + "/api/generate",
                             json={"model": model["name"], "prompt": "", "keep_alive": "30m", "stream": False})
            r.raise_for_status()
    except Exception as e:  # noqa: BLE001 - reported to the phone as a plain error
        return {"ok": False, "loaded": False, "load_ms": int((time.monotonic() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}"[:200]}
    return {"ok": True, "loaded": True, "load_ms": int((time.monotonic() - t0) * 1000), "error": None}


async def _setup(svc) -> None:
    """At startup: an EXISTING operator row gets the self-extension tools/rule (no row is created here)."""
    try:
        async with svc.db.session() as s:
            for agent in await _rows(s, agents_t, agents_t.c.role == ROLE):
                await _with_read(s, await _with_selfext(s, agent))
    except Exception as exc:  # noqa: BLE001 - never block startup; GET /api/operator retries it
        log.warning("pc_operator: self-extension upgrade skipped: %s", type(exc).__name__)


FEATURE = Feature(name="pc_operator", router=router, setup=_setup)
