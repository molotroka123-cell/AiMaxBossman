"""The finished `photo.edit` picture arrives by itself in the chat it was asked in (owner 23.10).

Before: the chat agent queued the edit (tools_photo_edit → DirectGenService.create) and the owner went hunting for
the result in «Генерации». Now `photo.edit` records {job_id, task_id, thread_id} here, and when that Direct Gen job
ends this feature:
  1. appends a visible NOTE to the same chat thread (chat_threads.append_note: a turn with a negative task_id and
     kind=note — the mobile pult shows the picture from `/api/direct-gen/jobs/{id}/file` through its own
     authenticated `/m/api/photo/jobs/{id}/file`, the job id and the measured seconds), or the honest failure reason;
  2. sends the finished file to the Telegram pult through the SAME call as POST /direct-gen/jobs/{id}/send-to-pult
     (DirectGenService.send_to_pult: one owner recipient, QA gate, 49 MB limit, token never echoed). The owner's chat
     command is the owner's request for that ONE file; nothing else is ever pushed (jobs not registered here — the
     «Генерации» tab, Telegram's own generations — stay on-request only).

Robustness:
  * reacts to `direct_gen.job` events on the shared bus, and re-scans the registry at startup and every
    RESCAN_SECONDS (a restart, a dropped/lagging bus queue, or a job the service closed in `_recover` without an
    event are all delivered by the scan);
  * never twice: the registry entry goes pending → sending (persisted BEFORE the Telegram call) → delivered; an entry
    found in «sending» after a restart is NOT sent again (the thread note says so); the thread note is keyed by the
    job id, so a crash between note and registry write does not duplicate it either; one in-flight delivery per job;
  * owner STOP (`<data>/computer/STOP` or `<data>/autonomy/STOP`): nothing goes to Telegram; the thread note says so.

Registry: `<data_dir>/chat/photo-edit-deliveries.json` (job id → entry). Only ids and statuses — no prompt, no picture.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import Feature

log = logging.getLogger("bcc.photo_edit_delivery")

PARTICIPANT = "owner"
FINAL = frozenset({"completed", "failed", "cancelled", "stopped"})
RESCAN_SECONDS = 15.0
KEEP_DELIVERED = 500
VERSION = 1


class _State:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()           # registry read-modify-write
        self.inflight: set[str] = set()      # one delivery per job at a time


def _state(svc: Any) -> _State:
    st = getattr(svc, "_photo_edit_delivery", None)
    if st is None:
        st = svc._photo_edit_delivery = _State()
    return st


def registry_path(svc: Any) -> Path:
    return Path(svc.settings.data_dir) / "chat" / "photo-edit-deliveries.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_sync(path: Path) -> dict[str, dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("photo_edit_delivery: registry unreadable, left as is: %s", path.name)
        raise
    items = raw.get("deliveries") if isinstance(raw, dict) else None
    return {k: v for k, v in (items or {}).items() if isinstance(k, str) and isinstance(v, dict)}


def _write_sync(path: Path, items: dict[str, dict]) -> None:
    from .chat_threads import _write_json_atomic
    done = sorted((k for k, v in items.items() if v.get("state") == "delivered"),
                  key=lambda k: str(items[k].get("delivered_at") or ""))
    for k in done[:-KEEP_DELIVERED] if len(done) > KEEP_DELIVERED else []:
        items.pop(k, None)
    _write_json_atomic(path, {"version": VERSION, "deliveries": items})


async def _load(svc: Any) -> dict[str, dict]:
    async with _state(svc).lock:
        try:
            return await asyncio.to_thread(_read_sync, registry_path(svc))
        except (OSError, ValueError):
            return {}


async def _mutate(svc: Any, fn) -> Any:
    path = registry_path(svc)
    async with _state(svc).lock:
        def run():
            items = _read_sync(path)          # unreadable -> raises: never overwrite what we could not read
            out = fn(items)
            _write_sync(path, items)
            return out
        return await asyncio.to_thread(run)


async def register(svc: Any, job_id: str, *, task_id: int | None, thread_id: str | None) -> None:
    """photo.edit queued `job_id` for the chat turn of `task_id` (in `thread_id`, None = not a chat thread)."""
    def apply(items: dict) -> None:
        if job_id not in items:
            items[job_id] = {"job_id": job_id, "task_id": task_id, "thread_id": thread_id,
                             "state": "pending", "created_at": _now()}
    await _mutate(svc, apply)


def stop_active(svc: Any) -> bool:
    data = Path(svc.settings.data_dir)
    return (data / "computer" / "STOP").exists() or (data / "autonomy" / "STOP").exists()


def _job_reason(job: dict) -> str:
    err = job.get("error") or {}
    if isinstance(err, dict):
        return str(err.get("message") or err.get("reason") or err.get("code") or "").strip()
    return str(err).strip()


def build_note(job_id: str, job: dict | None, pult: dict) -> tuple[dict, str]:
    """(note for the thread, its one-line text)."""
    short = job_id[:8]
    note: dict[str, Any] = {"key": f"photo_edit:{job_id}", "type": "photo_edit", "job_id": job_id, "pult": pult}
    if job is None:
        note.update(status="missing", reason="задача Direct Gen удалена с ПК — картинки нет")
        return note, f"Правка фото #{short}: задача удалена с ПК — картинки нет."
    status = str(job.get("status") or "")
    elapsed = job.get("elapsed_s")
    note.update(status=status, model=job.get("model"), elapsed_s=elapsed)
    secs = f" за {float(elapsed):.1f} с" if isinstance(elapsed, (int, float)) and elapsed else ""
    if status == "completed":
        res = job.get("result") or {}
        note.update(file_url=f"/api/direct-gen/jobs/{job_id}/file", mime=res.get("mime"),
                    width=res.get("width"), height=res.get("height"))
        if pult.get("sent") is True:
            tail = "отправлена в Пульт"
        elif pult.get("sent") is None:
            tail = f"в Пульт: {pult.get('reason')}"
        else:
            tail = f"в Пульт не отправлена: {pult.get('reason')}"
        return note, f"Правка фото готова #{short}{secs} · {job.get('model')} · {tail}."
    reason = _job_reason(job) or {"cancelled": "остановлено", "stopped": "остановлено"}.get(status, "причина не указана")
    note["reason"] = reason
    word = "остановлена" if status in ("cancelled", "stopped") else "не удалась"
    return note, f"Правка фото #{short} {word}{secs}: {reason}"


async def _send(svc: Any, service: Any, job_id: str) -> dict:
    """The send-to-pult endpoint's own call. Errors are reported, never raised."""
    from ..direct_gen.service import DirectGenError
    try:
        out = await service.send_to_pult(PARTICIPANT, job_id)
    except DirectGenError as exc:
        return {"sent": False, "code": exc.code, "reason": exc.message}
    except Exception as exc:  # noqa: BLE001 — delivery must not die on an unexpected error
        return {"sent": False, "code": "error", "reason": f"ошибка отправки ({type(exc).__name__})"}
    return {"sent": True, "message_id": (out or {}).get("message_id")}


async def deliver(svc: Any, job_id: str) -> dict | None:
    """Deliver one registered job if it has ended. Returns the note written (None = nothing to do now)."""
    from ..direct_gen.service import DirectGenError
    from . import chat_threads as ct
    st = _state(svc)
    if job_id in st.inflight:
        return None
    st.inflight.add(job_id)
    try:
        entry = (await _load(svc)).get(job_id)
        if not entry or entry.get("state") == "delivered":
            return None
        service = getattr(svc, "direct_gen", None)
        if service is None:
            return None
        try:
            job: dict | None = service.get(PARTICIPANT, job_id)
        except DirectGenError as exc:
            if exc.status != 404:
                return None
            job = None                                     # deleted from the PC before delivery
        if job is not None and job.get("status") not in FINAL:
            return None
        pult: dict[str, Any] = {"sent": False, "code": "not_applicable", "reason": "картинки нет"}
        if job is not None and job.get("status") == "completed":
            if entry.get("state") == "sending":
                pult = {"sent": None, "code": "interrupted",
                        "reason": "отправка была прервана перезапуском — проверьте Пульт, повторно не отправляю"}
            elif stop_active(svc):
                pult = {"sent": False, "code": "owner_stop", "reason": "активен STOP владельца"}
            else:
                await _mutate(svc, lambda items: items.get(job_id, {}).update(state="sending", sending_at=_now()))
                pult = await _send(svc, service, job_id)
        note, text = build_note(job_id, job, pult)
        thread_id = entry.get("thread_id")
        thread_result: dict[str, Any] = {"thread_id": thread_id}
        if thread_id:
            try:
                thread_result["note_idx"] = await ct.append_note(svc, thread_id, note, text)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                thread_result["error"] = detail.get("code") or f"HTTP_{exc.status_code}"

        def finish(items: dict) -> None:
            e = items.get(job_id)
            if e is not None:
                e.update(state="delivered", delivered_at=_now(), status=note.get("status"), pult=pult,
                         thread=thread_result)
        await _mutate(svc, finish)
        with contextlib.suppress(Exception):
            await svc.bus.emit("photo_edit.delivered", job_id=job_id, thread_id=thread_id, status=note.get("status"),
                               pult_sent=pult.get("sent"), pult_code=pult.get("code"))
        return note
    except (OSError, ValueError) as exc:                   # registry unreadable / disk trouble: retried by the scan
        log.warning("photo_edit_delivery: %s for job %s", type(exc).__name__, job_id[:8])
        return None
    finally:
        st.inflight.discard(job_id)


async def rescan(svc: Any) -> int:
    """Deliver every registered job that has ended (startup, periodic). Returns how many notes were written."""
    pending = [k for k, v in (await _load(svc)).items() if v.get("state") != "delivered"]
    n = 0
    for job_id in pending:
        if await deliver(svc, job_id) is not None:
            n += 1
    return n


async def _consume(svc: Any) -> None:
    q = svc.bus.subscribe()
    tasks: set[asyncio.Task] = set()
    try:
        with contextlib.suppress(Exception):
            await rescan(svc)                              # after a restart: what ended while we were down
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=RESCAN_SECONDS)
            except asyncio.TimeoutError:
                if not svc.bus.is_subscribed(q):           # dropped as a lagging reader: subscribe again
                    q = svc.bus.subscribe()
                with contextlib.suppress(Exception):
                    await rescan(svc)
                continue
            if msg.get("kind") != "direct_gen.job" or msg.get("status") not in FINAL:
                continue
            job_id = str(msg.get("job_id") or "")
            if job_id:
                # its own task: a Telegram upload (up to minutes) must not stall the bus reader
                task = asyncio.create_task(_deliver_quiet(svc, job_id), name=f"photo-edit-deliver-{job_id[:8]}")
                tasks.add(task)
                task.add_done_callback(tasks.discard)
    except asyncio.CancelledError:
        for task in list(tasks):
            task.cancel()
        raise
    finally:
        svc.bus.unsubscribe(q)


async def _deliver_quiet(svc: Any, job_id: str) -> None:
    with contextlib.suppress(Exception):
        await deliver(svc, job_id)


async def _setup(svc: Any) -> None:
    task = asyncio.create_task(_consume(svc), name="bcc-photo-edit-delivery")
    if hasattr(svc, "_tasks"):                             # svc.stop() cancels the subscription
        svc._tasks.append(task)


FEATURE = Feature(name="photo_edit_delivery", router=None, setup=_setup)
