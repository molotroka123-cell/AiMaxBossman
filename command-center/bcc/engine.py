"""Task Engine (раздел 4): persistent-очередь в БД, lease/heartbeat, checkpoint, retries.

Никакого состояния в памяти: worker берёт run из БД, продлевает аренду, после
каждого шага пишет checkpoint. Падение процесса не теряет задачу — при старте
протухшие аренды возвращаются в очередь (attempt+1), продолжение идёт с checkpoint.
"""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from .db import (Database, agents as agents_t, checkpoints as checkpoints_t, fetch_one,
                 run_events as run_events_t, task_runs as runs_t, tasks as tasks_t, utcnow)
from .events import EventBus
from .providers import ChatResult, ProviderError
from .registry import Registry

ACTIVE_RUN_STATUSES = ("queued", "leased", "running")


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
        # Хуки V2 (контракты §8): фичи регистрируют корутины в setup(); порядок вызова —
        # pick_model → before_run → on_step → gate_completion → on_failure → after_run.
        self.hooks: dict[str, list] = {k: [] for k in (
            "pick_model", "before_run", "on_step", "gate_completion",
            "on_failure", "after_run")}

    def add_hook(self, name: str, fn: Any) -> None:
        if name not in self.hooks:
            raise KeyError(f"нет такого хука: {name}")
        self.hooks[name].append(fn)

    async def _call_hooks(self, name: str, *args: Any) -> list[Any]:
        """Исключение хука логируется и не роняет run (контракты §8)."""
        results: list[Any] = []
        for fn in self.hooks.get(name, ()):
            try:
                results.append(await fn(*args))
            except Exception as exc:
                await self.bus.emit("worker.error",
                                    message=f"хук {name}: {type(exc).__name__}: {exc}")
        return results

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
        for run_id in active_ids:
            worker = self._active.get(run_id)
            if worker is not None and not worker.done():
                self._cancelling.add(run_id)
                worker.cancel()
        await self.bus.emit("task.stopped", task_id=task_id)
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
        return next(iter(self._active), None)

    @property
    def active_run_ids(self) -> list[int]:
        return list(self._active)

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
            # выключение процесса: незавершённые run'ы отдаём lease-recovery
            for t in self._active.values():
                t.cancel()

    async def _execute_pooled(self, run_id: int) -> None:
        """Исполнение в пуле: hard cancel по Stop завершает run как stopped
        с сохранением последнего checkpoint (сообщения уже в БД после каждого шага)."""
        try:
            await self.execute(run_id)
        except asyncio.CancelledError:
            # отмена по Stop: опираемся на статус ЗАДАЧИ в БД (его ставит stop()),
            # а не на разделяемое множество — оно может быть очищено гонкой worker_loop
            async with self.db.session() as s:
                run = await fetch_one(s, runs_t, run_id)
            if run and run["status"] in ("leased", "running"):
                task_status = await self._task_status(run["task_id"])
                if task_status == "stopped" or run_id in self._cancelling:
                    await self._log(run_id, "warn", "run.stopped",
                                    "остановлено оператором (hard cancel: активный вызов модели оборван)")
                    await self._finish(run_id, run["task_id"], "stopped", sync_task=False)
                    return
            raise
        except Exception as exc:
            await self.bus.emit("worker.error",
                                message=f"run {run_id}: {type(exc).__name__}: {exc}")

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
                worker_lease_until=now + timedelta(seconds=self.lease_seconds)))
            await s.commit()
            if not upd.rowcount:      # кто-то успел раньше (несколько процессов на одну БД)
                return None
        return run_id

    async def recover(self) -> int:
        """Crash recovery: протухшие leased/running → queued (attempt+1) либо failed."""
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
                    await s.execute(sa.update(runs_t).where(runs_t.c.id == run["id"]).values(
                        status="queued", attempt=attempt, worker_lease_until=None))
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
        heartbeat = asyncio.create_task(self._heartbeat(run_id))
        try:
            await self._run(run_id)
        finally:
            heartbeat.cancel()

    async def _heartbeat(self, run_id: int) -> None:
        """Продление аренды: пока worker жив, run не считается протухшим."""
        try:
            while True:
                await asyncio.sleep(self.heartbeat_seconds)
                async with self.db.session() as s:
                    await s.execute(sa.update(runs_t).where(
                        runs_t.c.id == run_id,
                        runs_t.c.status.in_(("leased", "running"))).values(
                        worker_lease_until=utcnow() + timedelta(seconds=self.lease_seconds)))
                    await s.commit()
        except asyncio.CancelledError:
            return

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
        for res in await self._call_hooks("before_run", task, run):
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

        while step < max_steps:
            if await self._check_interrupt(run_id, task["id"], messages, step):
                return
            if messages and messages[-1]["role"] == "assistant":
                # финальный ответ уже есть (например, сохранён до паузы) — модель не дёргаем
                break
            try:
                result, model = await self._call_model(task, agent, messages, run_id)
            except ProviderError as exc:
                await self._handle_failure(run_id, task, str(exc), messages, step)
                return
            except LookupError as exc:
                await self._fail_now(run_id, task["id"], str(exc))
                return

            step += 1
            alias = model.get("alias") or alias
            tokens_in += result.tokens_in
            tokens_out += result.tokens_out
            cost += _cost(model, result)
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
            await self._call_hooks("on_step", task, run_id,
                                   {"messages": messages, "step": step,
                                    "checkpoint_id": cp_id})
            # у MVP-агента нет инструментов: следующий виток увидит финальный ответ и выйдет;
            # когда появятся tool-calls, здесь же добавится их выполнение и новый шаг

        if await self._check_interrupt(run_id, task["id"], messages, step):
            return
        if not answer:
            answer = next((m["content"] for m in reversed(messages)
                           if m["role"] == "assistant"), "")
        # gate_completion (Reviewer Gate): задача не станет completed без PASS
        for res in await self._call_hooks("gate_completion", task, run_id, answer):
            if not isinstance(res, dict) or "verdict" not in res:
                continue
            await self.bus.emit("evaluation.completed", task_id=task["id"], run_id=run_id,
                                verdict=res["verdict"],
                                reasons=str(res.get("reasons") or "")[:500])
            if res["verdict"] != "fail":
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
        # лог пишем ДО смены статуса: увидев «completed», UI уже видит полную историю run'а
        await self._log(run_id, "info", "run.completed", "задача выполнена")
        await self._finish(run_id, task["id"], "completed", result=answer,
                           tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=round(cost, 6),
                           model_alias=alias)

    async def _call_model(self, task: dict, agent: dict, messages: list[dict],
                          run_id: int) -> tuple[ChatResult, dict]:
        """Вызов модели: сначала pick_model-хук (Smart Router) может перекрыть выбор;
        при ошибке маршрута — модель агента; при её ошибке — fallback_model."""
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
                result = await adapter.chat(model["name"], messages,
                                            max_tokens=agent.get("max_tokens"))
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
            return await adapter.chat(model["name"], messages,
                                      max_tokens=agent.get("max_tokens")), model
        except ProviderError as exc:
            if not agent.get("fallback_model_id"):
                raise
            await self._log(run_id, "warn", "model.fallback",
                            f"модель {model['alias']} недоступна ({exc}) — пробуем fallback")
            fb_adapter, fb_model = await self.registry.adapter_for(int(agent["fallback_model_id"]))
            result = await fb_adapter.chat(fb_model["name"], messages,
                                           max_tokens=agent.get("max_tokens"))
            await self.bus.emit("model.status", id=model["id"], alias=model["alias"],
                                status="error", detail=str(exc))
            return result, fb_model

    async def _handle_failure(self, run_id: int, task: dict, error: str,
                              messages: list[dict], step: int) -> None:
        """Ошибка провайдера: retry с экспоненциальной паузой, потом — failed."""
        async with self.db.session() as s:
            run = await fetch_one(s, runs_t, run_id)
        attempt = int((run or {}).get("attempt") or 0)
        max_retries = int(task.get("max_retries") or 0)
        await self._log(run_id, "error", "run.error", error)
        await self._call_hooks("on_failure", task, run_id, error)
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
            await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                status="running", started_at=utcnow(),
                worker_lease_until=utcnow() + timedelta(seconds=self.lease_seconds)))
            await s.execute(sa.update(tasks_t).where(
                tasks_t.c.id == task_id,
                tasks_t.c.status.notin_(("stopped", "paused"))).values(
                status="running", updated_at=utcnow()))
            await s.commit()
        await self._log(run_id, "info", "run.started", "выполнение начато")
        await self.bus.emit("task.started", task_id=task_id, run_id=run_id)

    async def _save_checkpoint(self, run_id: int, messages: list[dict], step: int,
                               note: str = "", **values: Any) -> None:
        async with self.db.session() as s:
            await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                checkpoint={"messages": messages, "step": step, "note": note}, **values))
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
            await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(**run_values))
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
            await self._call_hooks("after_run", task_id, run_id, status)

    async def _insert_checkpoint(self, run_id: int, messages: list[dict], step: int,
                                 note: str = "") -> int:
        async with self.db.session() as s:
            res = await s.execute(sa.insert(checkpoints_t).values(
                run_id=run_id, step=step, messages=messages, note=note, created_at=utcnow()))
            cp_id = int(res.inserted_primary_key[0])
            await s.commit()
        await self.bus.emit("checkpoint.created", checkpoint_id=cp_id, run_id=run_id, step=step)
        return cp_id

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


def _cost(model: dict, result: ChatResult) -> float:
    """Цены хранятся в USD за 1M токенов (как их публикуют провайдеры)."""
    price_in = float(model.get("price_in") or 0.0)
    price_out = float(model.get("price_out") or 0.0)
    return result.tokens_in / 1e6 * price_in + result.tokens_out / 1e6 * price_out
