"""Owner 20.10: the global STOP stayed on for hours after a test and the owner did not know.

The pult reminds the OWNER once when <core data>/computer/STOP is older than 60 min, then at most every 3 h while the
same STOP stays on; nothing when STOP is off; a restart inside the window sends nothing (state in the companion store).
Fake clock: ``now`` is passed in, the STOP age comes from the file's mtime.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import replace

from bcc.telegram_companion.config import CompanionError, Person, Settings
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', 10)
GUEST = Person(22222, 22222, 'guest')
T0 = 1_800_000_000.0          # STOP file mtime
MIN = 60.0


class FakeTelegram:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail
        self.authorize_delivery = None

    async def send(self, person, text, keyboard=None):
        if self.fail:
            raise CompanionError('NETWORK_UNAVAILABLE')
        self.sent.append((person, text, keyboard))


def _app(tmp_path, *, telegram=None, pc_control=True):
    settings = replace(Settings((OWNER, GUEST), local_model='local-test-fixture'), pc_control=pc_control,
                       core_data_dir=str(tmp_path / 'bcc'))
    tg = telegram or FakeTelegram()
    return Companion(settings, Store(tmp_path / 'pult'), tg, None, None), tg


def _stop_on(tmp_path, mtime=T0):
    f = tmp_path / 'bcc' / 'computer' / 'STOP'
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('owner\n0\n', encoding='utf-8')
    os.utime(f, (mtime, mtime))
    return f


def _tick(app, minutes):
    asyncio.run(app.notify_stop_reminder(now=T0 + minutes * MIN))


def test_timing_once_after_60_min_then_every_3_h(tmp_path):
    _stop_on(tmp_path)
    app, tg = _app(tmp_path)
    for m in (0, 30, 59, 59.9):
        _tick(app, m)
    assert tg.sent == [], 'nothing before 60 min'
    _tick(app, 61)
    assert len(tg.sent) == 1
    person, text, keyboard = tg.sent[0]
    assert person == OWNER
    assert text == '⛔ STOP включён уже 1 ч — нажми /resume или кнопку «Продолжить»'
    label, data = keyboard[0][0]
    assert label == '▶️ Продолжить' and app.buttons[data[2:]][1] == '/resume'
    for m in (62, 120, 180, 61 + 179):
        _tick(app, m)
    assert len(tg.sent) == 1, 'no repeat inside 3 h'
    _tick(app, 61 + 180)
    assert len(tg.sent) == 2 and tg.sent[1][1].startswith('⛔ STOP включён уже 4 ч')
    _tick(app, 61 + 181)
    assert len(tg.sent) == 2


def test_restart_inside_window_sends_nothing(tmp_path):
    _stop_on(tmp_path)
    app, tg = _app(tmp_path)
    _tick(app, 61)
    assert len(tg.sent) == 1
    app2, tg2 = _app(tmp_path)                 # restart: new process, same companion store
    for m in (61, 62, 90, 200):
        _tick(app2, m)
    assert tg2.sent == []
    _tick(app2, 61 + 180)
    assert len(tg2.sent) == 1


def test_no_reminder_when_stop_absent(tmp_path):
    app, tg = _app(tmp_path)
    for m in (61, 300, 10_000):
        _tick(app, m)
    assert tg.sent == []


def test_resume_then_new_stop_is_a_new_episode(tmp_path):
    f = _stop_on(tmp_path)
    app, tg = _app(tmp_path)
    _tick(app, 61)
    f.unlink()                                 # owner pressed «Продолжить»
    _tick(app, 70)
    assert app.store.get('stop_reminder') is None
    _stop_on(tmp_path, mtime=T0 + 80 * MIN)    # a new STOP 80 min after the first
    _tick(app, 80 + 59)
    assert len(tg.sent) == 1, 'the new STOP is younger than 60 min'
    _tick(app, 80 + 61)
    assert len(tg.sent) == 2


def test_owner_only_and_uncertain_send_not_replayed(tmp_path):
    _stop_on(tmp_path)
    app, tg = _app(tmp_path, telegram=FakeTelegram(fail=True))
    _tick(app, 61)
    tg.fail = False
    _tick(app, 62)
    assert tg.sent == [], 'an uncertain send is not repeated inside the window'
    _tick(app, 61 + 180)
    assert [p for p, _, _ in tg.sent] == [OWNER]


def test_console_off_still_reminds_without_button(tmp_path):
    _stop_on(tmp_path)
    app, tg = _app(tmp_path, pc_control=False)
    _tick(app, 61)
    assert len(tg.sent) == 1 and tg.sent[0][0] == OWNER and tg.sent[0][2] is None
