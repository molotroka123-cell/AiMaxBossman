"""Failure → learning case draft (флаг OFF по умолчанию).

Корпус обучения наполняется руками, и ровно поэтому две записи однажды были
написаны мимо API: они не прошли validate и потерялись молча. Здесь настоящий
отказ сам заводит ЧЕРНОВИК обучающей записи из того, что видно на шине —
симптом, воспроизведение, время наблюдения, участники (запуск, задача, модуль)
и выдержка из соседних событий вокруг момента отказа.

Главная ценность черновика — не заполненные поля, а честный список того, чего
до валидной записи не хватает: он получен настоящим learning.trace.validate,
своей копии схемы здесь нет. Иначе черновик проверялся бы не тем, чем корпус,
и «зелёный» черновик снова падал бы на записи.

Границы, которые не переступаем:
  * черновик — НЕ запись корпуса: LearningStore.add не зовётся, в data/learning
    ничего не пишется. Черновики живут в settings.data_dir; приём в корпус —
    отдельное решение владельца, модуль его только готовит;
  * поля, которых в событии нет, НЕ подделываются пустой строкой: у большинства
    полей схемы нет minLength, пустая строка прошла бы validate и черновик
    выглядел бы готовой записью. Такие поля просто отсутствуют и перечислены
    в needs_human;
  * при выключенном флаге подписки нет, ни один файл не создаётся, поведение
    приложения ровно прежнее.

Ручки (под /api): GET /failure-cases, GET /failure-cases/{id},
DELETE /failure-cases/{id} — удаление меняет состояние и требует флага.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections import deque
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import agents as agents_t, task_runs as runs_t, tasks as tasks_t, utcnow
from . import Feature

FLAG = "BOSSMAN_FAILURE_TO_CASE_ENABLED"
router = APIRouter()

DIRNAME = "failure-cases"                 # внутри settings.data_dir, никогда не в репозитории
REPO_ROOT = Path(__file__).resolve().parents[3]

RING = 40                                 # окно шины, из которого берётся «что делали до»
CONTEXT_BEFORE = 6
CONTEXT_AFTER = 4                         # выдержка «вокруг»: часть событий приходит уже после отказа
MAX_DETAIL = 300
MAX_LIST = 200

_RE_ID = re.compile(r"^[0-9a-f]{12}$")    # id черновика — только hex: путь из него собирается


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


# ---------- валидатор берём у слоя обучения, а не переписываем ----------

_trace_mod: Any = None


def trace():
    """learning/trace.py: установленный пакет, иначе — файл из checkout'а.
    Загрузка по пути (а не через sys.path) не меняет глобальное состояние
    импорта для остального приложения."""
    global _trace_mod
    if _trace_mod is None:
        try:
            import learning.trace as mod          # bossman-shared, если установлен
        except Exception:                          # noqa: BLE001 — checkout без установки
            import importlib.util as iu
            path = REPO_ROOT / "learning" / "trace.py"
            spec = iu.spec_from_file_location("bcc_learning_trace", path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"learning/trace.py недоступен: {path}")
            mod = iu.module_from_spec(spec)
            spec.loader.exec_module(mod)
        _trace_mod = mod
    return _trace_mod


def required_fields() -> list[str]:
    return list(trace().load_schema().get("required", []))


# ---------- что считаем настоящим отказом ----------

FAILURE_KINDS = frozenset({"task.failed", "worker.error", "scheduler.error", "metrics.error",
                           "hook.critical_failure", "hook.escalation_failed"})
FAILURE_SUFFIXES = (".failed", ".error", "_failed", "_failure")
_TEXT_KEYS = ("error", "message", "detail", "reason")
_MODULE_KEYS = ("module", "component", "tool", "server", "hook", "source", "log_kind")


def failure_signal(msg: dict) -> str | None:
    """Признак настоящего отказа: упавший запуск задачи, событие уровня error,
    сессия/джоба со статусом failed. Свои события пропускаем — иначе черновик
    о черновике зациклит подписку."""
    kind = str(msg.get("kind") or "")
    if not kind or kind.startswith("failure_to_case."):
        return None
    if kind in FAILURE_KINDS or kind.endswith(FAILURE_SUFFIXES):
        return kind
    if str(msg.get("level") or "").lower() == "error":
        return f"{kind} (level=error)"
    if str(msg.get("status") or "").lower() == "failed":
        return f"{kind} (status=failed)"
    return None


def _detail(msg: dict) -> str:
    """Человекочитаемая суть события: текстовое поле, иначе весь payload."""
    for key in _TEXT_KEYS:
        value = msg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_DETAIL]
    rest = {k: v for k, v in msg.items() if k not in ("kind", "ts")}
    if not rest:
        return ""
    return json.dumps(rest, ensure_ascii=False, sort_keys=True, default=str)[:MAX_DETAIL]


def _module(msg: dict) -> str:
    for key in _MODULE_KEYS:
        value = msg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    kind = str(msg.get("kind") or "")
    return kind.split(".", 1)[0] if "." in kind else kind


_RE_HEX = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_RE_NUM = re.compile(r"\d+")


def fingerprint(kind: str, module: str, detail: str) -> str:
    """Отпечаток отказа без изменчивого: id запуска, счётчики и хэши в тексте
    нормализуются, иначе один и тот же сбой десять раз дал бы десять черновиков."""
    norm = _RE_NUM.sub("N", _RE_HEX.sub("H", detail))
    return hashlib.sha256(f"{kind}|{module}|{norm}".encode("utf-8")).hexdigest()[:12]


def _summary(msg: dict, position: str) -> dict:
    return {"position": position, "kind": str(msg.get("kind") or ""),
            "ts": str(msg.get("ts") or ""), "detail": _detail(msg)}


# ---------- хранилище черновиков: только settings.data_dir ----------

def _dir(svc) -> Path:
    return Path(svc.settings.data_dir) / DIRNAME


def _path(svc, case_id: str) -> Path:
    return _dir(svc) / f"{case_id}.json"


def _read(svc, case_id: str) -> dict | None:
    path = _path(svc, case_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — битый черновик не должен ронять ленту
        return None


def _write(svc, draft: dict) -> None:
    directory = _dir(svc)
    directory.mkdir(parents=True, exist_ok=True)
    path = _path(svc, draft["id"])
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(draft, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def _all(svc) -> list[dict]:
    directory = _dir(svc)
    if not directory.exists():
        return []
    drafts = []
    for path in sorted(directory.glob("*.json")):
        try:
            drafts.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    drafts.sort(key=lambda d: str(d.get("last_seen") or ""), reverse=True)
    return drafts[:MAX_LIST]


# ---------- участники отказа ----------

async def _participants(svc, msg: dict) -> dict:
    """Кто участвовал: задача, запуск, агент, модель, модуль. Всё из БД и
    события — ничего не додумываем."""
    parts: dict[str, Any] = {"module": _module(msg), "trigger_kind": str(msg.get("kind") or "")}
    for key in ("task_id", "run_id", "session_id", "mission_id"):
        if msg.get(key) is not None:
            parts[key] = msg[key]
    task_id = msg.get("task_id")
    if isinstance(task_id, int):
        async with svc.db.session() as s:
            row = (await s.execute(
                sa.select(tasks_t.c.title, tasks_t.c.prompt, tasks_t.c.status,
                          agents_t.c.name.label("agent_name"))
                .select_from(tasks_t.outerjoin(agents_t, agents_t.c.id == tasks_t.c.agent_id))
                .where(tasks_t.c.id == task_id))).first()
        if row is not None:
            m = row._mapping
            parts["task_title"] = m["title"] or ""
            parts["task_prompt"] = (m["prompt"] or "")[:MAX_DETAIL]
            parts["task_status"] = m["status"] or ""
            parts["agent"] = m["agent_name"] or ""
    run_id = msg.get("run_id")
    if isinstance(run_id, int):
        async with svc.db.session() as s:
            row = (await s.execute(
                sa.select(runs_t.c.model_alias, runs_t.c.attempt, runs_t.c.status, runs_t.c.error)
                .where(runs_t.c.id == run_id))).first()
        if row is not None:
            m = row._mapping
            parts["model"] = m["model_alias"] or ""
            parts["attempt"] = m["attempt"]
            parts["run_status"] = m["status"] or ""
            if m["error"]:
                parts["run_error"] = str(m["error"])[:MAX_DETAIL]
    return parts


# ---------- сборка черновика записи ----------

def _repro_steps(parts: dict, before: list[dict], msg: dict) -> list[str]:
    """«Что делали» — шаги, реально прошедшие по шине до отказа."""
    steps: list[str] = []
    if parts.get("task_title"):
        steps.append(f"задача #{parts.get('task_id')}: {parts['task_title']}")
    if parts.get("task_prompt"):
        steps.append(f"запрос агенту: {parts['task_prompt']}")
    steps += [f"{e['kind']}: {e['detail']}".rstrip(": ") for e in before]
    steps.append(f"отказ {msg.get('kind')}: {_detail(msg)}".rstrip(": "))
    return steps


def build_case(msg: dict, parts: dict, excerpt: list[dict], observed_at: str) -> dict:
    """Частичная обучающая запись: только то, что действительно видно в событии.
    Невыводимые поля отсутствуют — их перечислит validate и needs_human."""
    detail = _detail(msg)
    symptom = f"{msg.get('kind')}: {detail}" if detail else f"{msg.get('kind')}: отказ без текста"
    case: dict[str, Any] = {
        "symptom": symptom,
        "reproduction": _repro_steps(parts, [e for e in excerpt if e["position"] == "before"], msg),
        "evidence": [f"{e['ts']} {e['kind']}: {e['detail']}".rstrip() for e in excerpt],
        "created_at": observed_at,
        # черновик не проверял никто — статус честный и не превращает его в VERIFIED
        "learning_status": "UNVERIFIED",
    }
    if parts.get("task_id") is not None:
        case["task_id"] = str(parts["task_id"])
    if parts.get("run_id") is not None:
        case["run_id"] = str(parts["run_id"])
    if parts.get("task_title"):
        case["task"] = parts["task_title"]
    if parts.get("agent"):
        case["agent"] = parts["agent"]
    if parts.get("model"):
        case["model"] = parts["model"]
    if parts.get("module"):
        case["tags"] = {"component": parts["module"]}
    return case


def _validated(draft: dict) -> dict:
    """Проверка тем же validate, что и корпус; результат хранится рядом с
    черновиком — владелец сразу видит, что дописать."""
    mod = trace()
    case = draft.get("case") or {}
    try:
        errors = mod.validate(case)
    except Exception as exc:  # noqa: BLE001 — недоступная схема не должна терять черновик
        errors = [f"validator unavailable: {type(exc).__name__}: {exc}"]
    draft["validation"] = {"validator": "learning.trace.validate", "errors": errors,
                           "checked_at": utcnow().isoformat(), "valid": not errors}
    try:
        missing = [f for f in required_fields() if f not in case]
    except Exception:  # noqa: BLE001
        missing = []
    draft["needs_human"] = missing
    return draft


# ---------- подписка на шину ----------

class _State:
    """Окно последних событий и «хвост» — сколько событий после отказа ещё
    доложить в выдержку текущего черновика."""

    def __init__(self) -> None:
        self.recent: deque = deque(maxlen=RING)
        self.tail_id: str | None = None
        self.tail_left: int = 0


def _append_tail(svc, state: _State, msg: dict) -> None:
    if not state.tail_left or not state.tail_id:
        return
    draft = _read(svc, state.tail_id)
    if draft is None:
        state.tail_left = 0
        return
    draft.setdefault("context_events", []).append(
        trace().redact_obj(_summary(msg, "after")))
    state.tail_left -= 1
    _write(svc, draft)


async def _on_failure(svc, state: _State, msg: dict, reason: str) -> dict:
    kind = str(msg.get("kind") or "")
    module = _module(msg)
    case_id = fingerprint(kind, module, _detail(msg))
    observed_at = str(msg.get("ts") or utcnow().isoformat())

    existing = _read(svc, case_id)
    if existing is not None:
        # тот же отказ: счётчик, а не второй черновик
        existing["occurrences"] = int(existing.get("occurrences", 1)) + 1
        existing["last_seen"] = observed_at
        _write(svc, existing)
        return existing

    parts = await _participants(svc, msg)
    before = [_summary(m, "before") for m in list(state.recent)[-CONTEXT_BEFORE:]]
    excerpt = before + [_summary(msg, "trigger")]
    draft = {
        "id": case_id,
        "kind": "learning_case_draft",
        "trigger_kind": kind,
        "trigger_reason": reason,
        "occurrences": 1,
        "first_seen": observed_at,
        "last_seen": observed_at,
        "collected_at": utcnow().isoformat(),
        "participants": parts,
        "context_events": excerpt,
        "case": build_case(msg, parts, excerpt, observed_at),
        # чтобы никто не принял черновик за запись корпуса
        "note": "черновик: в корпус data/learning не записан, приём решает владелец",
    }
    draft = trace().redact_obj(draft)
    _validated(draft)
    _write(svc, draft)
    await svc.bus.emit("failure_to_case.draft_created", case_id=case_id, trigger=kind,
                       missing=len(draft.get("needs_human") or []))
    return draft


async def _consume(svc) -> None:
    state: _State = svc.failure_to_case
    q = svc.bus.subscribe()
    try:
        while True:
            msg = await q.get()
            try:
                if str(msg.get("kind") or "").startswith("failure_to_case."):
                    continue          # свои события не попадают ни в окно, ни в выдержку
                reason = failure_signal(msg)
                if reason is None:
                    _append_tail(svc, state, msg)
                else:
                    draft = await _on_failure(svc, state, msg, reason)
                    state.tail_id, state.tail_left = draft["id"], CONTEXT_AFTER
                state.recent.append(msg)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — сбор черновиков не имеет права ронять ленту
                continue
    except asyncio.CancelledError:
        raise
    finally:
        svc.bus.unsubscribe(q)


# ---------- API ----------

def _require_id(case_id: str) -> str:
    if not _RE_ID.match(case_id or ""):
        raise HTTPException(404, {"message": "черновик не найден"})
    return case_id


@router.get("/failure-cases")
async def list_cases(request: Request):
    """Список черновиков: сколько раз повторилось и чего не хватает до записи."""
    svc = request.app.state.svc
    cases = []
    for draft in _all(svc):
        case = draft.get("case") or {}
        cases.append({
            "id": draft.get("id"),
            "occurrences": draft.get("occurrences", 1),
            "trigger_kind": draft.get("trigger_kind", ""),
            "first_seen": draft.get("first_seen", ""),
            "last_seen": draft.get("last_seen", ""),
            "symptom": case.get("symptom", ""),
            "missing_fields": draft.get("needs_human") or [],
            "validation_errors": (draft.get("validation") or {}).get("errors") or [],
        })
    return {"enabled": enabled(), "count": len(cases), "cases": cases}


@router.get("/failure-cases/{case_id}")
async def get_case(case_id: str, request: Request):
    svc = request.app.state.svc
    draft = _read(svc, _require_id(case_id))
    if draft is None:
        raise HTTPException(404, {"message": "черновик не найден"})
    return draft


@router.delete("/failure-cases/{case_id}")
async def delete_case(case_id: str, request: Request):
    svc = request.app.state.svc
    if not enabled():
        raise HTTPException(409, {"message": f"выключено: {FLAG}"})
    path = _path(svc, _require_id(case_id))
    if not path.exists():
        raise HTTPException(404, {"message": "черновик не найден"})
    path.unlink()
    return {"ok": True, "deleted": case_id}


async def _setup(svc) -> None:
    if not enabled():
        return                        # флаг выключен: подписки нет, черновиков нет
    svc.failure_to_case = _State()
    task = asyncio.create_task(_consume(svc), name="bcc-failure-to-case")
    if hasattr(svc, "_tasks"):        # чтобы svc.stop() отменил подписку
        svc._tasks.append(task)


FEATURE = Feature(name="failure_to_case", router=router, setup=_setup)
