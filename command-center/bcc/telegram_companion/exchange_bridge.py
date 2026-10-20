"""«💱 Обменник» — the SwapMe desk assistant inside the owner-only pult (owner 19.10).

Same assistant as the Mini App and the CLI (bossman_shared.exchange_assistant, one local book), so a quote
Q-… or a deal D-… made here is the same record everywhere. Owner only. The owner's STOP/pause
(`delegation_locked`) blocks every write; reading rates and totals still works. Nothing is sent to clients,
no transfers, no XE scraping.
"""
from __future__ import annotations

import re

from .config import Person

EXCHANGE_COMMANDS = {"/swap"}
EXCHANGE_LABEL = "💱 Обменник"
_QID = re.compile(r"\bQ-[0-9A-F]{8}\b")
_DID = re.compile(r"\bD-(\d+)\b")


def exchange_phrase(text: str):
    """Plain owner phrases («курс», «500 usdt в czk», «обмен выполнен Q-…», «итоги») -> ("/swap", text)."""
    try:
        from bossman_shared.exchange_assistant.agent import is_command
    except ImportError:
        return None
    return ("/swap", text) if is_command(text) else None


class ExchangeBridgeMixin:
    _exchange = None

    def exchange_assistant(self):
        if self._exchange is None:
            from bossman_shared.exchange_assistant import Assistant
            from bossman_shared.exchange_assistant.store import Store, default_db
            self._exchange = Assistant(Store(default_db()))
        return self._exchange

    def exchange_command(self, person: Person, arg: str):
        from .service import Reply
        if person.role != "owner":
            return "Помощник обменника доступен только владельцу."
        try:
            assistant = self.exchange_assistant()
        except Exception as exc:  # noqa: BLE001 - a missing package or locked file must not break the pult
            return f"Помощник обменника недоступен: {type(exc).__name__}"
        stopped = bool(self.store.get("delegation_locked", False))
        text = assistant.handle(arg or "помощь", "owner", stopped=stopped)
        b = lambda label, cmd: self.button(person, label, cmd)  # noqa: E731
        rows = []
        q = _QID.search(text)
        if q and text.startswith("Котировка"):
            rows.append([b("✅ Обмен выполнен", f"/swap обмен выполнен {q.group(0)}")])
        d = _DID.search(text)
        if d and text.startswith(("Сделка записана", "Эта котировка уже подтверждена")):
            rows.append([b(f"📄 Чек PDF D-{d.group(1)}", f"/swap чек D-{d.group(1)}")])
        rows.append([b("📈 Курсы", "/swap курс"), b("📊 Итоги месяца", "/swap итоги"), b("🗓 Сегодня", "/swap суточный отчёт")])
        return Reply(text, rows)
