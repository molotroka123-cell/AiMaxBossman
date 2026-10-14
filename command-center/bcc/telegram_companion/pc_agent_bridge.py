"""Мост Telegram-пульта к Argon PC-Agent в едином ядре Bossman (`bcc.features.pc_agent`).

Поток управления (единый контур Bossman, без второго ядра и без прямого shell в компаньоне):
  Telegram (личка владельца) → проверка личности (`agent_policy` + `console_allowed`) →
  задача Bossman (`POST /api/pc-agent/tasks`, таблица `tasks` + `task_runs`) →
  классификатор уровней T0 / T1 / T2 / T3 →
  кнопка владельца `✅ Разрешить #<id>` (одноразовый CAS-гейт `store.gate`) для T2 →
  исполнитель ядра (`observe -> act -> re-observe -> verify`) →
  append-only журнал (`audit.jsonl`) + отчёт и скриншот в Telegram.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import time
from pathlib import Path

from .config import CompanionError, Person
from .console import APPROVE_TTL_S, CONSOLE_OFF, NONCE_RE, _short

PC_AGENT_COMMANDS = {
    "/argon",
    "/jarvis",
    "/pc_status",
    "/pc_audit",
    "/procs",
    "/kill",
    "/windows",
    "/focus",
    "/close_win",
    "/services",
    "/sys",
    "/run",
    "/earn",
    "/earn_arena",
    "/earn_exam",
    "/earn_coach",
    "/trust",
    "/untrust",
    "/rollback",
    "/pc_yes",
    "/pc_no",
}

PC_AGENT_DENIED = "Нет прав. Управление компьютером через Argon PC-Agent доступно только владельцу в личном чате."
PC_AGENT_LOCKED = "Новые поручения заблокированы владельцем (СТОП или пауза). Снять — /resume."
PC_POLL_SECONDS = 1.0
PC_INITIAL_WAIT_S = 2.5

# Естественные фразы владельца в пульте → поручение Argon PC-Agent
_PC_NATURAL_RE = re.compile(
    r"^(?:пожалуйста[,\s]+)?(?:"
    r"(?:аргон|argon|джарвис|jarvis)\b[\s,:—-]*(.+)|"
    r"((?:открой|запусти)\s+(?:блокнот|notepad|калькулятор|проводник|терминал)\b.*)|"
    r"((?:выполни|сделай|запусти|проверь)\s+на\s+(?:пк|компьютере)\b.*)|"
    r"((?:выполни\s+команду|запусти\s+команду|выполни\s+в\s+терминале)\b.+)|"
    r"((?:открой\s+в\s+браузере(?:\s+на\s+пк)?)\b.*)|"
    r"((?:убей|заверши|останови)\s+(?:процесс|pid)\b.+)|"
    r"((?:какие\s+процессы|покажи\s+процессы|список\s+процессов|кто\s+жр[её]т\s+память|диспетчер\s+задач)\b.*)|"
    r"((?:покажи\s+окна|какие\s+окна\s+открыты|список\s+окон|переключись\s+на\s+окно|сфокусируй\s+окно|закрой\s+окно|сверни\s+окно|разверни\s+окно)\b.*)|"
    r"((?:проверь\s+служб[уы]|покажи\s+службы|список\s+служб|запусти\s+службу|останови\s+службу)\b.*)|"
    r"((?:сколько\s+места\s+на\s+диске|сколько\s+памяти|нагрузка\s+на\s+(?:пк|цп|систему)|состояние\s+железа)\b.*)|"
    r"((?:боссман\s+заработок|bossman\s+earn|статус\s+заработка|выполни\s+коммерческий\s+заказ)\b.*)"
    r")$",
    re.IGNORECASE | re.DOTALL,
)


def pc_phrase(text: str) -> tuple[str, str] | None:
    """Распознаёт естественное обращение владельца к Argon PC-Agent без слэш-команды."""
    clean = " ".join(str(text or "").split()).strip()
    if not clean or clean.startswith("/"):
        return None
    m = _PC_NATURAL_RE.match(clean)
    if not m:
        return None
    task = next((g for g in m.groups() if g), "").strip(" ,:;—-")
    if not task:
        return "/argon", ""
    return "/argon", task[:2500]


def _pc_approval_digest(task_id: int, pending: dict) -> str:
    blob = {
        "task_id": int(task_id),
        "approval_id": pending.get("approval_id"),
        "tool": pending.get("tool"),
        "args": pending.get("args"),
        "tier": pending.get("tier"),
        "category": pending.get("category"),
    }
    return hashlib.sha256(json.dumps(blob, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def format_pc_task_report(rec: dict) -> str:
    """Форматирует проверенный отчёт задачи Argon PC-Agent для Telegram."""
    tid = rec.get("task_id") or rec.get("id")
    status = str(rec.get("status") or "unknown")
    profile = str(rec.get("profile") or "Argon")
    if status == "completed" and rec.get("result"):
        return str(rec["result"])[:3600]
    if status == "waiting_approval":
        pending = rec.get("pending_approval") or {}
        aid = pending.get("approval_id")
        return (
            f"⏳ Задача #{tid} [{profile} PC-Agent] ждёт вашего решения (#{aid}):\n"
            f"• Уровень: {pending.get('tier', 'T2')} ({pending.get('category', 'risky')})\n"
            f"• Инструмент: `{pending.get('tool')}`\n"
            f"• Аргументы: `{json.dumps(pending.get('args') or {}, ensure_ascii=False)[:400]}`\n"
            f"• Причина: {pending.get('reason')}\n\n"
            "Без нажатия кнопки «✅ Разрешить» действие НЕ выполняется."
        )[:3600]
    if status in {"failed", "stopped", "cancelled"}:
        err = str(rec.get("error") or "без подробностей")
        icon = "⛔" if status in {"stopped", "cancelled"} else "❌"
        return f"{icon} Задача #{tid} [{profile} PC-Agent] ({status}):\n{err}"[:3600]
    steps = rec.get("step_results") or []
    return f"⚙️ Задача #{tid} [{profile} PC-Agent]: состояние `{status}`, выполнено шагов: {len(steps)}."


class PCAgentBridgeMixin:
    """Подмешивается в Companion. Не запускает дочерние процессы сам — работает только через Core API ядра."""

    def pc_agent_allowed(self, person: Person, message: dict | None = None) -> bool:
        if not self.console_allowed(person):
            return False
        if message is not None and self.authorized(message) != person:
            return False
        return True

    def pc_agent_menu(self, person: Person):
        b = lambda label, cmd: self.button(person, label, cmd)  # noqa: E731
        return [
            [b("⚙️ Процессы (/procs)", "/procs"),
             b("🪟 Окна (/windows)", "/windows"),
             b("🖥 Железо (/sys)", "/sys")],
            [b("💰 Bossman Earn (/earn)", "/earn"),
             b("📊 Paper Arena", "/earn_arena"),
             b("🎓 K1m6a OCR", "/earn_exam")],
            [b("📸 Экран и окна (T0)", "/argon покажи скриншот экрана и открытые окна"),
             b("🧩 Службы (/services)", "/services")],
            [b("📝 Тест Блокнота (T1)", "/argon открой Блокнот и напиши «Argon PC-Agent проверено»"),
             b("🌐 Тест Браузера (T1)", "/argon открой в браузере https://example.com")],
            [b("🔓 Доверие T2 на 15 мин", "/trust 15"),
             b("🔒 Снять доверие T2", "/untrust")],
            [b("📜 Журнал Argon", "/pc_audit 5"),
             b("⏪ Откат последней задачи", "/rollback"),
             b("⛔ СТОП", "/stop")],
        ]

    def _build_action_keyboard_for_record(self, person: Person, rec: dict, task_id: int):
        """Добавляет интерактивные кнопки управления процессами и окнами прямо под отчётом."""
        b = lambda label, cmd: self.button(person, label, cmd)  # noqa: E731
        rows = []
        protected = ("bcc", "bossman", "lsass", "csrss", "wininit", "services", "svchost", "defender", "smss", "winlogon", "dwm", "system", "ollama")
        for step in (rec.get("step_results") or []):
            if step.get("tool") == "pc.list_processes":
                kill_btns = []
                for p in (step.get("processes") or []):
                    pname = str(p.get("name") or "")
                    pid = p.get("pid")
                    if not pid or any(pr in pname.lower() for pr in protected):
                        continue
                    kill_btns.append(b(f"✖ {pname[:12]} ({pid})", f"/kill {pid}"))
                    if len(kill_btns) >= 4:
                        break
                if kill_btns:
                    rows.append(kill_btns[:2])
                    if len(kill_btns) > 2:
                        rows.append(kill_btns[2:4])
                rows.append([b("🔄 По памяти", "/procs"), b("⚡ По ЦП", "/procs cpu")])
            elif step.get("tool") == "pc.list_windows":
                win_btns = []
                for w in (step.get("windows") or [])[:3]:
                    pid = w.get("pid")
                    title = str(w.get("title") or "")[:14]
                    if pid:
                        win_btns.append(b(f"🎯 {title}", f"/focus {pid}"))
                if win_btns:
                    rows.append(win_btns)
            elif step.get("tool") == "pc.earn":
                rows.append([
                    b("💼 Заказ (T1)", "/earn task"),
                    b("📊 Арена", "/earn_arena"),
                    b("🎓 K1m6a OCR", "/earn_exam"),
                ])
        rows.append([
            b("📜 Журнал", "/pc_audit 5"),
            b("⏪ Откат", f"/rollback {task_id}"),
            b("🤖 Пульт Argon", "/argon"),
        ])
        return rows

    async def pc_agent_command(self, person: Person, command: str, arg: str, message: dict):
        from .service import Reply

        if not self.console_allowed(person):
            return CONSOLE_OFF
        if not self.pc_agent_allowed(person, message):
            return PC_AGENT_DENIED
        if self.core is None or not hasattr(self.core, "pc_agent_start"):
            return CONSOLE_OFF

        actor = f"tg:user:{person.user_id}@chat:{person.chat_id}"
        arg = (arg or "").strip()

        if command == "/pc_status":
            return await self._pc_agent_status_reply(person, arg)

        if command == "/pc_audit":
            limit = int(arg) if arg.isdecimal() and 1 <= int(arg) <= 30 else 8
            try:
                items = await self.core.pc_agent_audit(limit=limit)
            except CompanionError as exc:
                return f"Журнал Argon PC-Agent не прочитан: {_short(exc, 160)}."
            if not items:
                return "Журнал Argon PC-Agent пока пуст."
            lines = [f"📜 Журнал Argon PC-Agent (последние {len(items)} зап., hash-chain):"]
            for row in items[-limit:]:
                lines.append(
                    f"• #{row.get('seq')} [{row.get('tier')}/{row.get('decision')}] "
                    f"задача={row.get('task_id') or '—'} · `{row.get('tool')}` → "
                    f"{_short(row.get('result'), 140)}"
                )
            return Reply("\n".join(lines)[:3600], [[self.button(person, "🤖 Пульт Argon", "/argon")]])

        if command in {"/trust", "/untrust"}:
            if command == "/untrust" or arg.lower() in {"off", "0", "выкл", "стоп"}:
                try:
                    await self.core.pc_agent_trust(0, actor=actor)
                except CompanionError as exc:
                    return f"Не удалось сбросить окно доверия: {_short(exc, 160)}."
                self.store.put("pc_agent_trust_active", False)
                return "🔒 Временное окно доверия T2 выключено. Все действия T2 снова требуют кнопки «✅ Разрешить»."
            if self.store.get("delegation_locked", False):
                return PC_AGENT_LOCKED
            minutes = int(arg) if arg.isdecimal() else 15
            try:
                res = await self.core.pc_agent_trust(minutes, actor=actor)
            except CompanionError as exc:
                return f"Не удалось включить окно доверия T2: {_short(exc, 160)}."
            self.store.put("pc_agent_trust_active", True)
            return Reply(
                f"🔓 Временное окно доверия T2 включено на {res.get('minutes', minutes)} мин (актор: `{actor}`).\n"
                "• Обычные обратимые T2-действия выполняются без лишних вопросов с записью в журнал.\n"
                "• Действия из списка «всегда владельца» (перезагрузка, git push, удаление важных данных) "
                "по-прежнему требуют кнопки «✅ Разрешить».\n"
                "• Уровень T3 (ключи, vault, пин, конституция, платежи) заблокирован всегда.\n"
                "• Команда /stop или /untrust мгновенно снимает окно доверия.",
                [[self.button(person, "🔒 Выключить доверие", "/untrust"),
                  self.button(person, "⛔ СТОП", "/stop")]],
            )

        if command == "/rollback":
            tid_str = arg or str(self.store.get("pc_agent_last_task:" + person.key) or "")
            if not tid_str.isdecimal():
                return "Укажите номер задачи для отката: `/rollback <task_id>`."
            tid = int(tid_str)
            try:
                res = await self.core.pc_agent_rollback(tid)
            except CompanionError as exc:
                return f"Откат задачи #{tid} не выполнен: {_short(exc, 160)}."
            restored = res.get("restored") or []
            if not res.get("ok"):
                return f"⚠️ Откат задачи #{tid}: {res.get('reason') or res.get('detail') or 'нет резервных копий'}."
            lines = [f"⏪ Откат задачи #{tid} выполнен (ПРОВЕРЕНО):"]
            for item in restored[:10]:
                if isinstance(item, dict):
                    lines.append(f"• `{item.get('path')}` → {item.get('action')}")
                else:
                    lines.append(f"• {item}")
            return "\n".join(lines)[:3600]

        if command in {"/pc_yes", "/pc_no"}:
            return await self._pc_agent_decide(person, command == "/pc_yes", arg, message)

        # Прямые быстрые команды управления процессами, окнами, службами, системой и заработком (Bossman Earn)
        if command == "/procs":
            if arg.lower() in {"cpu", "цп"}:
                arg = "покажи процессы по цп"
            elif arg:
                arg = f"найди процесс {arg}"
            else:
                arg = "покажи процессы и память на ПК"
        elif command == "/kill":
            if not arg:
                return "Укажите PID или имя процесса: `/kill <pid|имя>` (или выберите процесс через `/procs`)."
            arg = f"заверши процесс {arg}"
        elif command == "/windows":
            arg = "покажи открытые окна"
        elif command == "/focus":
            if not arg:
                return "Укажите заголовок или PID окна: `/focus <окно|pid>`."
            arg = f"сфокусируй окно {arg}"
        elif command == "/close_win":
            if not arg:
                return "Укажите заголовок или PID окна: `/close_win <окно|pid>`."
            arg = f"закрой окно {arg}"
        elif command == "/services":
            arg = f"службы {arg}".strip()
        elif command == "/sys":
            arg = "система железо и статус боссмана"
        elif command == "/run":
            if not arg:
                return "Укажите команду для выполнения: `/run <команда и аргументы>`."
            arg = f"выполни в терминале {arg}"
        elif command == "/earn":
            arg = f"earn {arg}".strip() if arg else "earn status"
        elif command == "/earn_arena":
            arg = "earn arena"
        elif command == "/earn_exam":
            arg = "earn exam"
        elif command == "/earn_coach":
            arg = f"earn coach {arg}".strip()

        # Команды /argon, /jarvis, /pc <задание> (и транслированные выше быстрые команды)
        if not arg:
            return await self._pc_agent_status_reply(person, "")

        if self.store.get("delegation_locked", False):
            return PC_AGENT_LOCKED

        self.store.put("pc_agent_active", True)
        try:
            rec = await self.core.pc_agent_start(arg, actor=actor, wait=False)
        except CompanionError as exc:
            return f"Задача Argon PC-Agent не запущена: {_short(exc, 200)}."

        task_id = int(rec["task_id"])
        self.store.put("pc_agent_last_task:" + person.key, task_id)
        # Привязываем задачу к владельцу в store, чтобы /result <task_id> тоже её видел
        with contextlib.suppress(Exception):
            nonce = self.store.propose(person.key, {"prompt": arg, "executor": "argon", "agent_id": rec.get("agent_id") or 1})
            self.store.consume(person.key, nonce)
            self.store.delegated(person.key, nonce, task_id, "")

        # Короткое ожидание быстрых задач (T0/T1/T3), чтобы сразу вернуть проверенный результат
        deadline = time.monotonic() + PC_INITIAL_WAIT_S
        while time.monotonic() < deadline:
            status = rec.get("status")
            if status in {"completed", "failed", "stopped", "cancelled", "waiting_approval"}:
                break
            await asyncio.sleep(0.25)
            with contextlib.suppress(CompanionError):
                rec = await self.core.pc_agent_task(task_id)

        status = rec.get("status")
        if status == "waiting_approval":
            return self._build_pc_approval_reply(person, rec)
        if status in {"completed", "failed", "stopped", "cancelled"}:
            await self._maybe_send_pc_screenshot(person, rec)
            return Reply(
                format_pc_task_report(rec),
                self._build_action_keyboard_for_record(person, rec, task_id),
            )

        # Если задача ещё выполняется (длинный сценарий), запускаем фонового наблюдателя
        watch_task = asyncio.create_task(
            self._watch_pc_agent_task(person, task_id),
            name=f"pc-agent-watch-{task_id}",
        )
        self.agent_jobs[f"pc_agent:{task_id}"] = watch_task
        watch_task.add_done_callback(lambda _f, k=f"pc_agent:{task_id}": self.agent_jobs.pop(k, None))
        return Reply(
            f"🤖 Argon PC-Agent принял задачу #{task_id}.\n"
            f"Поручение: {arg[:300]}\n"
            "Выполняю цикл `наблюдение → действие → проверка результата`. Отчёт пришлю сюда.",
            [[self.button(person, "📋 Статус", f"/pc_status {task_id}"),
              self.button(person, "⛔ СТОП", "/stop")]],
        )

    def _build_pc_approval_reply(self, person: Person, rec: dict):
        from .service import Reply

        task_id = int(rec["task_id"])
        pending = rec.get("pending_approval") or {}
        aid = int(pending.get("approval_id") or 0)
        digest = _pc_approval_digest(task_id, pending)
        gate_payload = {
            "kind": "pc_agent.t2",
            "task_id": task_id,
            "approval_id": aid,
            "digest": digest,
            "chat_id": person.chat_id,
            "user_id": person.user_id,
        }
        yes_nonce = self.store.gate(person.key, {**gate_payload, "decision": True}, APPROVE_TTL_S)
        no_nonce = self.store.gate(person.key, {**gate_payload, "decision": False}, APPROVE_TTL_S)
        text = format_pc_task_report(rec)
        kb = [
            [self.button(person, f"✅ Разрешить #{aid}", f"/pc_yes {yes_nonce}"),
             self.button(person, f"⛔ Отклонить #{aid}", f"/pc_no {no_nonce}")],
            [self.button(person, "⛔ СТОП", "/stop")],
        ]
        return Reply(text, kb)

    async def _pc_agent_decide(self, person: Person, approve: bool, nonce: str, message: dict):
        from .service import Reply

        if not NONCE_RE.match(nonce or ""):
            return "Решение принимается только одноразовой кнопкой под запросом подтверждения Argon PC-Agent."
        gate = self.store.open_gate(person.key, nonce)
        if (
            gate.get("decision") is not approve
            or gate.get("chat_id") != person.chat_id
            or gate.get("user_id") != person.user_id
            or gate.get("kind") != "pc_agent.t2"
        ):
            self.store.close_gate(nonce, "refused")
            return "Эта кнопка принадлежит другому решению, человеку или чату."
        if self.authorized(message) != person:
            self.store.close_gate(nonce, "refused")
            raise CompanionError("IDENTITY_REVOKED")
        if approve and self.store.get("delegation_locked", False):
            self.store.close_gate(nonce, "refused")
            raise CompanionError("COMPUTER_STOPPED")

        task_id = int(gate["task_id"])
        aid = int(gate["approval_id"])
        actor = f"tg:user:{person.user_id}@chat:{person.chat_id}"
        try:
            current = await self.core.pc_agent_task(task_id)
            pending = current.get("pending_approval") or {}
            if current.get("status") != "waiting_approval" or pending.get("approval_id") != aid:
                raise CompanionError("APPROVAL_ALREADY_DECIDED")
            if _pc_approval_digest(task_id, pending) != gate.get("digest"):
                raise CompanionError("APPROVAL_CHANGED_REVIEW_AGAIN")
            decided_rec = await self.core.pc_agent_approve(task_id, aid, approve, actor)
        except (CompanionError, asyncio.CancelledError):
            self.store.close_gate(nonce, "decide_unknown")
            raise
        self.store.close_gate(nonce, "decided")
        await self._maybe_send_pc_screenshot(person, decided_rec)
        return Reply(
            format_pc_task_report(decided_rec),
            [[self.button(person, "📜 Журнал", "/pc_audit 5"),
              self.button(person, "⏪ Откат", f"/rollback {task_id}"),
              self.button(person, "🤖 Пульт Argon", "/argon")]],
        )

    async def _pc_agent_status_reply(self, person: Person, arg: str):
        from .service import Reply

        if arg.isdecimal():
            try:
                rec = await self.core.pc_agent_task(int(arg))
            except CompanionError as exc:
                return f"Задача #{arg} не прочитана: {_short(exc, 160)}."
            if rec.get("status") == "waiting_approval":
                return self._build_pc_approval_reply(person, rec)
            return Reply(format_pc_task_report(rec), self.pc_agent_menu(person))

        try:
            st = await self.core.pc_agent_status()
        except CompanionError as exc:
            return f"Статус Argon PC-Agent не получен: {_short(exc, 160)}."
        trust = "ВКЛЮЧЕНО" if st.get("trust_active") else "выключено (T2 по кнопке)"
        stopped = "ВКЛЮЧЁН (действия заблокированы)" if st.get("stopped") else "выключен"
        models = ", ".join(st.get("local_models") or []) or "—"
        last_tid = self.store.get("pc_agent_last_task:" + person.key)
        lines = [
            f"🤖 Argon PC-Agent (профиль `{st.get('profile')}`, агент #{st.get('agent_id')})",
            f"• СТОП: {stopped}",
            f"• Окно доверия T2: {trust}",
            f"• Активные задачи: {st.get('active_tasks') or 'нет'}",
            f"• Локальные модели в реестре: {models}",
            f"• Последняя задача чата: {('#' + str(last_tid)) if last_tid else 'ещё не было'}",
            "\nНапишите `/argon <задание>` или `/pc <задание>` (либо просто выберите действие кнопкой ниже):",
        ]
        return Reply("\n".join(lines), self.pc_agent_menu(person))

    async def _maybe_send_pc_screenshot(self, person: Person, rec: dict) -> None:
        """Если в шагах задачи есть проверенный PNG-скриншот (рабочий стол или браузер), отправляет его владельцу."""
        if self.telegram is None or not self.pc_agent_allowed(person):
            return
        steps = rec.get("step_results") or []
        shot_path = None
        for step in reversed(steps):
            cand = step.get("screenshot")
            if isinstance(cand, str) and cand:
                shot_path = cand
                break
        if not shot_path:
            return
        try:
            p = Path(shot_path)
            if not p.is_file() or p.is_symlink() or p.stat().st_size > 10 * 1024 * 1024:
                return
            data = p.read_bytes()
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                return
            tid = rec.get("task_id") or rec.get("id")
            await self.telegram.send_photo(
                person,
                data,
                f"📸 Снимок к задаче #{tid} [Argon PC-Agent] ({p.name})",
            )
        except Exception:
            return

    async def _watch_pc_agent_task(self, person: Person, task_id: int) -> None:
        """Фоновое наблюдение за задачей Argon PC-Agent до завершения или запроса кнопки T2."""
        announced_approval: int | None = None
        deadline = time.monotonic() + 600.0
        while time.monotonic() < deadline:
            if not self.pc_agent_allowed(person):
                return
            if self.store.get("delegation_locked", False):
                with contextlib.suppress(Exception):
                    await self.core.pc_agent_stop(actor="owner", reason="delegation_locked")
                return
            try:
                rec = await self.core.pc_agent_task(task_id)
            except asyncio.CancelledError:
                raise
            except CompanionError:
                await asyncio.sleep(PC_POLL_SECONDS)
                continue

            status = rec.get("status")
            if status == "waiting_approval":
                pending = rec.get("pending_approval") or {}
                aid = pending.get("approval_id")
                if isinstance(aid, int) and aid != announced_approval:
                    announced_approval = aid
                    reply = self._build_pc_approval_reply(person, rec)
                    with contextlib.suppress(CompanionError):
                        await self.telegram.send(person, str(reply), reply.keyboard)
            elif status in {"completed", "failed", "stopped", "cancelled"}:
                await self._maybe_send_pc_screenshot(person, rec)
                with contextlib.suppress(CompanionError):
                    await self.telegram.send(
                        person,
                        format_pc_task_report(rec),
                        [[self.button(person, "📜 Журнал", "/pc_audit 5"),
                          self.button(person, "⏪ Откат", f"/rollback {task_id}"),
                          self.button(person, "🤖 Пульт Argon", "/argon")]],
                    )
                return
            await asyncio.sleep(PC_POLL_SECONDS)
