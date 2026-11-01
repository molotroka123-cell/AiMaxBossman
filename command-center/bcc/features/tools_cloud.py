"""`cloud.ask` — локальный агент (Gemma на телефоне) советуется с СИЛЬНОЙ облачной моделью по просьбе владельца.

Владелец 21.10: «Запрещено читать локальные переписки данные и фото»; контекст чата остаётся внутри этого чата. Поэтому
инструмент отправляет в облако ТОЛЬКО явный текст аргумента `prompt` - ни историю беседы, ни вложения, ни память, ни пути
к фото/видео. Каждый вызов - отдельное одобрение владельца (ASK, пол политики): в одобрении видны модель и провайдер, точный
текст после удаления секретов, число знаков, оценка стоимости и остаток бюджета.

Что именно сделано, и почему так:

  * ASK всегда. `effect_hook` возвращает ask или deny (никогда None), `hook_is_floor=True` - правило владельца и выданное право
    его не снимают. Права `permission` у инструмента нет сознательно: ни одно из существующих не про облачный вывод, а
    выдаваемое право было бы лишь способом ослабить ASK.
  * Маршрут выбирается один раз, ДО одобрения (`bind_args`), и записывается в настоящие аргументы (`_cloud`): одобряется
    конкретная модель и конкретный текст (sha256 редактированного текста), исполняется ровно они. Обработчик повторно
    проверяет STOP, бюджет и готовность модели (`context_deny` работает и до одобрения, и перед исполнением).
  * Окно предпросмотра. Движок режет аргументы в одобрении на 2000 знаках (engine._execute_tool_calls). Текст в одобрении
    обязан быть виден ЦЕЛИКОМ, поэтому слишком длинный текст - deny с просьбой сократить, а не молчаливое усечение.
  * Модель, цена и бюджет - из каталога роутера мобильного агента (`build_model_router_catalog`, облачные исполнители
    `WORKERS`). Вызов идёт через тот же управляемый адаптер, что и `Registry.adapter_for` (Fable-потолок, режим «только
    бесплатное», длинный прогон, STOP, egress), а не напрямую. Транспорт подменяем (`transport`): тесты сети не касаются.
  * Расход пишется в тот же учёт, что и у роутера (`AgentSessionRuntime.spent_usd`), а в журнал аудита - только метаданные:
    время, модель, число знаков туда/обратно, стоимость, id одобрения. Ни текста вопроса, ни ответа.
  * Ответ - внешние данные (`external_output=True`) с первой строкой-происхождением «☁️ <модель> (облако, по запросу
    владельца)»: локальная модель не может выдать его за свой.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
from decimal import Decimal, ROUND_CEILING
import math
import os
import re
import threading
import time
import uuid
import weakref
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from .. import conversation_context
from ..db import task_runs as runs_t
from ..single_flight import await_shared
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from ..trace import current_trace_id
from . import Feature
from .agent_opencode import (DEFAULT_SESSION_BUDGET_USD, _orch, _session_detail, build_model_router_catalog,
                             redact_text)
from .chat_threads import _thread_of_task
from .coding_tasks import WORKERS, _worker_key

TOOL_NAME = "cloud.ask"
PROVENANCE = "☁️ {model} (облако, по запросу владельца)"

MAX_PROMPT_CHARS = 6000          # предел аргумента; практически меньше - см. PREVIEW_WINDOW
MAX_REASON_CHARS = 160
MAX_ANSWER_TOKENS = 1200         # потолок ответа: и для оценки худшего случая, и для провайдера
MAX_ANSWER_CHARS = 8000
CALL_TIMEOUT_S = 90.0
#: Движок показывает владельцу аргументы вызова первыми 2000 знаками (engine._execute_tool_calls).
PREVIEW_WINDOW = 2000
#: A paid reservation not settled within this time belongs to a call whose outcome nobody recorded (process died, hang):
#: whether the provider billed it is unknown, so it becomes billing_unknown spend that only the owner can reconcile.
RESERVATION_STALE_S = 15 * 60
#: Ledger statuses whose ACTUAL cost is not known. Their `cost_usd` is null (legacy rows: 0.0) and is never summed as spend.
_UNKNOWN_COST_STATUSES = ("billing_unknown", "reserved")

#: Подсказка о силе при равном ярусе роутера: от сильной к слабой. Это оценка, а не бенчмарк; явный `model` её перекрывает.
STRENGTH_HINT = ("claude-sonnet-4", "haiku-5.5", "nemotron-ultra-free", "qwen3-coder-paid", "deepseek-v4.1-flash",
                 "groq-gpt-oss-120b-free", "gemini-3-flash-free", "nvidia-nim", "openrouter-free", "minimax-m31-free",
                 "glm-flash", "nemotron-35-free", "poolside-laguna-free", "openrouter-code-free", "groq-qwen38-free")

_MEDIA_EXT = (r"(?:jpe?g|png|gif|webp|bmp|heic|heif|tiff?|dng|cr2|nef|arw|mp4|m4v|mov|mkv|avi|webm|wmv|flv|3gp|mts)")
_MEDIA_TOKEN = re.compile(r"(?i)[^\s\"'<>|]+\." + _MEDIA_EXT + r"\b")
_MEDIA_DATA_URI = re.compile(r"(?i)data:(?:image|video)/")
_LOCAL_DATA_PATH = re.compile(r"(?i)(?:chat[\\/]+attachments|terminal[\\/]+sessions)[\\/]")
_ATTACHMENT_BLOCK = re.compile(r"Вложение «[^»\n]{1,200}»")
_CHAT_PAIR = re.compile(r"(?m)^Владелец: .*\nBossman: ")
_PRICE_RE = re.compile(r"\$\s*([0-9]*\.?[0-9]+)\s*/\s*\$?\s*([0-9]*\.?[0-9]+)")


def _markers() -> tuple[str, ...]:
    """Метки контекста чата: заголовок беседы, «Новое сообщение», резюме /compact, память задачи."""
    from ..provider_governance import MEMORY_CONTEXT_MARKER
    from ..terminal_cli.chat import SUMMARY_LABEL
    return tuple(m.strip() for m in (conversation_context.HEADER, conversation_context.MARKER, SUMMARY_LABEL,
                                     MEMORY_CONTEXT_MARKER) if m.strip())


def _est_tokens(text: str) -> int:
    from .agent_extras import estimate_tokens           # лениво: agent_extras сам импортирует этот модуль
    return estimate_tokens(text)


class _State:
    """Services для `bind_args` (у хука нет ctx). Выставляется в setup(svc).

    Locks: `ledger_lock` (threading) + an OS file lock serialise every read-check-append of the ledger across threads and
    processes; `_budget_lock()` (one asyncio lock per event loop) serialises the in-process session-budget bookkeeping.
    """

    def __init__(self) -> None:
        self.svc: Any = None
        self.ledger_lock = threading.Lock()
        self.loop_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


_STATE = _State()


def _budget_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _STATE.loop_locks.get(loop)
    if lock is None:
        lock = _STATE.loop_locks[loop] = asyncio.Lock()
    return lock


def set_services(svc: Any) -> None:
    _STATE.svc = svc


#: Транспорт облачного вызова. Тесты подменяют его, продакшен - `default_transport`.
transport: Any = None


# ----------------------------------------------------------------------------------------------------------------- текст

def outgoing_text(args: dict) -> str:
    """Ровно то, что уйдёт из ПК: явный `prompt` после удаления секретов тем же редактором, что и события агента."""
    from ..plugin_security import redact_text as _scrub
    return _scrub(redact_text(str((args or {}).get("prompt") or ""))).strip()


@functools.lru_cache(maxsize=1)
def _header_reserve() -> int:
    """Сколько знаков окна предпросмотра занимают поля вокруг текста (худший случай по длине значений)."""
    worst = {"model": "x" * 90, "provider": "x" * 40, "paid": False, "chars_out": 6000, "est_cost_usd": 0.123456,
             "budget_remaining_usd": 100.123456, "reason": "x" * MAX_REASON_CHARS, "prompt": ""}
    return len(json.dumps(worst, ensure_ascii=False, indent=1))


def static_denial(args: dict) -> str | None:
    """Отказ по одним лишь аргументам (без сервисов): пустой текст, утечка контекста, классифицированные документы/Telegram-экспорты, фото/видео, RunPod, не влезает в окно."""
    from ..security_data_governance import DataClassification, classify_text

    if not isinstance(args, dict):
        return "CLOUD_ASK_EMPTY: аргументы должны быть объектом"
    raw = args.get("prompt")
    if not isinstance(raw, str) or not raw.strip():
        return "CLOUD_ASK_EMPTY: пустой текст - нечего отправлять"
    if len(raw) > MAX_PROMPT_CHARS:
        return f"CLOUD_ASK_TOO_LONG: текст длиннее {MAX_PROMPT_CHARS} знаков"
    if "runpod" in str(args.get("model") or "").lower():
        return "CLOUD_ASK_RUNPOD: RunPod и платные GPU-инстансы запрещены без явного подтверждения владельца"
    cls = classify_text(raw)
    if cls in (DataClassification.RESTRICTED_TELEGRAM_EXPORT, DataClassification.RESTRICTED_DOCUMENT):
        if not bool(args.get("owner_cloud_export_approved")):
            return (
                f"CLOUD_ASK_CLASSIFIED_EXPORT ({cls.value}): отправка Telegram-экспортов и классифицированных "
                "документов в облако запрещена без отдельного подтверждения владельца (owner_cloud_export_approved)"
            )
    if (any(m in raw for m in _markers()) or _ATTACHMENT_BLOCK.search(raw) or _CHAT_PAIR.search(raw)
            or _LOCAL_DATA_PATH.search(raw)):
        return ("CLOUD_ASK_CONTEXT_LEAK: в тексте контекст чата, вложение или путь к данным Bossman - историю беседы в облако "
                "не отправляем; задайте вопрос своими словами, без вставки переписки")
    if _MEDIA_TOKEN.search(raw) or _MEDIA_DATA_URI.search(raw):
        return "CLOUD_ASK_MEDIA_PATH: в тексте путь или данные фото/видео - личные фото и видео в облако не уходят"
    shown = len(json.dumps(outgoing_text(args), ensure_ascii=False))
    room = PREVIEW_WINDOW - _header_reserve()
    if shown > room:
        return (f"CLOUD_ASK_PREVIEW_TOO_LONG: текст не помещается в окно одобрения целиком ({shown} > {room} знаков с "
                "экранированием); владелец должен видеть весь уходящий текст - сократите вопрос")
    return None



def _effect(args: dict):
    reason = static_denial(args)
    if reason:
        return ("deny", reason)
    return ("ask", "текст уйдёт в облако: владелец одобряет каждый вызов")


# ------------------------------------------------------------------------------------------- STOP, бюджет, журнал

def stop_reason(svc: Any) -> str:
    """'' или причина: глобальный STOP владельца (живое состояние Computer Use + STOP-файл) или STOP автономии."""
    from ..autonomy import stop as autonomy_stop
    from .telegram_calls import global_stop_active
    data_dir = Path(svc.settings.data_dir)
    why = autonomy_stop.stop_reason(data_dir / "autonomy", data_dir)
    if why:
        return why
    return "owner STOP: глобальный STOP владельца активен" if global_stop_active(svc, fail_closed=True) else ""


def _audit_path(svc: Any) -> Path:
    return Path(svc.settings.data_dir) / "cloud_ask" / "audit.jsonl"


def _svc_for(data_dir: Path) -> Any:
    return SimpleNamespace(settings=SimpleNamespace(data_dir=data_dir))


def _now() -> float:
    return time.time()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ts_epoch(row: dict) -> float | None:
    try:
        return datetime.strptime(str(row.get("ts")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


@contextlib.contextmanager
def _ledger_lock(svc: Any):
    """Exclusive across threads (threading lock) and processes (OS lock on `audit.lock`, the Jeff budget's lock helper).

    A busy or broken lock raises OSError - callers fail closed (no paid call), never proceed unlocked.
    """
    from ..pit.cloud_budget import _process_lock
    with _STATE.ledger_lock, _process_lock(_audit_path(svc).with_name("audit.lock")):
        yield


def _money(value: Any, *, what: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"missing {what}")
    out = float(value)
    if not math.isfinite(out) or out < 0:
        raise ValueError(f"invalid {what}")
    return out


def _read_audit(svc: Any) -> list[dict]:
    """Read the complete audit; corrupt or unreadable data must not reset spend.

    Two money fields, never mixed: `cost_usd` = ACTUAL spend (null when unknown: billing_unknown / open reservation),
    `quoted_cost_usd` = the price quoted before the call (worst case: full answer budget). A reservation must carry its quote.
    """
    out = []
    try:
        with _audit_path(svc).open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("ts"), str):
                    raise ValueError("invalid audit row")
                status = row.get("status")
                if not (status in _UNKNOWN_COST_STATUSES and row.get("cost_usd") is None):
                    _money(row.get("cost_usd"), what="audit cost")
                if status == "reserved" or row.get("quoted_cost_usd") is not None:
                    _money(row.get("quoted_cost_usd"), what="audit quote")
                if status == "reserved" and not row.get("reservation_id"):
                    raise ValueError("reservation without id")
                out.append(row)
    except FileNotFoundError:
        return []
    return out


def _append_audit(svc: Any, row: dict) -> None:
    """Append under the ledger lock (cross-thread and cross-process)."""
    with _ledger_lock(svc):
        _append_audit_locked(svc, row)


def _append_audit_locked(svc: Any, row: dict) -> None:
    path = _audit_path(svc)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _row_sha256(row: dict) -> str:
    """Identity of one audit row: SHA-256 of the exact line `_append_audit` wrote (canonical JSON, sorted keys)."""
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _resolved_unknown(rows: list[dict]) -> set[str]:
    return {str(r.get("reconciles_sha256")) for r in rows
            if r.get("status") == "billing_reconciled" and r.get("decided_by") == "owner" and r.get("reconciles_sha256")}


def _settled_ids(rows: list[dict]) -> set[str]:
    return {str(r["reservation_id"]) for r in rows if r.get("status") != "reserved" and r.get("reservation_id")}


def _settled(rows: list[dict], row: dict) -> bool:
    return str(row.get("reservation_id")) in _settled_ids(rows)


def _ledger(rows: list[dict], *, now: float | None = None) -> dict[str, Any]:
    """Pure view of the ledger. Date-independent where money is unknown:

      * `unknown` - every billing_unknown row and every stale open reservation the owner has NOT reconciled, from ANY day.
        They block paid calls until reconciled (P0 31.10: they used to stop counting at UTC midnight).
      * `open` - fresh reservations (calls in flight, any day): their quote is held against the budget.
      * `spent_usd` - today's (UTC) known actual spend, including owner-confirmed amounts (counted on the reconciliation day).
    """
    now = _now() if now is None else now
    today = _today()
    resolved = _resolved_unknown(rows)
    settled = _settled_ids(rows)
    unknown: list[dict] = []
    open_rows: list[dict] = []
    spent = 0.0
    for r in rows:
        status = r.get("status")
        if status == "billing_unknown":
            if _row_sha256(r) not in resolved:
                unknown.append(r)
            continue                       # actual unknown: never summed as 0; the owner's reconciliation row carries it
        if status == "reserved":
            if str(r.get("reservation_id")) in settled:
                continue
            started = _ts_epoch(r)
            if started is None or now - started > RESERVATION_STALE_S:
                if _row_sha256(r) not in resolved:
                    unknown.append(r)
            else:
                open_rows.append(r)
            continue
        if str(r.get("ts", ""))[:10] == today:
            spent += float(r.get("cost_usd") or 0.0)
    return {"today": today, "spent_usd": round(spent, 6), "unknown": unknown, "open": open_rows,
            "reserved_usd": round(sum(float(r["quoted_cost_usd"]) for r in open_rows), 6)}


def unresolved_unknown_spend(data_dir: Path) -> list[dict]:
    """Unknown-cost rows the owner has not reconciled yet, any day (metadata only, never request text)."""
    led = _ledger(_read_audit(_svc_for(data_dir)))
    return [{"sha256": _row_sha256(r), "ts": r.get("ts"), "status": r.get("status"), "model": r.get("model"),
             "provider": r.get("provider"), "quoted_cost_usd": r.get("quoted_cost_usd"), "error": r.get("error")}
            for r in led["unknown"]]


def reconcile_unknown_spend_by_owner(data_dir: Path, *, target_record_sha256: str, actual_cost_usd: float, owner_id: str,
                                     evidence_note: str) -> dict[str, Any]:
    """Owner's append-only reconciliation of ONE unknown-cost row (trio audit 29.10, Codex H4-A; P0 31.10).

    Fail-closed stays the default: an unknown charge blocks paid calls - on every following day too - until the owner states
    the real amount from the provider's own usage. The original row is never rewritten; a `billing_reconciled` row is appended
    that names it by SHA-256 and carries the confirmed cost, which then counts in the spend of the reconciliation day.
    Idempotent: a second reconciliation of the same row returns the first.
    """
    if owner_id != "owner":
        raise PermissionError("only the owner reconciles unknown cloud spend")
    if isinstance(actual_cost_usd, bool) or not isinstance(actual_cost_usd, (int, float)) \
            or not math.isfinite(float(actual_cost_usd)) or float(actual_cost_usd) < 0:
        raise ValueError("actual_cost_usd must be a finite number >= 0")
    note = str(evidence_note or "").strip()
    if not note:
        raise ValueError("evidence_note (where the amount comes from) is required")
    svc = _svc_for(data_dir)
    with _ledger_lock(svc):
        rows = _read_audit(svc)
        existing = next((r for r in rows if r.get("status") == "billing_reconciled"
                         and r.get("reconciles_sha256") == target_record_sha256), None)
        if existing is not None:
            return existing
        target = next((r for r in _ledger(rows)["unknown"] if _row_sha256(r) == target_record_sha256), None)
        if target is None:
            raise LookupError("no unresolved billing_unknown row with this SHA-256")
        record = {"ts": _stamp(), "status": "billing_reconciled",
                  "reconciles_sha256": target_record_sha256, "cost_usd": round(float(actual_cost_usd), 6),
                  "quoted_cost_usd": target.get("quoted_cost_usd"),
                  "decided_by": "owner", "evidence_note": note[:500], "model": target.get("model"),
                  "provider": target.get("provider")}
        _append_audit_locked(svc, record)
    return record


async def audit(svc: Any, **row: Any) -> dict:
    """Одна строка журнала. Намеренно без полей текста: только метаданные вызова."""
    record = {"ts": _stamp(), **row}
    await asyncio.to_thread(_append_audit, svc, record)
    return record


async def _session_of_task(svc: Any, task_id: int | None) -> str | None:
    if not isinstance(task_id, int) or isinstance(task_id, bool):
        return None
    known = _orch(svc).task_to_session.get(task_id)
    if known:
        return known
    return await asyncio.to_thread(_thread_of_task, svc, task_id, exclude="")


async def _session_budget(svc: Any, task_id: int | None) -> dict | None:
    """Raw session budget of the task's agent session (router accounting), or None -> the day budget applies."""
    session_id = await _session_of_task(svc, task_id)
    if not session_id:
        return None
    try:
        detail = await _session_detail(svc, session_id)
        return {"session_id": session_id, "budget_usd": float(detail["budget_usd"]),
                "spent_usd": float(detail["spent_usd"]), "remaining_usd": float(detail["budget_remaining_usd"])}
    except Exception:  # noqa: BLE001 - сессия пропала или не читается: считаем по суткам, не по воздуху
        return None


_LEDGER_ERRORS = (OSError, UnicodeError, ValueError, TypeError, OverflowError)


def _blocked(**extra: Any) -> dict:
    return {"scope": "day", "session_id": None, "budget_usd": DEFAULT_SESSION_BUDGET_USD,
            "spent_usd": DEFAULT_SESSION_BUDGET_USD, "remaining_usd": 0.0, "billing_unknown": True, **extra}


async def budget_state(svc: Any, task_id: int | None) -> dict:
    """Бюджет, из которого платит вызов: сессия агента (учёт роутера) или, вне сессии, сутки по журналу cloud.ask.

    Any unreconciled unknown-cost row (any day) or an unreadable ledger -> remaining 0 (paid calls blocked).
    Quotes of calls in flight are held against the remaining budget. Advisory: the binding check is `reserve_paid_call_sync`.
    """
    try:
        rows = await asyncio.to_thread(_read_audit, svc)
    except _LEDGER_ERRORS:
        return _blocked(audit_unavailable=True)
    led = _ledger(rows)
    if led["unknown"]:
        return _blocked(unknown_count=len(led["unknown"]))
    session = await _session_budget(svc, task_id)
    if session:
        held = sum(float(r["quoted_cost_usd"]) for r in led["open"] if r.get("session_id") == session["session_id"])
        return {"scope": "session", **session, "reserved_usd": round(held, 6),
                "remaining_usd": round(max(0.0, session["remaining_usd"] - held), 6)}
    return {"scope": "day", "session_id": None, "budget_usd": DEFAULT_SESSION_BUDGET_USD, "spent_usd": led["spent_usd"],
            "reserved_usd": led["reserved_usd"],
            "remaining_usd": round(max(0.0, DEFAULT_SESSION_BUDGET_USD - led["spent_usd"] - led["reserved_usd"]), 6)}


def reserve_paid_call_sync(data_dir: Path, *, quoted_usd: float, budget_usd: float, scope: str,
                           session_id: str | None = None, session_remaining_usd: float | None = None,
                           meta: dict | None = None) -> tuple[dict | None, str | None]:
    """The binding budget check for ONE paid call: check and append a `reserved` row atomically under the ledger lock.

    Cross-process for the day budget: every process appends to the same ledger under the same OS file lock, so two processes
    cannot both spend the last of the budget. The session budget (`session_remaining_usd`) comes from the in-memory session
    runtime of THIS process; its in-flight quotes are held through the same ledger rows.
    Fails closed: unknown spend, an unreadable/corrupt ledger or a busy lock -> no reservation, no paid call.
    """
    try:
        quoted = _money(quoted_usd, what="quote")
        if quoted <= 0:
            raise ValueError("paid quote must be positive")
        quoted = float(Decimal(str(quoted)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING))
    except (TypeError, ValueError):
        return None, "CLOUD_ASK_NO_BUDGET: у платного вызова нет цены - без цены не отправляем"
    svc = _svc_for(data_dir)
    try:
        with _ledger_lock(svc):
            led = _ledger(_read_audit(svc))
            if led["unknown"]:
                return None, (f"CLOUD_ASK_NO_BUDGET: {len(led['unknown'])} платн. вызов(ов) с неизвестной стоимостью ждут "
                              "сверки владельцем (billing_unknown) - платные вызовы остановлены до сверки")
            if scope == "session" and session_remaining_usd is not None:
                held = sum(float(r["quoted_cost_usd"]) for r in led["open"] if r.get("session_id") == session_id)
                remaining = float(session_remaining_usd) - held
            else:
                scope, session_id = "day", None
                remaining = float(budget_usd) - led["spent_usd"] - led["reserved_usd"]
            remaining = round(remaining, 6)
            if remaining <= 0 or quoted > remaining:
                return None, f"CLOUD_ASK_NO_BUDGET: оценка {quoted} USD при остатке бюджета {max(0.0, remaining)} USD"
            row = {**(meta or {}), "ts": _stamp(), "status": "reserved", "reservation_id": uuid.uuid4().hex,
                   "quoted_cost_usd": round(quoted, 6), "cost_usd": None, "scope": scope, "session_id": session_id,
                   "pid": os.getpid()}
            _append_audit_locked(svc, row)
            return row, None
    except _LEDGER_ERRORS:
        return None, "CLOUD_ASK_NO_BUDGET: журнал расходов cloud.ask недоступен или повреждён - платный вызов не отправлен"


async def _task_id_from_trace(svc: Any) -> int | None:
    """Задача текущего run'а: движок ставит trace `run-<id>` на время исполнения (bcc.trace); у bind_args нет ctx."""
    trace = current_trace_id.get() or ""
    match = re.fullmatch(r"run-(\d+)", trace)
    if not match:
        return None
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(runs_t.c.task_id).where(runs_t.c.id == int(match.group(1))))).first()
    return int(row[0]) if row else None


# ----------------------------------------------------------------------------------------------------------- каталог

def _worker_prices(worker_id: str, spec: dict, tier_row: dict | None) -> tuple[float, float] | None:
    if tier_row is not None:
        return float(tier_row["price_in_1m"]), float(tier_row["price_out_1m"])
    if spec.get("tier") == "free":
        return 0.0, 0.0
    match = _PRICE_RE.search(str(spec.get("price_display") or ""))
    return (float(match.group(1)), float(match.group(2))) if match else None


async def cloud_catalog(svc: Any) -> list[dict]:
    """Облачные исполнители каталога роутера, сильные первыми: ярус роутера, затем STRENGTH_HINT."""
    catalog = await build_model_router_catalog(svc)
    tiers = {t["worker"]: t for t in catalog.get("tiers", []) if t.get("worker")}
    out: list[dict] = []
    for item in catalog.get("workers", []):
        wid = str(item["id"])
        spec = WORKERS.get(wid) or {}
        prices = _worker_prices(wid, spec, tiers.get(wid))
        paid = bool(spec.get("tier") == "paid" or (prices and max(prices) > 0))
        tier = int(tiers[wid]["tier"]) if wid in tiers else (4 if paid else 3)
        out.append({"id": wid, "tier_id": tiers[wid]["id"] if wid in tiers else f"worker:{wid}", "tier": tier,
                    "model": str(item["model"]), "label": str(item["label"]), "provider": str(item["provider"]),
                    "endpoint": str(spec.get("endpoint") or ""), "paid": paid, "ready": bool(item["ready"]),
                    "price_in_1m": prices[0] if prices else None, "price_out_1m": prices[1] if prices else None})
    hint = {wid: i for i, wid in enumerate(STRENGTH_HINT)}
    out.sort(key=lambda c: (-c["tier"], hint.get(c["id"], len(hint)), c["id"]))
    return out


def _governance_rows(cand: dict) -> tuple[dict, dict]:
    """Строки provider/model для политик governance - как у Registry, но из таблицы WORKERS."""
    from urllib.parse import urlsplit
    host = (urlsplit(cand["endpoint"]).hostname or "").lower()
    caps = {"free_tier": True} if (not cand["paid"] and not host.endswith("openrouter.ai")) else {}
    provider = {"id": 0, "name": cand["provider"], "kind": "openai_compat", "base_url": cand["endpoint"]}
    model = {"id": 0, "name": cand["model"], "alias": cand["id"], "kind": "cloud", "caps": caps,
             "price_in": cand["price_in_1m"], "price_out": cand["price_out_1m"],
             "pricing_known": cand["price_in_1m"] is not None and cand["price_out_1m"] is not None}
    return provider, model


def _governance_refusal(cand: dict) -> str:
    from ..provider_governance import free_only_refusal
    provider, model = _governance_rows(cand)
    return free_only_refusal(provider, model)


def estimate_cost(cand: dict, tokens_in: int, tokens_out: int = MAX_ANSWER_TOKENS) -> float | None:
    if cand["price_in_1m"] is None or cand["price_out_1m"] is None:
        return None
    amount = (Decimal(str(tokens_in)) * Decimal(str(cand["price_in_1m"]))
              + Decimal(str(tokens_out)) * Decimal(str(cand["price_out_1m"]))) / Decimal("1000000")
    return float(amount.quantize(Decimal("0.000001"), rounding=ROUND_CEILING))


def _match(cands: list[dict], wanted: str) -> dict | None:
    key = wanted.strip().lower()
    for c in cands:
        if key in (c["id"].lower(), c["tier_id"].lower(), c["model"].lower(), c["label"].lower()):
            return c
    return None


def choose(cands: list[dict], *, requested: str | None, tokens_in: int, remaining_usd: float) -> tuple[dict | None, str | None]:
    """(кандидат, отказ). Явная модель - только она; иначе самая сильная ГОТОВАЯ, разрешённая политикой и по бюджету."""
    def verdict(c: dict) -> tuple[str, str]:
        """(код отказа или '', подробность)."""
        if "runpod" in (c["id"] + c["model"] + c["endpoint"]).lower():
            return "CLOUD_ASK_RUNPOD", "RunPod запрещён без явного подтверждения владельца"
        if not c["ready"]:
            return "CLOUD_ASK_NO_MODEL", "нет ключа исполнителя"
        cost = estimate_cost(c, tokens_in)
        if cost is None:
            return "CLOUD_ASK_NO_MODEL", "цена модели неизвестна"          # как governance: без цены не отправляем
        refusal = _governance_refusal(c)
        if refusal:
            return "CLOUD_ASK_NO_MODEL", refusal
        if c["paid"] and (remaining_usd <= 0 or cost > remaining_usd):
            return "CLOUD_ASK_NO_BUDGET", f"оценка {cost} USD при остатке бюджета {remaining_usd} USD"
        return "", ""

    if requested:
        if "runpod" in requested.lower():
            return None, "CLOUD_ASK_RUNPOD: RunPod и платные GPU-инстансы запрещены без явного подтверждения владельца"
        c = _match(cands, requested)
        if c is None:
            names = ", ".join(x["id"] for x in cands if x["ready"]) or "нет готовых"
            return None, f"CLOUD_ASK_UNKNOWN_MODEL: модели «{requested[:60]}» нет в облачном каталоге; готовы: {names}"
        code, why = verdict(c)
        return (None, f"{code}: {c['id']}: {why}") if code else (c, None)
    refused: dict[str, int] = {}
    for c in cands:
        code, _why = verdict(c)
        if not code:
            return c, None
        refused[code] = refused.get(code, 0) + 1
    if refused.get("CLOUD_ASK_NO_BUDGET"):
        return None, ("CLOUD_ASK_NO_BUDGET: бюджет исчерпан или не покрывает платные модели, а бесплатной готовой "
                      f"облачной модели нет (остаток {remaining_usd} USD)")
    return None, "CLOUD_ASK_NO_MODEL: нет готовой облачной модели (проверьте ключи облачных исполнителей)"


async def resolve_route(svc: Any, args: dict, *, task_id: int | None, bound: dict | None = None
                        ) -> tuple[dict | None, str | None]:
    """(маршрут для привязки, отказ). `bound` - уже одобренный маршрут: проверяется, а не выбирается заново."""
    stopped = stop_reason(svc)
    if stopped:
        return None, f"CLOUD_ASK_STOP: {stopped}"
    text = outgoing_text(args)
    tokens_in = _est_tokens(text) + 16
    budget = await budget_state(svc, task_id)
    cands = await cloud_catalog(svc)
    wanted = str(bound.get("worker")) if bound else (str(args.get("model") or "").strip() or None)
    cand, denied = choose(cands, requested=wanted, tokens_in=tokens_in, remaining_usd=budget["remaining_usd"])
    if cand is None:
        return None, denied
    if bound and bound.get("model") != cand["model"]:
        return None, "CLOUD_ASK_CHANGED: модель маршрута изменилась после одобрения - запросите одобрение заново"
    return {"v": 1, "worker": cand["id"], "model": cand["model"], "label": cand["label"], "provider": cand["provider"],
            "paid": cand["paid"], "price_in_1m": cand["price_in_1m"], "price_out_1m": cand["price_out_1m"],
            "est_cost_usd": estimate_cost(cand, tokens_in), "budget_scope": budget["scope"],
            "budget_remaining_usd": budget["remaining_usd"], "chars_out": len(text),
            "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}, None


# --------------------------------------------------------------------------------------- хуки движка (до и после ASK)

def normalize_args(args: dict) -> dict:
    """Канонические аргументы одобрения: что видит владелец и что входит в digest. Поля маршрута - первыми, текст - последним."""
    cloud = args.get("_cloud") if isinstance(args.get("_cloud"), dict) else None
    out: dict[str, Any] = {}
    if cloud:
        out.update(model=cloud.get("model"), provider=cloud.get("provider"), paid=bool(cloud.get("paid")),
                   chars_out=cloud.get("chars_out"), est_cost_usd=cloud.get("est_cost_usd"),
                   budget_remaining_usd=cloud.get("budget_remaining_usd"))
    else:
        out["route"] = "не привязан"
    reason = " ".join(str(args.get("reason") or "").split())[:MAX_REASON_CHARS]
    if reason:
        out["reason"] = reason
    out["prompt"] = outgoing_text(args)
    return out


async def bind_route(args: dict) -> str | None:
    """Выбрать маршрут ДО одобрения и записать его в настоящие аргументы; None - привязано, строка - отказ."""
    svc = _STATE.svc
    denied = static_denial(args)
    if denied:
        return denied
    args.pop("_cloud", None)                 # маршрут пишет только эта функция, не модель
    if svc is None:
        return "CLOUD_ASK_NO_MODEL: сервисы Bossman недоступны - маршрут не выбран"
    route, denied = await resolve_route(svc, args, task_id=await _task_id_from_trace(svc))
    if route is None:
        return denied
    args["_cloud"] = route
    if len(json.dumps(normalize_args(args), ensure_ascii=False, indent=1)) > PREVIEW_WINDOW:
        args.pop("_cloud", None)
        return ("CLOUD_ASK_PREVIEW_TOO_LONG: описание вызова вместе с текстом не помещается в окно одобрения - "
                "сократите вопрос")
    return None


async def _context_deny(args: dict, ctx: ToolContext) -> str | None:
    """Читающая проверка до ASK и перед исполнением: STOP, бюджет, готовность модели. Права не расширяет."""
    denied = static_denial(args)
    if denied:
        return denied
    bound = args.get("_cloud") if isinstance(args.get("_cloud"), dict) else None
    task_id = (ctx.task or {}).get("id") if isinstance(ctx.task, dict) else None
    _route, denied = await resolve_route(ctx.svc, args, task_id=task_id, bound=bound)
    return denied


# --------------------------------------------------------------------------------------------------------- транспорт

async def default_transport(svc: Any, route: dict, messages: list[dict], *, max_tokens: int, timeout: float) -> Any:
    """Реальный вызов: адаптер OpenAI-совместимого эндпоинта исполнителя под управляемой обёрткой Registry (потолки и политики)."""
    from ..pit.jeff_kb_cloud import governed_adapter
    from ..providers import build_adapter
    cand = next((c for c in await cloud_catalog(svc) if c["id"] == route["worker"]), None)
    if cand is None:
        raise RuntimeError("исполнитель пропал из каталога")
    key = await _worker_key(WORKERS[cand["id"]]["key"], svc)
    if not key:
        raise RuntimeError("нет ключа исполнителя")
    provider, model = _governance_rows(cand)
    adapter = governed_adapter(provider, model, build_adapter("openai_compat", cand["endpoint"], key))
    try:
        return await adapter.chat(cand["model"], messages, max_tokens=max_tokens, temperature=0.2, timeout=timeout)
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                await close()


def _actual_cost(route: dict, result: Any) -> float | None:
    """Known actual cost or None; an estimate is not evidence of a provider charge."""
    if not route.get("paid"):
        return 0.0
    from .. import long_run
    priced = {"price_in": route.get("price_in_1m"), "price_out": route.get("price_out_1m")}
    spent = long_run.actual_eur(priced, result)
    return round(spent, 6) if spent is not None and math.isfinite(spent) and spent >= 0 else None


# ------------------------------------------------------------------------------------------------------- обработчик

def _fail(code: str, text: str) -> ToolResult:
    return ToolResult(content=f"{code}: {text}", one_line=f"{TOOL_NAME}: {code}", error=True)


async def _t_ask(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.svc
    # Одобрение владельца - условие исполнения: вызов по аренде или без строки approvals сюда не допускается.
    if ctx.approval_id is None:
        return _fail("CLOUD_ASK_NO_APPROVAL", "облачный запрос выполняется только по одобрению владельца на этот вызов")
    denied = static_denial(args)
    if denied:
        return _fail(denied.split(":", 1)[0], denied.split(":", 1)[-1].strip())
    bound = args.get("_cloud") if isinstance(args.get("_cloud"), dict) else None
    if bound is None:
        return _fail("CLOUD_ASK_UNBOUND", "маршрут не был привязан к одобрению - запросите одобрение заново")
    text = outgoing_text(args)
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != bound.get("prompt_sha256"):
        return _fail("CLOUD_ASK_CHANGED", "текст изменился после одобрения - запросите одобрение заново")
    task_id = (ctx.task or {}).get("id") if isinstance(ctx.task, dict) else None
    route, denied = await resolve_route(svc, args, task_id=task_id, bound=bound)
    if route is None:
        return _fail(denied.split(":", 1)[0], denied.split(":", 1)[-1].strip())
    paid = bool(bound.get("paid"))
    quoted = route.get("est_cost_usd")
    reservation: dict | None = None
    session: dict | None = None
    if paid:
        # Binding check: check + reserve the quote atomically, BEFORE the provider is called (P0 31.10: N parallel calls all
        # passed the advisory check above and overspent together). The asyncio lock covers the in-process session runtime;
        # the ledger's file lock covers the day budget across processes.
        async with _budget_lock():
            session = await _session_budget(svc, task_id)
            reservation, denied = await asyncio.to_thread(
                reserve_paid_call_sync, Path(svc.settings.data_dir), quoted_usd=quoted,
                budget_usd=DEFAULT_SESSION_BUDGET_USD, scope="session" if session else "day",
                session_id=(session or {}).get("session_id"),
                session_remaining_usd=(session or {}).get("remaining_usd"),
                meta={"model": bound["model"], "provider": bound["provider"], "worker": bound["worker"],
                      "approval_id": ctx.approval_id, "task_id": task_id, "run_id": ctx.run_id})
        if reservation is None:
            return _fail("CLOUD_ASK_NO_BUDGET", (denied or "").split(":", 1)[-1].strip())
    messages = [{"role": "user", "content": text}]
    call = transport or default_transport
    started = time.monotonic()
    base = {"model": bound["model"], "provider": bound["provider"], "worker": bound["worker"], "chars_out": len(text),
            "approval_id": ctx.approval_id, "task_id": task_id, "run_id": ctx.run_id,
            "scope": reservation["scope"] if reservation else "day", "quoted_cost_usd": quoted,
            "reservation_id": reservation["reservation_id"] if reservation else None}
    try:
        result = await asyncio.wait_for(call(svc, bound, messages, max_tokens=MAX_ANSWER_TOKENS, timeout=CALL_TIMEOUT_S),
                                        timeout=CALL_TIMEOUT_S + 5.0)
    except asyncio.CancelledError:
        if paid:
            # await_shared, not asyncio.shield (bcc/single_flight.py): the audit row must be written even if this caller is cancelled
            await await_shared(asyncio.ensure_future(audit(svc, **base, chars_in=0, cost_usd=None, status="billing_unknown",
                                                            error="CancelledError", ms=int((time.monotonic() - started) * 1000))))
        raise
    except Exception as exc:  # noqa: BLE001 - не включать текст исключения, он может содержать запрос
        # Paid: the request may have been billed - actual cost unknown (null, NOT 0) until the owner reconciles it.
        await audit(svc, **base, chars_in=0, cost_usd=None if paid else 0.0,
                    status="billing_unknown" if paid else "error", error=type(exc).__name__,
                    ms=int((time.monotonic() - started) * 1000))
        return _fail("CLOUD_ASK_FAILED", f"облачная модель не ответила ({type(exc).__name__}); повторите позже или "
                                          "сообщите владельцу")
    answer = str(getattr(result, "text", "") or "").strip()
    cost = _actual_cost(bound, result)
    async with _budget_lock():
        if cost and reservation and reservation["scope"] == "session" and session:
            rt = _orch(svc).runtime(session["session_id"])
            # max(): the session total is max(run costs, runtime); adding to the snapshot keeps every parallel call counted
            rt.spent_usd = round(max(float(rt.spent_usd or 0.0), session["spent_usd"]) + cost, 6)   # учёт роутера
            rt.emit("session.updated", spent_usd=rt.spent_usd, cloud_ask_cost_usd=cost)
        await audit(svc, **base, chars_in=len(answer), cost_usd=cost,
                    status="billing_unknown" if cost is None else ("ok" if answer else "empty"),
                    ms=int((time.monotonic() - started) * 1000))
    if not answer:
        return _fail("CLOUD_ASK_EMPTY_ANSWER", "облачная модель вернула пустой ответ")
    cut = len(answer) > MAX_ANSWER_CHARS
    head = PROVENANCE.format(model=bound["model"])
    return ToolResult(
        content=head + "\n" + answer[:MAX_ANSWER_CHARS], one_line=f"{TOOL_NAME}: {bound['model']}, {len(text)} знаков",
        truncated=cut, more="спросите короче или попросите краткий ответ" if cut else "", external=True,
        data={"model": bound["model"], "provider": bound["provider"], "chars_out": len(text), "chars_in": len(answer),
              "cost_usd": cost})


SPECS = [
    ToolSpec(
        name=TOOL_NAME,
        description=("Посоветоваться с СИЛЬНОЙ облачной моделью, но только когда владелец сам попросил (например: «спроси у "
                     "облака», «спроси сильную модель»). prompt - готовый самодостаточный вопрос своими словами: в облако "
                     "уходит ТОЛЬКО этот текст, без истории чата, файлов и фото; не вставляй в него переписку, пути к "
                     "фото/видео и секреты. Каждый вызов - отдельное одобрение владельца, он видит модель, текст и цену. "
                     "Ответ приходит с пометкой «☁️ модель (облако)» - передай её владельцу как ответ облака, не как свой."),
        handler=_t_ask,
        input_schema={
            "prompt": {"type": "string", "description": f"вопрос для облачной модели, до {MAX_PROMPT_CHARS} знаков"},
            "model": {"type": "string", "description": "необязательно: исполнитель из облачного каталога; по умолчанию "
                                                       "самый сильный готовый в пределах бюджета"},
            "reason": {"type": "string", "description": "почему нужен облачный совет (видит владелец; в облако не уходит)"},
        },
        required=["prompt"], category="send", permission="", source="cloud", default_effect="ask",
        timeout_seconds=CALL_TIMEOUT_S + 40.0, idempotent=False, external_output=True,
        effect_hook=_effect, normalize_args=normalize_args, context_deny=_context_deny, bind_args=bind_route),
]


async def _setup(svc) -> None:
    set_services(svc)
    for spec in SPECS:
        REGISTRY.register(spec)


router = APIRouter()


def _require_owner(request: Request) -> None:
    """Owner only: the role comes from the verified session/token, never from a header or the body."""
    principal = getattr(request.state, "principal", None)
    if principal is None or getattr(principal, "role", None) != "owner":
        raise HTTPException(403, {"code": "OWNER_CREDENTIAL_REQUIRED", "message": "сверку облачных расходов делает только владелец"})
    for header in ("x-participant", "x-bcc-participant"):
        value = request.headers.get(header)
        if value is not None and value.strip() != "owner":
            raise HTTPException(403, {"code": "owner_only", "message": "участник не может сверять расходы"})


@router.get("/cloud/budget/unknown")
async def api_cloud_budget_unknown(request: Request):
    _require_owner(request)
    svc = request.app.state.svc
    return {"unresolved": await asyncio.to_thread(unresolved_unknown_spend, Path(svc.settings.data_dir))}


@router.post("/cloud/budget/reconcile")
async def api_cloud_budget_reconcile(request: Request, body: dict[str, Any]):
    """Owner states the real amount of one billing_unknown call (from the provider's usage page); append-only."""
    _require_owner(request)
    svc = request.app.state.svc
    try:
        row = await asyncio.to_thread(                       # takes the ledger lock itself (cross-thread, cross-process)
            reconcile_unknown_spend_by_owner, Path(svc.settings.data_dir),
            target_record_sha256=str((body or {}).get("target_record_sha256") or ""),
            actual_cost_usd=(body or {}).get("actual_cost_usd"), owner_id="owner",
            evidence_note=str((body or {}).get("evidence_note") or ""))
    except LookupError as exc:
        raise HTTPException(404, {"code": "UNKNOWN_ROW_NOT_FOUND", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(422, {"code": "RECONCILE_INVALID", "message": str(exc)}) from exc
    return {"ok": True, "reconciliation": row, "budget": await budget_state(svc, None)}


FEATURE = Feature(name="tools_cloud", router=router, setup=_setup)
