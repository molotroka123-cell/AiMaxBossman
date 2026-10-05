"""The пульт relays development-tree zone reports to the owner once, cursor-based, never to a guest."""
from __future__ import annotations

import asyncio
from dataclasses import replace

from bcc.telegram_companion.config import CompanionError, Person, Settings
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', 10)
GUEST = Person(22222, 22222, 'guest')


class FakeCore:
    def __init__(self, rows):
        self.rows, self.afters = rows, []

    async def zone_reports(self, after):
        self.afters.append(after)
        return [r for r in self.rows if r['seq'] > after]


class FakeTelegram:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail
        self.authorize_delivery = None

    async def send(self, person, text, buttons=None):
        if self.fail:
            raise CompanionError('NETWORK_UNAVAILABLE')
        self.sent.append((person, text))


def _app(tmp_path, rows, *, pc_control=True, telegram=None):
    settings = replace(Settings((OWNER, GUEST), local_model='local-test-fixture'), pc_control=pc_control)
    tg = telegram or FakeTelegram()
    app = Companion(settings, Store(tmp_path), tg, FakeCore(rows), None)
    return app, tg


ROWS = [{'seq': 1, 'text': 'Зона «A»: Bossman начал работу'}, {'seq': 2, 'text': 'Зона «A»: кандидат готов'}]


def test_reports_go_to_the_owner_once(tmp_path):
    app, tg = _app(tmp_path, ROWS)
    asyncio.run(app.notify_zone_reports())
    asyncio.run(app.notify_zone_reports())
    assert [p for p, _ in tg.sent] == [OWNER, OWNER]
    assert 'кандидат готов' in tg.sent[1][1] and tg.sent[0][1].startswith('🌳 Дерево развития')
    assert app.core.afters == [0, 2]


def test_no_relay_when_owner_console_is_off(tmp_path):
    app, tg = _app(tmp_path, ROWS, pc_control=False)
    asyncio.run(app.notify_zone_reports())
    assert tg.sent == []


def test_uncertain_send_is_not_replayed(tmp_path):
    app, tg = _app(tmp_path, ROWS, telegram=FakeTelegram(fail=True))
    asyncio.run(app.notify_zone_reports())
    app.telegram.fail = False
    asyncio.run(app.notify_zone_reports())
    assert tg.sent == []  # cursor advanced before each send attempt
