"""Control API (раздел 6): единственная точка входа для UI — HTTP + WS.

Все /api/* требуют аутентификации (кроме POST /api/login и /api/logout):
HttpOnly-cookie серверной сессии + CSRF-заголовок на изменяющих методах, либо
заголовок X-BCC-Token для CLI, пока включён legacy-режим. WS берёт cookie той же
сессии. Ошибки отдаются как {error: {message, hint?}} — голых 500 наружу нет.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db as dbm, discovery
from . import __version__

# Метка приложения для настольного лаунчера (GET /api/identity)
APP_IDENTITY = "bossman-command-center"
from .approvals import Approvals
from .features import load_features
from .auth import HEADER, TokenAuth
from .sessions import COOKIE_NAME, CSRF_HEADER, SAFE_METHODS, SessionStore, cookie_kwargs
from .config import Settings, settings as default_settings
from .db import (Database, agents as agents_t, fetch_one, run_events as run_events_t,
                 rows_dicts, task_runs as runs_t, tasks as tasks_t, utcnow)
from .engine import TaskEngine
from .events import EventBus
from .metrics import MetricsSampler
from .providers import ADAPTERS, ProviderError
from .registry import Registry
from .scheduler import Scheduler
from .secrets import Vault


class ApiError(Exception):
    """Ошибка с человекочитаемым текстом и подсказкой — ровно то, что увидит оператор."""

    def __init__(self, message: str, *, status: int = 400, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.hint = hint


# ---------- сборка сервисов ----------

class Services:
    """Все компоненты процесса: БД, реестр, движок, планировщик, метрики, шина."""

    def __init__(self, settings: Settings, *, start_workers: bool = True,
                 adapter_factory: Any = None, engine_options: dict | None = None,
                 announce_token: bool = True):
        settings.ensure_dirs()
        self.settings = settings
        self.vault = Vault(settings.data_dir)
        self.auth = TokenAuth(settings.data_dir, announce=announce_token)
        self.db = Database(settings.database_url)
        self.bus = EventBus(self.db)
        self.registry = Registry(self.db, self.vault, self.bus, adapter_factory=adapter_factory)
        self.engine = TaskEngine(self.db, self.bus, self.registry, **(engine_options or {}))
        self.engine.services = self         # V2.1: инструментам нужен доступ к сервисам
        self.scheduler = Scheduler(self.db, self.bus, self.engine)
        self.metrics = MetricsSampler(self.db, self.bus)
        self.approvals = Approvals(self.db, self.bus)
        self.sessions = SessionStore(self.db, ttl_hours=settings.session_ttl_hours)
        self._wire_v2_managers()             # skills / terminal / browser (пак)
        self.features = load_features()      # V2: модули bcc/features/* (контракты §8)
        self.start_workers = start_workers
        self._tasks: list[asyncio.Task] = []
        self.started_at = utcnow()

    def _wire_v2_managers(self) -> None:
        """Опциональные рантаймы пака. Отсутствие Playwright/MCP НЕ ломает старт
        (CLAUDE_START_HERE §browser optional): менеджеры создаются лениво, а импорт
        тяжёлых зависимостей происходит только при первом реальном использовании."""
        from pathlib import Path
        repo_root = self.settings.ui_dir.parent.parent   # <repo>
        self.skills = None
        self.terminal = None
        self.browser = None
        try:
            from .v2.skill_library import SkillLibrary, default_skill_roots
            self.skills = SkillLibrary(default_skill_roots(repo_root),
                                       repo_root / ".agents" / "skills")
        except Exception as exc:  # библиотека скиллов не критична для старта
            self._v2_warn = f"skills: {exc}"
        try:
            from .v2.terminal_control import TerminalManager
            self.terminal = TerminalManager()
        except Exception:
            pass
        try:
            from .v2.browser_control import BrowserManager
            self.browser = BrowserManager(self.settings.data_dir / "browser")
        except Exception:
            self.browser = None  # Playwright может быть не установлен — это норм

    async def start(self) -> None:
        await self.db.create_all()
        for feature in self.features:        # хуки engine и подписки — до старта worker'а
            if feature.setup:
                await feature.setup(self)
        await self.engine.recover()          # crash recovery при старте процесса
        if self.start_workers:
            # Именно += : фичи регистрируют свои подписки в _tasks во время
            # setup() выше (missions, benchlab, failure_to_case). Присваивание
            # затирало бы их ручки, и stop() не отменял бы подписку — в тестах
            # это не видно, потому что там start_workers=False.
            self._tasks += [
                asyncio.create_task(self.engine.worker_loop(), name="bcc-worker"),
                asyncio.create_task(self.scheduler.loop(), name="bcc-scheduler"),
                asyncio.create_task(self.metrics.loop(), name="bcc-metrics"),
                # V2.1: решение по approval возвращает ожидающий run в очередь
                asyncio.create_task(self.engine.approval_watcher(), name="bcc-approvals"),
            ]
            for feature in self.features:
                if feature.tick and feature.tick_seconds > 0:
                    self._tasks.append(asyncio.create_task(
                        self._feature_tick(feature), name=f"bcc-{feature.name}"))

    async def _feature_tick(self, feature: Any) -> None:
        """Фоновая петля фичи (Governor, Healing, истечение резервов…):
        ошибка одного тика логируется и не убивает петлю."""
        while True:
            try:
                await feature.tick(self)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.bus.emit("worker.error",
                                    message=f"tick {feature.name}: {type(exc).__name__}: {exc}")
            await asyncio.sleep(feature.tick_seconds)

    #: Сколько ждать отменённые фоновые задачи. Предел нужен потому, что не всё
    #: отменяемо: `asyncio.to_thread` прерывать нельзя — поток дописывает своё, и
    #: безусловный `await task` висит, пока он не закончит. Без предела медленный
    #: диск или загруженная машина превращают остановку в зависание (в CI это
    #: видно как teardown на 178 секунд и срабатывание pytest-timeout).
    STOP_TIMEOUT = 10.0

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        pending = [t for t in self._tasks if not t.done()]
        if pending:
            done, alive = await asyncio.wait(pending, timeout=self.STOP_TIMEOUT)
            for task in done:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    task.result()
            if alive:
                # Молчать нельзя: зависшая задача — это диагноз, а не мелочь.
                names = ", ".join(sorted(t.get_name() for t in alive))
                self.stop_stragglers = names
                with contextlib.suppress(Exception):
                    await self.bus.emit("worker.error",
                                        message=f"остановка: задачи не завершились за "
                                                f"{self.STOP_TIMEOUT:.0f} c: {names}")
        self._tasks = []
        # Слить фоновые run/heartbeat-задачи движка ДО dispose пула БД:
        # осиротевшая задача, дошедшая до `await s.commit()` на закрываемом пуле,
        # вешает закрытие event loop под Python 3.12. Порядок «drain → dispose» —
        # и есть фикс (см. docs/context/FABLE5_GENERAL_OPTIMIZATION_AUDIT.md).
        with contextlib.suppress(Exception):
            await self.engine.aclose()
        if getattr(self, "browser", None) is not None:
            with contextlib.suppress(Exception):
                await self.browser.close()
        await self.db.close()


def services(request: Request) -> Services:
    return request.app.state.svc


async def require_token(request: Request, x_bcc_token: str | None = Header(default=None)) -> None:
    """Аутентификация запроса (V2.1 фаза N).

    1) HttpOnly-cookie сессии — основной путь для браузера; изменяющие методы
       дополнительно требуют CSRF-заголовок из той же сессии;
    2) заголовок X-BCC-Token — для CLI/скриптов, пока включён legacy-режим.
       Он не подвержен CSRF (браузер не поставит произвольный заголовок
       кросс-доменно, CORS мы не включаем).
    """
    svc: Services = request.app.state.svc
    sess = await svc.sessions.get(request.cookies.get(COOKIE_NAME))
    if sess is not None:
        if request.method not in SAFE_METHODS:
            sent = request.headers.get(CSRF_HEADER)
            if not sent or not hmac.compare_digest(str(sent), str(sess["csrf"])):
                raise ApiError("не пройдена CSRF-проверка", status=403,
                               hint=f"передайте заголовок {CSRF_HEADER} из ответа /api/login")
        request.state.session_id = sess["id"]
        await svc.sessions.touch(sess["id"])
        return
    if svc.settings.legacy_token_auth and svc.auth.check(x_bcc_token):
        request.state.session_id = None
        return
    hint = ("войдите через /api/login — сессия придёт HttpOnly-cookie"
            if not svc.settings.legacy_token_auth
            else f"войдите через /api/login или передайте заголовок {HEADER}")
    raise ApiError("нужна аутентификация", status=401, hint=hint)


# ---------- модели запросов ----------

class LoginIn(BaseModel):
    token: str
    label: str = "ui"           # чем подписать сессию в списке (браузер/телефон)


class ProviderIn(BaseModel):
    name: str
    kind: str
    base_url: str = ""
    api_key: str | None = None


class ModelIn(BaseModel):
    provider_id: int
    name: str
    alias: str | None = None
    kind: str = "local"
    context_window: int = 8192
    caps: dict = Field(default_factory=dict)
    price_in: float = 0.0
    price_out: float = 0.0


class DiscoverIn(BaseModel):
    extra_urls: list[str] = Field(default_factory=list)


class ModelPatch(BaseModel):
    name: str | None = None
    alias: str | None = None
    kind: str | None = None
    context_window: int | None = None
    caps: dict | None = None
    price_in: float | None = None
    price_out: float | None = None


class AgentIn(BaseModel):
    name: str
    role: str = ""
    system_prompt: str = ""
    model_id: int | None = None
    fallback_model_id: int | None = None
    tools: list = Field(default_factory=list)
    max_steps: int = 4
    max_tokens: int = 2048
    budget_usd: float = 0.0
    permissions: dict = Field(default_factory=dict)
    enabled: bool = True


class AgentPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    system_prompt: str | None = None
    model_id: int | None = None
    fallback_model_id: int | None = None
    tools: list | None = None
    max_steps: int | None = None
    max_tokens: int | None = None
    budget_usd: float | None = None
    permissions: dict | None = None
    enabled: bool | None = None


class ScheduleIn(BaseModel):
    name: str
    kind: str                                  # once | interval | daily
    at_time: datetime | None = None
    interval_minutes: int | None = None
    daily_time: str | None = None
    next_run_at: datetime | None = None
    enabled: bool = True
    task_template: dict = Field(default_factory=dict)


class SchedulePatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    at_time: datetime | None = None
    interval_minutes: int | None = None
    daily_time: str | None = None
    next_run_at: datetime | None = None
    enabled: bool | None = None
    task_template: dict | None = None


class TaskIn(BaseModel):
    prompt: str
    title: str = ""
    agent_id: int | None = None
    run_now: bool = True
    priority: int = 5
    max_retries: int = 2
    schedule: ScheduleIn | None = None


class ApprovalIn(BaseModel):
    approve: bool
    by: str = "owner"


class ApprovalCreate(BaseModel):
    kind: str
    preview: str = ""
    task_id: int | None = None
    run_id: int | None = None


# ---------- приложение ----------

def create_app(settings: Settings | None = None, *, start_workers: bool = True,
               adapter_factory: Any = None, engine_options: dict | None = None,
               announce_token: bool = True) -> FastAPI:
    svc = Services(settings or default_settings, start_workers=start_workers,
                   adapter_factory=adapter_factory, engine_options=engine_options,
                   announce_token=announce_token)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await svc.start()
        try:
            yield
        finally:
            await svc.stop()

    app = FastAPI(title="BOSSMAN Command Center", version="0.1", lifespan=lifespan)
    app.state.svc = svc
    _install_error_handlers(app)
    _install_testing_period_log(app)
    app.include_router(_public_router())
    app.include_router(_api_router())
    for feature in svc.features:             # V2-фичи: под /api и токен-auth
        if feature.router is not None:
            app.include_router(feature.router, prefix="/api",
                               dependencies=[Depends(require_token)])
    _mount_ui(app, svc.settings)
    return app


def _install_testing_period_log(app: FastAPI) -> None:
    """Режим тестового периода: каждый HTTP-запрос попадает в журнал владельца.

    Middleware нельзя добавить из `setup()` фичи — он вешается до старта
    приложения, а setup зовётся уже в lifespan. Поэтому подключение здесь, но
    вся работа и все границы секрета живут в самой фиче. Тела запросов и
    заголовки авторизации не читаются вовсе: там бывают токены.
    """
    from .features import testing_period

    if not testing_period.enabled():
        return

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        started = time.monotonic()
        status = 0
        failure = ""
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception as exc:  # noqa: BLE001 — записываем и отдаём дальше как есть
            status, failure = 500, f"{type(exc).__name__}: {exc}"
            raise
        finally:
            log = getattr(getattr(app.state, "svc", None), "testing_log", None)
            if log is not None and not request.url.path.startswith("/api/testing/log"):
                payload = {"method": request.method, "path": request.url.path,
                           "status": status, "ms": round((time.monotonic() - started) * 1000, 1)}
                if failure:
                    payload["error"] = failure
                kind = "http.error" if (status >= 400 or failure) else "http.request"
                # Дожидаемся записи, а не бросаем задачу в фон: журнал должен
                # быть согласован с ответом, который владелец только что видел,
                # а брошенная задача при ошибке даёт висящее исключение.
                await log.write("server", kind, payload)


def _install_error_handlers(app: FastAPI) -> None:
    """Единый формат ошибок для UI: {error: {message, hint?}}."""

    @app.exception_handler(ApiError)
    async def _api_error(_r: Request, exc: ApiError):
        body: dict[str, Any] = {"message": exc.message}
        if exc.hint:
            body["hint"] = exc.hint
        return JSONResponse({"error": body}, status_code=exc.status)

    @app.exception_handler(ProviderError)
    async def _provider_error(_r: Request, exc: ProviderError):
        body = {"message": str(exc)}
        if exc.hint:
            body["hint"] = exc.hint
        return JSONResponse({"error": body}, status_code=502)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_r: Request, exc: StarletteHTTPException):
        detail = exc.detail
        body = detail if isinstance(detail, dict) else {"message": str(detail)}
        return JSONResponse({"error": body}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_r: Request, exc: RequestValidationError):
        first = (exc.errors() or [{}])[0]
        where = ".".join(str(p) for p in first.get("loc", [])[1:]) or "тело запроса"
        return JSONResponse({"error": {"message": f"неверный запрос: {where} — "
                                                  f"{first.get('msg', 'некорректное значение')}",
                                       "hint": "проверьте поля запроса"}}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_r: Request, exc: Exception):
        return JSONResponse({"error": {"message": f"внутренняя ошибка: {type(exc).__name__}",
                                       "hint": "подробности — в логе сервера"}}, status_code=500)


def _mount_ui(app: FastAPI, settings: Settings) -> None:
    """Статика UI (её делает отдельный агент) — монтируется последней, /api остаётся выше."""
    if settings.ui_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(settings.ui_dir), html=True), name="ui")


def _public_router() -> APIRouter:
    """Без аутентификации: только вход, выход и WS (WS проверяет cookie сам)."""
    router = APIRouter(prefix="/api")

    @router.get("/identity")
    async def identity(svc: Services = Depends(services)):
        """Кто слушает этот порт. Нужен настольному лаунчеру: прежде чем
        переиспользовать «уже запущенный сервер», он обязан убедиться, что это
        именно Command Center, а не чужое приложение. Секретов здесь нет —
        только имя приложения, версия и время старта."""
        return {"app": APP_IDENTITY, "version": __version__, "started_at": svc.started_at}

    @router.post("/login")
    async def login(body: LoginIn, request: Request, response: Response,
                    svc: Services = Depends(services)):
        """Токен обменивается на серверную сессию: браузеру уходит HttpOnly-cookie,
        а CSRF-токен — в теле ответа (его хранит JS и шлёт заголовком)."""
        if not svc.auth.check(body.token):
            raise ApiError("неверный токен", status=401,
                           hint="токен печатается в консоль при старте сервера")
        sess = await svc.sessions.create(label=body.label or "ui")
        response.set_cookie(COOKIE_NAME, sess["id"],
                            **cookie_kwargs(request.url.scheme,
                                            svc.settings.cookie_secure,
                                            svc.settings.session_ttl_hours))
        return {"ok": True, "csrf": sess["csrf"], "expires_at": sess["expires_at"],
                "csrf_header": CSRF_HEADER}

    @router.post("/logout")
    async def logout(request: Request, response: Response, svc: Services = Depends(services)):
        """Выход инвалидирует сессию на сервере, а не только в браузере."""
        sid = request.cookies.get(COOKIE_NAME)
        revoked = await svc.sessions.revoke(sid) if sid else False
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True, "revoked": revoked}

    @router.websocket("/events")
    async def events_ws(ws: WebSocket, token: str | None = Query(default=None)):
        svc: Services = ws.app.state.svc
        # cookie — основной путь: секрет не попадает ни в URL, ни в логи прокси
        sess = await svc.sessions.get(ws.cookies.get(COOKIE_NAME))
        if sess is None and not (svc.settings.legacy_token_auth and svc.auth.check(token)):
            await ws.close(code=4401)
            return
        await ws.accept()
        queue = svc.bus.subscribe()
        try:
            await ws.send_json({"kind": "hello", "ts": utcnow().isoformat()})
            while True:
                msg = await queue.get()
                await ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            svc.bus.unsubscribe(queue)

    return router


def _api_router() -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

    # ---------- система ----------

    @router.get("/system")
    async def system(svc: Services = Depends(services)):
        now = svc.metrics.read()
        history = await svc.metrics.history(15)
        async with svc.db.session() as s:
            res = await s.execute(sa.select(runs_t.c.status, sa.func.count())
                                  .group_by(runs_t.c.status))
            queue = {str(r[0]): int(r[1]) for r in res.fetchall()}
        return {"metrics": now, "history": history, "queue": queue,
                "health": await _health(svc), "started_at": svc.started_at}

    @router.get("/activity")
    async def activity(limit: int = 50, svc: Services = Depends(services)):
        return await svc.bus.recent(min(limit, 200))

    # ---------- провайдеры и модели ----------

    @router.get("/providers/kinds")
    async def provider_kinds():
        return list(ADAPTERS)

    @router.get("/providers")
    async def list_providers(svc: Services = Depends(services)):
        return await svc.registry.list_providers()

    @router.post("/providers")
    async def create_provider(body: ProviderIn, svc: Services = Depends(services)):
        if body.kind not in ADAPTERS:
            raise ApiError(f"неизвестный вид провайдера: {body.kind}",
                           hint=f"доступны: {', '.join(ADAPTERS)}")
        return await svc.registry.create_provider(body.name, body.kind, body.base_url, body.api_key)

    @router.delete("/providers/{provider_id}")
    async def delete_provider(provider_id: int, svc: Services = Depends(services)):
        if not await svc.registry.delete_provider(provider_id):
            raise ApiError("провайдер не найден", status=404)
        return {"ok": True}

    @router.get("/models")
    async def list_models(svc: Services = Depends(services)):
        return await svc.registry.list_models()

    @router.post("/models")
    async def create_model(body: ModelIn, svc: Services = Depends(services)):
        try:
            return await svc.registry.create_model(**body.model_dump())
        except LookupError as exc:
            raise ApiError(str(exc), status=404) from None
        except sa.exc.IntegrityError:
            raise ApiError(f"алиас «{body.alias or body.name}» уже занят",
                           hint="алиас модели должен быть уникальным") from None

    @router.patch("/models/{model_id}")
    async def patch_model(model_id: int, body: ModelPatch, svc: Services = Depends(services)):
        row = await svc.registry.update_model(model_id, **body.model_dump(exclude_none=True))
        if row is None:
            raise ApiError("модель не найдена", status=404)
        return row

    @router.delete("/models/{model_id}")
    async def delete_model(model_id: int, svc: Services = Depends(services)):
        if not await svc.registry.delete_model(model_id):
            raise ApiError("модель не найдена", status=404)
        return {"ok": True}

    @router.post("/models/{model_id}/check")
    async def check_model(model_id: int, svc: Services = Depends(services)):
        try:
            return await svc.registry.check_model(model_id)
        except LookupError as exc:
            raise ApiError(str(exc), status=404) from None

    @router.post("/models/{model_id}/test")
    async def test_model(model_id: int, svc: Services = Depends(services)):
        try:
            return await svc.registry.test_model(model_id)
        except LookupError as exc:
            raise ApiError(str(exc), status=404) from None

    @router.post("/models/discover")
    async def discover_models(body: DiscoverIn | None = None,
                              svc: Services = Depends(services)):
        """Обнаружение локальных моделей: опрос известных портов + скан диска на *.gguf.
        Только чтение — ничего не запускает и не скачивает."""
        return await discovery.discover(
            extra_urls=(body.extra_urls if body else None),
            known_providers=await svc.registry.list_providers())

    # ---------- агенты ----------

    @router.get("/agents")
    async def list_agents(svc: Services = Depends(services)):
        async with svc.db.session() as s:
            res = await s.execute(sa.select(agents_t).order_by(agents_t.c.id))
            return rows_dicts(res.fetchall())

    @router.post("/agents")
    async def create_agent(body: AgentIn, svc: Services = Depends(services)):
        values = body.model_dump()
        async with svc.db.session() as s:
            res = await s.execute(sa.insert(agents_t).values(created_at=utcnow(), **values))
            aid = int(res.inserted_primary_key[0])
            await s.commit()
            row = await fetch_one(s, agents_t, aid)
        await svc.bus.emit("agent.created", id=aid, name=body.name)
        return row

    @router.patch("/agents/{agent_id}")
    async def patch_agent(agent_id: int, body: AgentPatch, svc: Services = Depends(services)):
        values = body.model_dump(exclude_none=True)
        async with svc.db.session() as s:
            if values:
                await s.execute(sa.update(agents_t).where(agents_t.c.id == agent_id).values(**values))
                await s.commit()
            row = await fetch_one(s, agents_t, agent_id)
        if row is None:
            raise ApiError("агент не найден", status=404)
        return row

    @router.delete("/agents/{agent_id}")
    async def delete_agent(agent_id: int, svc: Services = Depends(services)):
        async with svc.db.session() as s:
            res = await s.execute(sa.delete(agents_t).where(agents_t.c.id == agent_id))
            await s.commit()
        if not res.rowcount:
            raise ApiError("агент не найден", status=404)
        return {"ok": True}

    # ---------- задачи ----------

    @router.get("/tasks")
    async def list_tasks(status: str | None = None, limit: int = 100,
                         svc: Services = Depends(services)):
        async with svc.db.session() as s:
            stmt = sa.select(tasks_t).order_by(tasks_t.c.id.desc()).limit(min(limit, 500))
            if status:
                stmt = stmt.where(tasks_t.c.status.in_(status.split(",")))
            res = await s.execute(stmt)
            rows = rows_dicts(res.fetchall())
            for task in rows:
                run = await s.execute(sa.select(runs_t).where(runs_t.c.task_id == task["id"])
                                      .order_by(runs_t.c.id.desc()).limit(1))
                task["last_run"] = _run_public(dbm.row_dict(run.first()))
        return rows

    @router.post("/tasks")
    async def create_task(body: TaskIn, svc: Services = Depends(services)):
        template = {"title": body.title, "prompt": body.prompt, "agent_id": body.agent_id,
                    "priority": body.priority, "max_retries": body.max_retries}
        async with svc.db.session() as s:
            res = await s.execute(sa.insert(tasks_t).values(
                title=body.title or body.prompt[:80], prompt=body.prompt, agent_id=body.agent_id,
                priority=body.priority, max_retries=body.max_retries, status="draft",
                created_at=utcnow(), updated_at=utcnow()))
            task_id = int(res.inserted_primary_key[0])
            await s.commit()
        await svc.bus.emit("task.created", task_id=task_id, title=body.title, agent_id=body.agent_id)

        schedule = None
        if body.schedule is not None:
            values = body.schedule.model_dump()
            values["task_template"] = values.get("task_template") or template
            schedule = await svc.scheduler.create(**values)
            async with svc.db.session() as s:
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
                    schedule_id=schedule["id"]))
                await s.commit()
        elif body.run_now:
            await svc.engine.enqueue(task_id)

        async with svc.db.session() as s:
            task = await fetch_one(s, tasks_t, task_id)
        return {"task": task, "schedule": schedule}

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: int, svc: Services = Depends(services)):
        async with svc.db.session() as s:
            task = await fetch_one(s, tasks_t, task_id)
            if task is None:
                raise ApiError("задача не найдена", status=404)
            res = await s.execute(sa.select(runs_t).where(runs_t.c.task_id == task_id)
                                  .order_by(runs_t.c.id))
            runs = [_run_public(r) for r in rows_dicts(res.fetchall())]
        done = [r for r in runs if r["result"]]
        return {"task": task, "runs": runs, "result": done[-1]["result"] if done else None,
                "error": runs[-1]["error"] if runs else None}

    @router.post("/tasks/{task_id}/{action}")
    async def task_action(task_id: int, action: str, svc: Services = Depends(services)):
        async with svc.db.session() as s:
            task = await fetch_one(s, tasks_t, task_id)
        if task is None:
            raise ApiError("задача не найдена", status=404)
        if action == "run":
            if await svc.engine.active_run(task_id):
                raise ApiError("задача уже в очереди или выполняется",
                               hint="сначала остановите её")
            run_id = await svc.engine.enqueue(task_id)
            return {"ok": True, "status": "queued", "run_id": run_id}
        if action == "stop":
            return await svc.engine.stop(task_id)
        if action == "pause":
            return await svc.engine.pause(task_id)
        if action == "resume":
            return await svc.engine.resume(task_id)
        if action == "retry":
            return await svc.engine.retry(task_id)
        raise ApiError(f"неизвестное действие: {action}", status=404,
                       hint="доступны: run, stop, pause, resume, retry")

    # ---------- run'ы ----------

    @router.get("/runs/{run_id}")
    async def get_run(run_id: int, svc: Services = Depends(services)):
        async with svc.db.session() as s:
            run = await fetch_one(s, runs_t, run_id)
        if run is None:
            raise ApiError("run не найден", status=404)
        return _run_public(run)

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: int, after: int = 0, limit: int = 200,
                         svc: Services = Depends(services)):
        async with svc.db.session() as s:
            res = await s.execute(sa.select(run_events_t).where(
                run_events_t.c.run_id == run_id,
                run_events_t.c.id > after).order_by(run_events_t.c.id).limit(min(limit, 1000)))
            return rows_dicts(res.fetchall())

    # ---------- расписания ----------

    @router.get("/schedules")
    async def list_schedules(svc: Services = Depends(services)):
        return await svc.scheduler.list_schedules()

    @router.post("/schedules")
    async def create_schedule(body: ScheduleIn, svc: Services = Depends(services)):
        if body.kind not in ("once", "interval", "daily"):
            raise ApiError(f"неизвестный вид расписания: {body.kind}",
                           hint="доступны: once, interval, daily")
        return await svc.scheduler.create(**body.model_dump())

    @router.patch("/schedules/{schedule_id}")
    async def patch_schedule(schedule_id: int, body: SchedulePatch,
                             svc: Services = Depends(services)):
        row = await svc.scheduler.update(schedule_id, **body.model_dump(exclude_none=True))
        if row is None:
            raise ApiError("расписание не найдено", status=404)
        return row

    @router.delete("/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: int, svc: Services = Depends(services)):
        if not await svc.scheduler.delete(schedule_id):
            raise ApiError("расписание не найдено", status=404)
        return {"ok": True}

    # ---------- подтверждения ----------

    @router.get("/approvals")
    async def list_approvals(status: str | None = "pending", svc: Services = Depends(services)):
        return await svc.approvals.list(status)

    @router.post("/approvals")
    async def create_approval(body: ApprovalCreate, svc: Services = Depends(services)):
        return await svc.approvals.create(body.kind, body.preview, task_id=body.task_id,
                                          run_id=body.run_id)

    @router.post("/approvals/{approval_id}")
    async def decide_approval(approval_id: int, body: ApprovalIn,
                              svc: Services = Depends(services)):
        row = await svc.approvals.decide(approval_id, body.approve, body.by)
        if row is None:
            raise ApiError("подтверждение не найдено", status=404)
        return row

    return router


def _run_public(run: dict | None) -> dict | None:
    """Run наружу: без сырого checkpoint (в нём переписка) — только его мета."""
    if run is None:
        return None
    out = dict(run)
    checkpoint = out.pop("checkpoint", None) or {}
    out["checkpoint"] = {"step": checkpoint.get("step", 0), "note": checkpoint.get("note", ""),
                         "messages": len(checkpoint.get("messages") or [])}
    return out


async def _health(svc: Services) -> dict:
    """Здоровье компонентов для экрана System."""
    health: dict[str, dict] = {}
    try:
        await svc.db.ping()
        health["db"] = {"status": "ok", "detail": svc.db.url.split("://")[0]}
    except Exception as exc:
        health["db"] = {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
    health["queue_worker"] = _loop_health(svc.engine.last_tick, 10.0, svc.start_workers)
    health["queue_worker"]["current_run_id"] = svc.engine.current_run_id
    health["scheduler"] = _loop_health(svc.scheduler.last_tick, svc.scheduler.tick_seconds * 2,
                                       svc.start_workers)
    health["metrics"] = _loop_health(svc.metrics.last_tick, svc.metrics.interval * 3,
                                     svc.start_workers)
    # P1 no-fake-green: подсистемы с внешними зависимостями не должны выглядеть
    # зелёными, когда они недоступны. Пустой health или unknown не превращается в ok.
    try:
        browser = svc.browser
        if browser is None:
            from .v2.browser_control import BrowserManager
            browser = svc.browser = BrowserManager(svc.settings.data_dir / "browser")
        health["browser"] = ({"status": "ok", "detail": "playwright доступен"}
                             if browser.available else
                             {"status": "offline", "detail": "playwright/chromium не установлен"})
    except Exception as exc:                                  # честное unknown, не ok
        health["browser"] = {"status": "unknown", "detail": f"{type(exc).__name__}"}
    try:
        async with svc.db.session() as s:
            rows = (await s.execute(sa.select(dbm.models.c.status))).fetchall()
        statuses = [str(r.status or "unknown").lower() for r in rows]
        if not statuses:
            health["models"] = {"status": "empty", "detail": "ни одной модели не настроено"}
        else:
            bad = [x for x in statuses if x in ("offline", "error")]
            health["models"] = (
                {"status": "degraded", "detail": f"{len(bad)} из {len(statuses)} моделей недоступны"}
                if bad else {"status": "ok", "detail": f"моделей: {len(statuses)}"})
    except Exception as exc:
        health["models"] = {"status": "unknown", "detail": f"{type(exc).__name__}"}
    return health


def _loop_health(last_tick: float, max_age: float, enabled: bool) -> dict:
    if not enabled:
        return {"status": "stopped", "detail": "фоновые циклы отключены"}
    if not last_tick:
        return {"status": "starting", "detail": "ещё не было тика"}
    age = time.monotonic() - last_tick
    if age > max_age:
        return {"status": "stale", "detail": f"нет тика {int(age)} с"}
    return {"status": "ok", "detail": f"тик {int(age)} с назад"}
