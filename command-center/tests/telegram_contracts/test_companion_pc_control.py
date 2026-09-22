"""Owner-only computer control: switch, guest refusal, Claude mode routing, stop, real PowerShell."""
from __future__ import annotations

import asyncio
import sys

import pytest

from bcc.telegram_companion import pc_control
from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.service import PC_OFF, Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)
GUEST = Person(22222, 22222, 'guest', None)


def msg(p, text):
    return {'from': {'id': p.user_id, 'is_bot': False}, 'chat': {'id': p.chat_id, 'type': 'private'}, 'text': text}


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send(self, person, text, keyboard=None):
        self.sent.append((person, str(text)))

    async def call(self, *a, **k):
        return True


def build(tmp_path, **kw):
    s = Settings((OWNER, GUEST), local_url='http://127.0.0.1:8083/v1', local_model='best', bot_token='x', **kw)
    return Companion(s, Store(tmp_path), FakeTelegram(), None, None)


def test_disabled_by_default_and_never_for_guests(tmp_path):
    off = build(tmp_path / 'a')
    assert asyncio.run(off.handle(OWNER, msg(OWNER, '/sh Get-Date'))) == PC_OFF
    on = build(tmp_path / 'b', pc_control=True)
    for text in ('/sh Get-Date', '/claude hi', '/screen', '/mode claude', '/pc'):
        assert asyncio.run(on.handle(GUEST, msg(GUEST, text))) == PC_OFF
    assert not any('Управление ПК' in label for row in on.main_menu(GUEST) for label, _ in row)
    assert any('Управление ПК' in label for row in on.main_menu(OWNER) for label, _ in row)


def test_invalid_permission_mode_rejected():
    with pytest.raises(ValueError):
        Settings((OWNER,), pc_control=True, claude_permission_mode='yolo')


def test_claude_mode_routes_plain_text_and_keeps_session(tmp_path, monkeypatch):
    c = build(tmp_path, pc_control=True)
    calls = []

    async def fake_claude(prompt, *, session, cwd, permission_mode, timeout):
        calls.append((prompt, session, permission_mode))
        return 'готово', 'sess-1', 0.01

    monkeypatch.setattr(pc_control, 'claude', fake_claude)

    async def scenario():
        assert 'Режим Claude' in await c.handle(OWNER, msg(OWNER, '/mode claude'))
        await c.handle(OWNER, msg(OWNER, 'проверь диск'))
        await c.claude_job
        await c.handle(OWNER, msg(OWNER, 'и ещё раз'))
        await c.claude_job
    asyncio.run(scenario())
    assert calls == [('проверь диск', None, 'bypassPermissions'), ('и ещё раз', 'sess-1', 'bypassPermissions')]
    assert any('готово' in t for _, t in c.telegram.sent)


def test_claude_stop_cancels_running_job(tmp_path, monkeypatch):
    c = build(tmp_path, pc_control=True)

    async def slow(prompt, **kw):
        await asyncio.sleep(60)

    monkeypatch.setattr(pc_control, 'claude', slow)

    async def scenario():
        await c.handle(OWNER, msg(OWNER, '/claude долгая задача'))
        busy = await c.handle(OWNER, msg(OWNER, '/claude ещё'))
        assert 'ещё работает' in busy
        await asyncio.sleep(0)
        assert 'Останавливаю' in await c.handle(OWNER, msg(OWNER, '/claude_stop'))
        await asyncio.gather(c.claude_job, return_exceptions=True)
    asyncio.run(scenario())
    assert any('остановлен' in t for _, t in c.telegram.sent)


@pytest.mark.skipif(sys.platform != 'win32', reason='PowerShell on Windows')
def test_real_powershell_output_and_exit_code(tmp_path):
    c = build(tmp_path, pc_control=True)
    out = asyncio.run(c.handle(OWNER, msg(OWNER, '/sh Write-Output "привет $((6*7))"; exit 3')))
    assert 'код выхода 3' in out and 'привет 42' in out
