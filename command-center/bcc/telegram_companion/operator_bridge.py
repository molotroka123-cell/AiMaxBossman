"""``/work`` in the pult: give a task to the local uncensored Qwen operator (stage 2.2, owner 2026-10-13).

The operator is an L1-Lab worker (``bcc.features.qwen_operator``): isolated worktree, branch ``lab/*``, tests,
commit, receipts; never push / merge / deploy. The pult only carries the owner's words in and the receipts out.

  /work [ctx=64k] <task> - create the operator task (ctx= overrides the context of this task: 4k..256k);
                           while the operator is busy the task is QUEUED (FIFO) instead of refused
  /work_status [id]      - state of the last (or given) task + the running/queued list
  /work_stop [id]        - cancel the running task, or any task / queue entry by id
  /work_yes <id>         - approve ONE parked owner decision (button under the decision message)
  /work_no <id>          - deny it

Authority is the same as Claude/Codex control (``AgentBridgeMixin.agent_policy``): the owner's Telegram ID,
fresh config, checked on the command AND again before every message is delivered. A STOP / pause / lock
(``delegation_locked``) blocks new tasks exactly like the generation commands. No new config keys.

The watcher never gives up silently: a lost connection to Bossman is retried with backoff for hours, a
heartbeat line goes out every few minutes, and the final receipt is retried until Telegram takes it.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time

from .config import CompanionError, Person

WORK_COMMANDS = {"/work", "/work_stop", "/work_status", "/work_yes", "/work_no"}
WORK_DENIED = "Нет прав. Оператор Qwen доступен только с Telegram ID владельца."
WORK_LOCKED = "Новые поручения заблокированы владельцем (СТОП или пауза). Снять — /resume."
TEXT_LIMIT = 3600
POLL_SECONDS = 5.0
HEARTBEAT_SECONDS = 300.0          # «работает: шаг N, M мин»
BACKOFF_BASE = 5.0
BACKOFF_MAX = 60.0
OUTAGE_NOTICE_S = 120.0            # tell the owner the link is down after this long
OUTAGE_LIMIT_S = 6 * 3600.0        # keep retrying for hours; then say so loudly (never silent)
SEND_RETRIES = 12
OUTCOME_WAIT_S = 10.0              # how long the «Разрешить» answer waits for the real outcome
DECISION_KIND = "operator_decision"
TERMINAL = ("completed", "completed_with_pending_decisions", "failed", "stopped")
CTX_MIN, CTX_MAX = 4096, 262144

_CTX_RE = re.compile(r"^\s*ctx\s*=\s*(\d+)\s*([kKкК]?)(?=\s|$)\s*(.*)$", re.S)


def parse_ctx(arg: str) -> tuple[int | None, str, str]:
    """``ctx=64k fix X`` -> (65536, "fix X", ""). Error text (Russian) in the third slot."""
    m = _CTX_RE.match(arg)
    if not m:
        return None, arg.strip(), ""
    n = int(m.group(1)) * (1024 if m.group(2) else 1)
    if not CTX_MIN <= n <= CTX_MAX:
        return None, "", "ctx= должен быть от 4k до 256k, например /work ctx=64k <задача>"
    return n, m.group(3).strip(), ""


def _dur(seconds: float | int | None) -> str:
    s = int(seconds or 0)
    return f"{s}с" if s < 60 else f"{s // 60}м{s % 60:02d}с"


def _k(n) -> str:
    return f"{int(n) // 1024}k" if n else "?"


def outcome_text(d: dict) -> str:
    st = d.get("status")
    note = str(d.get("note") or "")
    if st == "APPROVED_NOT_EXECUTED":
        return f"✅ одобрено, но НЕ выполнено: {note[:300]}"
    if st == "APPROVED_BLOCKED_BY_RULE_4":
        return f"✅ одобрено, но заблокировано правилом 4: {note[:300]}"
    if st == "APPROVED_EXECUTED":
        return f"✅ выполнено в лаборатории (exit {d.get('exit_code')})"
    if st == "APPROVED_EXECUTED_FAILED":
        return f"⚠️ одобрено и выполнено, но команда упала (exit {d.get('exit_code')})"
    if st == "DENIED_BY_OWNER":
        return "⛔ отклонено: шаг не выполнялся"
    if st == "PENDING":
        return "ждёт применения в конце текущего шага"
    return f"{st}: {note[:200]}"


def predicted_outcome(d: dict | None) -> str:
    """What applying an approval WILL do, known before the operator reaches it (nothing here executes)."""
    if not d:
        return "Решение применится в конце текущего шага оператора; итог пришлю сюда."
    if d.get("category") in ("promotion", "force_push"):
        return ("Одобрено, но оператор push/merge НЕ выполняет (конституция v1, правило 4). "
                "Итог зафиксируется в конце текущего шага.")
    if not (d.get("executable") and d.get("argv")):
        return ("Одобрено, но оператор такое НЕ выполняет (защищённые файлы, конституция, деньги, публикации, "
                "ключи правит только владелец). Итог зафиксируется в конце текущего шага.")
    return "Команда выполнится в лаборатории в конце текущего шага; итог пришлю сюда."


def decision_text(task_id: str, d: dict) -> str:
    return (f"⚠️ Решение владельца · задача {task_id}\n"
            f"Категория: {d.get('category')}\n"
            f"Действие: {str(d.get('action'))[:600]}\n"
            f"Зачем: {str(d.get('why'))[:500]}\n"
            f"Рекомендация оператора: {str(d.get('recommendation'))[:500]}\n"
            f"Опасения: {str(d.get('concerns'))[:500]}\n"
            + ("Кнопка «Разрешить» СЛОЕТ проверенный коммит в main. " if d.get("category") == "release_main" else "")
            + "Этот шаг приостановлен, остальные продолжаются. Ваш ответ применится в конце ТЕКУЩЕГО шага "
            "оператора; фактический итог (выполнено / не выполнено и почему) пришлю сразу.")


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
    tm = rec.get("timings") or {}
    if tm:
        roles = ", ".join(f"{r} {_dur(v)}" for r, v in sorted((tm.get("by_role_s") or {}).items(),
                                                              key=lambda kv: -kv[1]) if v)
        extra = []
        if tm.get("tests_s"):
            extra.append(f"тесты {_dur(tm['tests_s'])}")
        if tm.get("load_s"):
            extra.append(f"загрузка модели {_dur(tm['load_s'])}")
        lines.append(f"Время: всего {_dur(tm.get('total_s'))}" + (f" · {roles}" if roles else "") +
                     ("".join(" · " + e for e in extra)))
        ph = sorted((tm.get("phase_s") or {}).items(), key=lambda kv: -kv[1])[:4]
        if ph:
            lines.append("Куда ушло время: " + ", ".join(f"{k} {_dur(v)}" for k, v in ph))
        ctxs = tm.get("ctx_by_role") or {}
        if any(ctxs.values()):
            lines.append("Контекст: " + ", ".join(f"{r} {_k(v)}" for r, v in ctxs.items() if v))
    cr = rec.get("ctx_report") or {}
    if cr.get("insufficient"):
        retry = cr.get("retries") or []
        lines.append(f"⚠️ контекста не хватало: {cr.get('max_pct')}%" +
                     (" · повтор с ctx " + _k(retry[-1].get("to")) if retry else "") +
                     (" · ответ был обрезан" if cr.get("truncated") else ""))
    elif cr.get("max_pct"):
        lines.append(f"Контекст: пик {cr['max_pct']}% — хватало")
    if rec.get("analysis"):
        lines.append("Анализ (только чтение): " + str(rec["analysis"])[:1200])
    decs = rec.get("decisions") or []
    for d in decs:
        if d.get("status") != "PENDING":
            lines.append(f"Решение {d.get('approval_id')} ({str(d.get('action'))[:60]}): {outcome_text(d)}")
    if rec.get("error"):
        lines.append(f"Ошибка: {str(rec['error'])[:300]}")
    pend = [d for d in decs if d.get("status") == "PENDING"]
    if pend:
        lines.append(f"Ждут вашего решения: {len(pend)}")
    calls = rec.get("bossman_calls") or []
    if calls:
        ok = sum(1 for c in calls if c.get("ok"))
        lines.append(f"Вызовы Bossman: {ok}/{len(calls)} успешно · " +
                     "; ".join(f"{c.get('method')} {c.get('path')} → {c.get('status')}" for c in calls[:5]))
    rel = [d for d in rec.get("decisions", []) if d.get("category") == "release_main"]
    if rel and rel[0].get("status") == "APPROVED_EXECUTED":
        lines.append("✅ Слито в main по вашей кнопке. " + str(rel[0].get("note") or ""))
    elif rel and rel[0].get("status") == "PENDING":
        lines.append("⏳ Слияние в main ждёт вашей кнопки «Разрешить» (после неё изменение попадёт в main).")
    elif rel and rel[0].get("status") == "APPROVED_REFUSED":
        lines.append("⛔ " + str(rel[0].get("note") or "слияние в main не выполнено"))
    else:
        lines.append("Продвижение в основной проект заблокировано (L1 Lab): " +
                     str((rec.get("promotion") or {}).get("reason", "правило 4 конституции v1")))
    return "\n".join(lines)[:TEXT_LIMIT]


class OperatorBridgeMixin:
    def _operator_live(self) -> list[asyncio.Task]:
        return [j for k, j in self.agent_jobs.items() if k.startswith("operator") and j is not None and not j.done()]

    def _operator_register(self, task_id: str, job: asyncio.Task) -> None:
        primary = self.agent_jobs.get("operator")
        if primary is None or primary.done():
            self.agent_jobs["operator"] = job
        else:
            self.agent_jobs[f"operator:{task_id}"] = job

    def _operator_promote(self, task_id: str, me: asyncio.Task | None) -> None:
        self.agent_jobs.pop(f"operator:{task_id}", None)
        if self.agent_jobs.get("operator") is me:
            nxt = next((j for k, j in list(self.agent_jobs.items())
                        if k.startswith("operator:") and j is not None and not j.done()), None)
            if nxt is not None:
                for k, j in list(self.agent_jobs.items()):
                    if j is nxt:
                        self.agent_jobs.pop(k, None)
                self.agent_jobs["operator"] = nxt
            else:
                self.agent_jobs.pop("operator", None)

    async def _decision_of(self, task_id: str, aid: int) -> dict | None:
        if not task_id:
            return None
        with contextlib.suppress(Exception):
            rec = await self.core.operator_task(task_id)
            return next((d for d in rec.get("decisions", []) if d.get("approval_id") == aid), None)
        return None

    async def work_command(self, person: Person, command: str, arg: str, message: dict):
        from .service import Reply
        if self.agent_policy(person, message) is None:
            return WORK_DENIED
        job = self.agent_jobs.get("operator")
        busy = bool(self._operator_live())
        if command == "/work_stop":
            if arg.strip():                       # a task id: running, queued, or held by another session
                with contextlib.suppress(CompanionError):
                    await self.core.operator_cancel(arg.strip())
                return "✋ Остановка запрошена; в лаборатории остаётся только то, что уже закоммичено."
            if job is None or job.done():
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
            last = self.store.get("operator_last_task", "")
            before = await self._decision_of(last, aid)
            await self.core.decide_approval(aid, command == "/work_yes", "owner-pult")
            if command == "/work_no":
                return "⛔ Отклонено. Оператор этот шаг пропускает и докладывает."
            # show the REAL outcome as soon as the backend has recorded it (bounded wait), else say exactly
            # what will happen and when - never «оператор продолжит этот шаг» for a file that is never edited
            deadline = time.monotonic() + (OUTCOME_WAIT_S if before else 0.0)
            while True:
                now = await self._decision_of(last, aid)
                if now and now.get("status") not in (None, "PENDING"):
                    return f"✅ Разрешено. Итог: {outcome_text(now)}"
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(1.0)
            return "✅ Разрешено. " + predicted_outcome(before or None)
        if command == "/work_status":
            tid = arg.strip() or self.store.get("operator_last_task", "")
            queue = []
            if not arg.strip() and hasattr(self.core, "operator_queue"):
                with contextlib.suppress(Exception):
                    queue = await self.core.operator_queue()
            if not tid and not queue:
                return "Задач оператора ещё не было."
            text = receipt_text(await self.core.operator_task(tid)) if tid else ""
            if queue:
                rows = [("▶ работает" if q.get("status") == "running" else f"⏳ №{q.get('queue_position') or '?'}")
                        + f" · {q.get('id')} · {str(q.get('task') or '')[:60]}" for q in queue]
                text = (text + "\n\n" if text else "") + "Очередь оператора:\n" + "\n".join(rows)
                buttons = [[self.button(person, f"✖ {q['id'][:6]}", f"/work_stop {q['id']}")]
                           for q in queue if q.get("status") == "queued"][:6]
                if buttons:
                    return Reply(text[:TEXT_LIMIT], buttons)
            return text[:TEXT_LIMIT]
        # /work <task>
        num_ctx, task, bad = parse_ctx(arg)
        if bad:
            return bad
        if not task:
            return "Напишите /work и задание, например: /work поправь докстринг функции X и запусти её тесты"
        if self.store.get("delegation_locked", False):
            return WORK_LOCKED
        try:
            rec = await (self.core.operator_start(task, num_ctx=num_ctx) if num_ctx
                         else self.core.operator_start(task))
        except CompanionError as exc:
            if str(exc) != "CONFLICT":
                raise
            active = None
            with contextlib.suppress(CompanionError):
                active = await self.core.operator_active()
            if active is None:
                return "Оператор сейчас занят. Подождите минуту и повторите /work."
            return Reply(f"Оператор занят задачей {active['id']}: {str(active.get('task') or '')[:200]}\n"
                         "Остановите её или дождитесь конца, затем повторите /work.",
                         [[self.button(person, "📋 Статус", f"/work_status {active['id']}"),
                           self.button(person, "✋ Остановить её", f"/work_stop {active['id']}")]])
        self.store.put("operator_last_task", rec["id"])
        queued = busy or rec.get("queue_position")
        self._operator_register(rec["id"], asyncio.create_task(self.work_turn(person, rec["id"])))
        ctx_note = f" · ctx {_k(num_ctx)}" if num_ctx else ""
        if queued:
            pos = rec.get("queue_position") or len(self._operator_live())
            return Reply(f"⏳ Оператор занят: задача {rec['id']} в очереди (№{pos}){ctx_note}. Начнётся сама, "
                         "результат пришлю сюда. Снять из очереди — кнопка.",
                         [[self.button(person, "✖ Убрать из очереди", f"/work_stop {rec['id']}"),
                           self.button(person, "📋 Очередь", "/work_status")]])
        return Reply(f"🛠 Оператор Qwen принял задание, задача {rec['id']}{ctx_note}. Работает в лаборатории (ветка "
                     f"{(rec.get('lab') or {}).get('branch')}), в основной проект ничего не попадает. Итог и "
                     "вопросы пришлю сюда.", [[self.button(person, "✋ Стоп оператору", "/work_stop")]])

    async def _send_reliable(self, person: Person, text: str, keyboard=None) -> bool:
        """Final receipts and decisions must reach the owner: retry with backoff, re-checking the owner policy."""
        delay = BACKOFF_BASE
        for _ in range(SEND_RETRIES):
            if self.agent_policy(person) is None:
                return False
            try:
                await (self.telegram.send(person, text, keyboard) if keyboard else self.telegram.send(person, text))
                return True
            except CompanionError:
                await asyncio.sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX)
        return False

    async def work_turn(self, person: Person, task_id: str) -> None:
        announced: set[int] = set()
        outcomes: set[int] = set()
        t_start = time.monotonic()
        last_beat = t_start
        down_since: float | None = None
        delay = BACKOFF_BASE
        told_down = False
        me = asyncio.current_task()
        try:
            while True:
                try:
                    rec = await self.core.operator_task(task_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - one NETWORK_UNAVAILABLE must not end the watch
                    now = time.monotonic()
                    down_since = down_since if down_since is not None else now
                    gone = now - down_since
                    if gone >= OUTAGE_LIMIT_S:
                        await self._send_reliable(person, f"Оператор: связи с Bossman нет уже {_dur(gone)} "
                                                          f"({exc}). Слежение остановлено; задача в Bossman "
                                                          f"могла завершиться: /work_status {task_id}")
                        return
                    if gone >= OUTAGE_NOTICE_S and not told_down:
                        told_down = True
                        with contextlib.suppress(Exception):
                            await self.telegram.send(person, f"Оператор: связь с Bossman потеряна ({exc}), "
                                                             f"повторяю. Задача {task_id} не потеряна.")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, BACKOFF_MAX)
                    continue
                if down_since is not None:
                    if told_down:
                        with contextlib.suppress(Exception):
                            await self.telegram.send(person, f"Оператор: связь с Bossman восстановлена "
                                                             f"(задача {task_id}).")
                    down_since, delay, told_down = None, BACKOFF_BASE, False
                if self.agent_policy(person) is None:
                    return
                if self.store.get("delegation_locked", False):
                    with contextlib.suppress(Exception):
                        await self.core.operator_cancel(task_id)
                    return
                for d in rec.get("decisions", []):
                    aid = d.get("approval_id")
                    if d.get("status") == "PENDING" and aid not in announced:
                        announced.add(aid)
                        await self._send_reliable(person, decision_text(task_id, d), [[
                            self.button(person, "✅ Разрешить", f"/work_yes {aid}"),
                            self.button(person, "⛔ Отклонить", f"/work_no {aid}")]])
                    elif d.get("status") not in (None, "PENDING") and aid in announced and aid not in outcomes:
                        outcomes.add(aid)
                        await self._send_reliable(person, f"Решение {aid} · задача {task_id}: {outcome_text(d)}")
                status = rec.get("status")
                if status in TERMINAL:
                    if not await self._send_reliable(person, receipt_text(rec)):
                        with contextlib.suppress(Exception):
                            self.store.put("operator_unsent_receipt", task_id)
                    return
                if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                    last_beat = time.monotonic()
                    mins = max(0, int((time.time() - float(rec.get("created_at") or time.time())) // 60))
                    if status == "queued":
                        beat = f"⏳ в очереди №{rec.get('queue_position') or '?'}, {mins} мин"
                    else:
                        ph = (rec.get("phase") or {}).get("name")
                        beat = (f"⏳ работает: шаг {len(rec.get('steps') or [])}, {mins} мин"
                                + (f" · сейчас: {ph}" if ph else ""))
                    with contextlib.suppress(Exception):
                        await self.telegram.send(person, f"{beat} · задача {task_id}")
                await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self.core.operator_cancel(task_id)
            if self.agent_policy(person) is not None:
                with contextlib.suppress(CompanionError, Exception):
                    await self.telegram.send(person, f"✋ Оператор Qwen остановлен (задача {task_id}).")
            raise
        finally:
            self._operator_promote(task_id, me)
