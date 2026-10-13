"""``/work`` in the pult: give a task to the local uncensored Qwen operator (stage 2.2, owner 2026-10-13).

The operator is an L1-Lab worker (``bcc.features.qwen_operator``): isolated worktree, branch ``lab/*``, tests,
commit, receipts; never push / merge / deploy. The pult only carries the owner's words in and the receipts out.

  /work <task>        - create the operator task, report progress and receipts here
  /work_status [id]   - state of the last (or given) task
  /work_stop          - cancel the running operator task (the owner's STOP/pause cancels it too)
  /work_yes <id>      - approve ONE parked owner decision (button under the decision message)
  /work_no <id>       - deny it

Authority is the same as Claude/Codex control (``AgentBridgeMixin.agent_policy``): the owner's Telegram ID,
fresh config, checked on the command AND again before every message is delivered. A STOP / pause / lock
(``delegation_locked``) blocks new tasks exactly like the generation commands. No new config keys.
"""
from __future__ import annotations

import asyncio
import contextlib

from .config import CompanionError, Person

WORK_COMMANDS = {"/work", "/work_stop", "/work_status", "/work_yes", "/work_no"}
WORK_DENIED = "Нет прав. Оператор Qwen доступен только с Telegram ID владельца."
WORK_LOCKED = "Новые поручения заблокированы владельцем (СТОП или пауза). Снять — /resume."
TEXT_LIMIT = 3600
POLL_SECONDS = 5.0
DECISION_KIND = "operator_decision"


def decision_text(task_id: str, d: dict) -> str:
    return (f"⚠️ Решение владельца · задача {task_id}\n"
            f"Категория: {d.get('category')}\n"
            f"Действие: {str(d.get('action'))[:600]}\n"
            f"Зачем: {str(d.get('why'))[:500]}\n"
            f"Рекомендация оператора: {str(d.get('recommendation'))[:500]}\n"
            f"Опасения: {str(d.get('concerns'))[:500]}\n"
            "Этот шаг приостановлен, остальные продолжаются.")


def receipt_text(rec: dict) -> str:
    calls = rec.get("model_calls") or []
    models = ", ".join(sorted({f"{c.get('role')}={c.get('model')}" for c in calls if c.get("model")})) or "—"
    tin = sum(int(c.get("tokens_in") or 0) for c in calls)
    tout = sum(int(c.get("tokens_out") or 0) for c in calls)
    lab = rec.get("lab") or {}
    ver = rec.get("verifier") or {}
    lines = [f"Оператор Qwen · задача {rec.get('id')}: {rec.get('status')}",
             f"Модели: {models}", f"Токены: {tin} → {tout} · стоимость $0.00",
             f"Лаборатория: {lab.get('branch')}" + (f" · коммит {str(rec.get('commit_sha'))[:10]}"
                                                  if rec.get("commit_sha") else " · коммита нет"),
             f"Проверка: {ver.get('verdict', 'не выполнялась')} ({ver.get('independence', '-')})"]
    if rec.get("error"):
        lines.append(f"Ошибка: {str(rec['error'])[:300]}")
    pend = [d for d in rec.get("decisions", []) if d.get("status") == "PENDING"]
    if pend:
        lines.append(f"Ждут вашего решения: {len(pend)}")
    lines.append("Продвижение в основной проект заблокировано (L1 Lab): " +
                 str((rec.get("promotion") or {}).get("reason", "правило 4 конституции v1")))
    return "\n".join(lines)[:TEXT_LIMIT]


class OperatorBridgeMixin:
    async def work_command(self, person: Person, command: str, arg: str, message: dict):
        from .service import Reply
        if self.agent_policy(person, message) is None:
            return WORK_DENIED
        job = self.agent_jobs.get("operator")
        busy = job is not None and not job.done()
        if command == "/work_stop":
            if arg.strip():                       # a task the backend holds but this chat did not start
                with contextlib.suppress(CompanionError):
                    await self.core.operator_cancel(arg.strip())
                return "✋ Остановка запрошена; в лаборатории остаётся только то, что уже закоммичено."
            if not busy:
                return "Оператор сейчас ничего не делает."
            job.cancel()
            return "✋ Оператор остановлен; в лаборатории остаётся только то, что уже закоммичено."
        if command in {"/work_yes", "/work_no"}:
            if not arg.strip().isdecimal():
                return "Укажите номер решения: /work_yes 12"
            aid = int(arg.strip())
            row = await self.core.approval(aid)
            if not row or row.get("kind") != DECISION_KIND:
                return "Это не решение оператора. Ничего не изменено."
            await self.core.decide_approval(aid, command == "/work_yes", "owner-pult")
            return ("✅ Разрешено. Оператор продолжит этот шаг." if command == "/work_yes"
                    else "⛔ Отклонено. Оператор этот шаг пропускает и докладывает.")
        if command == "/work_status":
            tid = arg.strip() or self.store.get("operator_last_task", "")
            if not tid:
                return "Задач оператора ещё не было."
            return receipt_text(await self.core.operator_task(tid))
        # /work <task>
        if not arg.strip():
            return "Напишите /work и задание, например: /work поправь докстринг функции X и запусти её тесты"
        if self.store.get("delegation_locked", False):
            return WORK_LOCKED
        if busy:
            return Reply("Оператор ещё работает над прошлым заданием.",
                         [[self.button(person, "✋ Стоп оператору", "/work_stop")]])
        try:
            rec = await self.core.operator_start(arg.strip())
        except CompanionError as exc:
            if str(exc) != "CONFLICT":
                raise
            # The backend allows one operator task at a time and it is held by a task this chat did not start
            # (API call, another session). Say so and give the way out instead of a generic «CONFLICT».
            active = None
            with contextlib.suppress(CompanionError):
                active = await self.core.operator_active()
            if active is None:
                return "Оператор сейчас занят (одна задача за раз). Подождите минуту и повторите /work."
            return Reply(f"Оператор занят задачей {active['id']}: {str(active.get('task') or '')[:200]}\n"
                         "Одна задача за раз. Остановите её или дождитесь конца, затем повторите /work.",
                         [[self.button(person, "📋 Статус", f"/work_status {active['id']}"),
                           self.button(person, "✋ Остановить её", f"/work_stop {active['id']}")]])
        self.store.put("operator_last_task", rec["id"])
        self.agent_jobs["operator"] = asyncio.create_task(self.work_turn(person, rec["id"]))
        return Reply(f"🛠 Оператор Qwen принял задание, задача {rec['id']}. Работает в лаборатории (ветка "
                     f"{(rec.get('lab') or {}).get('branch')}), в основной проект ничего не попадает. Итог и "
                     "вопросы пришлю сюда.", [[self.button(person, "✋ Стоп оператору", "/work_stop")]])

    async def work_turn(self, person: Person, task_id: str) -> None:
        announced: set[int] = set()
        try:
            while True:
                rec = await self.core.operator_task(task_id)
                if self.agent_policy(person) is None:
                    return
                if self.store.get("delegation_locked", False):
                    await self.core.operator_cancel(task_id)
                    return
                for d in rec.get("decisions", []):
                    aid = d.get("approval_id")
                    if d.get("status") == "PENDING" and aid not in announced:
                        announced.add(aid)
                        with contextlib.suppress(CompanionError):
                            await self.telegram.send(person, decision_text(task_id, d), [[
                                self.button(person, "✅ Разрешить", f"/work_yes {aid}"),
                                self.button(person, "⛔ Отклонить", f"/work_no {aid}")]])
                if rec.get("status") in ("completed", "completed_with_pending_decisions", "failed", "stopped"):
                    with contextlib.suppress(CompanionError):
                        await self.telegram.send(person, receipt_text(rec))
                    return
                await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self.core.operator_cancel(task_id)
            if self.agent_policy(person) is not None:
                with contextlib.suppress(CompanionError, Exception):
                    await self.telegram.send(person, f"✋ Оператор Qwen остановлен (задача {task_id}).")
            raise
        except CompanionError as exc:
            if self.agent_policy(person) is not None:
                with contextlib.suppress(CompanionError):
                    await self.telegram.send(person, f"Оператор: связь с Bossman потеряна ({exc}). "
                                                     f"Проверить: /work_status {task_id}")
