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

from .db import (Database, agents as agents_t, approvals as approvals_t,
                 checkpoints as checkpoints_t, fetch_one, run_events as run_events_t,
                 task_runs as runs_t, tasks as tasks_t, tool_calls as tool_calls_t, utcnow)
from .events import EventBus
from .plugin_security import redact as _ps_redact, redact_text as _ps_redact_text
from .providers import ChatResult, ProviderError
from .registry import Registry
from .tools import (REGISTRY as TOOLS, ToolContext, agent_policy_rules, allowed_tools_for,
                    approval_digest,
                    args_hash, decide_effect, execute_tool)

ACTIVE_RUN_STATUSES = ("queued", "leased", "running")

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
                coro = fn(*args)
                res = await (asyncio.wait_for(coro, timeout=timeout)
                             if timeout is not None else coro)
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

    async def enqueue(self, task_id: int, *, attempt: int = 0,
                      checkpoint: dict | None = None) -> int:
        """Создать run в состоянии queued и перевести задачу в queued."""
        async with self.db.session() as s:
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
        """Stop — жёсткий: флаг в БД + hard cancel активного run'а (обрывает
        и уже начатый HTTP-inference, не дожидаясь конца генерации)."""
        await self._set_task_status(task_id, "stopped")
        async with self.db.session() as s:
            # ещё не начатые run'ы гасим сразу — ждать нечего
            await s.execute(sa.update(runs_t).where(
                runs_t.c.task_id == task_id, runs_t.c.status == "queued").values(
                status="stopped", finished_at=utcnow()))
            res = await s.execute(sa.select(runs_t.c.id).where(
                runs_t.c.task_id == task_id,
                runs_t.c.status.in_(("leased", "running"))))
            active_ids = [int(r[0]) for r in res.fetchall()]
            await s.commit()
        # Событие — ДО отмены, и это не косметика. Stop зовут в том числе
        # изнутри самого прогона (инструмент, хук, тест через ASGI в одной
        # задаче с worker'ом). Тогда worker.cancel() отменяет ту же задачу,
        # которая сейчас исполняет stop(), и следующий же await обрывается —
        # вместе с записью события в БД, посреди работы драйвера. Соединение
        # после такого обрыва не возвращается в пул никаким close(): его
        # состояние уже неизвестно SQLAlchemy. Поэтому свою работу stop()
        # доводит до конца первым, а отмену выдаёт последней — после неё
        # здесь не остаётся ни одного await.
        # finally: упавшая запись события не имеет права отменить саму
        # остановку — иначе прогон продолжит работать из-за сбоя журнала.
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
        await self._set_task_status(task_id, "paused")
        await self.bus.emit("task.paused", task_id=task_id)
        return {"ok": True, "status": "paused"}

    async def resume(self, task_id: int) -> dict:
        """Снять паузу: run с checkpoint снова становится доступен worker'у."""
        run = await self.active_run(task_id)
        if run is None:
            last = await self.last_run(task_id)
            checkpoint = (last or {}).get("checkpoint")
            attempt = int((last or {}).get("attempt") or 0)
            await self.enqueue(task_id, attempt=attempt, checkpoint=checkpoint)
        else:
            async with self.db.session() as s:
                await s.execute(sa.update(runs_t).where(runs_t.c.id == run["id"]).values(
                    status="queued", worker_lease_until=None))
                await s.commit()
            await self._set_task_status(task_id, "queued")
        await self.bus.emit("task.queued", task_id=task_id, resumed=True)
        return {"ok": True, "status": "queued"}

    async def retry(self, task_id: int) -> dict:
        """Ручной перезапуск: новая попытка с нуля (счётчик attempt сбрасывается)."""
        run_id = await self.enqueue(task_id, attempt=0)
        return {"ok": True, "status": "queued", "run_id": run_id}

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
                        await asyncio.sleep(self.poll_interval / 2)
                        continue
                    run_id = await self.claim()
                    if run_id is None:
                        await asyncio.sleep(self.poll_interval)
                        continue
                    self._active[run_id] = asyncio.create_task(
                        self._execute_pooled(run_id), name=f"bcc-run-{run_id}")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # worker не должен умирать от одной задачи
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
                sa.select(runs_t, tasks_t.c.max_retries)
                .join(tasks_t, tasks_t.c.id == runs_t.c.task_id)
                .where(runs_t.c.status.in_(("leased", "running")),
                       runs_t.c.worker_lease_until.isnot(None),
                       runs_t.c.worker_lease_until <= now))
            stale = [dict(r._mapping) for r in res.fetchall()]
        for run in stale:
            attempt = int(run["attempt"] or 0) + 1
            max_retries = int(run["max_retries"] or 0)
            if attempt <= max_retries:
                async with self.db.session() as s:
                    # FL-01: новый epoch — прежний держатель (если он ещё жив и
                    # просто «замёрз») больше не может ни писать, ни продлевать аренду.
                    await s.execute(sa.update(runs_t).where(runs_t.c.id == run["id"]).values(
                        status="queued", attempt=attempt, worker_lease_until=None,
                        fence=sa.func.coalesce(runs_t.c.fence, 0) + 1))
                    await s.execute(sa.update(tasks_t).where(tasks_t.c.id == run["task_id"]).values(
                        status="queued", updated_at=utcnow()))
                    await s.commit()
                await self._log(run["id"], "warn", "run.recovered",
                                f"аренда истекла, задача возвращена в очередь (попытка {attempt})")
                await self.bus.emit("task.queued", task_id=run["task_id"], run_id=run["id"],
                                    attempt=attempt, recovered=True)
            else:
                await self._fail_now(run["id"], run["task_id"],
                                     "аренда истекла, попытки исчерпаны")
        return len(stale)

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

    async def _fenced_out_exit(self, run_id: int, why: str) -> None:
        """Выход зомби-воркера: только журнал и событие, никаких записей в run."""
        with contextlib.suppress(Exception):
            await self._log(run_id, "warn", "run.fenced_out", why[:500])
        with contextlib.suppress(Exception):
            await self.bus.emit("run.fenced_out", run_id=run_id, fence=self._fences.get(run_id),
                                reason=why[:200])

    async def _run(self, run_id: int) -> None:
        async with self.db.session() as s:
            run = await fetch_one(s, runs_t, run_id)
            if run is None:
                return
            task = await fetch_one(s, tasks_t, run["task_id"])
            agent = await fetch_one(s, agents_t, task["agent_id"]) if task and task["agent_id"] else None
        if task is None:
            return
        if agent is None:
            await self._fail_now(run_id, task["id"],
                                 "у задачи не выбран агент — некому её выполнять")
            return
        if not agent.get("enabled", True):
            await self._fail_now(run_id, task["id"], f"агент «{agent['name']}» выключен")
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
                    await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                        status="queued",
                        worker_lease_until=utcnow() + timedelta(seconds=delay)))
                    await s.commit()
                await self._log(run_id, "warn", "run.deferred",
                                f"отложено на {delay:.0f} с: {res.get('reason', '')}")
                return
            if isinstance(res, dict) and res.get("fail"):
                await self._fail_now(run_id, task["id"], str(res["fail"]))
                return

        await self._start(run_id, task["id"])
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
                                                       tools=tool_schemas)
            except ProviderError as exc:
                await self._handle_failure(run_id, task, str(exc), messages, step)
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
                    await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                        status="queued", worker_lease_until=None,
                        checkpoint={"messages": messages, "step": step, "note": "review_fail"}))
                    await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(
                        status="queued", updated_at=utcnow()))
                    await s.commit()
                await self.bus.emit("task.queued", task_id=task["id"], run_id=run_id,
                                    review_retry=True)
            else:
                # попытки ревью исчерпаны → человеку (waiting_approval), run ждёт с checkpoint
                async with self.db.session() as s:
                    await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                        status="queued", worker_lease_until=None,
                        checkpoint={"messages": messages, "step": step,
                                    "note": "review_escalated"}))
                    await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(
                        status=str(res.get("status") or "waiting_approval"),
                        updated_at=utcnow()))
                    await s.commit()
                await self.bus.emit("task.progress", task_id=task["id"], run_id=run_id,
                                    waiting_approval=True)
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
            await self._escalate_gate_failure(run_id, task, messages, step,
                                              CriticalHookFailure("finalize", "bcc.finalize.finalize_task", decision.reason))
            return
        await self._log(run_id, "info", "run.completed", "задача выполнена")

    async def _call_model(self, task: dict, agent: dict, messages: list[dict],
                          run_id: int, *, tools: list[dict] | None = None
                          ) -> tuple[ChatResult, dict]:
        """Вызов модели: сначала pick_model-хук (Smart Router) может перекрыть выбор;
        при ошибке маршрута — модель агента; при её ошибке — fallback_model.
        `tools` — схемы ТОЛЬКО выданных этому run'у инструментов."""
        kw: dict[str, Any] = {"max_tokens": agent.get("max_tokens")}
        if tools:
            kw["tools"] = tools
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
                appr = await self._approvals_create(
                    kind="tool",
                    preview=_ps_redact_text(
                        f"Агент «{agent.get('name')}» хочет выполнить {spec.name}\n"
                        f"причина политики: {reason}\n"
                        f"approval_digest: {digest[:16]}…\nаргументы: "
                        + json.dumps(_ps_redact(shown_args), ensure_ascii=False, indent=1)[:2000]),
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

            await self._run_tool_now(run_id, task, agent, messages, call, spec, step)
        return False

    async def _run_tool_now(self, run_id: int, task: dict, agent: dict,
                            messages: list[dict], call: Any, spec: Any, step: int,
                            *, approval_id: int | None = None,
                            approved_by: str | None = None) -> None:
        """Выполнить инструмент и положить результат в историю как tool-сообщение."""
        ctx = ToolContext(svc=self.services, task=task, run_id=run_id, agent=agent,
                          step=step, workspace=str(task.get("workspace_path") or ""),
                          call_id=str(call.id))
        # FL-01 §2.2 п.3: fence проверяется ДО эффекта, не только при записи receipt.
        await self.assert_fence(run_id)
        # INV-2 идемпотентность: неидемпотентный шаг с тем же (task, step, args)
        # уже исполнен прежней попыткой (рестарт между эффектом и checkpoint) —
        # исполнитель не вызывается второй раз, модели отдаётся сохранённый исход.
        if not getattr(spec, "idempotent", True):
            prior = await self._prior_effect(task["id"], run_id, step, spec.name, call.arguments)
            if prior is not None:
                await self._record_tool_call(
                    run_id, task["id"], step, call, spec,
                    effect="auto" if approval_id is None else "ask", status="replayed",
                    approval_id=approval_id, approved_by=approved_by,
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
        started = time.monotonic()
        result = await execute_tool(spec, call.arguments, ctx)
        duration = int((time.monotonic() - started) * 1000)
        await self._record_tool_call(
            run_id, task["id"], step, call, spec,
            effect="auto" if approval_id is None else "ask",
            status="error" if result.error else "executed",
            approval_id=approval_id, approved_by=approved_by,
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
            else:
                await self._mark_tool_call(run_id, call.id, status="approved",
                                           approved_by=str((row or {}).get("decided_by") or ""))
                await self._run_tool_now(run_id, task, agent, messages, call, spec, step,
                                         approval_id=int(approval_id) if approval_id else None,
                                         approved_by=str((row or {}).get("decided_by") or ""))
        else:
            await self._mark_tool_call(run_id, call.id, status="rejected",
                                       approved_by=str((row or {}).get("decided_by") or ""))
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

    async def _record_tool_call(self, run_id: int, task_id: int, step: int, call: Any,
                                spec: Any, *, effect: str, status: str,
                                approval_id: int | None = None,
                                approved_by: str | None = None, preview: str = "",
                                truncated: bool = False, duration_ms: int | None = None,
                                error: str | None = None) -> None:
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
        }
        # Строка пишется ПОСЛЕ того, как инструмент отработал, поэтому «сейчас» —
        # это момент завершения, а не начала. Раньше оба времени брались двумя
        # вызовами utcnow() подряд: получалась строка, в которой вызов длился
        # duration_ms по одному полю и ноль по другим двум. Начало восстанавливаем
        # из измеренной длительности — тогда finished_at - created_at и duration_ms
        # говорят одно и то же, и вопрос «когда этот вызов начался» имеет ответ.
        now = utcnow()
        done = status not in ("pending_approval",)
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
                    status=status, result_preview=preview, truncated=truncated,
                    duration_ms=duration_ms, error=error, approved_by=approved_by,
                    finished_at=utcnow()))
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

    async def _tool_call_status(self, run_id: int, call_id: str) -> str:
        async with self.db.session() as s:
            row = (await s.execute(sa.select(tool_calls_t.c.status).where(sa.and_(
                tool_calls_t.c.run_id == run_id,
                tool_calls_t.c.call_id == str(call_id))))).first()
        return str(row[0]) if row else ""

    async def _handle_failure(self, run_id: int, task: dict, error: str,
                              messages: list[dict], step: int) -> None:
        """Ошибка провайдера: retry с экспоненциальной паузой, потом — failed."""
        async with self.db.session() as s:
            run = await fetch_one(s, runs_t, run_id)
        attempt = int((run or {}).get("attempt") or 0)
        max_retries = int(task.get("max_retries") or 0)
        await self._log(run_id, "error", "run.error", error)
        await self._call_hooks_soft("on_failure", task, run_id, error)
        if attempt < max_retries:
            delay = min(self.retry_base_delay * (2 ** attempt), self.retry_max_delay)
            # пауза хранится в БД (queued + «не раньше»), а не в sleep — переживает рестарт
            async with self.db.session() as s:
                await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                    status="queued", attempt=attempt + 1, error=error,
                    checkpoint={"messages": messages, "step": step, "note": "retry"},
                    worker_lease_until=utcnow() + timedelta(seconds=delay)))
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(
                    status="queued", updated_at=utcnow()))
                await s.commit()
            await self._log(run_id, "warn", "run.retry",
                            f"попытка {attempt + 1}/{max_retries} через {delay:.0f} с")
            await self.bus.emit("task.queued", task_id=task["id"], run_id=run_id,
                                attempt=attempt + 1, retry=True)
            return
        await self._finish(run_id, task["id"], "failed", error=error,
                           checkpoint={"messages": messages, "step": step, "note": "failed"})

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
                      checkpoint: dict | None = None, sync_task: bool = True,
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
                runs_t.c.id == run_id, self._fence_clause(run_id)).values(**run_values))
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
            await self._log(run_id, "error", "run.gate_failed", reason[:500])
            await self._approvals_create(
                kind="review_escalation",
                preview=(f"Проверка завершения задачи «{str(task.get('title') or '')[:80]}» "
                         f"не выполнена: хук {exc.hook} — {exc.reason}. "
                         f"Задача НЕ считается выполненной; нужно решение человека."),
                task_id=task_id, run_id=run_id)
            async with self.db.session() as s:
                await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                    status="queued", worker_lease_until=None,
                    checkpoint={"messages": messages, "step": step,
                                "note": "gate_hook_failed"}))
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
                    status="waiting_approval", updated_at=utcnow()))
                await s.commit()
        except asyncio.CancelledError:
            raise
        except Exception as inner:  # noqa: BLE001 — эскалация не удалась → честный failed
            await self.bus.emit("hook.escalation_failed", hook=exc.name, fn=exc.hook,
                                error=type(inner).__name__)
            await self._fail_now(run_id, task_id,
                                 f"{reason}; escalation failed: {type(inner).__name__}")
            return
        await self.bus.emit("task.progress", task_id=task_id, run_id=run_id,
                            waiting_approval=True, gate_hook_failed=exc.hook)

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
