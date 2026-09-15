"""Task Engine (раздел 4): persistent-очередь в БД, lease/heartbeat, checkpoint, retries.

Никакого состояния в памяти: worker берёт run из БД, продлевает аренду, после
каждого шага пишет checkpoint. Падение процесса не теряет задачу — при старте
протухшие аренды возвращаются в очередь (attempt+1), продолжение идёт с checkpoint.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from . import run_provenance
from .db import (Database, agents as agents_t, approvals as approvals_t,
                 checkpoints as checkpoints_t, fetch_one, models as models_t,
                 run_events as run_events_t,
                 task_runs as runs_t, tasks as tasks_t, tool_calls as tool_calls_t, utcnow)
from . import approval_scope as _scope
from . import mission_budget as _budget
from .events import EventBus
from .plugin_security import redact as _ps_redact, redact_text as _ps_redact_text
from .providers import ChatResult, ProviderError
from .registry import Registry
from .tools import (REGISTRY as TOOLS, ToolContext, agent_policy_rules, allowed_tools_for,
                    approval_digest,
                    args_hash, decide_effect, execute_tool)

ACTIVE_RUN_STATUSES = ("queued", "leased", "running")
TERMINAL_TASK_STATUSES = ("completed", "failed", "stopped", "cancelled")

# Задача в этих статусах снята владельцем: решение по её подтверждению уже
# ничего не запускает, а вернуло бы её в очередь мёртвой (A7-02).
STOPPED_TASK_STATUSES = ("stopped", "cancelled")
STOP_DECIDER = "остановка задачи"

# P0-04: хуки безопасности fail-closed. Критичный хук (ревью/approval/Deep Fix
# gate, Resource Brain before_run, роутер pick_model) при исключении, таймауте
# или битом результате НЕ даёт задаче завершиться. Телеметрия (on_step,
# on_failure, after_run) деградирует мягко: событие hook.degraded и дальше.
CRITICAL_HOOK_NAMES = frozenset({"before_run", "gate_completion", "pick_model"})
GATE_VERDICTS = frozenset({"PASS", "FAIL", "NOT_APPLICABLE"})
_LEGACY_VERDICTS = {"pass": "PASS", "fail": "FAIL", "not_applicable": "NOT_APPLICABLE", "n/a": "NOT_APPLICABLE"}


def normalize_gate_verdict(raw: Any) -> str | None:
    """Типизированный вердикт критичного гейта: PASS | FAIL | NOT_APPLICABLE; иначе None."""
    if not isinstance(raw, str):
        return None
    v = raw.strip()
    v = _LEGACY_VERDICTS.get(v.lower(), v.upper())
    return v if v in GATE_VERDICTS else None
DEFAULT_HOOK_TIMEOUT_S = 60.0


class FencedOut(RuntimeError):
    """FL-01: у этого воркера устаревший fence — run уже перехвачен другим
    (recover после истечения аренды). Воркер обязан немедленно прекратить run
    без записи результата: ни receipt, ни checkpoint, ни статуса."""

    def __init__(self, run_id: int, fence: int | None):
        super().__init__(f"run {run_id}: fence {fence} устарел — run перехвачен другим воркером")
        self.run_id, self.fence = run_id, fence


class AmbiguousPriorEffect(RuntimeError):
    """A prior attempt dispatched this non-idempotent action and died before
    journaling its outcome. The effect may or may not have happened; only the
    owner may decide whether it runs again (Execution Truth §7/§9)."""

    def __init__(self, prior: dict):
        super().__init__(f"{prior.get('tool')}: dispatched by attempt of run {prior.get('run_id')} "
                         "before a crash; outcome never journaled")
        self.prior = prior


class TaskStateConflict(RuntimeError):
    """Owner/API lifecycle request conflicts with the persisted task state."""

    def __init__(self, task_id: int, action: str, status: str):
        self.task_id, self.action, self.status = int(task_id), str(action), str(status)
        super().__init__(f"task {task_id}: cannot {action} while status={status}")


class CriticalHookFailure(Exception):
    """Критичный хук упал/завис/вернул мусор — run не может считаться выполненным."""

    def __init__(self, name: str, hook: str, reason: str):
        self.name = name
        self.hook = hook
        self.reason = reason
        super().__init__(f"critical hook {name} failed: {hook}: {reason}")


def _hook_qualname(fn: Any) -> str:
    qual = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None)
    if qual is None:
        qual = type(fn).__qualname__
    mod = getattr(fn, "__module__", None)
    return f"{mod}.{qual}" if mod else str(qual)


class TaskEngine:
    def __init__(self, db: Database, bus: EventBus, registry: Registry, *,
                 lease_seconds: int = 90, heartbeat_seconds: int = 30,
                 poll_interval: float = 1.0, recover_every: float = 60.0,
                 retry_base_delay: float = 2.0, retry_max_delay: float = 300.0,
                 workers: int | None = None):
        self.db = db
        self.bus = bus
        self.registry = registry
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.poll_interval = poll_interval
        self.recover_every = recover_every
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay
        self.last_tick: float = 0.0          # для health в /api/system
        self.last_error: str | None = None
        # Worker Pool: до N run'ов параллельно (env BCC_WORKERS, default 3).
        # Resource Brain через before_run решает, сколько РЕАЛЬНО позволить.
        import os
        self.workers = workers if workers is not None else int(os.environ.get("BCC_WORKERS", "3"))
        self._active: dict[int, asyncio.Task] = {}       # run_id → задача исполнения
        self._cancelling: set[int] = set()               # hard cancel по Stop
        # FL-01: fence, под которым ЭТОТ движок держит run; условные записи
        # сравнивают с ним. Run без записи здесь (execute() напрямую) принимает
        # текущий fence из БД при старте.
        self._fences: dict[int, int] = {}
        self._held_since: dict[int, Any] = {}            # run_id → utcnow() на момент claim/execute
        self._fenced_out: set[int] = set()               # heartbeat обнаружил перехват
        # Хуки V2 (контракты §8): фичи регистрируют корутины в setup(); порядок вызова —
        # pick_model → before_run → on_step → gate_completion → on_failure → after_run.
        self.hooks: dict[str, list] = {k: [] for k in (
            "pick_model", "before_run", "on_step", "gate_completion",
            "on_failure", "after_run")}
        # критичность по id(fn): список self.hooks[...] остаётся списком корутин
        # (фичи/тесты могут его трогать напрямую), метаданные — отдельно.
        self._hook_critical: dict[int, bool] = {}
        # каждому вызову хука — свой таймаут (asyncio.wait_for); None = без лимита
        self.hook_timeout_s: float | None = DEFAULT_HOOK_TIMEOUT_S
        # Services проставляет себя после создания: инструментам нужен доступ к
        # approvals/vault/менеджерам браузера и терминала (V2.1).
        self.services: Any = None
        self.executors: dict[str, Any] = {}

    def register_executor(self, kind: str, handler: Any) -> None:
        """Host code only: deterministic jobs share admission, fencing and finalization."""
        if not kind or not callable(handler):
            raise ValueError("executor kind and callable required")
        self.executors[kind] = handler

    def add_hook(self, name: str, fn: Any, *, critical: bool | None = None) -> None:
        """Зарегистрировать хук. `critical=None` → по имени: before_run,
        gate_completion, pick_model критичны (fail-closed), остальные — телеметрия."""
        if name not in self.hooks:
            raise KeyError(f"нет такого хука: {name}")
        if critical is None:
            critical = name in CRITICAL_HOOK_NAMES
        self._hook_critical[id(fn)] = bool(critical)
        self.hooks[name].append(fn)

    def hook_is_critical(self, name: str, fn: Any) -> bool:
        return self._hook_critical.get(id(fn), name in CRITICAL_HOOK_NAMES)

    @staticmethod
    def _malformed_hook_result(name: str, res: Any) -> str | None:
        """Причина, если результат хука не по контракту; None — результат годный."""
        if name == "gate_completion":
            # Audit P0: критичный гейт обязан вернуть typed PASS/FAIL/NOT_APPLICABLE.
            # Молчаливый None или dict без verdict — не «мнения нет», а сбой гейта.
            if res is None:
                return "silent None from a critical gate (typed verdict required)"
            if not isinstance(res, dict):
                return f"malformed result: expected dict with verdict, got {type(res).__name__}"
            if "verdict" not in res:
                return "malformed result: gate dict without verdict"
            if normalize_gate_verdict(res["verdict"]) is None:
                return f"malformed verdict: {str(res['verdict'])[:40]!r} not in {sorted(GATE_VERDICTS)}"
            # EH-05 (TZ-01 §2.5): FAIL без явного `requeue` — не «по умолчанию повторить»,
            # а сбой гейта: гейт обязан сказать, возвращать ли run в очередь.
            if normalize_gate_verdict(res["verdict"]) == "FAIL" and "requeue" not in res:
                return "malformed result: FAIL verdict without explicit requeue"
            return None
        if name == "pick_model":
            if not res:
                return None
            raw = res.get("model_id") if isinstance(res, dict) else res
            try:
                int(raw)
            except (TypeError, ValueError):
                return f"malformed result: model_id {type(raw).__name__} is not an int"
        return None

    async def _call_hooks(self, name: str, *args: Any) -> list[Any]:
        """Вызвать хуки по порядку регистрации.

        Некритичный хук: исключение/таймаут/битый результат → событие
        hook.degraded, идём дальше (fail open, контракты §8 для телеметрии).
        Критичный хук: → событие hook.critical_failure и CriticalHookFailure —
        вызывающий код обязан НЕ завершать задачу как выполненную (P0-04).
        В событиях нет аргументов хука и промптов: только имя хука, функция и
        тип/короткая причина ошибки.
        """
        results: list[Any] = []
        timeout = self.hook_timeout_s
        for fn in list(self.hooks.get(name, ())):
            qual = _hook_qualname(fn)
            reason: str | None
            error_type = ""
            try:
                # V2 freeze exception (TRUTH-003 / FINDING V2-HOOK-CANCEL-01).
                # НЕ asyncio.wait_for: на Python 3.11 он исполняет хук в
                # ОТДЕЛЬНОЙ задаче и глотает отмену внешней задачи, если хук
                # завершился в том же шаге цикла (gh-86296). Ровно так работает
                # Governor: хук зовёт engine.stop(), тот отменяет текущий run —
                # и отмена терялась, а следующий за хуком retry-write перебивал
                # «stopped» на «queued» (бесконечный цикл, который Governor и
                # должен был прервать). asyncio.timeout исполняет хук в той же
                # задаче: отмена доставляется на первом же await после хука.
                coro = fn(*args)
                if timeout is not None:
                    async with asyncio.timeout(timeout):
                        res = await coro
                else:
                    res = await coro
            except asyncio.CancelledError:
                raise                       # Stop/shutdown — не ошибка хука
            except (asyncio.TimeoutError, TimeoutError):
                error_type = "TimeoutError"
                reason = f"timeout after {timeout}s"
            except Exception as exc:  # noqa: BLE001 — любой сбой хука обрабатывается тут
                error_type = type(exc).__name__
                reason = _ps_redact_text(f"{error_type}: {exc}")[:200]
            else:
                reason = self._malformed_hook_result(name, res)
                if reason is None:
                    results.append(res)
                    continue
                error_type = "MalformedResult"
            if self.hook_is_critical(name, fn):
                await self.bus.emit("hook.critical_failure", hook=name, fn=qual,
                                    error=error_type, reason=reason)
                raise CriticalHookFailure(name, qual, reason)
            await self.bus.emit("hook.degraded", hook=name, fn=qual,
                                error=error_type, reason=reason)
        return results

    async def _call_hooks_soft(self, name: str, *args: Any) -> list[Any]:
        """Хуки после терминального статуса (on_failure/after_run): статус уже
        зафиксирован, критичный сбой ничего не отменяет — событие уже отправлено."""
        try:
            return await self._call_hooks(name, *args)
        except CriticalHookFailure:
            return []

    # ---------- постановка в очередь ----------

    @staticmethod
    def _executor_block_reason(agent: dict | None) -> str | None:
        if agent is None:
            return "Исполнитель не выбран или недоступен. Выберите включённого агента для задачи."
        if not agent.get("enabled", True):
            return "Выбранный агент выключен. Включите его или выберите другого исполнителя."
        return None

    async def _executor_admission(self, task_id: int, *, run_id: int | None = None) -> bool:
        """Reject unavailable executors before creating/starting a run; never auto-escalate."""
        async with self.db.session() as s:
            task = await fetch_one(s, tasks_t, task_id)
            if task is None:
                raise ValueError("task not found")
            agent = await fetch_one(s, agents_t, task["agent_id"]) if task["agent_id"] else None
            reason = (None if agent is None and task["agent_id"] is None
                      and task.get("kind") in self.executors
                      else self._executor_block_reason(agent))
            meta = dict(task.get("meta") or {})
            if reason is None:
                if meta.get("reason_code") == "BLOCKED_CAPABILITY_UNAVAILABLE":
                    meta.pop("reason_code", None)
                    meta.pop("blocked_reason", None)
                    await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(meta=meta))
                    await s.commit()
                return True
            if run_id is not None:
                upd = await s.execute(sa.update(runs_t).where(
                    runs_t.c.id == run_id, self._fence_clause(run_id)).values(
                    status="blocked", error=reason, worker_lease_until=None, finished_at=utcnow()))
                if not upd.rowcount:
                    await s.rollback()
                    raise FencedOut(run_id, self._fences.get(run_id))
            meta.update(reason_code="BLOCKED_CAPABILITY_UNAVAILABLE", blocked_reason=reason)
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
                status="blocked", meta=meta, updated_at=utcnow()))
            await s.commit()
        await self.bus.emit("task.blocked", task_id=task_id, run_id=run_id,
                            code="BLOCKED_CAPABILITY_UNAVAILABLE", reason=reason)
        return False

    async def admission_result(self, task_id: int, run_id: int | None) -> dict:
        if run_id is not None:
            return {"ok": True, "status": "queued", "run_id": run_id}
        async with self.db.session() as s:
            task = await fetch_one(s, tasks_t, task_id)
        meta = (task or {}).get("meta") or {}
        return {"ok": False, "status": "blocked", "run_id": None,
                "code": meta.get("reason_code", "BLOCKED_CAPABILITY_UNAVAILABLE"),
                "reason": meta.get("blocked_reason", "Исполнитель недоступен")}

    async def enqueue(self, task_id: int, *, attempt: int = 0,
                      checkpoint: dict | None = None, only_if_draft: bool = False,
                      only_if_idle: bool = False) -> int | None:
        """Создать queued-run. `only_if_idle` is an atomic single-flight gate.

        API-side `active_run() -> enqueue()` was a TOCTOU: two clicks/clients could
        both observe no run and create two attempts.  The gate therefore lives
        beside the INSERT under the same task-row/SQLite write lock.
        """
        if not await self._executor_admission(task_id):
            return None
        async with self.db.session() as s:
            if only_if_draft or only_if_idle:
                if self.db.url.startswith("sqlite"):
                    await s.execute(sa.text("BEGIN IMMEDIATE"))
                query=sa.select(tasks_t.c.status).where(tasks_t.c.id==task_id)
                if not self.db.url.startswith("sqlite"):
                    query=query.with_for_update()
                status=(await s.execute(query)).scalar_one()
                existing=(await s.execute(sa.select(runs_t.c.id).where(
                    runs_t.c.task_id==task_id,
                    runs_t.c.status.in_(ACTIVE_RUN_STATUSES) if only_if_idle else sa.true())
                    .order_by(runs_t.c.id.desc()).limit(1))).scalar_one_or_none()
                if only_if_draft and (status!="draft" or existing is not None):
                    return int(existing or 0)
                if only_if_idle and existing is not None:
                    raise TaskStateConflict(task_id, "start another run", status)
            res = await s.execute(sa.insert(runs_t).values(
                task_id=task_id, attempt=attempt, status="queued", checkpoint=checkpoint))
            run_id = int(res.inserted_primary_key[0])
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
                status="queued", updated_at=utcnow()))
            await s.commit()
        await self.bus.emit("task.queued", task_id=task_id, run_id=run_id, attempt=attempt)
        return run_id

    async def active_run(self, task_id: int) -> dict | None:
        async with self.db.session() as s:
            res = await s.execute(sa.select(runs_t).where(
                runs_t.c.task_id == task_id,
                runs_t.c.status.in_(ACTIVE_RUN_STATUSES)).order_by(runs_t.c.id.desc()).limit(1))
            row = res.first()
        return dict(row._mapping) if row else None

    # ---------- управление задачей ----------

    async def stop(self, task_id: int) -> dict:
        """Hard Stop is sticky; historical terminal outcomes are immutable."""
        async with self.db.session() as s:
            if self.db.url.startswith("sqlite"):
                await s.execute(sa.text("BEGIN IMMEDIATE"))
            query = sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id)
            if not self.db.url.startswith("sqlite"):
                query = query.with_for_update()
            current = (await s.execute(query)).scalar_one_or_none()
            if current is None:
                raise ValueError("task not found")
            current = str(current)
            if current in ("stopped", "cancelled"):
                await s.commit()
                return {"ok": True, "status": "stopped"}
            if current in ("completed", "failed"):
                await s.rollback()
                raise TaskStateConflict(task_id, "stop", current)
            changed = await s.execute(sa.update(tasks_t).where(
                tasks_t.c.id == task_id, tasks_t.c.status == current).values(
                status="stopped", updated_at=utcnow()))
            if not changed.rowcount:
                await s.rollback()
                raise TaskStateConflict(task_id, "stop", await self._task_status(task_id))
            await s.execute(sa.update(runs_t).where(
                runs_t.c.task_id == task_id, runs_t.c.status == "queued").values(
                status="stopped", finished_at=utcnow()))
            res = await s.execute(sa.select(runs_t.c.id).where(
                runs_t.c.task_id == task_id,
                runs_t.c.status.in_(("leased", "running"))))
            active_ids = [int(r[0]) for r in res.fetchall()]
            await s.commit()
        await self._reject_parked_approvals(task_id)
        try:
            await self.bus.emit("task.stopped", task_id=task_id)
        finally:
            for run_id in active_ids:
                worker = self._active.get(run_id)
                if worker is not None and not worker.done():
                    self._cancelling.add(run_id)
                    worker.cancel()
        return {"ok": True, "status": "stopped"}

    async def pause(self, task_id: int) -> dict:
        async with self.db.session() as s:
            if self.db.url.startswith("sqlite"):
                await s.execute(sa.text("BEGIN IMMEDIATE"))
            query = sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id)
            if not self.db.url.startswith("sqlite"):
                query = query.with_for_update()
            current = (await s.execute(query)).scalar_one_or_none()
            if current is None:
                raise ValueError("task not found")
            current = str(current)
            if current == "paused":
                await s.commit()
                return {"ok": True, "status": "paused"}
            if current not in ("queued", "running", "waiting_approval"):
                await s.rollback()
                raise TaskStateConflict(task_id, "pause", current)
            await s.execute(sa.update(tasks_t).where(
                tasks_t.c.id == task_id, tasks_t.c.status == current).values(
                status="paused", updated_at=utcnow()))
            await s.commit()
        await self.bus.emit("task.paused", task_id=task_id)
        return {"ok": True, "status": "paused"}

    async def resume(self, task_id: int) -> dict:
        """Resume only an owner-paused task and never create parallel inference."""
        candidate = await self.active_run(task_id)
        if not await self._executor_admission(task_id, run_id=candidate["id"] if candidate else None):
            return await self.admission_result(task_id, None)
        async with self.db.session() as s:
            if self.db.url.startswith("sqlite"):
                await s.execute(sa.text("BEGIN IMMEDIATE"))
            query = sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id)
            if not self.db.url.startswith("sqlite"):
                query = query.with_for_update()
            current = (await s.execute(query)).scalar_one_or_none()
            if current is None:
                raise ValueError("task not found")
            current = str(current)
            if current != "paused":
                await s.rollback()
                raise TaskStateConflict(task_id, "resume", current)
            row = (await s.execute(sa.select(runs_t).where(
                runs_t.c.task_id == task_id,
                runs_t.c.status.in_(ACTIVE_RUN_STATUSES)).order_by(
                runs_t.c.id.desc()).limit(1))).first()
            run = dict(row._mapping) if row else None
            if run is not None and run["status"] in ("leased", "running"):
                await s.execute(sa.update(tasks_t).where(
                    tasks_t.c.id == task_id, tasks_t.c.status == "paused").values(
                    status="running", updated_at=utcnow()))
                await s.commit()
                run_id, result_status = int(run["id"]), "running"
            elif run is not None:
                await s.execute(sa.update(runs_t).where(
                    runs_t.c.id == run["id"], runs_t.c.status == "queued").values(
                    worker_lease_until=None))
                await s.execute(sa.update(tasks_t).where(
                    tasks_t.c.id == task_id, tasks_t.c.status == "paused").values(
                    status="queued", updated_at=utcnow()))
                await s.commit()
                run_id, result_status = int(run["id"]), "queued"
            else:
                last_row = (await s.execute(sa.select(runs_t).where(
                    runs_t.c.task_id == task_id).order_by(runs_t.c.id.desc()).limit(1))).first()
                last = dict(last_row._mapping) if last_row else {}
                inserted = await s.execute(sa.insert(runs_t).values(
                    task_id=task_id, attempt=int(last.get("attempt") or 0), status="queued",
                    checkpoint=last.get("checkpoint")))
                run_id = int(inserted.inserted_primary_key[0])
                await s.execute(sa.update(tasks_t).where(
                    tasks_t.c.id == task_id, tasks_t.c.status == "paused").values(
                    status="queued", updated_at=utcnow()))
                await s.commit()
                result_status = "queued"
        if result_status == "running":
            await self.bus.emit("task.progress", task_id=task_id, run_id=run_id,
                                resumed=True, status="running")
        else:
            await self.bus.emit("task.queued", task_id=task_id, run_id=run_id, resumed=True)
        return {"ok": True, "status": result_status, "run_id": run_id}

    async def retry(self, task_id: int) -> dict:
        """Explicit new attempt, but never alongside an existing active run."""
        run_id = await self.enqueue(task_id, attempt=0, only_if_idle=True)
        return await self.admission_result(task_id, run_id)

    async def last_run(self, task_id: int) -> dict | None:
        async with self.db.session() as s:
            res = await s.execute(sa.select(runs_t).where(runs_t.c.task_id == task_id)
                                  .order_by(runs_t.c.id.desc()).limit(1))
            row = res.first()
        return dict(row._mapping) if row else None

    # ---------- worker ----------

    @property
    def current_run_id(self) -> int | None:
        """Совместимость с health-эндпоинтом: первый из активных run'ов."""
        return next(iter(self.active_run_ids), None)

    @property
    def active_run_ids(self) -> list[int]:
        """Только реально выполняющиеся run'ы. Завершившиеся задачи убирает
        следующий виток worker_loop, поэтому фильтруем здесь: иначе занятость
        пула читалась бы завышенной сразу после окончания run'а."""
        return [rid for rid, task in self._active.items() if not task.done()]

    async def worker_loop(self) -> None:
        """Worker Pool: держит до self.workers параллельных run'ов.
        claim атомарен (status=queued→leased), поэтому один run не достанется двоим."""
        await self.recover()
        last_recover = time.monotonic()
        try:
            while True:
                self.last_tick = time.monotonic()
                try:
                    if time.monotonic() - last_recover >= self.recover_every:
                        await self.recover()
                        last_recover = time.monotonic()
                    # прибраться за завершившимися
                    for rid, t in list(self._active.items()):
                        if t.done():
                            self._active.pop(rid, None)
                            self._cancelling.discard(rid)
                    if len(self._active) >= self.workers:
                        self.last_error = None
                        await asyncio.sleep(self.poll_interval / 2)
                        continue
                    run_id = await self.claim()
                    self.last_error = None
                    if run_id is None:
                        await asyncio.sleep(self.poll_interval)
                        continue
                    self._active[run_id] = asyncio.create_task(
                        self._execute_pooled(run_id), name=f"bcc-run-{run_id}")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # worker не должен умирать от одной задачи
                    self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                    await self.bus.emit("worker.error", message=f"{type(exc).__name__}: {exc}")
                    await asyncio.sleep(self.poll_interval)
        finally:
            # выключение процесса: незавершённые run'ы отдаём lease-recovery.
            # ВАЖНО: не «cancel и забыли» — дожидаемся, пока каждая отменённая
            # задача отработает свой CancelledError и ОСВОБОДИТ коннект БД. Иначе
            # осиротевшая run/heartbeat-задача доходит до `await s.commit()` уже
            # на закрываемом пуле, и закрытие event loop виснет под Python 3.12
            # (см. docs/context/FABLE5_GENERAL_OPTIMIZATION_AUDIT.md).
            for t in self._active.values():
                t.cancel()
            if self._active:
                await asyncio.gather(*self._active.values(), return_exceptions=True)
            self._active.clear()

    async def aclose(self) -> None:
        """Отменить и ДОЖДАТЬСЯ все фоновые run/heartbeat-задачи движка.

        Вызывается из Services.stop СТРОГО до dispose пула БД. Порядок —
        суть фикса: сначала слить все задачи, держащие коннекты, потом закрывать
        пул. Работает и когда worker_loop не запускался (start_workers=False):
        задачи, порождённые ручным прогоном в тестах, тоже дренируются здесь.
        """
        tasks = list(self._active.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active.clear()

    async def _execute_pooled(self, run_id: int) -> None:
        """Исполнение в пуле: hard cancel по Stop завершает run как stopped
        с сохранением последнего checkpoint (сообщения уже в БД после каждого шага)."""
        try:
            await self.execute(run_id)
        except asyncio.CancelledError:
            # Отмена по Stop: опираемся на статус ЗАДАЧИ в БД (его ставит stop()),
            # а не на разделяемое множество — оно может быть очищено гонкой
            # worker_loop. Через повтор идут и ЧТЕНИЯ: отмена рвёт операцию
            # SQLite в полёте, соединение возвращается в пул мёртвым, и на него
            # приходится тот самый запрос, по которому мы решаем, что это была
            # остановка. Без повтора здесь разбор падал ещё до записи исхода.
            state: dict[str, Any] = {}

            async def read_state() -> None:
                async with self.db.session() as s:
                    state["run"] = await fetch_one(s, runs_t, run_id)
                found = state["run"]
                state["task_status"] = await self._task_status(found["task_id"]) if found else ""

            if not await self._retry_db(read_state):
                with contextlib.suppress(Exception):
                    await self.bus.emit(
                        "worker.error",
                        message=f"run {run_id}: не удалось прочитать состояние после отмены; "
                                f"строка останется «выполняется» до восстановления аренды")
                raise
            run = state["run"]
            if run and run["status"] in ("leased", "running"):
                if state["task_status"] == "stopped" or run_id in self._cancelling:
                    await self._finalize_stopped(run_id, run["task_id"])
                    return
            raise
        except Exception as exc:
            await self.bus.emit("worker.error",
                                message=f"run {run_id}: {type(exc).__name__}: {exc}")

    # Отмена рвёт операцию SQLite прямо в полёте, и соединение возвращается в
    # пул мёртвым: первый же следующий запрос получает "no active connection".
    # На этот запрос и приходится дозапись исхода, поэтому попытка не одна.
    # Окно повтора, а не число попыток: пул отдаёт живое соединение не с
    # определённой попытки, а спустя время, и на нагруженной машине оно больше.
    FINALIZE_DEADLINE = 5.0
    FINALIZE_PAUSE = 0.05

    async def _retry_db(self, write) -> bool:
        """Повторять запись, пока соединение не перестанет быть испорченным."""
        deadline = time.monotonic() + self.FINALIZE_DEADLINE
        pause = self.FINALIZE_PAUSE
        while True:
            try:
                await write()
                return True
            except asyncio.CancelledError:
                raise
            except SQLAlchemyError:
                if time.monotonic() >= deadline:
                    return False
                await asyncio.sleep(pause)
                pause = min(pause * 2, 0.5)

    async def _finalize_stopped(self, run_id: int, task_id: int) -> bool:
        """Дописать исход отменённого run'а, даже если отмена испортила соединение.

        Без повтора run навсегда оставался бы `running` без `finished_at`:
        задача показывает «остановлено», а прогон рядом с ней выглядит живым, и
        само это чинится только восстановлением аренды, то есть спустя минуты.

        Строку журнала повторяем так же, как и сам исход: она объясняет
        владельцу, ПОЧЕМУ прогон оборван. Молча проглотить её — значит оставить
        остановку без причины, что немногим лучше вечно живой строки. Каждая из
        двух записей повторяется до первого успеха, поэтому дубликатов нет.

        О неудаче говорим вслух: она видна в шине, а не только в интерфейсе.
        """
        logged = await self._retry_db(
            lambda: self._log(run_id, "warn", "run.stopped",
                              "остановлено оператором (hard cancel: активный вызов модели оборван)"))
        done = await self._retry_db(
            lambda: self._finish(run_id, task_id, "stopped", sync_task=False))
        if not done or not logged:
            with contextlib.suppress(Exception):
                await self.bus.emit(
                    "worker.error",
                    message=(f"run {run_id}: остановка записана не полностью "
                             f"(исход: {'да' if done else 'нет'}, причина в журнале: "
                             f"{'да' if logged else 'нет'})"))
        return done

    async def claim(self) -> int | None:
        """Взять run с наименьшим priority/id и поставить аренду на lease_seconds."""
        now = utcnow()
        async with self.db.session() as s:
            res = await s.execute(
                sa.select(runs_t.c.id)
                .join(tasks_t, tasks_t.c.id == runs_t.c.task_id)
                .where(runs_t.c.status == "queued",
                       tasks_t.c.status.in_(("queued", "running")),
                       sa.or_(runs_t.c.worker_lease_until.is_(None),
                              runs_t.c.worker_lease_until <= now))
                .order_by(tasks_t.c.priority.asc(), runs_t.c.id.asc()).limit(1))
            row = res.first()
            if row is None:
                return None
            run_id = int(row[0])
            upd = await s.execute(sa.update(runs_t).where(
                runs_t.c.id == run_id, runs_t.c.status == "queued").values(
                status="leased",
                fence=sa.func.coalesce(runs_t.c.fence, 0) + 1,
                worker_lease_until=now + timedelta(seconds=self.lease_seconds)))
            await s.commit()
            if not upd.rowcount:      # кто-то успел раньше (несколько процессов на одну БД)
                return None
            fence = (await s.execute(sa.select(runs_t.c.fence).where(runs_t.c.id == run_id))).scalar()
        self._fences[run_id] = int(fence or 0)
        self._held_since[run_id] = now
        return run_id

    async def recover(self) -> int:
        """Crash recovery: протухшие leased/running → queued (attempt+1) либо failed.
        Заодно подбираем approvals, решённые пока процесс был мёртв."""
        await self._resume_decided_approvals()
        now = utcnow()
        async with self.db.session() as s:
            res = await s.execute(
                sa.select(runs_t, tasks_t.c.max_retries, tasks_t.c.status.label("task_status"))
                .join(tasks_t, tasks_t.c.id == runs_t.c.task_id)
                .where(runs_t.c.status.in_(("leased", "running")),
                       runs_t.c.worker_lease_until.isnot(None),
                       runs_t.c.worker_lease_until <= now))
            stale = [dict(r._mapping) for r in res.fetchall()]
        recovered=0
        for run in stale:
            parent_status = str(run.get("task_status") or "")
            # Owner state outranks lease recovery. A crash in the short window
            # after Stop/Pause was previously enough to resurrect the task.
            if parent_status in STOPPED_TASK_STATUSES:
                async with self.db.session() as s:
                    changed = await s.execute(sa.update(runs_t).where(
                        runs_t.c.id == run["id"],
                        runs_t.c.status.in_(("leased", "running")),
                        sa.func.coalesce(runs_t.c.fence, 0) == int(run.get("fence") or 0)).values(
                        status="stopped", finished_at=now, worker_lease_until=None,
                        fence=sa.func.coalesce(runs_t.c.fence, 0) + 1))
                    await s.commit()
                if changed.rowcount:
                    await self._log(run["id"], "warn", "run.recovered_stopped",
                                    "lease expired after owner Stop; task was not resurrected")
                    recovered += 1
                continue
            if parent_status == "paused":
                async with self.db.session() as s:
                    changed = await s.execute(sa.update(runs_t).where(
                        runs_t.c.id == run["id"],
                        runs_t.c.status.in_(("leased", "running")),
                        sa.func.coalesce(runs_t.c.fence, 0) == int(run.get("fence") or 0)).values(
                        status="queued", worker_lease_until=None,
                        fence=sa.func.coalesce(runs_t.c.fence, 0) + 1))
                    await s.commit()
                if changed.rowcount:
                    await self._log(run["id"], "warn", "run.recovered_paused",
                                    "lease expired while paused; checkpoint remains parked")
                    recovered += 1
                continue
            if parent_status in ("completed", "failed"):
                async with self.db.session() as s:
                    changed = await s.execute(sa.update(runs_t).where(
                        runs_t.c.id == run["id"],
                        runs_t.c.status.in_(("leased", "running")),
                        sa.func.coalesce(runs_t.c.fence, 0) == int(run.get("fence") or 0)).values(
                        status="failed", finished_at=now, worker_lease_until=None,
                        error=f"parent task already terminal: {parent_status}",
                        fence=sa.func.coalesce(runs_t.c.fence, 0) + 1))
                    await s.commit()
                if changed.rowcount:
                    recovered += 1
                continue
            attempt = int(run["attempt"] or 0) + 1
            max_retries = int(run["max_retries"] or 0)
            still_stale=sa.and_(runs_t.c.status.in_(("leased","running")),
                runs_t.c.worker_lease_until<=now,
                sa.func.coalesce(runs_t.c.fence,0)==int(run.get("fence") or 0))
            if attempt <= max_retries:
                async with self.db.session() as s:
                    # FL-01: новый epoch — прежний держатель (если он ещё жив и
                    # просто «замёрз») больше не может ни писать, ни продлевать аренду.
                    changed=await s.execute(sa.update(runs_t).where(runs_t.c.id == run["id"],still_stale).values(
                        status="queued", attempt=attempt, worker_lease_until=None,
                        fence=sa.func.coalesce(runs_t.c.fence, 0) + 1))
                    if not changed.rowcount:continue
                    await s.execute(sa.update(tasks_t).where(tasks_t.c.id == run["task_id"]).values(
                        status="queued", updated_at=utcnow()))
                    await s.commit()
                await self._log(run["id"], "warn", "run.recovered",
                                f"аренда истекла, задача возвращена в очередь (попытка {attempt})")
                await self.bus.emit("task.queued", task_id=run["task_id"], run_id=run["id"],
                                    attempt=attempt, recovered=True)
            else:
                try:
                    await self._finish(run["id"],run["task_id"],"failed",_expected=still_stale,
                        fence=int(run.get("fence") or 0)+1,error="аренда истекла, попытки исчерпаны")
                except FencedOut:
                    continue
                await self._log(run["id"],"error","run.failed","аренда истекла, попытки исчерпаны")
            recovered+=1
        return recovered

    # ---------- выполнение ----------

    async def execute(self, run_id: int) -> None:
        if run_id not in self._fences:
            # прямой вызов (тесты/ручной прогон): держим run под его текущим fence
            async with self.db.session() as s:
                cur = (await s.execute(sa.select(runs_t.c.fence).where(runs_t.c.id == run_id))).scalar()
            self._fences[run_id] = int(cur or 0)
        self._held_since.setdefault(run_id, utcnow())
        self._fenced_out.discard(run_id)
        heartbeat = asyncio.create_task(self._heartbeat(run_id, asyncio.current_task()))
        from .trace import current_trace_id, run_trace_id
        trace_token = current_trace_id.set(run_trace_id(run_id))       # TRUTH-003 §14: один trace на run
        try:
            await self._run(run_id)
        except FencedOut as exc:
            await self._fenced_out_exit(run_id, str(exc))
        except asyncio.CancelledError:
            if run_id in self._fenced_out:
                await self._fenced_out_exit(run_id, "heartbeat: аренда перехвачена другим воркером")
                return
            raise
        finally:
            current_trace_id.reset(trace_token)
            self._fences.pop(run_id, None)
            self._held_since.pop(run_id, None)
            heartbeat.cancel()
            # Дожидаемся отмены heartbeat: он держит db-сессию в цикле
            # `sleep → s.execute → s.commit`; без await он переживает execute()
            # и виснет при закрытии пула на 3.12 (FABLE5 lifecycle audit).
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat

    async def _heartbeat(self, run_id: int, owner: asyncio.Task | None = None) -> None:
        """Продление аренды: пока worker жив, run не считается протухшим.

        FL-01: продление УСЛОВНО по fence. 0 обновлённых строк = run перехвачен
        (recover + claim другого воркера) → задача-владелец отменяется и выходит
        без записи результата (см. execute)."""
        try:
            while True:
                await asyncio.sleep(self.heartbeat_seconds)
                if not await self._heartbeat_once(run_id):
                    self._fenced_out.add(run_id)
                    if owner is not None and not owner.done():
                        owner.cancel()
                    return
        except asyncio.CancelledError:
            return

    async def _heartbeat_once(self, run_id: int) -> bool:
        """Одно условное продление аренды; False — fence устарел (0 строк)."""
        async with self.db.session() as s:
            upd = await s.execute(sa.update(runs_t).where(
                runs_t.c.id == run_id,
                runs_t.c.status.in_(("leased", "running")),
                self._fence_clause(run_id)).values(
                worker_lease_until=utcnow() + timedelta(seconds=self.lease_seconds)))
            if upd.rowcount:
                from .db import resource_reservations as reservations_t
                await s.execute(sa.update(reservations_t).where(
                    reservations_t.c.id.in_(sa.select(runs_t.c.reservation_id).where(runs_t.c.id == run_id)),
                    reservations_t.c.holder_kind == "video_job", reservations_t.c.status == "held"
                ).values(expires_at=utcnow() + timedelta(minutes=15)))
            await s.commit()
        return bool(upd.rowcount)

    # ---------- FL-01: fencing ----------

    def fence_of(self, run_id: int) -> int | None:
        """Fence, под которым этот движок держит run (None — run не наш)."""
        return self._fences.get(run_id)

    def _fence_clause(self, run_id: int):
        fence = self._fences.get(run_id)
        return sa.true() if fence is None else sa.func.coalesce(runs_t.c.fence, 0) == fence

    async def assert_fence(self, run_id: int) -> None:
        """Проверка ПЕРЕД внешним эффектом (TZ-05 §2.2 п.3): эффект не должен
        произойти, если run уже перехвачен. Run, который движок не держит, не
        проверяется (нечего сравнивать) — это путь V3-адаптера без claim."""
        fence = self._fences.get(run_id)
        if fence is None:
            return
        async with self.db.session() as s:
            cur = (await s.execute(sa.select(runs_t.c.fence).where(runs_t.c.id == run_id))).scalar()
        if int(cur or 0) != fence:
            raise FencedOut(run_id, fence)

    # Сколько раз выход зомби готов проглотить ЧУЖУЮ отмену, дожидаясь своей
    # диагностики. Отмена приходит не одна: владельца отменяет heartbeat, и на
    # Windows вторая доставка успевает попасть внутрь записи в БД.
    _DIAGNOSTIC_CANCELS = 5

    async def _fenced_out_exit(self, run_id: int, why: str) -> None:
        """Выход зомби-воркера: только журнал и событие, никаких записей в run.

        Диагностика пишется в ОТДЕЛЬНОЙ задаче, а не здесь. Причина конкретная:
        мы находимся внутри `except asyncio.CancelledError` — у этой задачи
        отмена уже в пути, и каждое следующее `await` может получить её снова.
        `contextlib.suppress(Exception)` от этого не спасал ВООБЩЕ:
        `CancelledError` наследуется от `BaseException`, а не от `Exception`,
        поэтому она пролетала сквозь подавление и уходила наружу из `execute()`
        — ровно то, что валило `windows paths` на записи `run.fenced_out`
        в aiosqlite.

        Своя задача отмены владельца не наследует, поэтому запись действительно
        доходит; наше ожидание её при этом может быть отменено ещё раз, и тогда
        мы ждём снова — ограниченное число раз, чтобы отсутствие отмены нельзя
        было спутать с зависанием.
        """
        async def diagnose() -> None:
            with contextlib.suppress(Exception):
                await self._log(run_id, "warn", "run.fenced_out", why[:500])
            with contextlib.suppress(Exception):
                await self.bus.emit("run.fenced_out", run_id=run_id,
                                    fence=self._fences.get(run_id), reason=why[:200])

        writer = asyncio.create_task(diagnose())
        for _ in range(self._DIAGNOSTIC_CANCELS):
            try:
                await asyncio.wait({writer})
                return
            except asyncio.CancelledError:
                continue          # наша отмена — не повод бросить диагностику
        # Бюджет исчерпан: задачу не бросаем (она допишет сама), но и висеть
        # здесь не имеем права. Молча это не проходит.
        with contextlib.suppress(Exception):
            self._log_sync_hint(run_id)

    @staticmethod
    def _log_sync_hint(run_id: int) -> None:
        import logging
        logging.getLogger(__name__).warning(
            "run %s: fenced-out diagnostics still pending after repeated cancellation", run_id)

    async def _run(self, run_id: int) -> None:
        async with self.db.session() as s:
            run = await fetch_one(s, runs_t, run_id)
            if run is None:
                return
            task = await fetch_one(s, tasks_t, run["task_id"])
            agent = await fetch_one(s, agents_t, task["agent_id"]) if task and task["agent_id"] else None
        if task is None:
            return
        executor = self.executors.get(task.get("kind"))
        if not await self._executor_admission(task["id"], run_id=run_id):
            return

        # before_run: Resource Brain может отложить ({"defer": сек, "reason"}) или
        # запретить ({"fail": причина}) запуск до старта выполнения
        try:
            before = await self._call_hooks("before_run", task, run)
        except CriticalHookFailure as exc:
            await self._fail_now(run_id, task["id"],
                                 f"critical hook before_run failed: {exc.hook}: {exc.reason}")
            return
        for res in before:
            if isinstance(res, dict) and res.get("defer"):
                delay = float(res["defer"])
                async with self.db.session() as s:
                    changed=await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id,self._fence_clause(run_id)).values(
                        status="queued",
                        worker_lease_until=utcnow() + timedelta(seconds=delay)))
                    if not changed.rowcount:return
                    await s.commit()
                await self._log(run_id, "warn", "run.deferred",
                                f"отложено на {delay:.0f} с: {res.get('reason', '')}")
                return
            if isinstance(res, dict) and res.get("fail"):
                await self._fail_now(run_id, task["id"], str(res["fail"]))
                return

        await self._start(run_id, task["id"])
        # §26: личность исполнителя снимается ЗДЕСЬ — после того как задача,
        # прогон и агент уже разрешены, и до того как что-либо выполнено. Это
        # единственная точка входа в выполнение, поэтому одного места хватает.
        if not await self._capture_provenance(run_id, task, run, agent):
            await self._fail_now(run_id, task["id"], "provenance_capture_failed: execution did not start")
            return
        await self._mark_interrupted_dispatches(run_id, task["id"])
        if executor is not None:
            try:
                await self.assert_fence(run_id)
                try:
                    answer = await executor(task, run, self)
                    if not isinstance(answer, str):
                        raise TypeError("executor must return a verified result string")
                except (asyncio.CancelledError, FencedOut):
                    raise
                except Exception as exc:
                    await self._fail_now(run_id, task["id"],
                                         f"executor failed: {type(exc).__name__}")
                    return
                await self._complete_run(run_id, task, answer, [], 0, 0, 0, 0.0, "local:deterministic")
                return
            finally:
                await self._release_executor_resources(run_id)
        checkpoint = run.get("checkpoint") or {}
        messages: list[dict] = list(checkpoint.get("messages") or [])
        if not messages:
            if agent.get("system_prompt"):
                messages.append({"role": "system", "content": agent["system_prompt"]})
            messages.append({"role": "user", "content": task["prompt"]})
        step = int(checkpoint.get("step") or 0)
        max_steps = max(1, int(agent.get("max_steps") or 1))
        tokens_in = int(run.get("tokens_in") or 0)
        tokens_out = int(run.get("tokens_out") or 0)
        cost = float(run.get("cost_usd") or 0.0)
        answer = ""
        alias = run.get("model_alias") or ""
        # §8: потолки прогона. Считаются один раз из task.meta — владелец задаёт
        # их на задачу, а не глобальной константой: сложная миссия законно
        # дороже правки документации.
        limits = _budget.Limits.for_task(task)
        # Ступень лестницы восстановления, выбранная прошлым сбоем: она живёт в
        # checkpoint'е, поэтому переживает рестарт вместе с остальным состоянием.
        recovery_model = checkpoint.get("recovery_model_id")
        recovery_model = int(recovery_model) if isinstance(recovery_model, int) else None
        recovery_degrade = checkpoint.get("recovery_degrade")
        if isinstance(recovery_degrade, dict) and recovery_degrade.get("tools") is False:
            # Упрощённый путь: без инструментов. Это СУЖЕНИЕ возможностей, а не
            # расширение прав — модель, которая не справилась с tool-calling,
            # получает более простую задачу, а не больше доступа.
            tool_schemas = None

        # V2.1: инструменты, выданные этому run'у. Пусто — поведение как в V2
        # (один вызов модели, без tools в payload).
        tool_specs = TOOLS.resolve(allowed_tools_for(task, agent))
        tool_schemas = [t.schema() for t in tool_specs] or None
        policy_rules = agent_policy_rules(agent)

        # Возобновление после подтверждения человеком: в checkpoint лежит
        # незавершённый вызов инструмента.
        pending = checkpoint.get("pending_tool_call")
        if pending:
            resumed = await self._resume_pending_tool(run_id, task, agent, messages,
                                                      pending, policy_rules)
            if resumed is False:
                return                      # решение ещё не принято — run ждёт
            checkpoint = dict(checkpoint)
            checkpoint.pop("pending_tool_call", None)

        while step < max_steps:
            if await self._check_interrupt(run_id, task["id"], messages, step):
                return
            if messages and messages[-1]["role"] == "assistant" \
                    and not messages[-1].get("tool_calls"):
                # финальный ответ уже есть (например, сохранён до паузы) — модель не дёргаем
                break
            try:
                result, model = await self._call_model(task, agent, messages, run_id,
                                                       tools=tool_schemas,
                                                       model_override=recovery_model)
            except ProviderError as exc:
                # `kind` carries what the adapter knew about the failure; the
                # text alone cannot always tell a network blip from a refusal.
                await self._handle_failure(run_id, task, str(exc), messages, step,
                                           kind=getattr(exc, "kind", None))
                return
            except LookupError as exc:
                await self._fail_now(run_id, task["id"], str(exc))
                return
            except CriticalHookFailure as exc:
                await self._fail_now(run_id, task["id"],
                                     f"critical hook pick_model failed: {exc.hook}: {exc.reason}")
                return

            step += 1
            alias = model.get("alias") or alias
            tokens_in += result.tokens_in
            tokens_out += result.tokens_out
            cost += _cost(model, result)
            breach = _budget.check_spend(limits, tokens_in=tokens_in,
                                         tokens_out=tokens_out, cost_usd=cost)
            if breach is not None:
                await self._budget_stop(run_id, task, messages, step, breach,
                                        tokens_in, tokens_out, cost, alias)
                return
            if result.cache_read_tokens or result.cache_write_tokens:
                # Prompt cache: только измерение провайдера (никаких «ожидаемых» экономий)
                await self._log(run_id, "info", "model.prompt_cache",
                                f"{alias}: cache_read={result.cache_read_tokens} "
                                f"cache_write={result.cache_write_tokens} "
                                f"hit={'yes' if result.cache_read_tokens else 'no'}")
            # PASS3: нормализованное наблюдение и для MISS/BYPASS/UNKNOWN (не только при read/write)
            try:
                obs = cache_observation_for(model, result, task_id=task["id"], run_id=run_id)
            except Exception:  # noqa: BLE001 — телеметрия не роняет run
                obs = None
            if obs is not None:
                await self.bus.emit("cache.observation", task_id=task["id"], run_id=run_id, **obs)

            if result.has_tool_calls:
                messages.append(_assistant_tool_message(result))
                await self._log(run_id, "info", "run.step",
                                f"шаг {step}/{max_steps}: {alias} запросил инструменты: "
                                + ", ".join(c.name for c in result.tool_calls))
                await self.bus.emit("task.progress", task_id=task["id"], run_id=run_id,
                                    step=step, max_steps=max_steps, model=alias,
                                    tool_calls=[c.name for c in result.tool_calls])
                waiting = await self._execute_tool_calls(
                    run_id, task, agent, messages, result.tool_calls, step,
                    policy_rules, tool_specs,
                    usage={"tokens_in": tokens_in, "tokens_out": tokens_out,
                           "cost_usd": round(cost, 6), "model_alias": alias})
                if waiting:
                    return                  # ждём человека: состояние в БД
                breach = await _budget.check_identical_calls(self.services, limits, run_id)
                if breach is not None:
                    await self._budget_stop(run_id, task, messages, step, breach,
                                            tokens_in, tokens_out, cost, alias)
                    return
                await self._save_checkpoint(run_id, messages, step, note="tools",
                                            tokens_in=tokens_in, tokens_out=tokens_out,
                                            cost_usd=round(cost, 6), model_alias=alias)
                cp_id = await self._insert_checkpoint(run_id, messages, step, note="tools")
                try:
                    await self._call_hooks("on_step", task, run_id,
                                           {"messages": messages, "step": step,
                                            "checkpoint_id": cp_id, "tools": True})
                except CriticalHookFailure as exc:
                    await self._fail_now(run_id, task["id"],
                                         f"critical hook on_step failed: {exc.hook}: {exc.reason}")
                    return
                continue                    # результаты инструментов → следующий шаг модели

            answer = result.text
            messages.append({"role": "assistant", "content": answer})
            breach = _budget.check_stalled_context(limits, messages)
            if breach is not None:
                await self._budget_stop(run_id, task, messages, step, breach,
                                        tokens_in, tokens_out, cost, alias)
                return
            await self._save_checkpoint(run_id, messages, step, note="answer",
                                        tokens_in=tokens_in, tokens_out=tokens_out,
                                        cost_usd=round(cost, 6), model_alias=alias)
            await self._log(run_id, "info", "run.step",
                            f"шаг {step}/{max_steps}: ответ модели {alias} "
                            f"({result.tokens_out} токенов)")
            await self.bus.emit("task.progress", task_id=task["id"], run_id=run_id,
                                step=step, max_steps=max_steps, model=alias)
            # каждый шаг — строка в checkpoints (история для Replay/Fork) + хук on_step
            cp_id = await self._insert_checkpoint(run_id, messages, step, note="answer")
            try:
                await self._call_hooks("on_step", task, run_id,
                                       {"messages": messages, "step": step,
                                        "checkpoint_id": cp_id})
            except CriticalHookFailure as exc:
                await self._fail_now(run_id, task["id"],
                                     f"critical hook on_step failed: {exc.hook}: {exc.reason}")
                return

        await self._complete_run(run_id, task, answer, messages, step,
                                 tokens_in, tokens_out, cost, alias)

    async def _park_for_owner(self, run_id: int, task: dict, messages: list[dict], step: int,
                              *, reason: str, instruction: str,
                              reason_code: str = "WAITING_FOR_OWNER_CHALLENGE") -> None:
        """The run needs the OWNER, not a retry and not a verdict: a human
        challenge (CAPTCHA, anti-bot, login wall) stands between the agent and
        the goal. Park exactly like an owner pause — run queued without a lease,
        checkpoint kept, task `paused` — and name the reason in `tasks.meta` so
        the UI shows what to do. Resume (task or browser session) re-queues the
        same run; the gate then judges the goal again. Never `completed`."""
        transcript = list(messages)
        if instruction:
            transcript.append({"role": "user", "content": instruction})
        async with self.db.session() as s:
            row = await fetch_one(s, tasks_t, int(task["id"]))
            meta = dict((row or {}).get("meta") or {})
            meta.update(reason_code=reason_code, blocked_reason=f"Нужно действие владельца: {reason}"[:500])
            changed = await s.execute(sa.update(runs_t).where(
                runs_t.c.id == run_id, self._fence_clause(run_id)).values(
                status="queued", worker_lease_until=None,
                checkpoint={"messages": transcript, "step": step, "note": "waiting_for_owner"}))
            if not changed.rowcount:
                return
            await s.execute(sa.update(tasks_t).where(
                tasks_t.c.id == task["id"], tasks_t.c.status.notin_(("stopped", "completed"))).values(
                status="paused", meta=meta, updated_at=utcnow()))
            await s.commit()
        await self._log(run_id, "warn", "run.waiting_for_owner", reason[:500])
        await self.bus.emit("task.paused", task_id=task["id"], run_id=run_id, step=step,
                            waiting_for_owner=True, code=reason_code, reason=reason[:300])

    async def _release_executor_resources(self, run_id):
        # A veto may leave a run waiting for review without calling _finish.
        # Release only our still-owned video's existing ledger reservation.
        from .db import resource_reservations as reservations_t
        async with self.db.session() as session:
            rid=(await session.execute(sa.select(runs_t.c.reservation_id).where(
                runs_t.c.id==run_id,self._fence_clause(run_id)))).scalar_one_or_none()
            if not rid:
                return
            held=(await session.execute(sa.select(reservations_t.c.id).where(
                reservations_t.c.id==rid,reservations_t.c.holder_kind=="video_job",
                reservations_t.c.status=="held"))).scalar_one_or_none()
        if held:
            from .features.resources import _release
            await _release(self.services,held)

    async def _complete_run(self, run_id, task, answer, messages, step,
                            tokens_in, tokens_out, cost, alias):
        """One completion path for both model and registered deterministic executors."""
        if await self._check_interrupt(run_id, task["id"], messages, step):
            return
        if not answer:
            answer = next((m["content"] for m in reversed(messages)
                           if m["role"] == "assistant"), "")
        # gate_completion (Reviewer Gate): задача не станет completed без PASS.
        # P0-04: упавший/зависший/битый gate — тоже НЕ completed: эскалация
        # человеку (waiting_approval + review_escalation), при сбое эскалации — failed.
        try:
            verdicts = await self._call_hooks("gate_completion", task, run_id, answer)
        except CriticalHookFailure as exc:
            await self._escalate_gate_failure(run_id, task, messages, step, exc)
            return
        try:
            await self.assert_fence(run_id)
        except FencedOut:
            return
        for res in verdicts:
            verdict = normalize_gate_verdict(res.get("verdict")) if isinstance(res, dict) else None
            if verdict is None:
                continue                      # недостижимо: _call_hooks уже отверг битый результат
            await self.bus.emit("evaluation.completed", task_id=task["id"], run_id=run_id,
                                verdict=verdict,
                                reasons=str(res.get("reasons") or "")[:500])
            if verdict != "FAIL":
                continue
            feedback = str(res.get("feedback") or res.get("reasons") or "ревью не пройдено")
            await self._log(run_id, "warn", "run.review_fail", feedback[:500])
            if res.get("requeue", True):
                # фидбек ревьюера — новым сообщением; run возвращается в очередь,
                # следующий шаг исправляет (лимит попыток ведёт сама фича-ревьюер)
                messages.append({"role": "user",
                                 "content": f"Ревью не пройдено. Исправь: {feedback}"})
                async with self.db.session() as s:
                    changed=await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id,self._fence_clause(run_id)).values(
                        status="queued", worker_lease_until=None,
                        checkpoint={"messages": messages, "step": step, "note": "review_fail"}))
                    if not changed.rowcount:return
                    await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(
                        status="queued", updated_at=utcnow()))
                    await s.commit()
                await self.bus.emit("task.queued", task_id=task["id"], run_id=run_id,
                                    review_retry=True)
            else:
                target_status = str(res.get("status") or "waiting_approval")
                if target_status in ("failed", "stopped"):
                    # Terminal gate veto must close BOTH projections. Previously
                    # task=failed was paired with run=queued forever.
                    await self._finish(run_id, task["id"], target_status, error=feedback,
                                       result=answer, tokens_in=tokens_in, tokens_out=tokens_out,
                                       cost_usd=round(cost, 6), model_alias=alias)
                elif target_status == "paused":
                    # A human challenge on the page: the owner acts, then Resume
                    # continues THIS run from its checkpoint with the gate's
                    # instruction as the next user turn, and the gate judges again.
                    await self._park_for_owner(run_id, task, messages, step,
                                               reason=str(res.get("reasons") or feedback),
                                               instruction=feedback)
                else:
                    async with self.db.session() as s:
                        changed=await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id,self._fence_clause(run_id)).values(
                            status="queued", worker_lease_until=None,
                            checkpoint={"messages": messages, "step": step,
                                        "note": "review_escalated"}))
                        if not changed.rowcount:return
                        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(
                            status=target_status, updated_at=utcnow()))
                        await s.commit()
                    await self.bus.emit("task.progress", task_id=task["id"], run_id=run_id,
                                        waiting_approval=(target_status == "waiting_approval"))
            return
        # EH-04: единственная точка финализации — bcc.lifecycle.finalize_task. Она
        # перепроверяет fence, объявленные эффекты свежим наблюдением и открытые
        # approval'ы; отказ = решение владельцу (как упавший гейт), не completed.
        from .finalize import finalize_task
        decision = await finalize_task(self, run_id, task["id"], answer=answer, verdicts=verdicts,
                                       usage={"tokens_in": tokens_in, "tokens_out": tokens_out,
                                              "cost_usd": round(cost, 6), "model_alias": alias})
        if not decision.ok:
            await self._log(run_id, "warn", "run.finalize_refused", decision.reason[:500])
            if decision.checks.get("failure_status") == "paused":
                await self._park_for_owner(
                    run_id, task, messages, step, reason=decision.reason,
                    instruction=("Владелец прошёл проверку на странице и нажал Resume. Продолжи "
                                 "задачу до её настоящей цели и не считай её выполненной, пока "
                                 "цель не достигнута."),
                    reason_code=str(decision.checks.get("reason_code") or "WAITING_FOR_OWNER"))
                return
            if decision.checks.get("failure_status") == "failed":
                # A missed classifier cannot turn a failed effect into success.
                # Keep the answer (including honest refusals), but do not park
                # this run behind an approval with no fulfillable obligation.
                await self._finish(run_id, task["id"], "failed", error=decision.reason,
                                   result=answer, tokens_in=tokens_in, tokens_out=tokens_out,
                                   cost_usd=round(cost, 6), model_alias=alias)
                return
            await self._escalate_gate_failure(run_id, task, messages, step,
                                              CriticalHookFailure("finalize", "bcc.finalize.finalize_task", decision.reason))
            return
        await self._log(run_id, "info", "run.completed", "задача выполнена")

    async def _call_model(self, task: dict, agent: dict, messages: list[dict],
                          run_id: int, *, tools: list[dict] | None = None,
                          model_override: int | None = None) -> tuple[ChatResult, dict]:
        from bossman_shared.privacy import execution_privacy
        with execution_privacy((task.get("meta") or {}).get("privacy", "public")):
            return await self._call_model_scoped(task, agent, messages, run_id, tools=tools,
                                                 model_override=model_override)

    async def _call_model_scoped(self, task: dict, agent: dict, messages: list[dict],
                          run_id: int, *, tools: list[dict] | None = None,
                          model_override: int | None = None) -> tuple[ChatResult, dict]:
        """Вызов модели: сначала pick_model-хук (Smart Router) может перекрыть выбор;
        при ошибке маршрута — модель агента; при её ошибке — fallback_model.
        `tools` — схемы ТОЛЬКО выданных этому run'у инструментов."""
        kw: dict[str, Any] = {"max_tokens": agent.get("max_tokens")}
        if tools:
            kw["tools"] = tools
        if model_override is not None:
            # Ступень «другая модель» лестницы восстановления. Действует только
            # на этот прогон и не переписывает конфигурацию агента: владелец её
            # задал, и молча менять её лестница не имеет права. Полномочия при
            # этом те же — меняется исполнитель, а не то, что ему позволено.
            try:
                adapter, model = await self.registry.adapter_for(int(model_override))
                result = await adapter.chat(model["name"], messages, **kw)
                return result, model
            except (ProviderError, LookupError) as exc:
                await self._log(run_id, "warn", "recovery.alternate_failed",
                                f"альтернативная модель {model_override} недоступна ({exc}) — "
                                f"возвращаемся к модели агента")
        picked = next((r for r in await self._call_hooks("pick_model", task, agent) if r), None)
        if picked is not None:
            model_id = int(picked["model_id"] if isinstance(picked, dict) else picked)
            if isinstance(picked, dict) and picked.get("route") is not None:
                async with self.db.session() as s:
                    await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                        route=picked["route"]))
                    await s.commit()
            try:
                adapter, model = await self.registry.adapter_for(model_id)
                result = await adapter.chat(model["name"], messages, **kw)
                return result, model
            except (ProviderError, LookupError) as exc:
                await self._log(run_id, "warn", "router.fallback",
                                f"маршрут (модель {model_id}) недоступен ({exc}) — модель агента")
                await self.bus.emit("router.fallback", task_id=task["id"],
                                    model_id=model_id, reason=str(exc))
        try:
            adapter, model = await self.registry.adapter_for(int(agent["model_id"]))
        except (LookupError, TypeError):
            raise LookupError("у агента не задана рабочая модель")
        try:
            return await adapter.chat(model["name"], messages, **kw), model
        except ProviderError as exc:
            if not agent.get("fallback_model_id"):
                raise
            await self._log(run_id, "warn", "model.fallback",
                            f"модель {model['alias']} недоступна ({exc}) — пробуем fallback")
            fb_adapter, fb_model = await self.registry.adapter_for(int(agent["fallback_model_id"]))
            result = await fb_adapter.chat(fb_model["name"], messages, **kw)
            await self.bus.emit("model.status", id=model["id"], alias=model["alias"],
                                status="error", detail=str(exc))
            return result, fb_model

    # ---------- инструменты (V2.1, фаза A) ----------

    async def _execute_tool_calls(self, run_id: int, task: dict, agent: dict,
                                  messages: list[dict], calls: list[Any], step: int,
                                  policy_rules: list[dict], tool_specs: list[Any],
                                  usage: dict) -> bool:
        """Выполнить вызовы одного шага модели.

        Возвращает True, если нужно ждать человека (ASK): состояние сохранено в
        checkpoint, задача переведена в waiting_approval, воркер освобождён.
        """
        by_api = {t.api_name: t for t in tool_specs}
        allowed_names = {t.name for t in tool_specs}
        for index, call in enumerate(calls):
            spec = by_api.get(call.name)
            # выданные инструменты — единственный источник правды: выдуманное
            # моделью имя или невыданный инструмент отклоняются всегда
            if spec is None or spec.name not in allowed_names:
                # инструмент не выдан этому агенту — отказ данными, run продолжается
                await self._record_tool_call(run_id, task["id"], step, call, None,
                                             effect="deny", status="denied",
                                             preview="инструмент не выдан")
                messages.append(_tool_message(call, f"инструмент {call.name} не выдан "
                                                    f"этому агенту — отказ"))
                await self._log(run_id, "warn", "tool.denied",
                                f"{call.name}: инструмент не выдан агенту")
                continue

            effect, reason = decide_effect(spec, call.arguments, agent, policy_rules)
            if effect != "deny":
                # A path forbidden by current owner roots must not ask for an
                # impossible approval. Context cannot lower the existing floor.
                from .tools import context_denial
                ctx = ToolContext(svc=self.services, task=task, run_id=run_id, agent=agent,
                                  step=step, workspace=str(task.get("workspace_path") or ""),
                                  call_id=str(call.id))
                blocked = await context_denial(spec, call.arguments, ctx)
                if blocked:
                    effect, reason = "deny", blocked
            if effect == "deny":
                await self._record_tool_call(run_id, task["id"], step, call, spec,
                                             effect="deny", status="denied", preview=reason)
                messages.append(_tool_message(
                    call, f"действие {spec.name} запрещено политикой ({reason}) — "
                          f"не выполнять и не повторять"))
                await self._log(run_id, "warn", "tool.denied", f"{spec.name}: {reason}")
                await self.bus.emit("tool.denied", task_id=task["id"], run_id=run_id,
                                    tool=spec.name, reason=reason)
                continue

            if effect == "ask" and not getattr(spec, "idempotent", True):
                # One owner decision, not two: if a previous attempt already dispatched
                # this exact action and crashed before its receipt, the question is not
                # "may it run" but "did it already happen" — ask that directly.
                ambiguous = await self._ambiguous_prior(task["id"], run_id, spec.name, call.arguments)
                if ambiguous is not None:
                    await self._park_reconciliation(run_id, task, agent, messages, call, spec, step,
                                                    remaining=calls[index + 1:], prior=ambiguous, usage=usage)
                    return True
            if effect == "ask":
                # F-013: одобрение привязывается к digest'у (инструмент + отпечаток
                # реализации + канонические аргументы + capability + контекст).
                # В предпросмотре — КАНОНИЧЕСКИЕ аргументы (для terminal.run это
                # уже резолвленный cwd), чтобы человек одобрял ровно то, что
                # исполнится; секреты в аргументах редактируются (D3/F-015).
                from .tools import normalized_args as _norm
                try:
                    shown_args = _norm(spec, call.arguments)
                except Exception as exc:  # noqa: BLE001 — нормализация обязана быть чистой
                    shown_args = {"_normalize_error": str(exc)[:200], **dict(call.arguments)}
                digest = approval_digest(spec, call.arguments, agent=agent, task=task)
                # §7: спросить владельца — последнее средство, а не первое.
                # Порядок строго от «ничего не разрешает» к «разрешает явно
                # выданной областью»: отказ уважается, дубль не задаётся, и
                # только потом тратится аренда, которую владелец выдал сам.
                call_hash = args_hash(spec.name, call.arguments)
                if await _scope.previously_rejected(self.services, args_hash=call_hash, run_id=run_id):
                    await self._record_tool_call(run_id, task["id"], step, call, spec,
                                                 effect="deny", status="denied",
                                                 preview="owner already refused this exact call")
                    messages.append(_tool_message(
                        call, f"действие {spec.name} уже отклонено владельцем в этом прогоне — "
                              f"не повторять и не переспрашивать"))
                    await self._log(run_id, "warn", "tool.rejected_repeat",
                                    f"{spec.name}: повтор отклонённого вызова")
                    await self.bus.emit("approval.repeat_suppressed", task_id=task["id"],
                                        run_id=run_id, tool=spec.name)
                    continue
                lease_scope = _scope.scope_for(spec.name, call.arguments, agent=agent, task=task)
                lease = await _scope.consume(self.services, lease_scope)
                if lease is not None:
                    await self._log(run_id, "info", "tool.lease_used",
                                    f"{spec.name}: покрыт арендой {lease['id']} "
                                    f"({lease['used']}/{lease['max_uses']})")
                    try:
                        await self._run_tool_now(run_id, task, agent, messages, call, spec, step,
                                                 approved_by=f"lease:{lease['id']}",
                                                 lease_id=int(lease["id"]))
                    except AmbiguousPriorEffect as exc:
                        await self._park_reconciliation(run_id, task, agent, messages, call, spec,
                                                        step, remaining=calls[index + 1:],
                                                        prior=exc.prior, usage=usage)
                        return True
                    continue
                reusable = await _scope.find_reusable(self.services, args_hash=call_hash, run_id=run_id)
                if reusable is not None:
                    await self._record_tool_call(run_id, task["id"], step, call, spec,
                                                 effect="ask", status="pending_approval",
                                                 approval_id=reusable.get("id"), preview=reason)
                    await self._park_for_approval(
                        run_id, task["id"], messages, step,
                        pending={"call": _call_dict(call), "tool": spec.name,
                                 "approval_id": reusable.get("id"),
                                 "args_hash": call_hash,
                                 "approval_digest": digest,
                                 "remaining": [_call_dict(c) for c in calls[index + 1:]],
                                 "step": step},
                        usage=usage)
                    await self.bus.emit("approval.deduplicated", task_id=task["id"], run_id=run_id,
                                        tool=spec.name, approval_id=reusable.get("id"))
                    return True
                over, used, budget = await _scope.budget_exceeded(self.services, task)
                if over:
                    # Исчерпанный бюджет НЕ выдаёт разрешение — он останавливает
                    # задачу. Иначе «лимит подтверждений» был бы расширением прав.
                    await self._record_tool_call(run_id, task["id"], step, call, spec,
                                                 effect="deny", status="denied",
                                                 preview=f"approval budget {used}/{budget} spent")
                    await self.bus.emit("approval.budget_exceeded", task_id=task["id"],
                                        run_id=run_id, spent=used, budget=budget)
                    await self._fail_now(run_id, task["id"],
                                         f"APPROVAL_BUDGET_EXCEEDED: задача запросила {used} "
                                         f"подтверждений при бюджете {budget}; выполнение "
                                         f"остановлено вместо продолжения расспросов")
                    return True
                appr = await self._approvals_create(
                    kind="tool",
                    preview=_ps_redact_text(
                        f"Агент «{agent.get('name')}» хочет выполнить {spec.name}\n"
                        f"причина политики: {reason}\n"
                        f"approval_digest: {digest[:16]}…\nаргументы: "
                        + json.dumps(_ps_redact(shown_args), ensure_ascii=False, indent=1)[:2000]
                        # §7: владелец не может согласиться на область, которой не
                        # видит. Предложение — текст, а не разрешение: без явного
                        # `lease` в решении ничего не выдаётся.
                        + _scope.lease_offer(lease_scope)),
                    task_id=task["id"], run_id=run_id)
                approval_id = (appr or {}).get("id")
                await self._record_tool_call(run_id, task["id"], step, call, spec,
                                             effect="ask", status="pending_approval",
                                             approval_id=approval_id, preview=reason)
                await self._park_for_approval(
                    run_id, task["id"], messages, step,
                    pending={"call": _call_dict(call), "tool": spec.name,
                             "approval_id": approval_id,
                             "args_hash": args_hash(spec.name, call.arguments),
                             "approval_digest": digest,
                             "remaining": [_call_dict(c) for c in calls[index + 1:]],
                             "step": step},
                    usage=usage)
                await self._log(run_id, "warn", "tool.ask",
                                f"{spec.name}: нужно подтверждение ({reason})")
                return True

            try:
                await self._run_tool_now(run_id, task, agent, messages, call, spec, step)
            except AmbiguousPriorEffect as exc:
                await self._park_reconciliation(run_id, task, agent, messages, call, spec, step,
                                                remaining=calls[index + 1:], prior=exc.prior, usage=usage)
                return True
        return False

    async def _authorization_at_effect_time(self, task: dict, agent: dict, call: Any,
                                            spec: Any, policy_rules: list[dict],
                                            approval_id: Any) -> tuple[str, str] | None:
        """Что должно быть верно В МОМЕНТ ЭФФЕКТА, а не в момент одобрения.

        `agent` и `policy_rules` здесь — текущие (run перечитывает агента из базы
        на старте), так что снятый инструмент и добавленный DENY видны. Порядок
        важен: сначала бесплатные проверки, и только потом — атомарный CAS
        одобрения, чтобы не «тратить» одобрение на вызов, который всё равно
        отклонит политика. Возвращает (код, причина) или None."""
        # 1. Инструмент всё ещё выдан этому агенту/задаче.
        allowed = {t.name for t in TOOLS.resolve(allowed_tools_for(task, agent))}
        if spec.name not in allowed:
            return ("tool_withdrawn",
                    f"инструмент {spec.name} снят с агента после одобрения")
        # 2. Текущая политика не говорит DENY. ASK здесь не препятствие — его и
        #    закрывало одобрение; препятствие только DENY, который снять нельзя.
        effect, why = decide_effect(spec, call.arguments, agent, policy_rules)
        if effect == "deny":
            return ("policy_deny_at_resume",
                    f"политика запрещает {spec.name} на момент исполнения ({why})")
        # 3. Атомарная граница: approved → consumed. Проигравший здесь отзыв
        #    уже не может отменить эффект — и не обещает этого.
        if approval_id is not None and not await self.services.approvals.accept_for_execution(approval_id):
            return ("revoked_before_dispatch",
                    f"одобрение {approval_id} отозвано или уже использовано до dispatch")
        return None

    async def _run_tool_now(self, run_id: int, task: dict, agent: dict,
                            messages: list[dict], call: Any, spec: Any, step: int,
                            *, approval_id: int | None = None,
                            approved_by: str | None = None,
                            reconcile_prior: int | None = None,
                            lease_id: int | None = None) -> None:
        """Выполнить инструмент и положить результат в историю как tool-сообщение.

        `reconcile_prior` — id строки tool_calls прежней прерванной отправки,
        повтор которой владелец явно одобрил (approval kind=effect_reconciliation).
        Без него неоднозначная прежняя отправка поднимает AmbiguousPriorEffect."""
        ctx = ToolContext(svc=self.services, task=task, run_id=run_id, agent=agent,
                          step=step, workspace=str(task.get("workspace_path") or ""),
                          call_id=str(call.id))
        # FL-01 §2.2 п.3: fence проверяется ДО эффекта, не только при записи receipt.
        await self.assert_fence(run_id)
        # INV-2 идемпотентность: неидемпотентный шаг с тем же (task, step, args)
        # уже исполнен прежней попыткой (рестарт между эффектом и checkpoint) —
        # исполнитель не вызывается второй раз, модели отдаётся сохранённый исход.
        write_ahead = not getattr(spec, "idempotent", True)
        if write_ahead:
            prior = await self._prior_effect(task["id"], run_id, step, spec.name, call.arguments)
            if prior is not None:
                await self._record_tool_call(
                    run_id, task["id"], step, call, spec,
                    effect="auto" if approval_id is None else "ask", status="replayed",
                    approval_id=approval_id, approved_by=approved_by, lease_id=lease_id,
                    preview=str(prior.get("result_preview") or "")[:500], duration_ms=0)
                messages.append(_tool_message(
                    call, "этот шаг уже исполнен прежней попыткой (run "
                          f"{prior.get('run_id')}); повтор не делаем. Сохранённый результат: "
                          + str(prior.get("result_preview") or "")))
                await self._log(run_id, "warn", "tool.replay_guard",
                                f"{spec.name}: неидемпотентный шаг {step} уже исполнен run'ом "
                                f"{prior.get('run_id')} — эффект не повторяется")
                await self.bus.emit("tool.replayed", task_id=task["id"], run_id=run_id,
                                    tool=spec.name, prior_run_id=prior.get("run_id"))
                return
            # Crash Matrix «после эффекта, до журнала»: прежняя попытка отправила
            # это же действие и умерла, не записав исход. Эффект МОГ произойти.
            # Повтор — только по явному решению владельца (reconcile_prior), а
            # не по памяти модели и не по тишине в журнале.
            ambiguous = await self._ambiguous_prior(task["id"], run_id, spec.name, call.arguments)
            if ambiguous is not None and (reconcile_prior is None or int(reconcile_prior) != int(ambiguous["id"])):
                raise AmbiguousPriorEffect(ambiguous)
            if reconcile_prior is not None:
                await self._resolve_prior(int(reconcile_prior), run_id, str(call.id),
                                          note=f"owner {approved_by or 'owner'} decided the effect did not "
                                               f"happen; re-executed once by run {run_id}/{call.id}")
            # Write-ahead: строка `started` ложится ДО эффекта. Если процесс умрёт
            # между эффектом и receipt'ом, следующая попытка увидит незавершённую
            # отправку, а не пустоту, и не повторит необратимое действие молча.
            await self._record_tool_call(
                run_id, task["id"], step, call, spec,
                effect="auto" if approval_id is None else "ask", status="started",
                approval_id=approval_id, approved_by=approved_by, lease_id=lease_id,
                preview="dispatched; outcome not yet journaled")
        started = time.monotonic()
        result = await execute_tool(spec, call.arguments, ctx)
        duration = int((time.monotonic() - started) * 1000)
        await self._record_tool_call(
            run_id, task["id"], step, call, spec,
            effect="auto" if (approval_id is None and lease_id is None) else "ask",
            status="error" if result.error else "executed",
            approval_id=approval_id, approved_by=approved_by, lease_id=lease_id,
            preview=_ps_redact_text(result.content[:500]), truncated=result.truncated,
            duration_ms=duration,
            error=_ps_redact_text(result.content[:500]) if result.error else None)
        messages.append(_tool_message(call, result.render()))
        await self._log(run_id, "warn" if result.error else "info",
                        "tool.error" if result.error else "tool.result",
                        f"{spec.name}: {result.one_line} ({duration} мс)")
        await self.bus.emit("tool.called", task_id=task["id"], run_id=run_id,
                            tool=spec.name, source=spec.source, ok=not result.error,
                            duration_ms=duration)
        if result.error:
            # ошибка инструмента — сигнал Governor'у/Self-Healing, но не провал run'а
            await self._call_hooks_soft("on_failure", task, run_id,
                                        f"tool:{spec.name}: {result.content[:200]}")

    async def _resume_pending_tool(self, run_id: int, task: dict, agent: dict,
                                   messages: list[dict], pending: dict,
                                   policy_rules: list[dict]) -> bool:
        """После решения человека: выполнить одобренное РОВНО один раз либо
        вернуть модели отказ. False — решения ещё нет, run ждёт дальше."""
        approval_id = pending.get("approval_id")
        row = None
        if approval_id:
            async with self.db.session() as s:
                row = await fetch_one(s, approvals_t, int(approval_id))
        status = str((row or {}).get("status") or "pending")
        if status == "pending":
            return False

        call = _call_from_dict(pending.get("call") or {})
        spec = TOOLS.get(str(pending.get("tool") or "")) or TOOLS.by_api_name(call.name)
        remaining = [_call_from_dict(c) for c in (pending.get("remaining") or [])]
        step = int(pending.get("step") or 0)

        already = await self._tool_call_status(run_id, call.id)
        reconcile_prior = pending.get("reconcile_prior")
        # `consumed` — одобрение уже ПРИНЯТО К ИСПОЛНЕНИЮ (CAS перед dispatch).
        # Прерванный после этого вызов — ровно тот случай, где владелец решает,
        # произошёл ли эффект: одобрение было, dispatch был, receipt'а нет.
        if already == "interrupted" and status in ("approved", "consumed") and spec is not None:
            # Одобренный вызов был отправлен прежней попыткой и оборван до receipt'а:
            # одобрение действия — не одобрение его ДУБЛЯ. Владелец решает заново.
            prior = await self._tool_call_row(run_id, call.id)
            await self._park_reconciliation(run_id, task, agent, messages, call, spec, step,
                                            remaining=remaining, prior=prior or {"id": 0}, usage={})
            return False
        if already in ("executed", "error", "denied"):
            # повтор после рестарта: результат уже есть — не исполняем второй раз
            await self._log(run_id, "warn", "tool.replay_guard",
                            f"{pending.get('tool')}: вызов уже исполнен, повтор не делаем")
            messages.append(_tool_message(call, "результат этого вызова уже получен ранее"))
        elif status == "approved" and spec is not None:
            # F-013: одобрение действительно ТОЛЬКО для того же инструмента, той же
            # реализации (поколение регистрации) и тех же канонических аргументов.
            # MCP refresh / перерегистрация / подмена аргументов в checkpoint →
            # digest не совпадает → DENY + нужно новое одобрение. Никогда не
            # «перерезолвим» одобренное действие в другую реализацию молча.
            expected = str(pending.get("approval_digest") or "")
            actual = approval_digest(spec, call.arguments, agent=agent, task=task)
            args_ok = (not pending.get("args_hash")
                       or pending.get("args_hash") == args_hash(spec.name, call.arguments))
            if not expected or expected != actual or not args_ok:
                await self._mark_tool_call(run_id, call.id, status="rejected",
                                           approved_by="system:identity_mismatch")
                messages.append(_tool_message(
                    call, f"действие {pending.get('tool')} НЕ выполнено: реализация или "
                          f"аргументы изменились после одобрения (approval identity mismatch) "
                          f"— требуется новое одобрение"))
                await self._log(run_id, "warn", "tool.approval_identity_mismatch",
                                f"{pending.get('tool')}: digest {expected[:12]}… != {actual[:12]}…")
                await self.bus.emit("tool.denied", task_id=task["id"], run_id=run_id,
                                    tool=spec.name, reason="approval identity mismatch")
            elif (blocked := await self._authorization_at_effect_time(
                    task, agent, call, spec, policy_rules, approval_id)) is not None:
                # Execution Truth §8: одобрение — не бессрочный токен. Права,
                # правила и сам факт одобрения проверяются В МОМЕНТ ЭФФЕКТА.
                code, why = blocked
                await self._mark_tool_call(run_id, call.id, status="rejected",
                                           approved_by=f"system:{code}")
                messages.append(_tool_message(
                    call, f"действие {pending.get('tool')} НЕ выполнено: {why} — "
                          f"не выполнять и не повторять без нового решения владельца"))
                await self._log(run_id, "warn", f"tool.{code}", f"{spec.name}: {why}")
                await self.bus.emit("tool.denied", task_id=task["id"], run_id=run_id,
                                    tool=spec.name, reason=code)
            else:
                await self._mark_tool_call(run_id, call.id, status="approved",
                                           approved_by=str((row or {}).get("decided_by") or ""))
                try:
                    await self._run_tool_now(run_id, task, agent, messages, call, spec, step,
                                             approval_id=int(approval_id) if approval_id else None,
                                             approved_by=str((row or {}).get("decided_by") or ""),
                                             reconcile_prior=reconcile_prior)
                except AmbiguousPriorEffect as exc:
                    await self._park_reconciliation(run_id, task, agent, messages, call, spec, step,
                                                    remaining=remaining, prior=exc.prior, usage={})
                    return False
        else:
            await self._mark_tool_call(run_id, call.id, status="rejected",
                                       approved_by=str((row or {}).get("decided_by") or ""))
            if reconcile_prior is not None:
                await self._resolve_prior(int(reconcile_prior), run_id, str(call.id),
                                          note=f"owner {(row or {}).get('decided_by') or 'owner'} declined "
                                               "re-execution after an interrupted dispatch; effect unobserved")
            messages.append(_tool_message(
                call, f"действие {pending.get('tool')} отклонено пользователем — "
                      f"не выполнять и не повторять"))
            await self._log(run_id, "warn", "tool.rejected",
                            f"{pending.get('tool')}: отклонено пользователем")

        # остальные вызовы того же шага модели
        specs = TOOLS.resolve(allowed_tools_for(task, agent))
        if remaining:
            waiting = await self._execute_tool_calls(
                run_id, task, agent, messages, remaining, step, policy_rules, specs,
                usage={})
            if waiting:
                return False
        return True

    async def _park_for_approval(self, run_id: int, task_id: int, messages: list[dict],
                                 step: int, pending: dict, usage: dict) -> None:
        """Освободить воркер и ждать человека: всё состояние — в БД."""
        checkpoint = {"messages": messages, "step": step, "note": "tool_approval",
                      "pending_tool_call": pending}
        values: dict[str, Any] = {"status": "queued", "worker_lease_until": None,
                                  "checkpoint": checkpoint}
        for key in ("tokens_in", "tokens_out", "cost_usd", "model_alias"):
            if usage.get(key) is not None:
                values[key] = usage[key]
        async with self.db.session() as s:
            await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(**values))
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
                status="waiting_approval", updated_at=utcnow()))
            await s.commit()
        await self.bus.emit("task.progress", task_id=task_id, run_id=run_id,
                            waiting_approval=True, tool=pending.get("tool"))

    async def on_approval_decided(self, approval_id: int) -> None:
        """Решение принято → вернуть ожидающий run в очередь.

        Подписка на шину (см. approval_watcher) — работает для любого пути
        принятия решения: API, фича, мобильный пульт.
        """
        async with self.db.session() as s:
            row = (await s.execute(sa.select(tool_calls_t).where(
                sa.and_(tool_calls_t.c.approval_id == approval_id,
                        tool_calls_t.c.status == "pending_approval")))).first()
            if row is None:
                return
            rec = dict(row._mapping)
            # Fail-closed: остановленную задачу решение не воскрешает. Иначе
            # гонка «решение в полёте, пока stop() гасит run» оставляет её
            # `queued` при stopped-run'е — такую задачу не возьмёт ни один воркер.
            status = (await s.execute(sa.select(tasks_t.c.status).where(
                tasks_t.c.id == rec["task_id"]))).scalar_one_or_none()
            if status in STOPPED_TASK_STATUSES:
                await s.execute(sa.update(tool_calls_t).where(
                    tool_calls_t.c.id == rec["id"]).values(
                    status="rejected", finished_at=utcnow()))
                await s.commit()
                return
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == rec["task_id"]).values(
                status="queued", updated_at=utcnow()))
            await s.execute(sa.update(runs_t).where(
                sa.and_(runs_t.c.id == rec["run_id"], runs_t.c.status == "queued")).values(
                worker_lease_until=None))
            await s.commit()
        await self.bus.emit("task.queued", task_id=rec["task_id"], run_id=rec["run_id"],
                            approval_resumed=True)

    async def _resume_decided_approvals(self) -> int:
        """Свип: вызовы, ждущие подтверждения, решение по которым уже принято.

        Нужен, потому что событие могло прийти, когда процесс был выключен —
        на одну живую подписку полагаться нельзя.
        """
        async with self.db.session() as s:
            rows = (await s.execute(
                sa.select(tool_calls_t.c.approval_id)
                .join(approvals_t, approvals_t.c.id == tool_calls_t.c.approval_id)
                .where(sa.and_(tool_calls_t.c.status == "pending_approval",
                               approvals_t.c.status != "pending")))).fetchall()
        for row in rows:
            await self.on_approval_decided(int(row[0]))
        return len(rows)

    async def approval_watcher(self) -> None:
        """Фоновая подписка: approval.decided → продолжить ожидающий run."""
        q = self.bus.subscribe()
        try:
            while True:
                msg = await q.get()
                if msg.get("kind") != "approval.decided":
                    continue
                try:
                    await self.on_approval_decided(int(msg.get("id")))
                except Exception as exc:
                    await self.bus.emit("worker.error",
                                        message=f"approval_watcher: {type(exc).__name__}: {exc}")
        except asyncio.CancelledError:
            return
        finally:
            self.bus.unsubscribe(q)

    async def _approvals_create(self, **kw: Any) -> dict:
        svc = self.services
        if svc is not None and getattr(svc, "approvals", None) is not None:
            return await svc.approvals.create(**kw) or {}
        # движок может работать без Services (тесты): пишем строку сами
        async with self.db.session() as s:
            res = await s.execute(sa.insert(approvals_t).values(
                kind=kw.get("kind", "tool"), preview=kw.get("preview", ""),
                task_id=kw.get("task_id"), run_id=kw.get("run_id"),
                status="pending", created_at=utcnow()))
            aid = int(res.inserted_primary_key[0])
            await s.commit()
        await self.bus.emit("approval.created", id=aid, approval_kind=kw.get("kind", "tool"),
                            preview=str(kw.get("preview", ""))[:500],
                            task_id=kw.get("task_id"), run_id=kw.get("run_id"))
        return {"id": aid}

    async def _reject_parked_approvals(self, task_id: int) -> None:
        """Снять подтверждения, припаркованные остановленной задачей.

        Без этого строка остаётся в «Ждут вашего решения» уже мёртвой задачи, а
        «Разрешить» по ней поднимает задачу обратно в `queued`: run у неё уже
        stopped, воркер её не возьмёт никогда — зомби в очереди навсегда.
        Решаем отказом: остановка владельца не может стать разрешением.
        """
        async with self.db.session() as s:
            parked = sa.and_(tool_calls_t.c.task_id == task_id,
                             tool_calls_t.c.status == "pending_approval")
            rows = (await s.execute(sa.select(tool_calls_t.c.approval_id)
                                    .where(parked))).fetchall()
            if not rows:
                return
            await s.execute(sa.update(tool_calls_t).where(parked).values(
                status="rejected", finished_at=utcnow()))
            await s.commit()
        for row in rows:
            if row[0] is not None:
                await self._reject_approval(int(row[0]))

    async def _reject_approval(self, approval_id: int) -> None:
        """Отказ по CAS: уже принятое человеком решение не переписываем."""
        svc = self.services
        if svc is not None and getattr(svc, "approvals", None) is not None:
            await svc.approvals.decide(approval_id, False, by=STOP_DECIDER)
            return
        # движок может работать без Services (тесты): решаем строку сами
        async with self.db.session() as s:
            res = await s.execute(sa.update(approvals_t).where(
                sa.and_(approvals_t.c.id == approval_id,
                        approvals_t.c.status == "pending")).values(
                status="rejected", decided_by=STOP_DECIDER, decided_at=utcnow()))
            await s.commit()
        if res.rowcount:
            await self.bus.emit("approval.decided", id=approval_id, status="rejected",
                                by=STOP_DECIDER)

    async def _record_tool_call(self, run_id: int, task_id: int, step: int, call: Any,
                                spec: Any, *, effect: str, status: str,
                                approval_id: int | None = None,
                                approved_by: str | None = None, preview: str = "",
                                truncated: bool = False, duration_ms: int | None = None,
                                error: str | None = None, lease_id: int | None = None) -> None:
        name = spec.name if spec is not None else str(call.name)
        values = {
            "run_id": run_id, "task_id": task_id, "step": step,
            "call_id": str(call.id), "tool": name,
            "source": spec.source if spec is not None else "unknown",
            # V2.6 D3: в аудит-таблицу args идут только через redact (по именам
            # ключей); anti-replay hash считается от СЫРЫХ аргументов — он не
            # обратим и должен совпадать между попытками.
            "args": _ps_redact(call.arguments), "args_hash": args_hash(name, call.arguments),
            "effect": effect, "status": status, "approval_id": approval_id,
            "approved_by": approved_by, "result_preview": preview,
            "truncated": truncated, "duration_ms": duration_ms, "error": error,
            # §7: какая аренда полномочия покрыла вызов — иначе «подтверждений
            # стало меньше» неотличимо от «спрашивать перестали».
            "lease_id": lease_id,
        }
        # Строка пишется ПОСЛЕ того, как инструмент отработал, поэтому «сейчас» —
        # это момент завершения, а не начала. Раньше оба времени брались двумя
        # вызовами utcnow() подряд: получалась строка, в которой вызов длился
        # duration_ms по одному полю и ноль по другим двум. Начало восстанавливаем
        # из измеренной длительности — тогда finished_at - created_at и duration_ms
        # говорят одно и то же, и вопрос «когда этот вызов начался» имеет ответ.
        now = utcnow()
        done = status not in ("pending_approval", "started")
        values["finished_at"] = now if done else None
        values["created_at"] = (now - timedelta(milliseconds=int(duration_ms))
                                if done and duration_ms is not None else now)
        values["receipt_json"] = self._action_receipt(run_id, task_id, step, call, name, spec, status, values)
        await self.assert_fence(run_id)          # FL-01: receipt пишет только держатель
        async with self.db.session() as s:
            try:
                await s.execute(sa.insert(tool_calls_t).values(**values))
                await s.commit()
            except sa.exc.IntegrityError:
                # anti-replay: строка на (run_id, call_id) уже есть — обновляем исход
                await s.rollback()
                await s.execute(sa.update(tool_calls_t).where(sa.and_(
                    tool_calls_t.c.run_id == run_id,
                    tool_calls_t.c.call_id == str(call.id))).values(
                    status=status, effect=effect, approval_id=approval_id,
                    result_preview=preview, truncated=truncated,
                    duration_ms=duration_ms, error=error, approved_by=approved_by,
                    finished_at=values["finished_at"]))
                await s.commit()

    def _action_receipt(self, run_id: int, task_id: int, step: int, call: Any, name: str, spec: Any,
                        status: str, values: dict) -> dict | None:
        """TRUTH-003 §2: ActionReceipt как заявление исполнителя (observation_type=tool_result_only,
        verification UNVERIFIED). Верифицирует только наблюдатель пост-состояния (lifecycle/review_gate)."""
        try:
            from bossman_shared.action_receipt import ActionReceipt
        except Exception:  # noqa: BLE001 — bcc без общего пакета: receipt не пишется, исполнение не страдает
            return None
        try:
            side = "READ_ONLY" if (spec is None or getattr(spec, "idempotent", True)) else "IDEMPOTENT_WRITE"
            rec = ActionReceipt.from_v3(
                task_id=str(task_id), step_id=f"step-{step}/{call.id}", action_type=name, effect_type=side,
                args=dict(getattr(call, "arguments", {}) or {}), started_at=values.get("created_at"),
                finished_at=values.get("finished_at"), observed_at=None, executor_status=status,
                observation_type="tool_result_only", observation_ref="", verification_status="UNVERIFIED",
                verification_reason="tool result is not post-state verification", run_id=str(run_id),
                fencing_token=self._fences.get(run_id), executor_metadata={"effect": values.get("effect"),
                                                                            "args_hash": values.get("args_hash")})
            return rec.to_dict()
        except Exception:  # noqa: BLE001
            return None

    async def _mark_tool_call(self, run_id: int, call_id: str, *, status: str,
                              approved_by: str = "") -> None:
        async with self.db.session() as s:
            await s.execute(sa.update(tool_calls_t).where(sa.and_(
                tool_calls_t.c.run_id == run_id,
                tool_calls_t.c.call_id == str(call_id))).values(
                status=status, approved_by=approved_by or None))
            await s.commit()

    async def _prior_effect(self, task_id: int, run_id: int, step: int, tool: str,
                            arguments: dict) -> dict | None:
        """INV-2: исполненный receipt того же шага/инструмента/аргументов из
        ПРЕЖНЕЙ попытки той же задачи — другой run или тот же run до того, как
        этот воркер его взял (recover сохраняет строку run'а, attempt+1).
        Повторы внутри текущей попытки (два одинаковых вызова в одном шаге) —
        не дубль по рестарту и не перехватываются. None — эффекта не было."""
        held_since = self._held_since.get(run_id)
        async with self.db.session() as s:
            row = (await s.execute(sa.select(tool_calls_t).where(sa.and_(
                tool_calls_t.c.task_id == task_id,
                (tool_calls_t.c.run_id != run_id) if held_since is None
                else sa.or_(tool_calls_t.c.run_id != run_id,
                            tool_calls_t.c.created_at < held_since),
                tool_calls_t.c.step == step,
                tool_calls_t.c.tool == tool,
                tool_calls_t.c.args_hash == args_hash(tool, arguments),
                tool_calls_t.c.status == "executed")).order_by(
                tool_calls_t.c.id.desc()).limit(1))).first()
        return dict(row._mapping) if row is not None else None

    async def _ambiguous_prior(self, task_id: int, run_id: int, tool: str, arguments: dict) -> dict | None:
        """Прежняя отправка того же действия (инструмент+аргументы) этой задачи,
        исход которой не записан: `started` (журнал write-ahead без receipt'а) или
        `interrupted` (помечена при перехвате). Только прежние попытки — другой run
        или строки до того, как этот воркер взял run. None — неоднозначности нет."""
        held_since = self._held_since.get(run_id)
        async with self.db.session() as s:
            row = (await s.execute(sa.select(tool_calls_t).where(sa.and_(
                tool_calls_t.c.task_id == task_id,
                (tool_calls_t.c.run_id != run_id) if held_since is None
                else sa.or_(tool_calls_t.c.run_id != run_id,
                            tool_calls_t.c.created_at < held_since),
                tool_calls_t.c.tool == tool,
                tool_calls_t.c.args_hash == args_hash(tool, arguments),
                tool_calls_t.c.status.in_(("started", "interrupted")))).order_by(
                tool_calls_t.c.id.desc()).limit(1))).first()
        return dict(row._mapping) if row is not None else None

    async def _mark_interrupted_dispatches(self, run_id: int, task_id: int) -> int:
        """Перехват/рестарт: отправки прежних попыток без исхода становятся
        `interrupted` — явное состояние «эффект не наблюдён», а не вечно живая
        строка `started`. Ничего не повторяется и не удаляется."""
        held_since = self._held_since.get(run_id)
        async with self.db.session() as s:
            res = await s.execute(sa.update(tool_calls_t).where(sa.and_(
                tool_calls_t.c.task_id == task_id,
                tool_calls_t.c.status == "started",
                (tool_calls_t.c.run_id != run_id) if held_since is None
                else sa.or_(tool_calls_t.c.run_id != run_id,
                            tool_calls_t.c.created_at < held_since))).values(
                status="interrupted", finished_at=utcnow(),
                error="attempt interrupted after dispatch; effect unobserved (no receipt)"))
            await s.commit()
        if res.rowcount:
            await self._log(run_id, "warn", "tool.interrupted",
                            f"{res.rowcount} dispatch(es) of a previous attempt never journaled an outcome; "
                            "their effects are unobserved and will not be replayed silently")
        return int(res.rowcount or 0)

    async def _resolve_prior(self, prior_id: int, run_id: int, call_id: str, *, note: str) -> None:
        async with self.db.session() as s:
            await s.execute(sa.update(tool_calls_t).where(sa.and_(
                tool_calls_t.c.id == prior_id,
                tool_calls_t.c.status.in_(("started", "interrupted")),
                sa.not_(sa.and_(tool_calls_t.c.run_id == run_id, tool_calls_t.c.call_id == call_id)))).values(
                status="reconciled", finished_at=utcnow(), error=note[:500]))
            await s.commit()

    async def _park_reconciliation(self, run_id: int, task: dict, agent: dict, messages: list[dict],
                                   call: Any, spec: Any, step: int, *, remaining: list, prior: dict,
                                   usage: dict) -> None:
        """Неоднозначный прежний эффект → решение владельца (approval
        kind=effect_reconciliation). approve = эффекта не было, выполнить ровно один
        раз; reject = не повторять. Воркер освобождается, состояние — в БД."""
        from .tools import normalized_args as _norm
        try:
            shown_args = _norm(spec, call.arguments)
        except Exception as exc:  # noqa: BLE001
            shown_args = {"_normalize_error": str(exc)[:200], **dict(call.arguments)}
        digest = approval_digest(spec, call.arguments, agent=agent, task=task)
        prior_id = int(prior.get("id") or 0)
        appr = await self._approvals_create(
            kind="effect_reconciliation",
            preview=_ps_redact_text(
                f"Действие {spec.name} MAY ALREADY HAVE HAPPENED: a previous attempt (run "
                f"{prior.get('run_id')}, call {prior.get('call_id')}) dispatched it and crashed before "
                f"journaling the outcome. Approve = the effect did NOT happen, run it once. "
                f"Reject = do not run it again.\napproval_digest: {digest[:16]}…\nаргументы: "
                + json.dumps(_ps_redact(shown_args), ensure_ascii=False, indent=1)[:2000]),
            task_id=task["id"], run_id=run_id)
        approval_id = (appr or {}).get("id")
        if prior_id and prior.get("status") == "started":
            # The orphaned dispatch becomes explicit the moment it is detected,
            # not only on the next takeover sweep.
            async with self.db.session() as s:
                await s.execute(sa.update(tool_calls_t).where(sa.and_(
                    tool_calls_t.c.id == prior_id, tool_calls_t.c.status == "started")).values(
                    status="interrupted", finished_at=utcnow(),
                    error="attempt interrupted after dispatch; effect unobserved (no receipt)"))
                await s.commit()
        await self._record_tool_call(run_id, task["id"], step, call, spec,
                                     effect="ask", status="pending_approval",
                                     approval_id=approval_id, preview="ambiguous prior effect: owner decides")
        await self._park_for_approval(
            run_id, task["id"], messages, step,
            pending={"call": _call_dict(call), "tool": spec.name, "approval_id": approval_id,
                     "args_hash": args_hash(spec.name, call.arguments), "approval_digest": digest,
                     "reconcile_prior": prior_id,
                     "remaining": [_call_dict(c) for c in remaining], "step": step},
            usage=usage)
        await self._log(run_id, "warn", "tool.ambiguous_effect",
                        f"{spec.name}: dispatched by a previous attempt without a journaled outcome — "
                        "not re-executed; owner reconciliation requested")
        await self.bus.emit("tool.ambiguous_effect", task_id=task["id"], run_id=run_id, tool=spec.name,
                            prior_run_id=prior.get("run_id"), prior_call_id=prior.get("call_id"),
                            approval_id=approval_id)

    async def _tool_call_row(self, run_id: int, call_id: str) -> dict | None:
        async with self.db.session() as s:
            row = (await s.execute(sa.select(tool_calls_t).where(sa.and_(
                tool_calls_t.c.run_id == run_id,
                tool_calls_t.c.call_id == str(call_id))))).first()
        return dict(row._mapping) if row is not None else None

    async def _tool_call_status(self, run_id: int, call_id: str) -> str:
        async with self.db.session() as s:
            row = (await s.execute(sa.select(tool_calls_t.c.status).where(sa.and_(
                tool_calls_t.c.run_id == run_id,
                tool_calls_t.c.call_id == str(call_id))))).first()
        return str(row[0]) if row else ""

    async def _handle_failure(self, run_id: int, task: dict, error: str,
                              messages: list[dict], step: int,
                              *, kind: str | None = None) -> None:
        """Сбой провайдера: СМЕНА СТРАТЕГИИ, а не повтор того же запроса.

        Прежде здесь был только retry с экспоненциальной паузой: тот же
        маршрут, та же модель, те же сообщения. Для разового сетевого сбоя это
        верно, а для всего остального — нет. Приёмочный прогон показал цену:
        просроченный ключ провайдера сжёг все попытки, потому что ожидание не
        чинит отвергнутый ключ.

        Лестница (bcc/reality/recovery) выбирает следующий шаг по КЛАССУ сбоя и
        тратит каждую ступень не больше одного раза, поэтому задача приходит к
        терминальному состоянию за ограниченное число шагов. Ни одна ступень не
        расширяет полномочия: смена модели меняет КАК делается запрос, а не что
        ему позволено — те же права, та же область подтверждений, те же гейты
        доказательств."""
        from .reality import recovery as _recovery
        async with self.db.session() as s:
            run = await fetch_one(s, runs_t, run_id)
        attempt = int((run or {}).get("attempt") or 0)
        max_retries = int(task.get("max_retries") or 0)
        await self._log(run_id, "error", "run.error", error)
        await self._call_hooks_soft("on_failure", task, run_id, error)

        checkpoint = (run or {}).get("checkpoint") or {}
        failure_class = _recovery.classify_failure(error, kind=kind)
        ladder = _recovery.Ladder.from_dict(checkpoint.get("recovery_ladder"), failure_class)
        agent = await self._agent_of(task)
        rung = _recovery.next_rung(
            ladder,
            current_model_id=self._current_model_id(run, agent),
            fallback_model_id=(agent or {}).get("fallback_model_id"),
            healthy_models=await self._healthy_models(),
            retries_left=max(0, max_retries - attempt), max_retries=max_retries)
        await self.bus.emit("recovery.rung_selected", task_id=task["id"], run_id=run_id,
                            failure_class=failure_class, **rung.to_dict())

        if rung.terminal:
            # Эскалация владельцу — настоящий ответ, а не провал лестницы:
            # отдать проблему человеку лучше, чем перебирать пути, которые не
            # могут сработать.
            await self._log(run_id, "error", "run.recovery_exhausted", rung.reason[:500])
            await self._finish(run_id, task["id"], "failed",
                               error=f"{error} | {rung.reason}",
                               checkpoint={"messages": messages, "step": step,
                                           "note": "recovery_exhausted",
                                           "recovery_ladder": rung.ladder.to_dict()})
            return

        note = {"messages": messages, "step": step, "note": f"recovery:{rung.name}",
                "recovery_ladder": rung.ladder.to_dict()}
        if rung.model_id is not None:
            # Модель на этот прогон, а не смена модели агента: владелец настроил
            # агента, и лестница не переписывает его конфигурацию молча.
            note["recovery_model_id"] = int(rung.model_id)
        if rung.degrade:
            note["recovery_degrade"] = dict(rung.degrade)
        delay = (min(self.retry_base_delay * (2 ** attempt), self.retry_max_delay)
                 if rung.name == _recovery.RETRY_SAME else 0.0)
        # Owner intervention wins over recovery: a task the owner stopped while
        # the failing call was in flight is not re-queued behind their back, and
        # a paused one keeps its pause (the run waits without a lease).
        owner_status = await self._task_status(task["id"])
        if owner_status in ("stopped", "cancelled"):
            await self._log(run_id, "warn", "run.stopped", "остановлена владельцем во время сбоя")
            await self._finish(run_id, task["id"], "stopped",
                               checkpoint={"messages": messages, "step": step, "note": "stopped"},
                               sync_task=False)
            return
        async with self.db.session() as s:
            await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                status="queued", attempt=attempt + 1, error=error, checkpoint=note,
                worker_lease_until=(utcnow() + timedelta(seconds=delay)) if delay else None))
            if owner_status != "paused":
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(
                    status="queued", updated_at=utcnow()))
            await s.commit()
        # `run.retry` остаётся прежним именем события для ступени «тот же
        # маршрут»: это ровно то поведение, которое было, и у него есть
        # потребители (UI, тесты). Смена стратегии — новое событие.
        log_kind = "run.retry" if rung.name == _recovery.RETRY_SAME else "run.recovery"
        message = (f"попытка {attempt + 1}/{max_retries} через {delay:.0f} с"
                   if rung.name == _recovery.RETRY_SAME
                   else f"{failure_class}: ступень {rung.name} — {rung.reason}")
        await self._log(run_id, "warn", log_kind, message)
        await self.bus.emit("task.queued", task_id=task["id"], run_id=run_id,
                            attempt=attempt + 1, retry=True, recovery_rung=rung.name)

    async def _agent_of(self, task: dict) -> dict | None:
        agent_id = task.get("agent_id")
        if not agent_id:
            return None
        async with self.db.session() as s:
            return await fetch_one(s, agents_t, int(agent_id))

    def _current_model_id(self, run: dict | None, agent: dict | None) -> int | None:
        route = (run or {}).get("route")
        if isinstance(route, dict) and route.get("model_id") is not None:
            return int(route["model_id"])
        model_id = (agent or {}).get("model_id")
        return int(model_id) if model_id is not None else None

    async def _healthy_models(self) -> list[tuple[int, Any]]:
        """Измеренное здоровье моделей для выбора альтернативы (B5).

        Телеметрия не имеет права ронять восстановление: если здоровье не
        читается, лестница просто не увидит альтернатив и пойдёт дальше."""
        try:
            from . import model_health as mh
            async with self.db.session() as s:
                rows = (await s.execute(sa.select(models_t.c.id, models_t.c.health))).fetchall()
            return [(int(r[0]), mh.HealthRecord.from_dict(r[1])) for r in rows]
        except Exception:  # noqa: BLE001
            return []

    async def _check_interrupt(self, run_id: int, task_id: int, messages: list[dict],
                               step: int) -> bool:
        """Stop/Pause проверяются между шагами — по актуальному статусу задачи в БД."""
        status = await self._task_status(task_id)
        if status == "stopped":
            await self._log(run_id, "warn", "run.stopped", "задача остановлена оператором")
            await self._finish(run_id, task_id, "stopped",
                               checkpoint={"messages": messages, "step": step, "note": "stopped"},
                               sync_task=False)
            return True
        if status == "paused":
            async with self.db.session() as s:
                await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                    status="queued", worker_lease_until=None,
                    checkpoint={"messages": messages, "step": step, "note": "paused"}))
                await s.commit()
            await self._log(run_id, "warn", "run.paused", "пауза: checkpoint сохранён")
            await self.bus.emit("task.paused", task_id=task_id, run_id=run_id, step=step)
            return True
        return False

    # ---------- служебное ----------

    async def _capture_provenance(self, run_id: int, task: dict, run: dict,
                                 agent: dict | None) -> bool:
        """Capture configuration once, under the current worker's fencing token.

        Capturing an existing record is an idempotent read. A failed new capture
        stops execution before dispatch; exception bodies may contain secrets.
        """
        try:
            async with self.db.session() as s:
                current = await fetch_one(s, runs_t, run_id)
                fence = self.fence_of(run_id)
                if current is None or (fence is not None and int(current.get("fence") or 0) != fence):
                    raise FencedOut(run_id, fence)
                if current.get("provenance") is not None:
                    return True
                if current["status"] != "running" or current["task_id"] != task["id"]:
                    raise ValueError("provenance requires the active matching run")
                model = fallback = None
                if agent:
                    if agent.get("model_id"):
                        model = await fetch_one(s, models_t, int(agent["model_id"]))
                    if agent.get("fallback_model_id"):
                        fallback = await fetch_one(s, models_t, int(agent["fallback_model_id"]))
                record = run_provenance.build(
                    task=task, run={**current, "previous_started_at": run.get("started_at")},
                    agent=agent, model=model, fallback_model=fallback)
                updated = await s.execute(sa.update(runs_t).where(
                    runs_t.c.id == run_id, self._fence_clause(run_id),
                    runs_t.c.status == "running", runs_t.c.provenance.is_(None)).values(
                    provenance=record))
                if not updated.rowcount:
                    await s.rollback()
                    raise FencedOut(run_id, fence)
                await s.commit()
            return True
        except FencedOut:
            raise
        except Exception as exc:
            await self._log(run_id, "error", "run.provenance_not_captured",
                            f"provenance capture failed: {type(exc).__name__}")
            return False

    async def _start(self, run_id: int, task_id: int) -> None:
        async with self.db.session() as s:
            upd = await s.execute(sa.update(runs_t).where(
                runs_t.c.id == run_id, self._fence_clause(run_id)).values(
                status="running", started_at=utcnow(),
                worker_lease_until=utcnow() + timedelta(seconds=self.lease_seconds)))
            if not upd.rowcount:
                await s.rollback()
                raise FencedOut(run_id, self._fences.get(run_id))
            await s.execute(sa.update(tasks_t).where(
                tasks_t.c.id == task_id,
                tasks_t.c.status.notin_(("stopped", "paused"))).values(
                status="running", updated_at=utcnow()))
            await s.commit()
        await self._log(run_id, "info", "run.started", "выполнение начато")
        await self.bus.emit("task.started", task_id=task_id, run_id=run_id)

    async def _save_checkpoint(self, run_id: int, messages: list[dict], step: int,
                               note: str = "", **values: Any) -> None:
        ckpt: dict[str, Any] = {"messages": messages, "step": step, "note": note}
        sm = getattr(self, "_sm", None)
        if sm is not None:
            try:
                ckpt["sm"] = sm.checkpoint()
            except Exception:
                pass
        async with self.db.session() as s:
            upd = await s.execute(sa.update(runs_t).where(
                runs_t.c.id == run_id, self._fence_clause(run_id)).values(
                checkpoint=ckpt, **values))
            if not upd.rowcount:
                await s.rollback()
                raise FencedOut(run_id, self._fences.get(run_id))
            await s.commit()

    async def _finish(self, run_id: int, task_id: int, status: str, *,
                      error: str | None = None, result: str | None = None,
                      checkpoint: dict | None = None, sync_task: bool = True, _expected=None,
                      **values: Any) -> None:
        run_values: dict[str, Any] = {"status": status, "finished_at": utcnow(), **values}
        if error is not None:
            run_values["error"] = error
        if result is not None:
            run_values["result"] = result
        if checkpoint is not None:
            run_values["checkpoint"] = checkpoint
        async with self.db.session() as s:
            upd = await s.execute(sa.update(runs_t).where(
                runs_t.c.id == run_id, self._fence_clause(run_id),
                sa.true() if _expected is None else _expected).values(**run_values))
            if not upd.rowcount:
                # FL-01: закрыть run может только текущий держатель fence
                await s.rollback()
                raise FencedOut(run_id, self._fences.get(run_id))
            if sync_task:
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
                    status=status, updated_at=utcnow()))
            await s.commit()
        kind = {"completed": "task.completed", "failed": "task.failed",
                "stopped": "task.stopped"}.get(status, "task.progress")
        payload: dict[str, Any] = {"task_id": task_id, "run_id": run_id}
        if error:
            payload["error"] = error
        if result is not None:
            payload["result"] = result[:500]
        await self.bus.emit(kind, **payload)
        if status in ("completed", "failed", "stopped"):
            await self._call_hooks_soft("after_run", task_id, run_id, status)

    async def _insert_checkpoint(self, run_id: int, messages: list[dict], step: int,
                                 note: str = "") -> int:
        async with self.db.session() as s:
            res = await s.execute(sa.insert(checkpoints_t).values(
                run_id=run_id, step=step, messages=messages, note=note, created_at=utcnow()))
            cp_id = int(res.inserted_primary_key[0])
            await s.commit()
        await self.bus.emit("checkpoint.created", checkpoint_id=cp_id, run_id=run_id, step=step)
        return cp_id

    async def _escalate_gate_failure(self, run_id: int, task: dict, messages: list[dict],
                                     step: int, exc: CriticalHookFailure) -> None:
        """Gate упал → задача НЕ completed: run паркуется (queued, без аренды,
        checkpoint), задача → waiting_approval, человеку — review_escalation с
        именем упавшего хука. Если сама эскалация падает (БД approvals) — failed."""
        task_id = int(task["id"])
        reason = f"critical hook gate_completion failed: {exc.hook}: {exc.reason}"
        try:
            await self.assert_fence(run_id)
            await self._log(run_id, "error", "run.gate_failed", reason[:500])
            await self._approvals_create(
                kind="review_escalation",
                preview=(f"Проверка завершения задачи «{str(task.get('title') or '')[:80]}» "
                         f"не выполнена: хук {exc.hook} — {exc.reason}. "
                         f"Задача НЕ считается выполненной; нужно решение человека."),
                task_id=task_id, run_id=run_id)
            async with self.db.session() as s:
                changed=await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id,self._fence_clause(run_id)).values(
                    status="queued", worker_lease_until=None,
                    checkpoint={"messages": messages, "step": step,
                                "note": "gate_hook_failed"}))
                if not changed.rowcount:return
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
                    status="waiting_approval", updated_at=utcnow()))
                await s.commit()
        except asyncio.CancelledError:
            raise
        except FencedOut:
            return
        except Exception as inner:  # noqa: BLE001 — эскалация не удалась → честный failed
            await self.bus.emit("hook.escalation_failed", hook=exc.name, fn=exc.hook,
                                error=type(inner).__name__)
            await self._fail_now(run_id, task_id,
                                 f"{reason}; escalation failed: {type(inner).__name__}")
            return
        await self.bus.emit("task.progress", task_id=task_id, run_id=run_id,
                            waiting_approval=True, gate_hook_failed=exc.hook)

    async def _budget_stop(self, run_id: int, task: dict, messages: list[dict], step: int,
                           breach: Any, tokens_in: int, tokens_out: int,
                           cost: float, alias: str) -> None:
        """§8: потолок сработал — прогон закрывается ОТКАЗОМ с названной причиной.

        Ни обрезки контекста, ни понижения модели, ни «частичного успеха»: бюджет,
        который тихо выдаёт худший ответ, неотличим от бага. Транскрипт
        сохраняется в checkpoint — владелец должен видеть, на чём остановились,
        чтобы спорить с потолком уликами, а не поднимать его рефлекторно."""
        await self._log(run_id, "error", "run.budget_stop", str(breach)[:500])
        await self.bus.emit("run.budget_exceeded", task_id=task["id"], run_id=run_id,
                            code=breach.code, detail=breach.detail[:300],
                            tokens_in=tokens_in, tokens_out=tokens_out,
                            cost_usd=round(cost, 6), step=step)
        await self._finish(run_id, task["id"], "failed", error=str(breach),
                           checkpoint={"messages": messages, "step": step,
                                       "note": f"budget:{breach.code}"},
                           tokens_in=tokens_in, tokens_out=tokens_out,
                           cost_usd=round(cost, 6), model_alias=alias)

    async def _fail_now(self, run_id: int, task_id: int, error: str) -> None:
        """Провал без ретраев (нет агента/модели, попытки исчерпаны) — с записью в лог run'а."""
        await self._log(run_id, "error", "run.failed", error)
        await self._finish(run_id, task_id, "failed", error=error)

    async def _task_status(self, task_id: int) -> str:
        async with self.db.session() as s:
            res = await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id))
            row = res.first()
        return str(row[0]) if row else "stopped"

    async def _set_task_status(self, task_id: int, status: str) -> None:
        async with self.db.session() as s:
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
                status=status, updated_at=utcnow()))
            await s.commit()

    async def _log(self, run_id: int, level: str, kind: str, message: str,
                   data: dict | None = None) -> None:
        """Строка лога run'а: в run_events и в живую ленту (секретов в message нет)."""
        async with self.db.session() as s:
            await s.execute(sa.insert(run_events_t).values(
                run_id=run_id, ts=utcnow(), level=level, kind=kind, message=message, data=data))
            await s.commit()
        await self.bus.emit("run.log", run_id=run_id, level=level, log_kind=kind, message=message)


def _assistant_tool_message(result: ChatResult) -> dict:
    """Ответ модели с инструментами — в истории как есть (OpenAI-формат).
    Anthropic-адаптер конвертирует это обратно в свои блоки."""
    return {
        "role": "assistant",
        "content": result.text or "",
        "tool_calls": [{"id": c.id, "type": "function",
                        "function": {"name": c.name,
                                     "arguments": c.raw_arguments
                                     or json.dumps(c.arguments, ensure_ascii=False)}}
                       for c in result.tool_calls],
    }


def _tool_message(call: Any, content: str) -> dict:
    return {"role": "tool", "tool_call_id": str(call.id), "name": str(call.name),
            "content": content}


def _call_dict(call: Any) -> dict:
    return {"id": str(call.id), "name": str(call.name),
            "arguments": call.arguments, "raw_arguments": call.raw_arguments}


def _call_from_dict(data: dict) -> Any:
    from .providers import ToolCall
    return ToolCall(id=str(data.get("id") or ""), name=str(data.get("name") or ""),
                    arguments=dict(data.get("arguments") or {}),
                    raw_arguments=str(data.get("raw_arguments") or ""))


def _cost(model: dict, result: ChatResult) -> float:
    """Цены хранятся в USD за 1M токенов (как их публикуют провайдеры).

    PASS3: корзины fresh / cache_read / cache_write считаются РАЗДЕЛЬНО.
    result.tokens_in = fresh + read + write (см. AnthropicAdapter). Цены
    кэш-корзин берутся из model.price_cache_read / price_cache_write, если они
    заданы; иначе — консервативно по price_in (верхняя граница, помечается
    оценкой в наблюдении — «экономия» без известной цены не заявляется)."""
    price_in = float(model.get("price_in") or 0.0)
    price_out = float(model.get("price_out") or 0.0)
    read = int(getattr(result, "cache_read_tokens", 0) or 0)
    write = int(getattr(result, "cache_write_tokens", 0) or 0)
    fresh = max(0, result.tokens_in - read - write)
    p_read = model.get("price_cache_read")
    p_write = model.get("price_cache_write")
    p_read = float(p_read) if p_read is not None else price_in
    p_write = float(p_write) if p_write is not None else price_in
    return (fresh / 1e6 * price_in + read / 1e6 * p_read + write / 1e6 * p_write
            + result.tokens_out / 1e6 * price_out)


def cache_telemetry_enabled() -> bool:
    """BOSSMAN_CACHE_TELEMETRY_V2 — безопасная числовая телеметрия (без контента);
    по умолчанию включена, выключается явным 0."""
    import os
    return os.environ.get("BOSSMAN_CACHE_TELEMETRY_V2", "1").strip().lower() not in ("0", "false", "no")


def cache_observation_for(model: dict, result: ChatResult, *, task_id, run_id) -> dict | None:
    """PASS3 normalized observation для прямого маршрута Command Center.
    Только числа/хэши; None, если shared-контракт недоступен или телеметрия выключена."""
    if not cache_telemetry_enabled():
        return None
    from ._shared import cache_observation as co
    if co is None:
        return None
    meta = result.provider_meta or {}
    raw = meta.get("usage") if isinstance(meta.get("usage"), dict) else None
    pc = meta.get("prompt_cache") if isinstance(meta.get("prompt_cache"), dict) else {}
    provider_kind = str(model.get("provider_kind") or model.get("kind") or "unknown")
    anthropic = bool(pc) or (raw is not None and "input_tokens" in raw and "cache_read_input_tokens" in raw)
    if anthropic:
        buckets = co.normalize_anthropic_usage(raw)
        eligible = bool(pc.get("applied"))
        provider = "anthropic"
    else:
        buckets = co.normalize_openai_style_usage(raw)
        eligible = False                       # прямой не-Anthropic маршрут: кэш не запрашивался
        provider = provider_kind
    route = "local" if str(model.get("kind")) == "local" else "direct"
    price_in = model.get("price_in"); price_out = model.get("price_out")
    from decimal import Decimal
    actual = baseline = None
    est = True
    if buckets is not None and price_in is not None and price_out is not None:
        actual_d, baseline_d, est = co.cost_pair(
            buckets, fresh_per_m=Decimal(str(price_in)),
            read_per_m=(Decimal(str(model["price_cache_read"])) if model.get("price_cache_read") is not None else None),
            write_per_m=(Decimal(str(model["price_cache_write"])) if model.get("price_cache_write") is not None else None),
            output_per_m=Decimal(str(price_out)))
        actual = float(actual_d) if actual_d is not None else None
        baseline = float(baseline_d) if baseline_d is not None else None
    obs = co.build_observation(provider=provider, model=str(model.get("alias") or model.get("name") or "?"),
                               route=route, eligible=eligible, buckets=buckets,
                               cache_control_applied=bool(pc.get("applied")),
                               ttl=("5m" if pc.get("applied") else None),
                               actual_cost_usd=actual, baseline_cost_usd=baseline, baseline_is_estimate=est,
                               task_id_hash=co.opaque(task_id), session_id_hash=co.opaque(run_id))
    return obs.as_dict()
