"""Bossman Core — FastAPI: API из раздела 11, WS-события, статика UI.

Слушает только внутри docker-сети / на 127.0.0.1; наружу — через Tailscale serve.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import approvals as approvals_mod
from . import db, errors, events, obs, runner, telegram
from .agents import load_all, set_cloud_policy
from .notifications.store import CallbackRejected
from .perimeter import (
    SCOPE_ADMIN,
    SCOPE_APPROVE,
    SCOPE_CHAT,
    SCOPE_EVENTS,
    authenticate_websocket,
    require_scope,
)
from .config import ROOT, settings
from .lifecycle import registry as _subsystems
from .projects.plan import State, journal_tail, project_dir
from .projects.planner import plan_project
from .projects.runner import run_project

app = FastAPI(title="Bossman Core", version="0.3")
_background: set[asyncio.Task] = set()

# ЭТАП 4–7: единый рендер ошибок домена (BossmanError + складываемые легаси-
# исключения → {"error":{code,message,cid}}) и структурный лог с вычисткой секретов.
obs.configure_logging()
errors.install_error_handlers(app)

# Подсистемы этапов 4–7 регистрируются здесь (до startup); реестр их и поднимает,
# и грациозно останавливает. Импорт — ленивый и терпимый: если пакет ещё не
# подъехал (частичная сборка), ядро всё равно стартует.
def _register_subsystems() -> None:
    for modname, attr in (
        ("bossman.resource_brain", "build_subsystem"),
        ("bossman.remote_client", "build_subsystem"),
        ("bossman.search_everything", "build_subsystem"),
        ("bossman.video_factory", "build_subsystem"),
        ("bossman.sandbox", "build_subsystem"),
        ("bossman.dev_factory", "build_subsystem"),
        ("bossman.computer_operator", "build_subsystem"),
        ("bossman.cost_control", "build_subsystem"),
        ("bossman.world_intelligence", "build_subsystem"),
        ("bossman.notifications", "build_subsystem"),
        ("bossman.profiles", "build_subsystem"),
    ):
        try:
            import importlib
            mod = importlib.import_module(modname)
            build = getattr(mod, attr, None)
            if build is None:
                continue
            sub = build()
            if sub is not None:
                _subsystems.register(sub)
        except Exception as exc:  # noqa: BLE001 — подсистема опциональна на этапе сборки
            obs.get_logger("bossman.api").warning("subsystem register skipped: %s (%s)", modname, exc)


def _include_stage_routers() -> None:
    for modname in (
        "bossman.resource_brain",
        "bossman.remote_client",
        "bossman.search_everything",
        "bossman.video_factory",
        "bossman.sandbox",
        "bossman.dev_factory",
        "bossman.ai_lab",
        "bossman.computer_operator",
        "bossman.cost_control",
        "bossman.world_intelligence",
        "bossman.notifications",
        "bossman.profiles",
        "bossman.trading_learning",
    ):
        try:
            import importlib
            mod = importlib.import_module(modname)
            router = getattr(mod, "router", None)
            if router is not None:
                app.include_router(router)
        except Exception as exc:  # noqa: BLE001
            obs.get_logger("bossman.api").warning("router include skipped: %s (%s)", modname, exc)


_register_subsystems()
_include_stage_routers()


def _spawn(coro) -> None:
    t = asyncio.get_event_loop().create_task(coro)
    _background.add(t)
    t.add_done_callback(_background.discard)


@app.on_event("startup")
async def startup() -> None:
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    await db.pool()
    await runner.mark_interrupted()   # после перезагрузки: незавершённое помечено и видно
    _spawn(runner.worker())
    # ЭТАП 4–7: поднять зарегистрированные подсистемы. Критичная (validate/start
    # с critical=True) уронит boot; опциональная деградирует, но не мешает старту.
    await _subsystems.start_all()


@app.on_event("shutdown")
async def shutdown() -> None:
    for t in _background:
        t.cancel()
    # ЭТАП 4–7: остановить подсистемы в обратном порядке ДО закрытия ядровых
    # ресурсов (браузер/gateway/context/БД). Каждая stop() идемпотентна.
    await _subsystems.stop_all()
    # Закрыть браузерные контексты ДО закрытия БД: иначе Chromium остаётся
    # осиротевшим процессом после каждой остановки сервиса. shutdown() у
    # менеджера идемпотентен — если браузер не поднимался, он ничего не делает.
    from .toolkit.browser import MANAGER as _BROWSER
    try:
        await _BROWSER.shutdown()
    except Exception:
        pass
    # ЭТАП 3: закрыть HTTP-клиент Gateway, чтобы не осталось осиротевших соединений
    from .llm import aclose_gateway
    try:
        await aclose_gateway()
    except Exception:
        pass
    # ЭТАП 2.222: чисто закрыть SQLite-соединения context_engine (WAL flush).
    try:
        from .context_engine import close_all as _close_context
        _close_context()
    except Exception:
        pass
    await db.close()


# ---------- задачи ----------

class TaskIn(BaseModel):
    text: str
    agent: str | None = None     # None = «сам разберётся»
    source: str = "ui"


@app.post("/tasks", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def create_task(body: TaskIn):
    row = await db.fetchrow(
        "INSERT INTO tasks (agent, source, text) VALUES ($1,$2,$3) RETURNING *",
        body.agent, body.source, body.text)
    await runner.enqueue(row["id"])
    events.emit("task.created", id=row["id"], agent=body.agent, text=body.text[:200])
    return row


@app.get("/tasks/{task_id}", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def get_task(task_id: int):
    row = await db.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
    if not row:
        raise HTTPException(404)
    return row


@app.get("/tasks/{task_id}/explain", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def explain_task(task_id: int):
    """V2.6 Flight Recorder: связный трейс задачи (почему модель/инструмент/
    эскалация/остановка, ресурсы, evidence). Read-side сборка из канонических
    таблиц — ноль накладных на петлю; секреты в выводе редактируются."""
    from . import flight_recorder
    trace = await flight_recorder.explain_task(task_id)
    if trace is None:
        raise HTTPException(404)
    return trace


@app.get("/tasks", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def list_tasks(status: str | None = None, limit: int = 50):
    # Stage 13: limit не доверяем клиенту — отрицательный/гигантский лимит в
    # Postgres означает «без лимита» (выкачка таблицы аутентифицированным чатом).
    limit = max(1, min(limit, 500))
    if status:
        return await db.fetch("SELECT * FROM tasks WHERE status=$1 ORDER BY id DESC LIMIT $2",
                              status, limit)
    return await db.fetch("SELECT * FROM tasks ORDER BY id DESC LIMIT $1", limit)


# ---------- события ----------

@app.websocket("/events")
async def ws_events(ws: WebSocket):
    """Шина событий — только со скоупом events (Stage 6), проверка ДО подписки.

    Браузер не умеет Authorization на WS, поэтому токен едет субпротоколом
    `bossman.bearer.<token>` (заголовок, не URL). Отказ — закрытие 1008 до
    того, как открыта подписка: анонимный клиент не видит ни одного события.
    """
    try:
        _, chosen = await authenticate_websocket(ws, SCOPE_EVENTS)
    except errors.BossmanError:
        # 1008 = policy violation; причину не детализируем (не оракул для подбора)
        await ws.close(code=1008)
        return
    await ws.accept(subprotocol=chosen)
    q = events.subscribe()
    try:
        while True:
            msg = await q.get()
            await ws.send_text(msg)
    except WebSocketDisconnect:
        pass
    finally:
        events.unsubscribe(q)


# ---------- подтверждения ----------

class Decision(BaseModel):
    approve: bool
    by: str = "ui"


@app.get("/approvals", dependencies=[Depends(require_scope(SCOPE_APPROVE))])
async def list_approvals(status: str = "pending"):
    return await db.fetch("SELECT * FROM approvals WHERE status=$1 ORDER BY id", status)


@app.post("/approvals/{approval_id}", dependencies=[Depends(require_scope(SCOPE_APPROVE))])
async def decide_approval(approval_id: int, body: Decision):
    """Решение по подтверждению — только устройство Stage 6 со скоупом approve.

    Telegram-вебхук ниже — отдельный вход с собственной проверкой секрета;
    /remote/... — тот же Stage 6 с тем же скоупом. Общее у всех входов: ни один
    не пускает решать анонимно, и localhost НЕ считается аутентификацией.
    """
    row = await approvals_mod.decide(approval_id, body.approve, body.by)
    if not row:
        raise HTTPException(409, "уже решено или не существует")
    return row


@app.post("/telegram/webhook")
async def telegram_webhook(update: dict, request: Request):
    """Кнопки из Telegram — теперь opaque `b:<token>` callback'и, а не сырые
    approve:<id>/reject:<id>. Разбор и вся проверка (секрет заголовка, чат,
    TTL, single-use) — в notifications.telegram_transport; здесь только граница
    HTTP: секрет заголовка передаётся как есть, отказ = CallbackRejected → 401.
    approvals.decide() вызывается telegram_transport, не этим хендлером.
    """
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    try:
        return await telegram.handle_webhook(update, got)
    except CallbackRejected as exc:
        raise errors.AuthDenied(f"telegram callback denied: {exc}") from exc


# ---------- агенты ----------

@app.get("/agents", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def list_agents():
    out = []
    for spec in load_all().values():
        last = await db.fetchrow(
            "SELECT tool, created_at FROM tool_calls WHERE agent=$1 ORDER BY id DESC LIMIT 1",
            spec.name)
        spend = await db.fetchrow(
            """SELECT coalesce(sum(prompt_tokens+completion_tokens),0) AS tokens,
                      coalesce(sum((prompt_tokens+completion_tokens)) FILTER (WHERE is_cloud),0) AS cloud_tokens
               FROM model_calls WHERE agent=$1 AND created_at > now() - interval '30 days'""",
            spec.name)
        out.append({"name": spec.name, "title": spec.title, "model": spec.model,
                    "cloud_policy": spec.cloud_policy,
                    "tools": [g.name for g in spec.tools], "schedule": spec.schedule,
                    "last_action": last, "spend_30d": spend})
    return out


class AgentPatch(BaseModel):
    cloud_policy: str


@app.patch("/agents/{name}", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def patch_agent(name: str, body: AgentPatch):
    try:
        spec = set_cloud_policy(name, body.cloud_policy)
    except FileNotFoundError:
        raise HTTPException(404, f"нет агента {name}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    events.emit("agent.updated", name=name, cloud_policy=spec.cloud_policy)
    return {"name": spec.name, "cloud_policy": spec.cloud_policy}


# ---------- модели ----------

@app.get("/models", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def list_models():
    """Установленные (из /opt/bossman/models), загруженные сейчас (llama-swap /running),
    среднее заполнение окна и доля кэша по агентам (10.7)."""
    installed = []
    models_dir = Path("/models") if Path("/models").exists() else Path("/opt/bossman/models")
    if models_dir.exists():
        installed = sorted(p.name for p in models_dir.iterdir() if p.is_dir())
    running, swap_err = [], None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.llama_swap_url}/running")
            running = resp.json().get("running", resp.json()) if resp.status_code == 200 else []
    except Exception as exc:
        swap_err = str(exc)
    ctx_stats = await db.fetch(
        """SELECT agent, round(avg(window_fill)::numeric, 3) AS avg_fill,
                  round(avg(CASE WHEN prefix_cache_hit THEN 1 ELSE 0 END)::numeric, 3) AS cache_rate,
                  count(*) AS calls
           FROM model_calls WHERE created_at > now() - interval '7 days' GROUP BY agent""")
    prompt_cache = {
        "state": "UNSUPPORTED" if not settings.gateway_url else "DEGRADED",
        "miss_reason": "gateway disabled" if not settings.gateway_url else "gateway unavailable",
        "cache_hit_percent": 0.0,
        "cached_tokens": 0,
        "fresh_input_tokens": 0,
        "cache_write_tokens": 0,
        "saved_usd": 0.0,
        # No gateway snapshot -> no provider evidence -> savings are not claimable.
        "savings_basis": "none",
        "savings_events": 0,
        "actual_cost_usd": 0.0,
        "session_affinity": False,
        "provider": None,
        "model": None,
        "ttl": None,
        "prefix_stability_percent": None,
    }
    if settings.gateway_url:
        try:
            from .llm import gateway_metrics
            gateway_snapshot = await gateway_metrics()
            candidate = (gateway_snapshot or {}).get("prompt_cache")
            if isinstance(candidate, dict):
                prompt_cache = candidate
        except Exception:
            pass  # explicit DEGRADED above; no fake-green on unavailable Gateway
    return {"installed": installed, "running": running, "llama_swap_error": swap_err,
            "context_stats": ctx_stats, "prompt_cache": prompt_cache}


@app.post("/models/{alias}/load", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def load_model(alias: str):
    # llama-swap грузит модель при первом запросе к ней; health апстрима — самый дешёвый триггер
    async with httpx.AsyncClient(timeout=900) as client:
        resp = await client.get(f"{settings.llama_swap_url}/upstream/{alias}/health")
    return {"alias": alias, "status": resp.status_code}


@app.post("/models/{alias}/unload", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def unload_model(alias: str):
    # эндпоинт сверить с актуальным README llama-swap (в старых версиях /unload общий)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{settings.llama_swap_url}/unload", params={"model": alias})
    return {"alias": alias, "status": resp.status_code}


# ---------- расходы ----------

@app.get("/spend", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def spend():
    """Локальные токены (бесплатно, для статистики) и облачные по агентам за день/месяц."""
    return {
        "by_agent_day": await db.fetch(
            """SELECT agent, is_cloud, sum(prompt_tokens+completion_tokens) AS tokens
               FROM model_calls WHERE created_at > now() - interval '1 day'
               GROUP BY agent, is_cloud ORDER BY agent"""),
        "by_agent_month": await db.fetch(
            """SELECT agent, is_cloud, sum(prompt_tokens+completion_tokens) AS tokens
               FROM model_calls WHERE created_at > now() - interval '30 days'
               GROUP BY agent, is_cloud ORDER BY agent"""),
        "cloud_calls_month": await db.fetchval(
            "SELECT count(*) FROM cloud_calls WHERE created_at > now() - interval '30 days'"),
        "projects": await db.fetch(
            "SELECT slug, spent, budget_limit FROM projects ORDER BY updated_at DESC LIMIT 20"),
    }


# ---------- изменения (лента действий агентов; коммиты/PR — из ATLAS поверх) ----------

@app.get("/changes", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def changes(limit: int = 100):
    limit = max(1, min(limit, 500))  # Stage 13: см. list_tasks — потолок выкачки
    return await db.fetch(
        """SELECT agent, tool, args, status, approved_by, created_at
           FROM tool_calls ORDER BY id DESC LIMIT $1""", limit)


# ---------- проекты ----------

class ProjectIn(BaseModel):
    slug: str
    title: str
    brief: str
    budget_limit: float | None = None


async def _project(slug_or_id: str) -> dict:
    row = await db.fetchrow("SELECT * FROM projects WHERE slug=$1 OR id::text=$1", slug_or_id)
    if not row:
        raise HTTPException(404, f"нет проекта {slug_or_id}")
    return row


@app.post("/projects", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def create_project(body: ProjectIn):
    """brief → план и оценка. План уходит на утверждение до любых трат."""
    await db.execute(
        """INSERT INTO projects (slug, title, brief, budget_limit) VALUES ($1,$2,$3,$4)
           ON CONFLICT (slug) DO UPDATE SET brief=excluded.brief, updated_at=now()""",
        body.slug, body.title, body.brief, body.budget_limit)
    _spawn(_plan_and_register(body.slug, body.brief))
    events.emit("project.updated", slug=body.slug, status="draft")
    return {"slug": body.slug, "status": "planning"}


async def _plan_and_register(slug: str, brief: str) -> None:
    try:
        await plan_project(slug, brief)
        from .projects.plan import load_plan
        plan = load_plan(slug)
        row = await db.fetchrow("SELECT id FROM projects WHERE slug=$1", slug)
        for t in plan.tasks:
            await db.execute(
                """INSERT INTO project_tasks (project_id, stage, name, tool, params)
                   VALUES ($1,$2,$3,$4,$5)""", row["id"], t.stage, t.name, t.tool, t.params)
        events.emit("project.updated", slug=slug, status="awaiting_approval")
    except Exception as exc:
        await db.execute("UPDATE projects SET status='failed' WHERE slug=$1", slug)
        events.emit("project.updated", slug=slug, status="failed", reason=str(exc))


@app.get("/projects", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def list_projects():
    return await db.fetch("SELECT * FROM projects ORDER BY updated_at DESC")


@app.post("/projects/{slug}/approve", dependencies=[Depends(require_scope(SCOPE_APPROVE))])
async def approve_project(slug: str):
    row = await _project(slug)
    await db.execute("UPDATE projects SET status='approved', updated_at=now() WHERE id=$1", row["id"])
    events.emit("project.updated", slug=row["slug"], status="approved")
    return {"slug": row["slug"], "status": "approved"}


@app.post("/projects/{slug}/run", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def run_project_ep(slug: str):
    row = await _project(slug)
    if row["status"] not in ("approved", "paused", "preview_gate", "running", "failed"):
        raise HTTPException(409, f"проект в статусе {row['status']} — сначала approve")
    st = State(row["slug"])
    st.data["status"] = "running"
    st.save()
    _spawn(run_project(row["slug"]))
    return {"slug": row["slug"], "status": "running"}


@app.post("/projects/{slug}/pause", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def pause_project(slug: str):
    row = await _project(slug)
    st = State(row["slug"])
    st.data["status"] = "paused"
    st.save()
    await db.execute("UPDATE projects SET status='paused', updated_at=now() WHERE id=$1", row["id"])
    events.emit("project.updated", slug=row["slug"], status="paused")
    return {"slug": row["slug"], "status": "paused"}


@app.get("/projects/{slug}/state", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def project_state(slug: str):
    row = await _project(slug)
    st = State(row["slug"])
    tasks = await db.fetch(
        "SELECT stage, name, tool, status, attempts, cost FROM project_tasks WHERE project_id=$1 ORDER BY id",
        row["id"])
    return {"project": row, "state": st.data, "tasks": tasks}


@app.get("/projects/{slug}/journal", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def project_journal(slug: str, lines: int = 100):
    row = await _project(slug)
    return {"journal": journal_tail(row["slug"], lines)}


@app.post("/projects/{slug}/tasks/{tid}/retry", dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def retry_project_task(slug: str, tid: str):
    """«Пересобрать этап/задачу»: сбросить в state.json и запустить заново."""
    row = await _project(slug)
    st = State(row["slug"])
    if tid not in st.data["tasks"]:
        raise HTTPException(404, f"нет задачи {tid} в state.json")
    st.data["tasks"][tid]["status"] = "pending"
    st.save()
    _spawn(run_project(row["slug"]))
    return {"slug": row["slug"], "task": tid, "status": "pending"}


# ---------- UI ----------

UI_DIR = Path(ROOT) / "ui"
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")

    @app.get("/")
    async def index():
        return FileResponse(UI_DIR / "index.html")


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
