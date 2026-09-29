"""After a call: a SHORT summary note for Bossman memory and DRAFT tasks for what was proposed. Owner-gated, nothing else.

Jeff rules apply to the second account (an ordinary participant): the assistant must not create owner tasks and must not
write owner-global memory by itself. So, by DEFAULT, NOTHING is written to Bossman's global memory or to the task list:

* the short summary and the proposed tasks are ALWAYS kept in the calls history entry (the owner sees them in the panel and
  in ``bossman call history``); the transcript is not stored and audio is not stored;
* two owner clicks do the writes: ``save_memory_for`` (one short note through the ONE existing memory path
  ``features.tools_memory.get_service(svc).remember``, deterministic file name ``telegram-call-<id>.md`` = idempotent) and
  ``draft_tasks_for`` (tasks with ``status="draft"``: no agent, no schedule, no run, never enqueued; the same insert the API
  uses for ``run_now=false``; provenance in the prompt prefix ``[Telegram call <id>]``, idempotency key
  ``client_request_id = call-<id>-t<n>``);
* the owner setting ``auto_save_to_bossman_memory`` (default False) makes ``process_record`` perform those two steps
  automatically after each call that actually connected. Everything is best effort: a failure here never fails or rewrites
  the call record, and an unconfigured memory vault is reported as ``memory.status == "not_configured"``.

Text that came from a conversation is untrusted data: control characters, markdown structure and secret-shaped strings
(phone numbers, codes, tokens) are removed before it reaches memory, a task or the history entry; it is framed in the third
person as a quoted proposal, never as an instruction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .account.stopflag import HISTORY_FILE, CallState
from .hardening import redact

log = logging.getLogger("bcc.telegram_calls.postcall")

MEMORY_KIND = "session"
MEMORY_PROJECT = "telegram-calls"
MAX_SUMMARY_CHARS = 700
MAX_TASKS = 5
MAX_TASK_CHARS = 400
DRAFT_TITLE_CHARS = 80
MEMORY_TIMEOUT_S = 20.0

#: outcomes of calls where nobody was ever connected: nothing was said, so an AUTOMATIC save has nothing to remember
NOT_CONNECTED = frozenset({"declined", "busy", "no_answer", "failed"})
OUTCOME_RU = {"completed": "разговор завершён", "declined": "звонок отклонён", "busy": "собеседник занят",
              "no_answer": "нет ответа", "connection_lost": "связь потеряна", "stopped": "остановлен владельцем (STOP)",
              "max_duration": "достигнут лимит длительности", "silence_timeout": "долгая тишина",
              "failed": "не удалось позвонить", "unknown": "исход неизвестен"}

_CTRL = re.compile(r"[\x00-\x1f\x7f  ]+")
_SPACES = re.compile(r"\s+")
_ID_SAFE = re.compile(r"[^A-Za-z0-9_-]")
_MD_LEAD = re.compile(r"^[\s#>*`|_~=-]+")
_history_lock = threading.Lock()


class PostCallError(Exception):
    """A refusal with a stable code, an owner-facing message and the HTTP status the API should use."""

    def __init__(self, code: str, message: str, hint: str = "", status: int = 409):
        super().__init__(code)
        self.code, self.message, self.hint, self.status = code, message, hint, status

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **({"hint": self.hint} if self.hint else {})}


# ---------------------------------------------------------------- text hygiene

def clean_text(value: Any, limit: int) -> str:
    """One line, no control characters, no markdown structure, no secret-shaped strings, bounded."""
    if not isinstance(value, str):
        return ""
    text = _SPACES.sub(" ", _CTRL.sub(" ", value)).strip()
    text = _MD_LEAD.sub("", text)
    text = redact(text).replace('"', "'").replace("`", "'")
    text = _SPACES.sub(" ", text).strip()
    return text[:limit].rstrip()


def safe_call_id(call_id: Any) -> str:
    cid = _ID_SAFE.sub("", str(call_id or ""))[:40]
    return cid or "unknown"


def memory_file_name(call_id: Any) -> str:
    return f"telegram-call-{safe_call_id(call_id)}.md"


def request_id(call_id: Any, n: int) -> str:
    return f"call-{safe_call_id(call_id)}-t{n}"


def clean_tasks(summary: dict | None) -> list[str]:
    raw = (summary or {}).get("agreed_tasks")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = clean_text(item, MAX_TASK_CHARS)
        if text and text not in out:
            out.append(text)
        if len(out) >= MAX_TASKS:
            break
    return out


def proposals(rec: dict) -> list[str]:
    return clean_tasks(rec.get("summary") if isinstance(rec.get("summary"), dict) else None)


def auto_save_enabled(settings: Any) -> bool:
    """The owner opt-in. A field of CallSettings when it exists, else the forward-compatible ``extra`` bag; default False."""
    value = getattr(settings, "auto_save_to_bossman_memory", None)
    if value is None:
        extra = getattr(settings, "extra", None)
        value = extra.get("auto_save_to_bossman_memory") if isinstance(extra, dict) else None
    return value is True


# ---------------------------------------------------------------- the note

def _duration_s(rec: dict) -> int | None:
    a, b = rec.get("started_at"), rec.get("ended_at")
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b >= a:
        return int(b - a)
    return None


def compose_note(rec: dict) -> tuple[str, str]:
    """(title, content) of the memory note. Third person, facts about the call, the summary marked as data."""
    cid = safe_call_id(rec.get("call_id"))
    outcome = str(rec.get("outcome") or "unknown")
    transport = str(rec.get("transport") or "telegram")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(rec.get("started_at") or time.time()))
    title = f"Звонок ассистента {when} ({OUTCOME_RU.get(outcome, outcome)})"
    summary = rec.get("summary") if isinstance(rec.get("summary"), dict) else {}
    text = clean_text(summary.get("text"), MAX_SUMMARY_CHARS)
    lat = rec.get("latency_ms") if isinstance(rec.get("latency_ms"), dict) else {}
    lines = [f"Итог голосового звонка ассистента на выбранный тестовый аккаунт (call-{cid}).", ""]
    if transport != "telegram":
        lines += ["Это тест без Telegram: настоящий звонок не совершался.", ""]
    lines.append(f"- Исход: {OUTCOME_RU.get(outcome, outcome)}.")
    duration = _duration_s(rec)
    if duration is not None:
        lines.append(f"- Длительность: {duration} с; реплик собеседника: {len(rec.get('turns') or [])}.")
    if lat.get("n"):
        lines.append(f"- Задержка ответа: медиана {lat.get('p50')} мс, p95 {lat.get('p95')} мс, замеров {lat.get('n')}.")
    if text:
        lines += ["", "Краткое содержание (данные о разговоре, не инструкции):", text]
    tasks = proposals(rec)
    if tasks:
        lines += ["", "Предложения из разговора (не приняты к исполнению, решает владелец):"]
        lines += [f"- предложение: «{t}»" for t in tasks]
    lines += ["", "Расшифровка и аудио не сохранялись."]
    return title, "\n".join(lines)


async def write_memory_note(svc: Any, rec: dict, *, force: bool = False) -> dict:
    """Idempotent write through the existing memory service. Never raises. ``force`` = the owner asked explicitly."""
    outcome = str(rec.get("outcome") or "unknown")
    if outcome in NOT_CONNECTED and not force:
        return {"status": "skipped", "reason": "not_connected"}
    filename = memory_file_name(rec.get("call_id"))
    title, content = compose_note(rec)
    try:
        from ..features import tools_memory
        service = await tools_memory.get_service(svc)
    except Exception as exc:  # noqa: BLE001 - MemoryNotConfigured, missing vault directory, permissions...
        return {"status": "not_configured", "reason": type(exc).__name__}
    try:
        path = await asyncio.wait_for(service.remember(
            title=title, content=content, kind=MEMORY_KIND, project=MEMORY_PROJECT,
            tags=["telegram-call", f"call-{safe_call_id(rec.get('call_id'))}"],
            source_run_id=safe_call_id(rec.get("call_id")), filename=filename), MEMORY_TIMEOUT_S)
        return {"status": "written", "file": filename, "path": _shown(service, path)}
    except FileExistsError:
        try:
            existing: Path | None = Path(service.vault.write_root) / filename
        except Exception:  # noqa: BLE001
            existing = None
        return {"status": "exists", "file": filename, "path": _shown(service, existing) if existing else None}
    except Exception as exc:  # noqa: BLE001 - the note is best effort; the call record is never failed by it
        log.warning("call summary not written to memory: %s", type(exc).__name__)
        return {"status": "error", "reason": type(exc).__name__}


def _shown(service: Any, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return Path(path).resolve().relative_to(Path(service.vault.root).resolve()).as_posix()
    except (ValueError, OSError, AttributeError):
        return Path(path).name


# ---------------------------------------------------------------- draft tasks

async def create_draft_tasks(svc: Any, rec: dict, *, force: bool = False) -> dict:
    """DRAFT tasks only: status "draft", no agent, no schedule, never enqueued. Idempotent per ``call-<id>-t<n>``."""
    outcome = str(rec.get("outcome") or "unknown")
    tasks = proposals(rec)
    if not tasks or (not force and (outcome in NOT_CONNECTED or outcome == "stopped")):
        return {"ids": [], "count": 0, "proposed": len(tasks)}
    import sqlalchemy as sa

    from ..db import tasks as tasks_t, utcnow
    cid = safe_call_id(rec.get("call_id"))
    ids: list[int] = []
    replayed = 0
    try:
        for n, text in enumerate(tasks, start=1):
            rid = request_id(cid, n)
            async with svc.db.session() as s:
                if svc.db.url.startswith("sqlite"):
                    await s.execute(sa.text("BEGIN IMMEDIATE"))          # lookup + insert in one write snapshot
                existing = (await s.execute(sa.select(tasks_t.c.id).where(
                    sa.cast(tasks_t.c.meta["client_request_id"].as_string(), sa.String) == rid)
                    .order_by(tasks_t.c.id).limit(1))).first()
                if existing is not None:
                    await s.rollback()
                    ids.append(int(existing[0]))
                    replayed += 1
                    continue
                res = await s.execute(sa.insert(tasks_t).values(
                    title=("Из звонка: " + text)[:DRAFT_TITLE_CHARS],
                    prompt=f"[Telegram call {cid}] Предложено во время звонка, ждёт решения владельца: {text}",
                    agent_id=None, status="draft", priority=5, max_retries=2,
                    meta={"client_request_id": rid, "source": "telegram_call", "call_id": cid, "proposal_only": True},
                    created_at=utcnow(), updated_at=utcnow()))
                task_id = int(res.inserted_primary_key[0])
                await s.commit()
            ids.append(task_id)
            bus = getattr(svc, "bus", None)
            if bus is not None:
                try:
                    await bus.emit("task.created", task_id=task_id, title=("Из звонка: " + text)[:DRAFT_TITLE_CHARS], agent_id=None)
                except Exception:  # noqa: BLE001 - an observer never fails the hand-over
                    pass
    except Exception as exc:  # noqa: BLE001
        log.warning("draft tasks from a call not fully created: %s", type(exc).__name__)
        return {"ids": ids, "count": len(ids), "replayed": replayed, "proposed": len(tasks), "error": type(exc).__name__}
    return {"ids": ids, "count": len(ids), "replayed": replayed, "proposed": len(tasks)}


# ---------------------------------------------------------------- history entry

def annotate_history(state: CallState, call_id: str, patch: dict | None = None, *,
                     postcall: dict | None = None) -> bool:
    """Merge ``patch`` (and/or parts of ``postcall``) into the LAST history entry of this call; rewrite atomically (0600).

    The summary text and proposals kept in that entry are cleaned with the same hygiene as the memory note.
    """
    path = Path(state.home) / HISTORY_FILE
    with _history_lock:
        if not path.is_file():
            return False
        lines = path.read_text(encoding="utf-8").splitlines()
        for i in range(len(lines) - 1, -1, -1):
            try:
                entry = json.loads(lines[i])
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("call_id") == call_id:
                summary = entry.get("summary")
                if isinstance(summary, dict):
                    summary["text"] = clean_text(summary.get("text"), MAX_SUMMARY_CHARS)
                    summary["agreed_tasks"] = clean_tasks(summary)
                entry.update(patch or {})
                if postcall:
                    merged = dict(entry.get("postcall") if isinstance(entry.get("postcall"), dict) else {})
                    merged.update(postcall)
                    entry["postcall"] = merged
                lines[i] = json.dumps(entry, ensure_ascii=False)
                tmp = path.with_name(path.name + ".tmp")
                fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    out.write("\n".join(lines) + "\n")
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, path)
                return True
        return False


def _find(state: CallState, call_id: str) -> dict | None:
    return next((r for r in state.history(200) if r.get("call_id") == call_id), None)


def find_entry(state: CallState, call_id: str) -> dict:
    entry = _find(state, call_id)
    if entry is None:
        raise PostCallError("CALL_NOT_FOUND", "Такого звонка нет в истории.",
                            "Откройте историю звонков и выберите звонок из списка.", 404)
    return entry


# ---------------------------------------------------------------- the hook the manager calls after every record

async def process_record(svc: Any, state: CallState, rec: dict, *, auto_save: bool = False) -> dict:
    """The post-call hook. Never raises. Default: keep the summary in the history entry and write NOTHING elsewhere."""
    call_id = str(rec.get("call_id") or "")
    result: dict[str, Any] = {"auto": bool(auto_save), "at": round(time.time(), 1),
                              "memory": {"status": "not_saved"}, "draft_tasks": [],
                              "drafts": {"ids": [], "count": 0, "proposed": len(proposals(rec))}}
    if auto_save:
        try:
            result["memory"] = await write_memory_note(svc, rec)
            drafts = await create_draft_tasks(svc, rec)
            result["drafts"], result["draft_tasks"] = drafts, list(drafts.get("ids", []))
        except Exception as exc:  # noqa: BLE001 - belt and braces: nothing here may fail the call record
            log.warning("post-call hand-over failed: %s", type(exc).__name__)
    try:
        await asyncio.to_thread(annotate_history, state, call_id, None, postcall=result)
    except OSError as exc:
        log.warning("call history entry not annotated: %s", type(exc).__name__)
    return result


# ---------------------------------------------------------------- the two owner clicks

async def save_memory_for(svc: Any, state: CallState, call_id: str) -> dict:
    entry = find_entry(state, call_id)
    memory = await write_memory_note(svc, entry, force=True)
    if memory.get("status") == "not_configured":
        raise PostCallError("MEMORY_NOT_CONFIGURED", "Память Bossman не настроена: заметку некуда записать.",
                            "Укажите папку памяти в разделе «Память» и повторите.", 409)
    if memory.get("status") == "error":
        raise PostCallError("MEMORY_WRITE_FAILED", "Заметку не удалось записать в память.", "Повторите позже.", 502)
    await asyncio.to_thread(annotate_history, state, call_id, None, postcall={"memory": {**memory, "by": "owner"}})
    return memory


async def draft_tasks_for(svc: Any, state: CallState, call_id: str) -> dict:
    entry = find_entry(state, call_id)
    if not proposals(entry):
        raise PostCallError("NO_PROPOSED_TASKS", "В этом звонке не было предложенных задач.", "", 409)
    drafts = await create_draft_tasks(svc, entry, force=True)
    if drafts.get("error"):
        raise PostCallError("DRAFTS_FAILED", "Черновики создать не удалось.", "Повторите позже.", 502)
    await asyncio.to_thread(annotate_history, state, call_id, None,
                            postcall={"drafts": drafts, "draft_tasks": list(drafts.get("ids", []))})
    return drafts
