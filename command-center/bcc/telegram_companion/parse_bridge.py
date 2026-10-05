"""``/parse`` in the пульт: start the Jeff Master Parser from the owner's phone.

Owner request 2026-09-28. Same authority rule as Claude/Codex control
(``AgentBridgeMixin.agent_policy``): fresh config, the owner's Telegram ID,
exact match — on the command AND again before the result is delivered. The
пульт never touches Jeff's conversations itself: it runs
``bcc.pit.master_parser`` (the same code as ``bossman pit master-parse`` and the
Bossman button) in a worker thread — no process, no shell — and relays its
summary. Nothing is sent to participants.

  /parse          — full run (collect everything new, update passports)
  /parse dry      — dry run: show what would be added, write nothing
  /parse status   — progress of the current/last run
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from .config import CompanionError, Person

PARSE_COMMANDS = {"/parse"}
PARSE_DENIED = "Нет прав. Master Parser Jeff доступен только с Telegram ID владельца."
TEXT_LIMIT = 3800


class ParseBridgeMixin:
    parse_job: asyncio.Task | None = None

    def parse_data_dir(self) -> Path:
        from bcc.pit.config import default_data_dir
        return default_data_dir()

    def parse_run(self, settings, options) -> dict:
        """Runs in a worker thread with its own event loop: the пульт keeps answering."""
        from bcc.pit.master_parser import run_master_parse
        return asyncio.run(run_master_parse(settings, options))

    async def parse_command(self, person: Person, command: str, arg: str, message: dict):
        if self.agent_policy(person, message) is None:
            return PARSE_DENIED
        from bcc.pit.config import config_path
        from bcc.pit.master_parser import Options, is_running, read_status, resolve_settings
        data_dir = self.parse_data_dir()
        word = (arg or "").strip().lower()
        if word in {"status", "статус"}:
            return _status_text(read_status(data_dir))
        job = self.parse_job
        if (job is not None and not job.done()) or is_running(data_dir):
            return "Master Parser уже работает.\n" + _status_text(read_status(data_dir))
        try:
            settings = resolve_settings(config_path(data_dir))
        except (OSError, ValueError):
            return "Jeff (PIT) не настроен на этом компьютере — Master Parser запускать не на чем."
        dry = word in {"dry", "dry-run", "пробно", "проба"}
        self.parse_job = asyncio.create_task(self.parse_turn(person, settings, Options(dry_run=dry)))
        return ("🔎 Master Parser запущен" + (" (пробный прогон, ничего не записывается)" if dry else "")
                + ". Итог пришлю сюда. Ход: /parse status")

    async def parse_turn(self, person: Person, settings, options) -> None:
        from bcc.pit.master_parser import AlreadyRunning
        from bcc.pit.master_parser.cli import summary_ru
        try:
            text = summary_ru(await asyncio.to_thread(self.parse_run, settings, options))
        except AlreadyRunning:
            text = "Master Parser уже работает — дождитесь итога."
        except Exception as exc:  # noqa: BLE001 — the owner gets a stable, secret-free code
            text = f"Master Parser остановился: {type(exc).__name__}. Повтор продолжит с места остановки."
        # Delivery is an effect too: the owner is checked again right before it.
        if self.agent_policy(person) is None:
            return
        with contextlib.suppress(CompanionError):
            await self.telegram.send(person, text[:TEXT_LIMIT])


def _status_text(status: dict) -> str:
    state = {"never": "ещё не запускался", "running": "идёт", "done": "завершён",
             "failed": "ошибка", "interrupted": "прерван"}.get(status.get("state"), status.get("state"))
    if status.get("state") == "never":
        return "Master Parser: ещё не запускался."
    return (f"Master Parser: {state} · запуск {status.get('run_id')} · "
            f"новых сообщений {status.get('collected_new', 0)} · "
            f"участники {status.get('persons_done', 0)}/{status.get('persons_total', 0)} · "
            f"сообщения {status.get('messages_done', 0)}/{status.get('messages_total', 0)} · "
            f"{status.get('rate_msgs_per_s', 0)} сообщ/с · фактов +{status.get('facts_added', 0)}"
            + (f" · абзацы {status.get('narratives_done', 0)}/{status.get('narratives_total', 0)}"
               if status.get("phase") == "narrative" else ""))
