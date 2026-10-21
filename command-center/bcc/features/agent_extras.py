"""Контекст, сжатие, память и палитра команд мобильного агента - `/api/agent/*` (дополнение к agent_opencode).

Владелец 21.10: «Запрещено читать локальные переписки данные и фото». Поэтому НИ ОДИН ответ этого модуля не содержит текста
ходов, ответов, вложений и резюме: только счётчики, проценты, статусы и id. Контекст каждого чата остаётся внутри этого
чата; в общую память он попадает только по явному вызову владельца (`POST .../memory`) и только как резюме, а не как ходы.

  GET  /api/agent/palette?session_id=        палитра: команды, инструменты, навыки, агент сессии
  GET  /api/agent/sessions/{id}/context      датчик контекста: токены, окно, процент, уровень
  POST /api/agent/sessions/{id}/compact      /compact обычной задачей Bossman, в фоне (202)
  POST /api/agent/sessions/{id}/memory       резюме чата -> общая память «BOSSMAN Memory» (только по вызову владельца)
  GET  /api/agent/cloud-ask                  состояние инструмента cloud.ask: облачный каталог, у кого он выдан
  PUT  /api/agent/cloud-ask                  выдать/снять cloud.ask агенту (владелец; идемпотентно, не при старте)

Сжатие - не второй путь запуска задач: тот же preflight/создание/запуск, что у сообщения чата (chat_threads._internal), промпт
- `terminal_cli.chat.compact_prompt` без копии, итог пишется в тот же файл сессии с теми же полями, что у `Session.compacted`
(summary, compacted_at_turn, compact_tasks). Неудача, таймаут, STOP - файл сессии не меняется, состояние `failed` + причина.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..db import agents as agents_t, task_runs as runs_t
from ..permissions import agent_allowed, is_dangerous
from ..tools import REGISTRY as TOOL_REGISTRY
from . import Feature, tools_cloud
from .agent_opencode import (MODEL_TIERS, _orch, _resolve_agent_id_for_model, _session_cfg, _session_detail,
                             list_agent_tools)
from .chat_threads import (_catalog, _cli_limits, _compacted_at, _compose_prompt, _created_ts, _entry, _fail,
                           _internal, _iso, _live_turns, _model_of_agent, _passthrough, _read_meta,
                           _read_session_raw, _require_session, _sanitize, _session_path, _start, _state,
                           _task_views, _turns, _write_json_atomic)
from .coding_tasks import WORKERS

router = APIRouter()

PALETTE_TOOLS_MAX = 400
PALETTE_SKILLS_MAX = 60
PALETTE_TRIGGERS_MAX = 8
TOOL_DESCRIPTION_CHARS = 200
WARN_PCT = 70.0
CRITICAL_PCT = 90.0
ANCHOR_SCAN_TURNS = 12
SINCE_ANCHOR_TURNS = 20
COMPACT_TIMEOUT_S = 900.0              # как max_seconds у /compact в `bossman chat`
COMPACT_POLL_S = 0.4
MEMORY_NOTE_CHARS = 1000
#: Как ответ, который Bossman вернул задаче: нет таких данных - модель их не получает.
_TERMINAL = frozenset({"completed", "failed", "stopped", "cancelled", "blocked"})


# ----------------------------------------------------------------------------------------------------------- оценка

def estimate_tokens(text: str) -> int:
    """Оценка токенов с учётом письменности: ASCII ~4 знака/токен, кириллица и прочий не-ASCII ~2.2.

    chars/4 для русского занижает вдвое, и датчик контекста врал бы «зелёным» до самого переполнения."""
    if not text:
        return 0
    ascii_chars = len(text.encode("ascii", "ignore"))
    return math.ceil(ascii_chars / 4 + (len(text) - ascii_chars) / 2.2)


def level_of(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    return "critical" if pct >= CRITICAL_PCT else "warn" if pct >= WARN_PCT else "ok"


# ------------------------------------------------------------------------------------------------------------ состояние

class _Extras:
    """Состояние фичи на Services: идущие сжатия (в памяти) и замок файла состояния."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.running: dict[str, dict[str, Any]] = {}


def _x(svc: Any) -> _Extras:
    state = getattr(svc, "_agent_extras", None)
    if state is None:
        state = svc._agent_extras = _Extras()
    return state


def _compact_state_path(svc: Any) -> Path:
    return Path(svc.settings.data_dir) / "terminal" / "agent-compact.json"


def _read_compact_states(svc: Any) -> dict[str, dict]:
    try:
        raw = json.loads(_compact_state_path(svc).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sessions = raw.get("sessions") if isinstance(raw, dict) else None
    return {k: v for k, v in sessions.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(sessions, dict) else {}


def _write_compact_state(svc: Any, session_id: str, record: dict) -> None:
    states = _read_compact_states(svc)
    states[session_id] = record
    _write_json_atomic(_compact_state_path(svc), {"version": 1, "sessions": states})


def _compact_view(svc: Any, session_id: str) -> dict:
    live = _x(svc).running.get(session_id)
    if live is not None:
        return {"state": "running", "task_id": live.get("task_id"), "finished_at": None, "error": None}
    rec = _read_compact_states(svc).get(session_id) or {}
    if rec.get("state") in ("done", "failed"):
        return {"state": rec["state"], "task_id": rec.get("task_id"), "finished_at": rec.get("finished_at"),
                "error": rec.get("error")}
    return {"state": "idle", "task_id": None, "finished_at": None, "error": None}


# --------------------------------------------------------------------------------------------------- окно модели

def _parse_window(value: Any) -> int | None:
    """'128K' / '1M' / 131072 -> число токенов (WORKERS хранит окно строкой)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value > 0 else None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KkMm]?)\s*", str(value or ""))
    if not match:
        return None
    scale = {"": 1, "k": 1024, "m": 1024 * 1024}[match.group(2).lower()]
    return int(float(match.group(1)) * scale) or None


async def _context_window(svc: Any, cfg: dict) -> tuple[int | None, str | None]:
    """Окно выбранной модели сессии: исполнитель -> ярус роутера -> реестр моделей. Нет данных - (None, None)."""
    worker = cfg.get("worker")
    if worker and worker in WORKERS:
        window = _parse_window(WORKERS[worker].get("context_window"))
        if window:
            return window, "worker"
    clean = str(cfg.get("model") or "").removeprefix("local:").strip()
    for tier in MODEL_TIERS:
        if clean and clean in (tier["model"], tier["id"]):
            return int(tier["context_window"]), "router_tier"
    agent_id = await _resolve_agent_id_for_model(svc, cfg.get("agent_id"), clean)
    view = await _model_of_agent(svc, agent_id) if agent_id is not None else None
    window = (view or {}).get("context_window")
    return (int(window), "model_registry") if isinstance(window, int) and window > 0 else (None, None)


# ---------------------------------------------------------------------------------------------------- датчик контекста

async def _usage_anchor(svc: Any, live: list[tuple[int, dict]]) -> tuple[int, int] | None:
    """(индекс хода, токены in+out) последнего ЗАВЕРШЁННОГО прогона среди живых ходов: данные провайдера, не оценка."""
    recent = live[-ANCHOR_SCAN_TURNS:]
    ids = sorted({int(t["task_id"]) for _, t in recent})
    if not ids:
        return None
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(runs_t.c.task_id, runs_t.c.tokens_in, runs_t.c.tokens_out)
                                .where(runs_t.c.task_id.in_(ids), runs_t.c.status == "completed",
                                       runs_t.c.tokens_in > 0)
                                .order_by(runs_t.c.id.desc()))).fetchall()
    latest: dict[int, int] = {}
    for task_id, tin, tout in rows:
        latest.setdefault(int(task_id), int(tin or 0) + int(tout or 0))
    for idx, turn in reversed(recent):
        if int(turn["task_id"]) in latest:
            return idx, latest[int(turn["task_id"])]
    return None


async def context_view(svc: Any, session_id: str, raw: dict) -> dict:
    """Датчик контекста сессии. Только числа и статусы: текст ходов сюда не попадает."""
    cfg = _session_cfg(svc, session_id)
    live = _live_turns(raw)
    summary = str(raw.get("summary") or "")
    turns_n, chars, _max_prompt = _cli_limits()
    anchor = await _usage_anchor(svc, live)
    if anchor is not None:
        anchor_idx, anchored = anchor
        since = [(i, t) for i, t in live if i > anchor_idx][-SINCE_ANCHOR_TURNS:]
        views = await _task_views(svc, [t["task_id"] for _, t in since])
        extra = 0
        for _, turn in since:
            view = views.get(int(turn["task_id"])) or {}
            extra += (estimate_tokens(_sanitize(turn.get("text"))[:chars])
                      + estimate_tokens(_sanitize(view.get("result") or view.get("error") or "")[:chars]))
        used, source = anchored + extra, "provider_usage"
    else:
        try:
            used = estimate_tokens(await _compose_prompt(svc, raw, "", []))
        except Exception:  # noqa: BLE001 - датчик не должен падать из-за отказа сборки промпта
            used = estimate_tokens(summary)
        source = "estimate"
    window, window_source = await _context_window(svc, cfg)
    pct = round(100.0 * used / window, 1) if window else None
    return {
        "session_id": session_id, "turns_total": len(_turns(raw)), "turns_in_context": min(len(live), max(0, turns_n)),
        "compacted": bool(summary), "compacted_at_turn": _compacted_at(raw), "summary_chars": len(summary),
        "used_tokens": used, "context_window": window, "used_pct": pct,
        "left_tokens": max(0, window - used) if window else None,
        "left_pct": round(max(0.0, 100.0 - pct), 1) if pct is not None else None,
        "level": level_of(100.0 * used / window if window else None), "source": source,
        "compact": _compact_view(svc, session_id), "context_window_source": window_source,
    }


@router.get("/agent/sessions/{session_id}/context")
async def get_session_context(session_id: str, request: Request):
    svc = request.app.state.svc
    return await context_view(svc, session_id, _require_session(svc, session_id)["raw"])


# --------------------------------------------------------------------------------------------------------- /compact

class CompactIn(BaseModel):
    instruction: str | None = Field(default=None, max_length=500)


class _ReplayRequest:
    """Учётные данные владельца из его запроса для внутренних вызовов фоновой задачи (как `chat_threads._auth_headers`)."""

    def __init__(self, request: Request) -> None:
        self.app = request.app
        self.headers = dict(request.headers)


class _ViewClient:
    """Минимальный `Client` для `compact_prompt`: ответы задач берутся из базы, как их отдаёт GET /api/tasks/{id}."""

    def __init__(self, views: dict[int, dict]) -> None:
        self.views = views

    def get(self, path: str) -> dict:
        from ..terminal_cli.api_client import BossmanError
        match = re.fullmatch(r"/api/tasks/(\d+)", path)
        view = self.views.get(int(match.group(1))) if match else None
        if view is None:
            raise BossmanError("задача не читается", kind="not_found")
        return {"result": view.get("result"), "error": view.get("error")}


class _SnapshotSession:
    """Срез сессии для `compact_prompt`: резюме и ходы после точки сжатия (то же, что `Session.live_turns`)."""

    def __init__(self, summary: str, live: list[dict]) -> None:
        self.summary = summary
        self._live = live

    def live_turns(self) -> list[dict]:
        return self._live


def _apply_compaction_sync(path: Path, task_id: int, summary: str, snapshot: dict) -> str | None:
    """Записать итог в файл сессии как `Session.compacted`; всё остальное в файле сохраняется. None - записано."""
    raw = _read_session_raw(path)
    if raw is None:
        return "файл чата не читается"
    turns = raw.get("turns") if isinstance(raw.get("turns"), list) else []
    sha = hashlib.sha256(str(raw.get("summary") or "").encode("utf-8")).hexdigest()
    if _compacted_at(raw) != snapshot["at"] or sha != snapshot["summary_sha256"] or len(turns) < snapshot["turns_len"]:
        return "беседа изменилась во время сжатия (другое сжатие или правка) - резюме не применено"
    raw["summary"] = summary
    raw["compacted_at_turn"] = snapshot["turns_len"]            # резюме покрывает ровно те ходы, что были в промпте
    raw["compact_tasks"] = [t for t in raw.get("compact_tasks") or [] if isinstance(t, int)] + [task_id]
    _write_json_atomic(path, raw)
    return None


async def _finish_compaction(svc: Any, replay: _ReplayRequest, session_id: str, task_id: int, snapshot: dict) -> None:
    """Дождаться задачи сжатия и применить итог. Любая неудача оставляет файл сессии нетронутым."""
    error: str | None = "не завершилось"
    stop_task = False
    try:
        deadline = time.monotonic() + COMPACT_TIMEOUT_S
        while True:
            why = tools_cloud.stop_reason(svc)
            if why:
                error, stop_task = "STOP владельца: сжатие остановлено", True
                break
            view = (await _task_views(svc, [task_id])).get(task_id) or {}
            status = str(view.get("status") or "")
            if status == "completed":
                summary = _sanitize(view.get("result") or "").strip()[:2 * snapshot["summary_chars"]]
                if not summary:
                    error = "модель вернула пустое резюме"
                    break
                async with _state(svc).lock:
                    error = await asyncio.to_thread(_apply_compaction_sync, _session_path(svc, session_id), task_id,
                                                    summary, snapshot)
                    _state(svc).sessions.pop(session_id, None)
                break
            if status in _TERMINAL:
                error = f"задача сжатия завершилась со статусом {status}"
                break
            if status in ("waiting_approval", "paused"):
                error, stop_task = "сжатию потребовалось подтверждение владельца - отменено", True
                break
            if time.monotonic() > deadline:
                error, stop_task = f"таймаут {int(COMPACT_TIMEOUT_S)} с", True
                break
            await asyncio.sleep(COMPACT_POLL_S)
    except asyncio.CancelledError:
        error, stop_task = "сжатие прервано остановкой Bossman", True
        raise
    except Exception as exc:  # noqa: BLE001 - причина без текста исключения: он может нести содержимое беседы
        error = f"ошибка сжатия: {type(exc).__name__}"
    finally:
        if stop_task:
            try:
                await asyncio.shield(_internal(replay, "POST", f"/api/tasks/{task_id}/stop"))
            except BaseException:  # noqa: BLE001 - остановка задачи best-effort
                pass
        record = {"state": "failed" if error else "done", "task_id": task_id,
                  "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "error": error}
        try:
            async with _x(svc).lock:
                await asyncio.to_thread(_write_compact_state, svc, session_id, record)
        except BaseException:  # noqa: BLE001
            pass
        _x(svc).running.pop(session_id, None)
        try:
            _orch(svc).runtime(session_id).emit("session.updated", compact_state=record["state"],
                                                compact_task_id=task_id, compact_error=error)
        except Exception:  # noqa: BLE001
            pass


@router.post("/agent/sessions/{session_id}/compact", status_code=202)
async def compact_session(session_id: str, request: Request, body: CompactIn | None = None):
    """/compact обычной задачей Bossman для агента этой сессии; итог пишется в файл сессии в фоне."""
    from ..terminal_cli.chat import compact_prompt
    svc = request.app.state.svc
    raw = _require_session(svc, session_id)["raw"]
    live = _live_turns(raw)
    if not live:
        raise _fail(409, "сжимать нечего: после последнего /compact новых ходов нет", code="AGENT_NOTHING_TO_COMPACT")
    extras = _x(svc)
    if session_id in extras.running:
        raise _fail(409, "сжатие этого чата уже идёт", code="AGENT_COMPACT_RUNNING",
                    hint="состояние: GET /api/agent/sessions/{id}/context -> compact")
    why = tools_cloud.stop_reason(svc)
    if why:
        raise _fail(409, f"действует STOP владельца: {why}", code="AGENT_STOP_ACTIVE")

    cfg = _session_cfg(svc, session_id)
    agent_id = await _resolve_agent_id_for_model(svc, cfg["agent_id"], cfg["model"])
    if agent_id is None:
        raise _fail(409, "для сессии нет исполнителя с моделью", code="AGENT_COMPACT_NO_AGENT")
    model = await _model_of_agent(svc, agent_id) or {}
    if model.get("locality") == "cloud":
        # Сжатие идёт на собственном агенте сессии и не уходит в облако молча.
        if not cfg["allow_cloud"]:
            raise _fail(409, "агент сессии работает на облачной модели, а облако для этой сессии выключено",
                        code="AGENT_COMPACT_CLOUD_BLOCKED", hint="включите allow_cloud сессии или выберите локальную модель")
        if model.get("free") is not True and (await _session_detail(svc, session_id))["budget_remaining_usd"] <= 0:
            raise _fail(402, "Лимит бюджета сессии исчерпан - платные облачные модели заблокированы",
                        code="BUDGET_EXCEEDED")

    ids = [int(t["task_id"]) for t in live]
    client = _ViewClient(await _task_views(svc, ids))
    snapshot = {"at": _compacted_at(raw), "turns_len": len(raw.get("turns") or []), "summary_chars": _summary_limit(),
                "summary_sha256": hashlib.sha256(str(raw.get("summary") or "").encode("utf-8")).hexdigest()}
    prompt = compact_prompt(client, _SnapshotSession(str(raw.get("summary") or ""), [t for _, t in live]),  # type: ignore[arg-type]
                            (body.instruction if body else None) or "")

    # Тот же путь допуска, что у сообщения чата: preflight -> черновик -> запуск (свои права, бюджеты и STOP).
    code, pre = await _internal(request, "POST", "/api/tasks/preflight", {"prompt": prompt, "agent_id": agent_id})
    if code >= 400 or not isinstance(pre, dict):
        raise _passthrough(code, pre)
    if not pre.get("ok") or not isinstance(pre.get("agent"), dict):
        raise _fail(409, pre.get("reason") or "исполнитель недоступен",
                    code=pre.get("code") or "BLOCKED_CAPABILITY_UNAVAILABLE", hint=pre.get("hint"))
    code, created = await _internal(request, "POST", "/api/tasks", {
        "prompt": prompt, "title": f"/compact: резюме беседы {session_id}"[:80], "agent_id": pre["agent"]["id"],
        "run_now": False, "client_request_id": f"cmp-{time.strftime('%Y%m%d%H%M%S')}-{hashlib.sha256(session_id.encode()).hexdigest()[:8]}"})
    if code >= 400 or not isinstance(created, dict) or not isinstance(created.get("task"), dict):
        raise _passthrough(code, created)
    task_id = int(created["task"]["id"])
    _run_id, admission = await _start(request, task_id)
    if not admission.get("ok"):
        with _suppress():
            await _internal(request, "POST", f"/api/tasks/{task_id}/stop")
        raise _fail(409, admission.get("reason") or "задача сжатия не принята", code=admission.get("code") or "AGENT_COMPACT_REFUSED")

    if session_id in extras.running:                         # второй запрос успел раньше, пока шёл допуск
        with _suppress():
            await _internal(request, "POST", f"/api/tasks/{task_id}/stop")
        raise _fail(409, "сжатие этого чата уже идёт", code="AGENT_COMPACT_RUNNING")
    extras.running[session_id] = {"task_id": task_id, "started_at": time.time()}
    job = asyncio.create_task(_finish_compaction(svc, _ReplayRequest(request), session_id, task_id, snapshot),
                              name=f"bcc-agent-compact-{session_id}")
    if hasattr(svc, "_tasks"):
        svc._tasks.append(job)
    return {"session_id": session_id, "task_id": task_id, "status": "running"}


def _summary_limit() -> int:
    from ..terminal_cli.chat import COMPACT_SUMMARY_CHARS
    return int(COMPACT_SUMMARY_CHARS)


class _suppress:
    """contextlib.suppress(Exception), короче и без импорта в каждом месте."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, Exception)


# ---------------------------------------------------------------------------------------------------------- /memory

class MemoryIn(BaseModel):
    note: str | None = Field(default=None, max_length=MEMORY_NOTE_CHARS)


@router.post("/agent/sessions/{session_id}/memory")
async def save_summary_to_memory(session_id: str, request: Request, body: MemoryIn | None = None):
    """Запрос памяти: РЕЗЮМЕ чата - в общую память BOSSMAN. Вызывает только владелец; сырые ходы и вложения не пишутся."""
    from ..hybrid.context_filter import SecretMaterialRefused
    from ..oss.qdrant import QdrantUnavailable
    from . import tools_memory
    svc = request.app.state.svc
    data = _require_session(svc, session_id)
    raw = data["raw"]
    summary = str(raw.get("summary") or "").strip()
    if not summary:
        raise _fail(409, "в чате нет резюме для общей памяти", code="AGENT_NO_SUMMARY",
                    hint="сначала выполните /compact: в память идёт только резюме беседы")
    sha = hashlib.sha256(str(raw.get("summary") or "").encode("utf-8")).hexdigest()
    try:
        service = await tools_memory.get_service(svc)
    except (tools_memory.MemoryNotConfigured, FileNotFoundError, QdrantUnavailable) as exc:
        raise _fail(503, str(exc)[:300], code="AGENT_MEMORY_UNAVAILABLE", hint="настройте vault: POST /api/memory/config") from None

    cfg = _session_cfg(svc, session_id)
    entry = _entry(_read_meta(svc), session_id)
    agent_name = ""
    agent_id = await _resolve_agent_id_for_model(svc, cfg["agent_id"], cfg["model"])
    if agent_id is not None:
        agents, _models, _providers = await _catalog(svc)
        agent_name = next((str(a.get("name") or "") for a in agents if int(a["id"]) == int(agent_id)), "")
    session_date = (_iso(_created_ts(session_id, entry, data["mtime"])) or "")[:10]
    note = " ".join((body.note if body else None or "").split()) if body and body.note else ""
    content = summary + (f"\n\n## Заметка владельца\n\n{note}" if note else "")
    rel_name = f"chat-summary-{session_id}-{sha[:12]}.md"            # один файл на (чат, резюме): повтор идемпотентен
    title = f"Резюме чата: {entry['title']}" if entry["title"] else f"Резюме чата {session_id}"
    saved = {"session_id": session_id, "summary_sha256": sha, "saved": True, "already_saved": False, "indexed": True}
    try:
        path = await service.remember(
            title=title, content=content, kind="session", project=entry["project"] or "",
            tags=["chat-summary"], source_run_id=f"agent-session:{session_id}", filename=rel_name,
            extra_frontmatter={"session_id": session_id, "session_date": session_date, "agent": agent_name,
                               "model": str(cfg["model"] or ""), "summary_sha256": sha,
                               "compacted_at_turn": str(_compacted_at(raw))})
    except FileExistsError:
        path = (service.vault.write_root / rel_name).resolve()
        saved["already_saved"] = True
    except QdrantUnavailable:
        path = (service.vault.write_root / rel_name).resolve()
        saved["indexed"] = False                                       # заметка записана, индекс позже
    except SecretMaterialRefused:
        raise _fail(422, "память не хранит секреты: резюме состоит только из секретного материала",
                    code="AGENT_MEMORY_SECRET_REFUSED") from None
    except PermissionError:
        raise _fail(403, "запись в память вне папки BOSSMAN Memory запрещена", code="AGENT_MEMORY_WRITE_DENIED") from None
    except ValueError as exc:
        raise _fail(422, str(exc)[:200], code="AGENT_MEMORY_INVALID") from None
    try:
        saved["path"] = path.relative_to(service.vault.root).as_posix()
    except ValueError:
        saved["path"] = rel_name
    return saved


# ---------------------------------------------------------------------------------------------------------- палитра

def _commands() -> list[dict]:
    """Команды палитры. Подписи существующих команд CLI берутся из `terminal_cli.slash.COMMANDS`, без копий."""
    from ..terminal_cli.slash import COMMANDS as CLI
    sid = "/api/agent/sessions/{id}"

    def cmd(name: str, kind: str, endpoint: str | None, usage: str | None = None, help_: str | None = None) -> dict:
        cli_usage, cli_help = CLI.get(name, ("/" + name, ""))
        return {"name": name, "usage": usage or cli_usage, "help": help_ or cli_help, "kind": kind, "endpoint": endpoint}

    msg = f"POST {sid}/messages"
    return [
        cmd("code", "message", msg, "/code <задача>", "режим CODE: задача кодеру в изолированной копии (OpenCode)"),
        cmd("pc", "message", msg, "/pc <задача>", "режим COMPUTER: действия на ПК под одобрениями"),
        cmd("auto", "message", msg, "/auto <задача>", "режим AUTO: агент действует сам, одобрения принимаются автоматически"),
        cmd("improve", "message", msg, "/improve [что улучшить]", "цикл самоулучшения: доводится до одобрения владельца"),
        cmd("context", "action", f"GET {sid}/context"),
        cmd("compact", "action", f"POST {sid}/compact", "/compact [инструкция]"),
        cmd("memory", "action", f"POST {sid}/memory", "/memory [заметка]",
            "сохранить РЕЗЮМЕ этого чата в общую память (только по вашему вызову; сначала /compact)"),
        cmd("tools", "action", "GET /api/agent/tools", help_="инструменты агента и их политика"),
        cmd("skills", "action", "GET /api/skill-catalog"),
        cmd("models", "action", "GET /api/agent/models"),
        cmd("status", "action", f"GET {sid}"),
        cmd("stop", "action", f"POST {sid}/stop", "/stop", "остановить задачи этой сессии"),
        cmd("diff", "action", "GET /api/coding-tasks/{id}"),
        cmd("undo", "action", None, "/undo", "отменить последнюю применённую правку (серверного вызова пока нет)"),
        cmd("cloud", "action", "GET /api/agent/cloud-ask", "/cloud",
            "облачный совет: каталог моделей и состояние инструмента cloud.ask (каждый вызов - с вашим одобрением)"),
        cmd("new", "client", None, "/new", "новый чат"),
        cmd("clear", "client", None),
        cmd("help", "client", None),
    ]


async def _agent_block(svc: Any, session_id: str) -> tuple[dict | None, dict | None]:
    """(блок агента для палитры, сам агент для проверки прав). Права и системный промпт наружу не идут."""
    cfg = _session_cfg(svc, session_id)
    agent_id = await _resolve_agent_id_for_model(svc, cfg["agent_id"], cfg["model"])
    if agent_id is None:
        return None, None
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(agents_t.c.id, agents_t.c.name, agents_t.c.role, agents_t.c.enabled,
                                         agents_t.c.tools, agents_t.c.permissions)
                               .where(agents_t.c.id == agent_id))).first()
    if row is None:
        return None, None
    agent = dict(row._mapping)
    tools = agent.get("tools")
    names = ([str(t) if not isinstance(t, dict) else str(t.get("name") or "") for t in tools if t]
             if isinstance(tools, list) else [k for k, v in tools.items() if v] if isinstance(tools, dict) else [])
    perms = agent.get("permissions")
    model = await _model_of_agent(svc, agent_id) or {}
    return ({"id": agent["id"], "name": agent["name"], "role": agent.get("role") or "", "enabled": bool(agent["enabled"]),
             "mode": cfg["mode"], "model": cfg["model"], "worker": cfg["worker"], "allow_cloud": cfg["allow_cloud"],
             "locality": model.get("locality"), "tools_count": len(names),
             "cloud_ask": any(spec.name == tools_cloud.TOOL_NAME for spec in TOOL_REGISTRY.resolve(names)),
             "cloud_allowed": bool(perms.get("cloud_allowed")) if isinstance(perms, dict) else False},
            {"permissions": perms if perms is not None else {}})


async def _palette_skills(svc: Any) -> tuple[list[dict], int, str | None]:
    """Выбираемые навыки каталога: id, название, статус, триггеры. Текст навыка наружу не отдаётся никогда."""
    from ..v2 import skill_catalog as catalog_mod
    from . import skills as skills_feature
    try:
        revoked = await skills_feature.catalog_revocations(svc)
    except Exception:  # noqa: BLE001 - нечитаемый список отзывов: навыков нет (fail closed)
        return [], 0, "skills_unavailable"
    entries = await asyncio.to_thread(lambda: skills_feature._catalog(svc).entries(revoked))
    pick = sorted((e for e in entries if e.status in catalog_mod.SELECTABLE), key=lambda e: e.id)
    items = [{"id": e.id, "title": e.name, "status": e.status,
              "triggers": [str(t)[:40] for t in e.triggers[:PALETTE_TRIGGERS_MAX]]} for e in pick[:PALETTE_SKILLS_MAX]]
    return items, len(pick), None


@router.get("/agent/palette")
async def get_palette(request: Request, session_id: str | None = Query(default=None, max_length=64)):
    """Палитра для слэш-меню телефона. Размеры ограничены, секретов и содержимого чатов нет."""
    from ..permissions import ALL_PERMISSIONS  # noqa: F401  (проверка, что модуль прав доступен)
    svc = request.app.state.svc
    agent: dict | None = None
    agent_row: dict = {}
    if session_id:
        _require_session(svc, session_id)
        agent, row = await _agent_block(svc, session_id)
        agent_row = row or {}
    catalog = await list_agent_tools(request)
    tools = []
    for item in catalog["tools"][:PALETTE_TOOLS_MAX]:
        perms = item.get("required_permissions") or []
        gated = any(is_dangerous(p) and not agent_allowed(agent_row, p) for p in perms)
        tools.append({"name": item["name"], "group": item["group"],
                      "description": str(item["description"] or "")[:TOOL_DESCRIPTION_CHARS], "risk": item["risk_level"],
                      "needs_approval": item["default_effect"] == "ask" or gated, "effect": item["default_effect"]})
    skills, skills_total, warning = await _palette_skills(svc)
    return {"commands": _commands(), "tools": tools, "skills": skills, "agent": agent,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "counts": {"tools_total": catalog["total"], "skills_total": skills_total},
            "warnings": [warning] if warning else []}


# ------------------------------------------------------------------------------------------------------- cloud.ask

class CloudAskIn(BaseModel):
    enabled: bool
    agent_id: int | None = Field(default=None, ge=1)


@router.get("/agent/cloud-ask")
async def cloud_ask_status(request: Request, session_id: str | None = Query(default=None, max_length=64)):
    """Состояние `cloud.ask`: облачный каталог (без ключей), у каких агентов выдан, бюджет сессии. Ничего не вызывает."""
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(agents_t.c.id, agents_t.c.name, agents_t.c.enabled, agents_t.c.tools)
                                .order_by(agents_t.c.id))).fetchall()
    holders = []
    for r in rows:
        tools = r._mapping["tools"]
        names = ([str(t) for t in tools if isinstance(t, str)] if isinstance(tools, list)
                 else [k for k, v in tools.items() if v] if isinstance(tools, dict) else [])
        if any(s_.name == tools_cloud.TOOL_NAME for s_ in TOOL_REGISTRY.resolve(names)):
            holders.append({"id": r._mapping["id"], "name": r._mapping["name"], "enabled": bool(r._mapping["enabled"])})
    budget = None
    if session_id:
        detail = await _session_detail(svc, session_id)
        budget = {"scope": "session", "budget_usd": detail["budget_usd"], "spent_usd": detail["spent_usd"],
                  "remaining_usd": detail["budget_remaining_usd"]}
    return {"tool": tools_cloud.TOOL_NAME, "registered": TOOL_REGISTRY.get(tools_cloud.TOOL_NAME) is not None,
            "stop_active": bool(tools_cloud.stop_reason(svc)), "agents": holders, "budget": budget,
            "models": [{"id": c["id"], "label": c["label"], "provider": c["provider"], "tier": c["tier"], "paid": c["paid"],
                        "ready": c["ready"], "price_in_1m": c["price_in_1m"], "price_out_1m": c["price_out_1m"]}
                       for c in await tools_cloud.cloud_catalog(svc)],
            "limits": {"max_prompt_chars": tools_cloud.MAX_PROMPT_CHARS, "preview_window": tools_cloud.PREVIEW_WINDOW,
                       "approval": "every_call"}}


@router.put("/agent/cloud-ask")
async def cloud_ask_grant(body: CloudAskIn, request: Request):
    """Выдать (или снять) `cloud.ask` в списке инструментов агента. Явное действие владельца, идемпотентно, при старте не
    вызывается. Флаг `permissions.cloud_allowed` НЕ меняется: cloud.ask - инструмент с одобрением на каждый вызов, не маршрутизация."""
    svc = request.app.state.svc
    agent_id = body.agent_id or await _resolve_agent_id_for_model(svc, None, "gemma4-31b-uncensored:latest")
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(agents_t.c.id, agents_t.c.name, agents_t.c.tools, agents_t.c.permissions)
                               .where(agents_t.c.id == agent_id))).first() if agent_id is not None else None
        if row is None:
            raise _fail(404, "агент не найден", code="AGENT_NOT_FOUND", hint="передайте agent_id")
        tools = row._mapping["tools"]
        name = tools_cloud.TOOL_NAME
        if isinstance(tools, dict):
            updated: Any = {**tools, name: True} if body.enabled else {k: v for k, v in tools.items() if k != name}
            present = bool(tools.get(name))
        else:
            current = [t for t in (tools or []) if isinstance(t, (str, dict))]
            present = any((t if isinstance(t, str) else str(t.get("name"))) == name for t in current)
            updated = (current if present else current + [name]) if body.enabled else [
                t for t in current if (t if isinstance(t, str) else str(t.get("name"))) != name]
        changed = present != body.enabled
        if changed:
            await s.execute(sa.update(agents_t).where(agents_t.c.id == int(row._mapping["id"])).values(tools=updated))
            await s.commit()
    perms = row._mapping["permissions"]
    return {"agent_id": int(row._mapping["id"]), "name": row._mapping["name"], "tool": name, "enabled": body.enabled,
            "changed": changed, "tools_count": len(updated),
            "cloud_allowed": bool(perms.get("cloud_allowed")) if isinstance(perms, dict) else False}


async def _setup(svc: Any) -> None:
    _x(svc)


FEATURE = Feature(name="agent_extras", router=router, setup=_setup)
