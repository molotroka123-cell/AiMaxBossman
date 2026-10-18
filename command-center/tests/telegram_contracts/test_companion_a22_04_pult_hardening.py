"""A22-04 (A18 section 5): owner-only intake, skip-permissions needs a per-call approval, STOP always works.

Offline: fake Telegram, fake core, real Store. The one-time /work_yes code is covered in
test_companion_work_command.py.
"""
from __future__ import annotations

import asyncio
import time

from bcc.telegram_companion import agent_bridge as ab
from bcc.telegram_companion import claude_bridge
from bcc.telegram_companion.config import CompanionError, Person, Settings
from bcc.telegram_companion.console import CONSOLE_OFF
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)
GUEST = Person(22222, 22222, 'guest', None)


def msg(p, text, **extra):
    return {'from': {'id': p.user_id, 'is_bot': False}, 'chat': {'id': p.chat_id, 'type': 'private'},
            'text': text, **extra}


class FakeTelegram:
    def __init__(self):
        self.sent, self.calls, self.deleted = [], [], []

    async def send(self, person, text, keyboard=None):
        self.sent.append((str(text), keyboard))

    async def call(self, method, payload=None, *a, **k):
        self.calls.append((method, payload))
        return True


class FakeCore:
    def __init__(self, fail=False):
        self.stopped, self.fail = 0, fail

    async def computer_stop(self):
        if self.fail:
            raise CompanionError("down")
        self.stopped += 1
        return {}


def build(tmp_path, **settings):
    s = Settings((OWNER, GUEST), local_url='http://127.0.0.1:8083/v1', local_model='best', bot_token='x', **settings)
    tg = FakeTelegram()
    core = FakeCore()
    return Companion(s, Store(tmp_path), tg, core, None), tg, core


def inbox(c):
    return c.store.db.execute('SELECT count(*) FROM inbox').fetchone()[0]


def text_of(out):
    return out if isinstance(out, str) else getattr(out, 'text', str(out))


# ---------------------------------------------------------------- 1. owner-only intake

def test_owner_message_is_accepted_guest_and_stranger_are_dropped(tmp_path):
    c, tg, _ = build(tmp_path)
    asyncio.run(c.ingest({'update_id': 1, 'message': msg(OWNER, 'привет')}))
    assert inbox(c) == 1
    asyncio.run(c.ingest({'update_id': 2, 'message': msg(GUEST, 'привет')}))
    stranger = Person(99999, 99999, 'guest', None)
    asyncio.run(c.ingest({'update_id': 3, 'message': msg(stranger, 'привет')}))
    assert inbox(c) == 1
    assert tg.sent == [] and tg.calls == []              # nothing answered, deleted or fetched


def test_guest_secret_key_and_manual_are_dropped_before_any_processing(tmp_path):
    c, tg, _ = build(tmp_path)
    for n, text in enumerate(('/key OPENROUTER_API_KEY=sk-or-v1-' + 'a' * 40, '/manual 5', '/work x'), start=10):
        asyncio.run(c.ingest({'update_id': n, 'message': msg(GUEST, text)}))
    assert inbox(c) == 0 and tg.sent == [] and tg.calls == []


def test_forwarded_owner_message_is_dropped(tmp_path):
    c, tg, _ = build(tmp_path)
    for n, extra in enumerate(({'forward_origin': {'type': 'user'}}, {'forward_from': {'id': 1}},
                               {'sender_chat': {'id': -5}}), start=20):
        asyncio.run(c.ingest({'update_id': n, 'message': msg(OWNER, '/stop', **extra)}))
    assert inbox(c) == 0 and tg.sent == []


def test_owner_id_inside_a_group_chat_is_dropped(tmp_path):
    c, tg, _ = build(tmp_path)
    group = {'from': {'id': OWNER.user_id, 'is_bot': False}, 'chat': {'id': -100500, 'type': 'supergroup'},
             'text': 'всем привет'}
    asyncio.run(c.ingest({'update_id': 30, 'message': group}))
    group2 = {**group, 'chat': {'id': OWNER.chat_id, 'type': 'group'}}
    asyncio.run(c.ingest({'update_id': 31, 'message': group2}))
    assert inbox(c) == 0 and tg.sent == []


def test_edited_updates_are_ignored_and_guest_button_press_is_silent(tmp_path):
    c, tg, _ = build(tmp_path)
    asyncio.run(c.ingest({'update_id': 40, 'edited_message': msg(OWNER, 'x')}))
    owner_btn = c.button(OWNER, 'ok', '/status')[1]
    press = {'update_id': 41, 'callback_query': {'id': 'q', 'from': {'id': GUEST.user_id, 'is_bot': False},
                                                 'data': owner_btn,
                                                 'message': {'chat': {'id': GUEST.chat_id, 'type': 'private'}}}}
    asyncio.run(c.ingest(press))
    assert inbox(c) == 0 and tg.calls == []


# ---------------------------------------------------------------- 3. skip-permissions only with a per-call approval

def bridge_app(tmp_path, monkeypatch, **settings):
    seen = []

    async def fake_claude(prompt, *, session, cwd, permission_mode, timeout):
        seen.append(('claude', permission_mode))
        return 'ok', 's1', None

    async def fake_codex(prompt, *, session, cwd, sandbox, timeout):
        seen.append(('codex', sandbox))
        return 'ok', 't1'
    monkeypatch.setattr(claude_bridge, 'claude', fake_claude)
    monkeypatch.setattr(claude_bridge, 'codex', fake_codex)
    c, tg, _core = build(tmp_path, claude_bridge=True, codex_bridge=True, **settings)
    return c, tg, seen


def run_agent(c, text, callback=False, who=OWNER):
    async def go():
        m = msg(who, text)
        if callback:
            m['_callback'] = True
        out = await c.handle(who, m)
        for name in ('claude', 'codex'):
            job = c.agent_jobs.get(name)
            if job is not None:
                await job
        return out
    return asyncio.run(go())


def go_command(reply, c):
    return [c.buttons[d[2:]][1] for row in reply.keyboard for _l, d in row if '_bypass_go' in c.buttons[d[2:]][1]][0]


def test_configured_bypass_does_not_apply_without_a_per_call_approval(tmp_path, monkeypatch):
    c, _tg, seen = bridge_app(tmp_path, monkeypatch, claude_permission_mode='bypassPermissions',
                              codex_sandbox='danger-full-access')
    run_agent(c, '/claude сделай')
    run_agent(c, '/codex сделай')
    assert seen == [('claude', 'default'), ('codex', 'workspace-write')]


def test_other_configured_modes_are_not_changed_owner_not_locked_out(tmp_path, monkeypatch):
    c, _tg, seen = bridge_app(tmp_path, monkeypatch, claude_permission_mode='acceptEdits', codex_sandbox='read-only')
    run_agent(c, '/claude сделай')
    run_agent(c, '/codex сделай')
    assert seen == [('claude', 'acceptEdits'), ('codex', 'read-only')]


def test_bypass_runs_once_after_the_button_and_not_again(tmp_path, monkeypatch):
    c, _tg, seen = bridge_app(tmp_path, monkeypatch)
    reply = run_agent(c, '/claude --bypass почини всё')
    assert seen == []                                    # asking for it runs nothing
    cmd = go_command(reply, c)
    run_agent(c, cmd, callback=True)
    assert seen == [('claude', 'bypassPermissions')]
    again = run_agent(c, cmd, callback=True)             # replay of the same button
    assert seen == [('claude', 'bypassPermissions')] and 'не подходит' in text_of(again)
    run_agent(c, '/claude следующее')                     # the next plain call is back to the normal mode
    assert seen[-1] == ('claude', 'acceptEdits')


def test_codex_bypass_uses_full_access_for_one_call_only(tmp_path, monkeypatch):
    c, _tg, seen = bridge_app(tmp_path, monkeypatch)
    cmd = go_command(run_agent(c, '/codex --bypass задача'), c)
    run_agent(c, cmd, callback=True)
    run_agent(c, '/codex задача 2')
    assert seen == [('codex', 'danger-full-access'), ('codex', 'workspace-write')]


def test_typed_bypass_go_without_the_button_is_refused(tmp_path, monkeypatch):
    c, _tg, seen = bridge_app(tmp_path, monkeypatch)
    cmd = go_command(run_agent(c, '/claude --bypass задача'), c)
    assert 'только кнопкой' in text_of(run_agent(c, cmd, callback=False))
    assert seen == []
    run_agent(c, cmd, callback=True)                     # the token is still valid for the real button
    assert seen == [('claude', 'bypassPermissions')]


def test_bypass_token_expires_and_is_bound_to_its_agent(tmp_path, monkeypatch):
    c, _tg, seen = bridge_app(tmp_path, monkeypatch)
    cmd = go_command(run_agent(c, '/claude --bypass задача'), c)
    nonce = cmd.split()[1]
    assert 'не подходит' in text_of(run_agent(c, f'/codex_bypass_go {nonce}', callback=True))
    cmd = go_command(run_agent(c, '/claude --bypass задача'), c)
    real = time.time
    monkeypatch.setattr(ab.time, 'time', lambda: real() + ab.BYPASS_TTL_S + 1)
    assert 'не подходит' in text_of(run_agent(c, cmd, callback=True))
    assert seen == []


def test_guest_cannot_request_or_use_bypass(tmp_path, monkeypatch):
    c, _tg, seen = bridge_app(tmp_path, monkeypatch)
    for text in ('/claude --bypass x', '/claude_bypass_go abc'):
        assert text_of(run_agent(c, text, callback=True, who=GUEST)) == ab.AGENT_DENIED
    assert seen == []


def test_stop_voids_pending_bypass_and_work_codes(tmp_path, monkeypatch):
    c, _tg, seen = bridge_app(tmp_path, monkeypatch, pc_control=False)
    cmd = go_command(run_agent(c, '/claude --bypass задача'), c)
    c.issue_work_grant(OWNER, 7, None)
    run_agent(c, '/stop')
    assert c._work_grants() == {}
    c.store.put('delegation_locked', False)
    assert 'не подходит' in text_of(run_agent(c, cmd, callback=True))
    assert seen == []


# ---------------------------------------------------------------- 4. /stop works without pc_control

def test_stop_works_with_pc_control_off_and_does_the_full_stop(tmp_path):
    c, _tg, core = build(tmp_path, pc_control=False)

    async def go():
        async def forever():
            await asyncio.sleep(60)
        job = asyncio.create_task(forever())
        c.agent_jobs['claude'] = job
        out = await c.handle(OWNER, msg(OWNER, '/stop'))
        await asyncio.sleep(0)
        return out, job
    out, job = asyncio.run(go())
    assert out is not CONSOLE_OFF and 'СТОП' in text_of(out)
    assert c.store.get('delegation_locked', False) is True
    assert core.stopped == 1 and job.cancelled()


def test_pause_works_with_pc_control_off_but_other_console_commands_stay_off(tmp_path):
    c, _tg, _core = build(tmp_path, pc_control=False)
    assert 'Пауза' in text_of(asyncio.run(c.handle(OWNER, msg(OWNER, '/pause'))))
    assert asyncio.run(c.handle(OWNER, msg(OWNER, '/queue'))) == CONSOLE_OFF


def test_stop_reports_honestly_when_the_core_is_down_and_still_locks(tmp_path):
    c, _tg, core = build(tmp_path, pc_control=False)
    core.fail = True
    out = text_of(asyncio.run(c.handle(OWNER, msg(OWNER, '/stop'))))
    assert 'НЕ подтвердил' in out and c.store.get('delegation_locked', False) is True


def test_guest_stop_is_refused_and_locks_nothing(tmp_path):
    c, _tg, core = build(tmp_path, pc_control=True)
    assert asyncio.run(c.handle(GUEST, msg(GUEST, '/stop'))) == CONSOLE_OFF
    assert c.store.get('delegation_locked', False) is False and core.stopped == 0
