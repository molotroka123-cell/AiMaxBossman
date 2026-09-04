"""Feature 09 — Browser Live View (Playwright, DOM-first, Human Take Over).

Поверх готового bcc/v2/browser_control (политика AUTO/ASK/DENY: navigate/click/
type→auto, login/upload/download/submit→ask, payment/wallet/bank→deny; Take Over
блокирует действия агента до Resume). Сессии — в browser_sessions. Скриншоты — на
диск. DOM-snapshot дёшев и первичен; vision — по требованию.

Chromium предустановлен: launch с executable_path из PLAYWRIGHT/пути пака.
Нет Playwright → honest 503, сервис не падает.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..db import utcnow
from ..v2.browser_control import (BrowserApprovalRequired, BrowserManager, BrowserPolicy,
                                  BrowserPolicyDenied, BrowserTakeoverActive, BrowserUnavailable)
from ..v2.tables import browser_sessions as bs_t
from . import Feature

router = APIRouter()
CHROMIUM = "/opt/pw-browsers/chromium"      # предустановлен в этом окружении


def _mgr(svc) -> BrowserManager:
    if getattr(svc, "browser", None) is None:
        svc.browser = BrowserManager(svc.settings.data_dir / "browser")
    # научим менеджер пользоваться предустановленным Chromium, если есть
    _patch_executable(svc.browser)
    return svc.browser


def _patch_executable(mgr: BrowserManager) -> None:
    """launch без загрузки браузера: используем предустановленный Chromium."""
    if getattr(mgr, "_exec_patched", False):
        return
    if not Path(CHROMIUM).exists():
        mgr._exec_patched = True
        return
    orig_start = mgr.start

    async def start(session_id, policy, *, profile_name="default", headless=True):
        # monkeypatch chromium.launch чтобы подставить executable_path
        pw = await mgr._playwright()
        real_launch = pw.chromium.launch

        async def launch(**kw):
            kw.setdefault("executable_path", CHROMIUM)
            return await real_launch(**kw)
        pw.chromium.launch = launch
        try:
            return await orig_start(session_id, policy, profile_name=profile_name, headless=headless)
        finally:
            pw.chromium.launch = real_launch
    mgr.start = start
    mgr._exec_patched = True


async def _record(svc, session_id: int, **values) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(bs_t).where(bs_t.c.id == session_id).values(**values))
        await s.commit()


@router.get("/browser/health")
async def browser_health(request: Request):
    """Честное состояние рантайма браузера: available/false, а не «пусто = зелёный»."""
    mgr = _mgr(request.app.state.svc)
    return {"available": bool(mgr.available),
            "active_sessions": len(getattr(mgr, "_sessions", {}) or {})}


@router.post("/browser/sessions")
async def create_session(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    async with svc.db.session() as s:
        sid = int((await s.execute(sa.insert(bs_t).values(
            status="created", agent_id=body.get("agent_id"), task_id=body.get("task_id"),
            created_at=utcnow()))).inserted_primary_key[0])
        await s.commit()
    policy = BrowserPolicy.from_dict(body.get("policy"))
    try:
        status = await _mgr(svc).start(sid, policy, headless=True)
    except BrowserUnavailable as exc:
        await _record(svc, sid, status="failed")
        raise HTTPException(503, {"message": str(exc)})
    except BrowserPolicyDenied as exc:
        await _record(svc, sid, status="failed")
        raise HTTPException(403, {"message": str(exc)})
    except HTTPException:
        await _record(svc, sid, status="failed")
        raise
    except Exception as exc:  # noqa: BLE001
        # Playwright падает не только двумя нашими типами: нет браузера, нет
        # памяти, умер GPU-процесс. Раньше такое исключение уходило наружу, а
        # строка сессии навсегда оставалась в status="created" — по базе нельзя
        # было отличить «создаётся» от «взорвалось».
        await _record(svc, sid, status="failed")
        await svc.bus.emit("run.log", level="error", message=f"browser session {sid} failed to start",
                           session_id=sid)
        raise HTTPException(500, {"message": f"не удалось запустить сессию браузера: {exc}"}) from exc
    await _record(svc, sid, status="running", current_url=status.get("url", ""))
    await svc.bus.emit("agent.tool_call", tool="browser", session_id=sid, action="start")
    return {"session_id": sid, **status}


@router.get("/browser/sessions")
async def list_sessions(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(bs_t).order_by(bs_t.c.id.desc()).limit(50))).fetchall()
    mgr = _mgr(svc)
    # Строка в browser_sessions переживает рестарт процесса, рантайм-контекст
    # Playwright — нет: без этого поля UI опрашивал бы screenshot/state для
    # сессий, которых в этом процессе уже никогда не будет (см.
    # BCC-V2-SESSION-20783913FA36-P1-FIX-001, P1-D).
    return [{**dict(r._mapping), "live": mgr.is_live(r._mapping["id"])} for r in rows]


@router.get("/browser/sessions/{session_id}/state")
async def state(session_id: int, request: Request):
    svc = request.app.state.svc
    try:
        return await _mgr(svc).status(session_id)
    except LookupError:
        raise HTTPException(404, {"message": "сессия не запущена (возможно, после рестарта)"})


@router.post("/browser/sessions/{session_id}/act")
async def act(session_id: int, request: Request):
    """Действие агента: navigate/click/type/select/back/reload/snapshot.
    Политика: ask без approved → 202; deny → 403; takeover → 409."""
    svc = request.app.state.svc
    body = await request.json()
    action = body.get("action")
    mgr = _mgr(svc)
    actor = str(body.get("actor") or "agent")
    if actor not in ("agent", "human"):
        raise HTTPException(422, {"message": "actor: agent|human"})
    subject = str(body.get("url", body.get("selector", "")))
    preview = f"browser {action}: {subject}"
    # F-015: подтверждение — только запись approvals(kind=browser, тот же preview),
    # предъявленная как approval_id (одноразовая). «approved: true» в теле — не авторитет.
    if body.get("approved") and not body.get("approval_id"):
        raise HTTPException(403, {"message": "самоутверждённый флаг approved не принимается: "
                                             "нужен approval_id одобренной записи"})
    approved = await svc.approvals.consume(body.get("approval_id"), kind="browser",
                                           preview=preview)
    try:
        if action == "navigate":
            res = await mgr.navigate(session_id, body["url"], actor=actor, approved=approved)
        elif action == "click":
            res = await mgr.click(session_id, body["selector"], actor=actor, approved=approved)
        elif action == "type":
            res = await mgr.type_text(session_id, body["selector"], body.get("text", ""),
                                      actor=actor, approved=approved)
        elif action == "snapshot":
            res = await mgr.snapshot(session_id, actor=actor, approved=approved)
        elif action == "back":
            res = await mgr.back(session_id, actor=actor)
        elif action == "reload":
            res = await mgr.reload(session_id, actor=actor)
        else:
            raise HTTPException(422, {"message": f"неизвестное действие: {action}"})
    except LookupError:
        raise HTTPException(404, {"message": "сессия не запущена"})
    except BrowserTakeoverActive:
        raise HTTPException(409, {"message": "активен Human Take Over — действия агента заблокированы"})
    except BrowserApprovalRequired:
        aid = await svc.approvals.create(kind="browser", preview=preview)
        raise HTTPException(202, {"message": "нужно подтверждение",
                                  "approval_id": aid.get("id")})
    except BrowserPolicyDenied as exc:
        raise HTTPException(403, {"message": f"действие запрещено политикой: {exc}"})
    await _record(svc, session_id, current_url=res.get("url", ""), last_action=action)
    await svc.bus.emit("agent.tool_call", tool="browser", session_id=session_id,
                       action=action, url=res.get("url", ""))
    return res


@router.get("/browser/sessions/{session_id}/screenshot")
async def screenshot(session_id: int, request: Request):
    svc = request.app.state.svc
    try:
        png = await _mgr(svc).screenshot(session_id, actor="human", approved=True)
    except LookupError:
        raise HTTPException(404, {"message": "сессия не запущена"})
    path = svc.settings.data_dir / "browser" / f"shot-{session_id}-{uuid.uuid4().hex[:6]}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return FileResponse(path, media_type="image/png")


@router.post("/browser/sessions/{session_id}/takeover")
async def takeover(session_id: int, request: Request):
    svc = request.app.state.svc
    res = await _mgr(svc).takeover(session_id)
    await _record(svc, session_id, takeover=True)
    await svc.bus.emit("agent.warning", tool="browser", session_id=session_id, takeover=True)
    return res


@router.post("/browser/sessions/{session_id}/resume")
async def resume(session_id: int, request: Request):
    svc = request.app.state.svc
    res = await _mgr(svc).resume(session_id)
    await _record(svc, session_id, takeover=False, paused=False)
    return res


@router.post("/browser/sessions/{session_id}/stop")
async def stop(session_id: int, request: Request):
    svc = request.app.state.svc
    await _mgr(svc).stop(session_id)
    await _record(svc, session_id, status="stopped", finished_at=utcnow())
    return {"ok": True}


FEATURE = Feature(name="browser", router=router)
