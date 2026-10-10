"""Jeff 2.0 module ``longtask`` (order 85): long tasks a participant asks for EXPLICITLY, resumed after a restart.

The state machine is the existing Jeff 1.5 store (``bcc/pit/tasks.py``: one JSON per task, fsynced, idempotency key
per external action, STARTED persisted before the action, UNKNOWN_OUTCOME instead of a blind repeat). This module only
creates tasks from a conversation, runs them in the background and resumes them when Jeff starts again.

Conversation contract (Russian, plain):

* «поставь задачу: <цель>» / «создай длинную задачу — <цель>» creates ONE task with two steps:
  ``work`` (Jeff's own model works on the goal; no external effect; the text is kept next to the task file) and
  ``deliver`` (the result is sent to the participant once). Nothing else ever creates a task;
* «мои задачи» lists them with their state; «результат задачи 2» shows a finished result again;
  «отмени задачу 2» stops one; «удали мои задачи» removes every task file of this participant;
* consent: a task is written to disk, so it needs memory on (``/resume_memory``); a revoked participant has none;
* after a restart PLANNED/RUNNING tasks continue from the last safe point; a delivery that was in flight when the
  process died is NOT repeated (UNKNOWN_OUTCOME): the result stays available through «результат задачи N».

Limits: at most ``MAX_OPEN`` unfinished tasks per participant, one task worked on at a time (local model), a work
step is bounded by ``WORK_TIMEOUT_S``. Status and logs carry counts only, never goals or results.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..secret_filter import redact_secrets
from ..tasks import TERMINAL, ActionNotPerformed, TaskError, TaskStore
from ..vault import _atomic_json
from .contract import Advice, BaseModule, TurnContext

log = logging.getLogger("bcc.pit.j2.longtask")

MAX_OPEN = 3
WORK_TIMEOUT_S = 600.0
RESUME_INTERVAL_S = 60.0
RESULT_CHARS = 3500
GOAL_MIN = 3
_PERSON = re.compile(r"^[0-9a-f]{64}$")

_CREATE = re.compile(r"^\W*(?:пожалуйста\W+)?(?:поставь|создай|заведи|запусти)\s+(?:мне\s+)?"
                     r"(?:длинную\s+|долгую\s+|фоновую\s+|большую\s+)?задачу\s*[:\-—]\s*(?P<goal>.+)$", re.I | re.S)
_LIST = re.compile(r"^(?:мои\s+задачи|покажи\s+(?:мои\s+)?задачи|статус\s+задач\w*|список\s+задач)\W*$", re.I)
_RESULT = re.compile(r"^(?:покажи\s+)?(?:результат|итог)\s+задачи\s+(?:№\s*)?(\d{1,2})\W*$", re.I)
_CANCEL = re.compile(r"^(?:отмени|останови)\s+задачу\s+(?:№\s*)?(\d{1,2})\W*$", re.I)
_FORGET = re.compile(r"^(?:удали|сотри|забудь)\s+(?:все\s+)?мои\s+задачи\W*$", re.I)

STATE_RU = {"PLANNED": "в очереди", "RUNNING": "в работе", "WAITING_INPUT": "ждёт ответа",
            "WAITING_APPROVAL": "ждёт подтверждения", "DONE": "готово", "FAILED": "не выполнена",
            "UNKNOWN_OUTCOME": "результат готов, доставка не подтверждена"}

Drafter = Callable[[str, str], Awaitable[str]]          # (person_key, goal) -> result text
Sender = Callable[[str, str, str], Awaitable[Any]]      # proactive sender contract: True/"sent" | False/"retry" | "undeliverable"


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


class LongTaskModule(BaseModule):
    name = "longtask"
    version = "1"
    order = 85

    def __init__(self, store: TaskStore, *, drafter: Drafter, sender: Sender,
                 allowed: Callable[[str], bool] | None = None, interval: float = RESUME_INTERVAL_S,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep, work_timeout: float = WORK_TIMEOUT_S):
        self.store = store
        self.drafter, self.sender = drafter, sender
        self.allowed = allowed or (lambda person_key: True)
        self._interval, self._sleep, self._work_timeout = interval, sleep, work_timeout
        self._runs: dict[str, asyncio.Task] = {}
        self._gate = asyncio.Semaphore(1)
        self._loop_task: asyncio.Task | None = None
        self.counters = {"created": 0, "resumed": 0, "done": 0, "failed": 0, "unknown": 0}

    # -- files next to the task -------------------------------------------------------------------------------
    def _result_path(self, owner: str, task_id: str) -> Path:
        # NOT *.json: TaskStore.list() reads every *.json in the folder as a task record
        return self.store._dir(owner) / f"{task_id}.result"

    def result_text(self, owner: str, task_id: str) -> str | None:
        try:
            import json
            data = json.loads(self._result_path(owner, task_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        text = data.get("text") if isinstance(data, dict) else None
        return text if isinstance(text, str) else None

    # -- executors (bound to one task) --------------------------------------------------------------------------
    def _executors(self, owner: str, task: dict[str, Any]) -> tuple[dict, dict]:
        task_id, goal = task["id"], task["goal"]

        async def work(_key: str, _params: dict) -> str:
            try:
                text = await asyncio.wait_for(self.drafter(owner, goal), timeout=self._work_timeout)
            except (asyncio.CancelledError, ActionNotPerformed):
                raise
            except Exception as exc:                          # noqa: BLE001 - work has no external effect:
                raise ActionNotPerformed(type(exc).__name__) from None   # a failure is «not performed», never unknown
            clean = redact_secrets(str(text or "").strip())[0][:RESULT_CHARS]
            if not clean:
                raise ActionNotPerformed("empty result")      # nothing happened outside: safe to retry
            try:
                _atomic_json(self._result_path(owner, task_id), {"text": clean})
            except OSError:
                raise ActionNotPerformed("result not written") from None
            return f"chars:{len(clean)}"

        async def work_done(_key: str, _params: dict) -> bool:
            return self.result_text(owner, task_id) is not None   # no external effect: absent == safe to redo

        async def deliver(key: str, _params: dict) -> str:
            result = self.result_text(owner, task_id)
            if result is None:
                raise ActionNotPerformed("no result to deliver")
            raw = await self.sender(owner, f"Задача готова: «{goal[:120]}»\n\n{result}", key)
            if raw is True or raw == "sent":
                return "sent"
            if raw == "undeliverable":
                return "next_turn"                            # Jeff window: «результат задачи N» shows it
            raise ActionNotPerformed("not sent")             # certainly not delivered (sender said retry)

        return {"jeff_work": work, "jeff_deliver": deliver}, {"jeff_work": work_done}

    # -- running ------------------------------------------------------------------------------------------------
    def _schedule(self, owner: str, task_id: str) -> bool:
        run_id = f"{owner}/{task_id}"
        current = self._runs.get(run_id)
        if current is not None and not current.done():
            return False
        self._runs[run_id] = asyncio.get_running_loop().create_task(self._run(owner, task_id),
                                                                     name=f"jeff-longtask-{task_id}")
        return True

    async def _run(self, owner: str, task_id: str) -> None:
        async with self._gate:
            try:
                if not self.allowed(owner) or not self.switched_on():
                    return
                task = self.store.get(owner, task_id)
                if task["state"] in TERMINAL:
                    return
                executors, verifiers = self._executors(owner, task)
                final = await self.store.run(owner, task_id, executors, verifiers=verifiers)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                          # noqa: BLE001 - one task never stops Jeff
                log.warning("long task run failed: %s", type(exc).__name__)
                return
        state = final.get("state")
        if state == "DONE":
            self.counters["done"] += 1
        elif state == "FAILED":
            self.counters["failed"] += 1
        elif state == "UNKNOWN_OUTCOME":
            self.counters["unknown"] += 1

    def resume_all(self) -> int:
        """Schedule every PLANNED/RUNNING task of every participant (after a restart, or after a switch-on)."""
        n = 0
        try:
            rows = self.store.resumable()
        except Exception:                                     # noqa: BLE001
            return 0
        for task in rows:
            owner = str(task.get("owner") or "")
            if not _PERSON.fullmatch(owner) or task.get("state") not in ("PLANNED", "RUNNING"):
                continue
            if self.allowed(owner) and self._schedule(owner, task["id"]):
                n += 1
        self.counters["resumed"] += n
        return n

    async def start(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                if self.switched_on():
                    self.resume_all()
            except Exception as exc:                          # noqa: BLE001
                log.warning("long task resume failed: %s", type(exc).__name__)
            await self._sleep(self._interval)

    async def stop(self) -> None:
        tasks = [t for t in [self._loop_task, *self._runs.values()] if t is not None]
        self._loop_task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._runs.clear()

    # -- conversation ---------------------------------------------------------------------------------------------
    def _open(self, owner: str) -> list[dict[str, Any]]:
        return [t for t in self._listed(owner) if t["state"] not in TERMINAL]

    def _listed(self, owner: str) -> list[dict[str, Any]]:
        try:
            rows = self.store.list(owner)
        except TaskError:
            return []
        return sorted(rows, key=lambda t: (str(t.get("created_at", "")), int(t.get("created_ns") or 0), t.get("id", "")))

    async def pre_route(self, ctx: TurnContext) -> Advice | None:
        text = ctx.text.strip()
        if not text or len(text) > 2000 or text.startswith("/") or not _PERSON.fullmatch(ctx.person_key):
            return None
        norm = _norm(text)
        create = _CREATE.match(text)
        if create:
            return Advice(reply=self._create(ctx, create.group("goal")), tags=("longtask",))
        if _LIST.match(norm):
            return Advice(reply=self._listing(ctx.person_key), tags=("longtask",))
        m = _RESULT.match(norm)
        if m:
            return Advice(reply=self._result(ctx.person_key, int(m.group(1))), tags=("longtask",))
        m = _CANCEL.match(norm)
        if m:
            return Advice(reply=await self._cancel(ctx.person_key, int(m.group(1))), tags=("longtask",))
        if _FORGET.match(norm):
            return Advice(reply=await self._forget(ctx.person_key), tags=("longtask",))
        return None

    async def _stop_runs(self, run_ids: list[str]) -> None:
        """Cancel and WAIT: a run unwinding after the cancel must not write over the cancel or the deletion."""
        runs = [self._runs.pop(r) for r in run_ids if r in self._runs]
        for run in runs:
            run.cancel()
        if runs:
            await asyncio.gather(*runs, return_exceptions=True)

    def _create(self, ctx: TurnContext, goal: str) -> str:
        owner = ctx.person_key
        if not _PERSON.fullmatch(owner):
            return "Длинные задачи доступны только участникам Jeff."
        if not self.allowed(owner) or not ctx.memory_enabled:
            return ("Длинная задача хранится на диске до готовности, а память у тебя выключена. "
                    "Включи её (/resume_memory) — и поставь задачу снова.")
        goal = " ".join(str(goal).split())
        if len(goal) < GOAL_MIN:
            return "Напиши, что сделать: «поставь задачу: составить план поездки в Казань на 3 дня»."
        if redact_secrets(goal)[1]:
            return "В задаче похоже есть пароль или ключ. Я такое не сохраняю — убери секрет и поставь задачу снова."
        if len(self._open(owner)) >= MAX_OPEN:
            return f"У тебя уже {MAX_OPEN} незавершённые задачи. Дождись их или отмени: «мои задачи»."
        try:
            task = self.store.create(owner, goal[:500], [
                {"id": "work", "title": "Работа над задачей", "kind": "jeff_work"},
                {"id": "deliver", "title": "Отправить результат", "kind": "jeff_deliver"},
            ])
        except TaskError:
            return "Не получилось поставить задачу. Попробуй сформулировать короче."
        self.counters["created"] += 1
        try:
            self._schedule(owner, task["id"])
        except RuntimeError:                                  # no running loop (sync caller): the resume loop takes it
            pass
        n = len(self._listed(owner))
        return (f"Поставил задачу {n}: «{goal[:120]}». Работаю над ней в фоне и пришлю результат, когда будет готов — "
                "даже если я перезапущусь. Статус: «мои задачи».")

    def _listing(self, owner: str) -> str:
        rows = self._listed(owner)
        if not rows:
            return "Задач нет. Поставить: «поставь задачу: …»."
        lines = [f"{n}. {STATE_RU.get(t['state'], t['state'])} — {t['goal'][:80]}" for n, t in enumerate(rows[:20], 1)]
        return "Твои задачи:\n" + "\n".join(lines) + "\nРезультат: «результат задачи 1». Отменить: «отмени задачу 1»."

    def _nth(self, owner: str, n: int) -> dict[str, Any] | None:
        rows = self._listed(owner)
        return rows[n - 1] if 1 <= n <= len(rows) else None

    def _result(self, owner: str, n: int) -> str:
        task = self._nth(owner, n)
        if task is None:
            return "Нет задачи с таким номером. Список: «мои задачи»."
        text = self.result_text(owner, task["id"])
        if text is None:
            return f"Задача {n} ещё {STATE_RU.get(task['state'], 'в работе')}; результата пока нет."
        return f"Результат задачи {n} («{task['goal'][:80]}»):\n\n{text}"

    async def _cancel(self, owner: str, n: int) -> str:
        task = self._nth(owner, n)
        if task is None:
            return "Нет задачи с таким номером. Список: «мои задачи»."
        if task["state"] in TERMINAL:
            return f"Задача {n} уже завершена."
        await self._stop_runs([f"{owner}/{task['id']}"])
        try:
            self.store.cancel(owner, task["id"])
        except TaskError:
            return f"Задачу {n} сейчас нельзя отменить."
        return f"Отменил задачу {n}."

    async def _forget(self, owner: str) -> str:
        await self._stop_runs([k for k in self._runs if k.startswith(owner + "/")])
        try:
            folder = self.store._dir(owner)
        except TaskError:
            return "Задач нет."
        if not folder.is_dir():
            return "Задач нет."
        shutil.rmtree(folder, ignore_errors=True)
        return "Удалил все твои задачи и их результаты."

    def status(self) -> dict[str, Any]:
        try:
            summary = self.store.summary()
        except Exception:                                     # noqa: BLE001
            summary = {}
        return {"name": self.name, "version": self.version, "counters": dict(self.counters),
                "running": sum(1 for t in self._runs.values() if not t.done()),
                "loop": self._loop_task is not None and not self._loop_task.done(),
                "states": {k: v for k, v in summary.items() if v}}


# ------------------------------------------------------------------------------------------------- wiring
def _runtime_drafter(runtime: Any, resolve: Callable[[str], Any]) -> Drafter:
    async def draft(person_key: str, goal: str) -> str:
        person = resolve(person_key)
        if person is None:
            raise ActionNotPerformed("participant not reachable on this surface")
        consent = runtime.vault.consent(person_key)
        prompt = ("Это длинная задача, которую участник попросил выполнить. Сделай её полностью и по шагам, "
                  "дай готовый результат, без вопросов в конце.\n\nЗадача: " + goal)
        return await runtime._chat_route(person, person_key, prompt, consent, message_id="longtask",
                                         scan_crisis=False)
    return draft


def create(runtime: Any) -> LongTaskModule:
    home = getattr(runtime, "home", None)
    vault = getattr(runtime, "vault", None)
    if home is None or vault is None:
        raise ValueError("longtask needs a runtime with a PIT home and a persona vault")
    from .proactive import TelegramSender
    sender = TelegramSender(runtime)
    data_dir = getattr(vault, "data_dir", None)

    def allowed(person_key: str) -> bool:
        try:
            if not vault.consent(person_key).memory_enabled:
                return False
        except Exception:                                     # noqa: BLE001
            return False
        if data_dir is None:
            return True
        from .. import participant_profile
        return not participant_profile.is_revoked(data_dir, person_key)

    build = str(getattr(getattr(runtime, "settings", None), "build_sha", "") or "unknown")
    return LongTaskModule(TaskStore(Path(home), build_sha=build), drafter=_runtime_drafter(runtime, sender._resolve),
                          sender=sender, allowed=allowed)
