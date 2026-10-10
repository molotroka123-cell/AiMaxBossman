"""Delivery of the answering machine's log to the OWNER's Telegram console: the companion polls the outbox, sends one notice, acks.

Fake Telegram and fake core (as in ``tests/telegram_contracts``): nothing is sent anywhere, no token is read.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bcc.telegram_calls.answering_store import build_report, render_notice
from bcc.telegram_calls.types import IncomingCall
from bcc.telegram_companion.config import CompanionError, Person
from bcc.telegram_companion.service import Companion

OWNER = Person(11111, 11111, "owner", 7)
GUEST = Person(22222, 22222, "guest", None)


class Store:
    def __init__(self):
        self.kv = {}

    def get(self, key, default=None):
        return self.kv.get(key, default)

    def put(self, key, value):
        self.kv[key] = value


class Telegram:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail
        self.authorize_delivery = lambda person: True

    async def send(self, person, text, keyboard=None):
        if self.fail:
            raise CompanionError("NETWORK_UNAVAILABLE")
        self.sent.append((person, text))
        return 101


class Core:
    def __init__(self, rows=(), ack_fails=False):
        self.rows, self.acked, self.ack_fails = list(rows), [], ack_fails

    async def answering_reports(self):
        return [r for r in self.rows if r["id"] not in self.acked]

    async def ack_answering_report(self, report_id):
        if self.ack_fails:
            raise CompanionError("NETWORK_UNAVAILABLE")
        self.acked.append(report_id)
        return {"id": report_id, "delivered": True}


def row(outcome="message_taken", **kw):
    rep = build_report(call=IncomingCall("r", 555, "Иван", 1_700_000_000.0, "telegram"), outcome=outcome,
                       record={"call_id": "c-1", "started_at": 100.0, "ended_at": 140.0}, answered=True, summary_text="Иван просит перезвонить завтра.",
                       transcript=[{"role": "user", "text": "Перезвоните мне завтра"}], **kw)
    return {**rep, "notice": render_notice(rep)}


def companion(core, telegram=None, *, console=True, people=(OWNER, GUEST)):
    settings = SimpleNamespace(people=tuple(people), pc_control=console)
    c = Companion(settings, Store(), telegram or Telegram(), core, models=object(), policy_provider=lambda: settings)
    return c


def test_the_owner_gets_one_notice_per_report_and_the_report_is_acknowledged_afterwards():
    r = row()
    core, tg = Core([r]), Telegram()
    c = companion(core, tg)
    asyncio.run(c.notify_answering_reports())
    assert [p for p, _ in tg.sent] == [OWNER], "only the owner is ever told"
    text = tg.sent[0][1]
    assert "Иван" in text and "Просьба перезвонить: да" in text and "Звонящий: Перезвоните мне завтра" in text
    assert core.acked == [r["id"]]
    asyncio.run(c.notify_answering_reports())
    assert len(tg.sent) == 1, "an acknowledged report is not sent again"


def test_a_failed_send_leaves_the_report_in_the_outbox_for_the_next_round():
    r = row()
    core = Core([r])
    c = companion(core, Telegram(fail=True))
    asyncio.run(c.notify_answering_reports())
    assert core.acked == [] and c.store.get("answering_sent:" + r["id"]) is None
    c.telegram = Telegram()                                                # Telegram is back
    asyncio.run(c.notify_answering_reports())
    assert core.acked == [r["id"]] and len(c.telegram.sent) == 1


def test_a_lost_acknowledgement_repeats_only_the_acknowledgement_never_the_notice():
    r = row()
    core, tg = Core([r], ack_fails=True), Telegram()
    c = companion(core, tg)
    asyncio.run(c.notify_answering_reports())
    assert len(tg.sent) == 1 and core.acked == []                          # sent, but the backend never heard about it
    core.ack_fails = False
    asyncio.run(c.notify_answering_reports())
    assert len(tg.sent) == 1 and core.acked == [r["id"]], "the owner is not told twice"


def test_with_the_owner_console_off_nothing_is_sent_and_the_report_waits():
    core, tg = Core([row()]), Telegram()
    asyncio.run(companion(core, tg, console=False).notify_answering_reports())
    assert tg.sent == [] and core.acked == []
    asyncio.run(companion(core, tg, console=True).notify_answering_reports())   # negative control: with the console on it is sent
    assert len(tg.sent) == 1


def test_no_owner_in_the_policy_means_nobody_is_told():
    core, tg = Core([row()]), Telegram()
    asyncio.run(companion(core, tg, people=(GUEST,)).notify_answering_reports())
    assert tg.sent == [] and core.acked == []


def test_a_report_without_a_finished_notice_is_never_improvised_from_the_raw_data():
    r = row()
    r.pop("notice")
    core, tg = Core([r]), Telegram()
    asyncio.run(companion(core, tg).notify_answering_reports())
    assert tg.sent == [] and core.acked == []


def test_a_broken_core_response_is_swallowed_not_raised():
    class Broken(Core):
        async def answering_reports(self):
            raise CompanionError("ANSWERING_REPORTS_INVALID")
    tg = Telegram()
    asyncio.run(companion(Broken(), tg).notify_answering_reports())
    assert tg.sent == []


def test_the_core_adapter_only_accepts_well_formed_report_ids(monkeypatch):
    from bcc.telegram_companion.adapters import Core as RealCore
    seen = []

    class Fake(RealCore):
        def __init__(self):
            pass

        async def _request(self, method, path, payload=None, timeout=10):
            seen.append((method, path))
            if method == "GET":
                return {"items": [{"id": "ar-0123456789ab"}, {"id": "../../x"}, {"id": 5}, "junk"]}
            return {"id": "ar-0123456789ab", "delivered": True}

    core = Fake()
    assert [r["id"] for r in asyncio.run(core.answering_reports())] == ["ar-0123456789ab"]
    assert asyncio.run(core.ack_answering_report("ar-0123456789ab"))["delivered"] is True
    for bad in ("../x", "ar-xyz", "", None):
        try:
            asyncio.run(core.ack_answering_report(bad))
        except CompanionError as exc:
            assert str(exc) == "ANSWERING_REPORT_ID_INVALID"
        else:
            raise AssertionError("a malformed id must never reach the backend")
    assert seen == [("GET", "/api/telegram/calls/answering/reports?pending=true&limit=10"),
                    ("POST", "/api/telegram/calls/answering/reports/ar-0123456789ab/delivered")]
