"""«💱 Обменник» in the pult: owner only, phrases routed, confirm button, STOP blocks writes, control lane (owner 19.10)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bcc.telegram_companion.config import Person
from bcc.telegram_companion.exchange_bridge import ExchangeBridgeMixin, exchange_phrase
from bcc.telegram_companion.store import Store

from bossman_shared.exchange_assistant import Assistant, Rate, RateBook
from bossman_shared.exchange_assistant.store import Store as Book

OWNER = Person(101, 101, "owner")
GUEST = Person(202, 202, "guest")
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, key, default=None):
        return self.d.get(key, default)


class Fake(ExchangeBridgeMixin):
    def __init__(self, tmp_path):
        self.store = FakeStore()
        self.pressed = {}
        book = Book(tmp_path / "assistant.db")
        self._exchange = Assistant(book, fx_db=tmp_path / "none.db", clock=lambda: NOW, stop_file=tmp_path / "STOP")

        def rates():                     # official rate without touching the live FX log
            b = RateBook([Rate("USDCZK", Decimal("21.6"), "ЧНБ", NOW - timedelta(hours=1), "official")])
            for r in book.manual_rates():
                b.put(r)
            return b
        self._exchange.book = rates

    def button(self, person, label, command):
        token = f"b:{len(self.pressed):016d}"
        self.pressed[token] = command
        return (label, token)


def _labels(reply):
    return [label for row in reply.keyboard for label, _ in row]


def test_phrases_are_routed_only_when_they_are_exchange_commands():
    assert exchange_phrase("курс") == ("/swap", "курс")
    assert exchange_phrase("500 usdt в czk") == ("/swap", "500 usdt в czk")
    assert exchange_phrase("обмен выполнен Q-1A2B3C4D") == ("/swap", "обмен выполнен Q-1A2B3C4D")
    assert exchange_phrase("итоги месяц") == ("/swap", "итоги месяц")
    for other in ("нарисуй кота", "какой курс у биткоина завтра?", "сделай видео 500 кадров", "привет"):
        assert exchange_phrase(other) is None, other


def test_owner_quote_has_confirm_button_and_same_book(tmp_path):
    f = Fake(tmp_path)
    assert "Записан ручной курс" in str(f.exchange_command(OWNER, "курс usdt/usd 0.999 источник binance"))
    reply = f.exchange_command(OWNER, "500 usdt в czk")
    assert str(reply).startswith("Котировка Q-")
    assert _labels(reply)[0] == "✅ Обмен выполнен"
    confirm = f.pressed[reply.keyboard[0][0][1]]
    assert confirm.startswith("/swap обмен выполнен Q-")
    assert "Сделка записана: D-1" in str(f.exchange_command(OWNER, confirm.partition(" ")[2]))
    assert "повтор не создан" in str(f.exchange_command(OWNER, confirm.partition(" ")[2]))
    assert len(f._exchange.store.deals()) == 1


def test_guest_is_refused_and_nothing_written(tmp_path):
    f = Fake(tmp_path)
    for cmd in ("курс", "500 usdt в czk", "курс usdt/usd 1 источник x"):
        assert f.exchange_command(GUEST, cmd) == "Помощник обменника доступен только владельцу."
    assert f._exchange.store.manual_rates() == []


def test_stop_blocks_writes_but_not_reads(tmp_path):
    f = Fake(tmp_path)
    f.exchange_command(OWNER, "курс usdt/usd 0.999 источник binance")
    qid = next(w for w in str(f.exchange_command(OWNER, "100 usdt в czk")).split() if w.startswith("Q-"))
    f.store.d["delegation_locked"] = True
    assert "СТОП" in str(f.exchange_command(OWNER, f"обмен выполнен {qid}"))
    assert "СТОП" in str(f.exchange_command(OWNER, "курс usdt/usd 0.998 источник x"))
    assert f._exchange.store.deals() == []
    assert "Курсы" in str(f.exchange_command(OWNER, "курс"))
    assert "Итоги" in str(f.exchange_command(OWNER, "итоги"))
    f.store.d["delegation_locked"] = False
    assert "Сделка записана" in str(f.exchange_command(OWNER, f"обмен выполнен {qid}"))


def test_swap_is_control_lane_and_in_main_menu():
    assert Store.lane({"text": "/swap 500 usdt в czk"}) == "control"
    from bcc.telegram_companion.console import ConsoleMixin

    class Menu(ConsoleMixin):
        def button(self, person, label, command):
            return (label, command)

    rows = Menu().console_menu(OWNER)
    assert ("💱 Обменник", "/swap") in rows[0] and len(rows[0]) <= 4
