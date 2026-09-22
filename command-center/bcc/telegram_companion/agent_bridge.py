"""Owner-only control of Claude Code and Codex from Telegram (owner decision, 2026-09-22).

Rules the owner set, enforced here:
* rights exist only for the owner's Telegram ID; every other login gets nothing;
* the check runs on EVERY call and again before the answer is delivered — a revoked
  or changed owner in the local config stops both the start and the delivery;
* the owner can drive Claude Code and Codex and hand them the next task from the phone;
* STOP (and the local delegation lock) cancels a running turn and blocks new ones.
There is still no raw shell command from Telegram.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
from pathlib import Path

from . import claude_bridge
from .config import CompanionError, Person

AGENT_COMMANDS = {"/claude", "/claude_new", "/claude_stop", "/codex", "/codex_new", "/codex_stop",
                  "/agents", "/audits"}
AGENT_DENIED = "Нет прав. Управление Claude и Codex доступно только с Telegram ID владельца."
REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_DIRS = ("owner-repair/evidence", "owner-repair/coaching-exam-20260922")
AUDIT_LIMIT = 5
TEXT_LIMIT = 12000


def _owner(settings) -> Person | None:
    return next((p for p in settings.people if p.role == "owner"), None)


class AgentBridgeMixin:
    agent_jobs: dict

    # ------------------------------------------------------------ authority
    def agent_policy(self, person: Person, message: dict | None = None):
        """Fresh config, owner identity, exact match — or None. Never cached."""
        try:
            current = self.policy_provider()
        except (OSError, ValueError, TypeError):
            return None
        owner = _owner(current)
        if owner is None or person is None or person.key != owner.key or person.role != "owner":
            return None
        if message is not None and current.authorize(message) != owner:
            return None
        return current

    def _agent_log(self, record: dict):
        path = Path(self.store.path).parent / "agent-bridge.log"
        with contextlib.suppress(OSError):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record},
                                    ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ commands
    async def agent_command(self, person: Person, command: str, arg: str, message: dict):
        from .service import Reply
        current = self.agent_policy(person, message)
        if current is None:
            self._agent_log({"who": getattr(person, "key", None), "command": command, "result": "denied"})
            return AGENT_DENIED
        if command == "/agents":
            return Reply(self.agents_status(current), self.agents_menu(person))
        if command == "/audits":
            return await self.send_audits(person, arg)
        name = "claude" if command.startswith("/claude") else "codex"
        enabled = current.claude_bridge if name == "claude" else current.codex_bridge
        if not enabled:
            return (f"{name.title()} из Telegram выключен локально "
                    f"({name}_bridge: false в config.json на компьютере).")
        key = f"agent_session:{name}:{person.key}"
        job = self.agent_jobs.get(name)
        if command.endswith("_stop"):
            if job is None or job.done():
                return f"{name.title()} сейчас ничего не делает."
            job.cancel()
            return f"✋ {name.title()} остановлен, процесс завершён."
        if command.endswith("_new"):
            self.store.put(key, None)
            return f"🆕 Следующее сообщение {name.title()} начнёт новую сессию."
        if not arg:
            return f"Напишите /{name} и задание, например: /{name} проверь тесты и пришли отчёт"
        if self.store.get("delegation_locked", False):
            return "Новые поручения заблокированы (СТОП или пауза). Снять — /resume."
        if job is not None and not job.done():
            return Reply(f"{name.title()} ещё работает над прошлым заданием.",
                         [[self.button(person, f"✋ Стоп {name.title()}", f"/{name}_stop")]])
        self._agent_log({"who": person.key, "agent": name, "result": "started",
                         "prompt_sha256": hashlib.sha256(arg.encode("utf-8")).hexdigest()})
        self.agent_jobs[name] = asyncio.create_task(self.agent_turn(name, person, arg, key))
        return Reply(f"🤖 {name.title()} принял задание. Ответ пришлю сюда.",
                     [[self.button(person, f"✋ Стоп {name.title()}", f"/{name}_stop")]])

    async def agent_turn(self, name: str, person: Person, prompt: str, key: str):
        current = self.agent_policy(person)
        cwd = (current.claude_cwd if current else "") or str(REPO_ROOT.parent)
        started = time.monotonic()
        try:
            if current is None:
                raise PermissionError("OWNER_REVOKED")
            if name == "claude":
                text, session, cost = await claude_bridge.claude(
                    prompt, session=self.store.get(key), cwd=cwd,
                    permission_mode=current.claude_permission_mode, timeout=current.claude_timeout)
                footer = "\n\n— 🤖 Claude Code" + (f" · ${cost:.2f}" if isinstance(cost, (int, float)) else "")
            else:
                text, session = await claude_bridge.codex(
                    prompt, session=self.store.get(key), cwd=cwd,
                    sandbox=current.codex_sandbox, timeout=current.claude_timeout)
                footer = "\n\n— 🧩 Codex"
            self.store.put(key, session)
            reply = text[:TEXT_LIMIT] + footer
            result = "done"
        except asyncio.CancelledError:
            reply, result = f"✋ {name.title()} остановлен.", "cancelled"
        except PermissionError:
            reply, result = None, "denied"
        except (RuntimeError, OSError, ValueError) as exc:
            missing = str(exc) in {"CLAUDE_CLI_NOT_FOUND", "CODEX_CLI_NOT_FOUND"}
            reply = (f"{name.title()} CLI не найден на этом ПК." if missing
                     else f"{name.title()}: ошибка {type(exc).__name__}")
            result = "error"
        self._agent_log({"who": person.key, "agent": name, "result": result,
                         "seconds": round(time.monotonic() - started, 1)})
        # Delivery is an effect too: the owner is checked again right before it.
        if reply is None or self.agent_policy(person) is None:
            return
        with contextlib.suppress(CompanionError):
            await self.telegram.send(person, reply, self.agents_menu(person))

    def agents_menu(self, person: Person):
        b = self.button
        return [[b(person, "🤖 Claude: новая сессия", "/claude_new"), b(person, "✋ Стоп Claude", "/claude_stop")],
                [b(person, "🧩 Codex: новая сессия", "/codex_new"), b(person, "✋ Стоп Codex", "/codex_stop")],
                [b(person, "📋 Аудиты", "/audits"), b(person, "🏠 Меню", "/menu")]]

    def agents_status(self, current) -> str:
        def state(name, on):
            job = self.agent_jobs.get(name)
            busy = job is not None and not job.done()
            return f"• {name.title()}: {'включён' if on else 'выключен'}{' · работает' if busy else ''}"
        return "\n".join(["Агенты на компьютере (только для владельца):",
                          state("claude", current.claude_bridge), state("codex", current.codex_bridge),
                          "/claude задание · /codex задание · /audits — последние отчёты"])

    async def send_audits(self, person: Person, arg: str):
        files = []
        for rel in AUDIT_DIRS:
            root = REPO_ROOT / rel
            if root.is_dir():
                files += [p for p in root.glob("*.md") if p.is_file()]
        if arg:
            files = [p for p in files if arg.lower() in p.name.lower()]
        files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:AUDIT_LIMIT]
        if not files:
            return "Отчётов не найдено."
        sent = 0
        for path in files:
            if self.agent_policy(person) is None:
                break
            try:
                await self.telegram.send_document(person, path.name, path.read_bytes(),
                                                  f"{path.parent.name}/{path.name}")
                sent += 1
            except (CompanionError, OSError):
                continue
        return f"📋 Отправлено отчётов: {sent} из {len(files)}."

    def cancel_agents(self) -> int:
        n = 0
        for job in self.agent_jobs.values():
            if job is not None and not job.done():
                job.cancel()
                n += 1
        return n
