"""Owner-only Claude Code / Codex bridge; still no raw shell (owner decision 2026-09-22).

History: section 5 of the owner's spec removed /sh and /claude. On 2026-09-22 the owner
asked for Claude Code and Codex back, from the phone, with these rules — which this file
proves: rights exist ONLY for the owner's Telegram ID; every other login gets nothing;
the check runs on every call and again before delivery; the bridge is off unless the
local config turns it on; STOP cancels a running agent; /sh stays removed.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from bcc.telegram_companion import agent_bridge, claude_bridge, service
from bcc.telegram_companion.agent_bridge import AGENT_DENIED
from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.console import CONSOLE_OFF, NO_DIRECT_SHELL
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)
GUEST = Person(22222, 22222, 'guest', None)
PACKAGE = Path(service.__file__).parent


def msg(p, text):
    return {'from': {'id': p.user_id, 'is_bot': False}, 'chat': {'id': p.chat_id, 'type': 'private'}, 'text': text}


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.docs = []

    async def send(self, person, text, keyboard=None):
        self.sent.append((person, str(text)))

    async def send_document(self, person, name, data, caption=''):
        self.docs.append((person, name))

    async def call(self, *a, **k):
        return True


def build(tmp_path, **kw):
    s = Settings((OWNER, GUEST), local_url='http://127.0.0.1:8083/v1', local_model='best', bot_token='x', **kw)
    return Companion(s, Store(tmp_path), FakeTelegram(), None, None)


def run(c, person, text):
    return asyncio.run(c.handle(person, msg(person, text)))


def test_raw_shell_is_still_gone_for_everyone(tmp_path):
    c = build(tmp_path, pc_control=True, claude_bridge=True, codex_bridge=True)
    for text in ('/sh Get-Date', '/mode claude', '/pc'):
        assert run(c, OWNER, text) == NO_DIRECT_SHELL
        assert run(c, GUEST, text) == CONSOLE_OFF


def test_bridge_is_off_unless_the_local_config_turns_it_on(tmp_path, monkeypatch):
    calls = []

    async def fake_claude(*a, **k):
        calls.append(a)
        return 'ok', 's1', 0.0

    monkeypatch.setattr(claude_bridge, 'claude', fake_claude)
    c = build(tmp_path)
    assert 'выключен локально' in run(c, OWNER, '/claude привет')
    assert 'выключен локально' in run(c, OWNER, '/codex привет')
    assert calls == []


def test_guests_get_no_agent_command_at_all(tmp_path, monkeypatch):
    calls = []

    async def fake(*a, **k):
        calls.append(a)
        return 'ok', 's1', 0.0

    monkeypatch.setattr(claude_bridge, 'claude', fake)
    monkeypatch.setattr(claude_bridge, 'codex', fake)
    c = build(tmp_path, claude_bridge=True, codex_bridge=True)
    for text in ('/claude сделай', '/codex сделай', '/claude_new', '/codex_stop', '/agents', '/audits'):
        assert run(c, GUEST, text) == AGENT_DENIED
    assert calls == [] and c.telegram.docs == []


def test_owner_drives_claude_and_the_session_continues(tmp_path, monkeypatch):
    seen = []

    async def fake_claude(prompt, *, session, cwd, permission_mode, timeout):
        seen.append((prompt, session, permission_mode))
        return f'ответ на {prompt}', 'sess-1', 0.01

    monkeypatch.setattr(claude_bridge, 'claude', fake_claude)
    c = build(tmp_path, claude_bridge=True, claude_permission_mode='acceptEdits')

    async def scenario():
        first = await c.handle(OWNER, msg(OWNER, '/claude проверь тесты'))
        await c.agent_jobs['claude']
        await c.handle(OWNER, msg(OWNER, '/claude а теперь пуш'))
        await c.agent_jobs['claude']
        return first

    assert 'принял задание' in asyncio.run(scenario())
    assert seen == [('проверь тесты', None, 'acceptEdits'), ('а теперь пуш', 'sess-1', 'acceptEdits')]
    assert [t for p, t in c.telegram.sent if p == OWNER and 'ответ на' in t]


def test_owner_revoked_mid_run_gets_no_delivery(tmp_path, monkeypatch):
    c = build(tmp_path, claude_bridge=True)
    revoked = Settings((Person(33333, 33333, 'owner', None),), local_url='http://127.0.0.1:8083/v1',
                       local_model='best', bot_token='x', claude_bridge=True)

    async def fake_claude(*a, **k):
        c.policy_provider = lambda: revoked      # the local config changed while Claude worked
        return 'секретный результат', 's', 0.0

    monkeypatch.setattr(claude_bridge, 'claude', fake_claude)

    async def scenario():
        await c.handle(OWNER, msg(OWNER, '/claude сделай'))
        await c.agent_jobs['claude']

    asyncio.run(scenario())
    assert not any('секретный результат' in t for _, t in c.telegram.sent)


def test_stop_cancels_a_running_agent_and_blocks_new_ones(tmp_path, monkeypatch):
    async def slow_claude(*a, **k):
        await asyncio.sleep(60)
        return 'never', None, None

    monkeypatch.setattr(claude_bridge, 'claude', slow_claude)
    c = build(tmp_path, pc_control=True, claude_bridge=True)

    class Core:
        async def computer_stop(self):
            return True

    c.core = Core()

    async def scenario():
        await c.handle(OWNER, msg(OWNER, '/claude долгая работа'))
        job = c.agent_jobs['claude']
        await asyncio.sleep(0)
        halted = await c.handle(OWNER, msg(OWNER, '/stop'))
        await job                                   # the turn ends itself after the cancel
        assert any('остановлен' in t for _, t in c.telegram.sent)
        again = await c.handle(OWNER, msg(OWNER, '/claude ещё'))
        return str(halted), str(again)

    halted, again = asyncio.run(scenario())
    assert 'Claude/Codex остановлены' in halted
    assert 'заблокированы' in again


def test_only_the_bridge_module_starts_processes_and_never_through_a_shell():
    """Subprocess use is confined to claude_bridge.py and never uses a shell."""
    hits = []
    for file in PACKAGE.glob('*.py'):
        tree = ast.parse(file.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {'create_subprocess_exec', 'create_subprocess_shell'}:
                hits.append((file.name, node.attr))
    assert hits == [('claude_bridge.py', 'create_subprocess_exec')]
    assert not (PACKAGE / 'pc_control.py').exists()


def test_invalid_bridge_settings_are_refused():
    with pytest.raises(ValueError):
        Settings((OWNER,), claude_permission_mode='yolo')
    with pytest.raises(ValueError):
        Settings((OWNER,), codex_sandbox='everything')
    with pytest.raises(ValueError):
        Settings((OWNER,), claude_timeout=5)


def test_old_config_still_loads_and_bossman_launch_stays_retired(tmp_path):
    from bcc.telegram_companion.config import load
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({
        'people': [{'user_id': 11111, 'chat_id': 11111, 'role': 'owner'}],
        'local_url': 'http://127.0.0.1:8083/v1', 'local_model': 'best', 'pc_control': True,
        'claude_cwd': r'C:\Users\asd', 'claude_permission_mode': 'bypassPermissions',
        'claude_timeout': 1800, 'bossman_launch': 'Start-Process bossman.exe'}), encoding='utf-8')
    settings = load(path)
    assert settings.claude_bridge is False                  # an old config does not switch it on
    assert settings.claude_permission_mode == 'bypassPermissions'
    assert not hasattr(settings, 'bossman_launch')
