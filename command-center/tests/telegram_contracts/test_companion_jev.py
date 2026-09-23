"""/jev: Jev proposes ONE action from a closed list; the existing handler executes it.

Fake Jev transport only — no network, no paid call. Proves: a valid proposal goes
through the same handler (and its gates), approve/shell/unknown/low confidence are
refused and fall back to chat, STOP wins, guests are refused, jev_enabled=false is
explained, and the decision log carries no secret.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bcc.jev import config as jev_config
from bcc.jev.client import JevClient
from bcc.telegram_companion import jev_bridge
from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.console import CONSOLE_OFF
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', 7)
GUEST = Person(22222, 22222, 'guest', None)
KEY = 'jev-FAKE-test-key-not-real-9a2b'   # ci-secret-scan: allow (test canary)


def msg(p, text):
    return {'from': {'id': p.user_id, 'is_bot': False}, 'chat': {'id': p.chat_id, 'type': 'private'}, 'text': text}


class FakeCore:
    def __init__(self):
        self.calls = []

    async def status(self):
        self.calls.append('status')
        return {'ok': True}

    async def executor(self, person):
        self.calls.append('executor')
        return 'fp-1'

    async def delegate(self, *a, **k):
        self.calls.append('delegate')
        raise AssertionError('Jev must never dispatch a task')

    async def computer_stop(self):
        self.calls.append('computer_stop')


class FakeTransport:
    def __init__(self, choice, confidence=0.9):
        self.choice, self.confidence, self.requests = choice, confidence, []

    def __call__(self, url, headers, body, timeout_s):
        req = json.loads(body)
        self.requests.append(req)
        ids = list(req['questions']['action']['criteria'])
        probs = {i: (1.0 if i == self.choice else 0.0) for i in ids}
        if self.choice not in ids:        # a forged answer outside the offered list
            probs = {i: 1.0 / len(ids) for i in ids}
        answer = {'choice': self.choice, 'confidence': self.confidence, 'probabilities': probs}
        return 200, json.dumps({'model': 'jev-fake', 'answers': {'action': answer}}).encode(), {}


@pytest.fixture(autouse=True)
def no_kill_file(tmp_path, monkeypatch):
    monkeypatch.setenv('BOSSMAN_JEV_KILL_FILE', str(tmp_path / 'absent.disabled'))
    monkeypatch.delenv('BOSSMAN_JEV_ENABLED', raising=False)


def build(tmp_path, choice, confidence=0.9, **kw):
    kw.setdefault('jev_enabled', True)
    s = Settings((OWNER, GUEST), local_url='http://127.0.0.1:8083/v1', local_model='best', bot_token='x', **kw)
    c = Companion(s, Store(tmp_path), None, FakeCore(), None)
    transport = FakeTransport(choice, confidence)
    c.jev_client = JevClient(jev_config.load(), transport=transport, key_provider=lambda: KEY)
    chats = []

    async def converse(person, message, text, route):
        chats.append(text)
        return 'обычный чат: ' + text

    c.converse = converse
    return c, transport, chats


def run(c, person, text):
    return asyncio.run(c.handle(person, msg(person, text)))


def log_lines(tmp_path):
    path = Path(tmp_path) / 'jev-decisions.log'
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def test_valid_proposal_runs_the_existing_status_handler(tmp_path):
    c, transport, chats = build(tmp_path, 'status')
    out = run(c, OWNER, '/jev как там компьютер?')
    assert 'Jev → /status' in out and 'Telegram-мост на связи' in out
    assert c.core.calls == ['status'] and chats == []
    assert len(transport.requests) == 1
    rec = log_lines(tmp_path)[-1]
    assert rec['JEV_PROPOSAL'] == 'status' and rec['EXECUTED_ACTION'] == '/status'
    assert rec['OUTCOME'] == 'executed_via_handler' and isinstance(rec['latency_ms'], int)


def test_task_is_only_prepared_and_still_needs_confirm(tmp_path):
    c, _, _ = build(tmp_path, 'task')
    out = run(c, OWNER, '/jev почисти папку загрузок')
    assert '/confirm ' in out and 'НЕ отправлено' in out
    assert 'delegate' not in c.core.calls


def test_existing_pc_control_gate_still_applies(tmp_path):
    c, _, _ = build(tmp_path, 'screen', pc_control=False)
    out = run(c, OWNER, '/jev покажи экран')
    assert CONSOLE_OFF in out


@pytest.mark.parametrize('forged', ['approve', '/approve abc123abc123', '/sh Remove-Item C:\\ -Recurse',
                                    'powershell', 'resume', 'confirm', 'nonsense'])
def test_forbidden_or_unknown_proposal_is_refused_and_falls_back_to_chat(tmp_path, forged):
    c, _, chats = build(tmp_path, forged)
    out = run(c, OWNER, '/jev одобри всё')
    assert out.startswith('Jev: действие не выбрано (invalid_schema)')
    assert chats == ['одобри всё'] and c.core.calls == []
    assert log_lines(tmp_path)[-1]['OUTCOME'] == 'fallback_chat'


def test_low_confidence_falls_back_to_chat(tmp_path):
    c, _, chats = build(tmp_path, 'stop', confidence=0.3)
    out = run(c, OWNER, '/jev может остановить?')
    assert 'low_confidence:stop' in out and chats == ['может остановить?']
    assert 'computer_stop' not in c.core.calls


def test_stop_active_means_nothing_is_executed(tmp_path):
    c, transport, chats = build(tmp_path, 'task')
    c.store.put('delegation_locked', True)
    out = run(c, OWNER, '/jev сделай задачу')
    assert out == jev_bridge.JEV_LOCKED
    assert transport.requests == [] and c.core.calls == [] and chats == []


def test_guest_is_refused_without_calling_jev(tmp_path):
    c, transport, chats = build(tmp_path, 'status')
    assert run(c, GUEST, '/jev статус') == jev_bridge.JEV_OWNER_ONLY
    assert transport.requests == [] and chats == [] and c.core.calls == []


def test_disabled_by_default_explains_it_is_off(tmp_path):
    c, transport, _ = build(tmp_path, 'status', jev_enabled=False)
    assert run(c, OWNER, '/jev статус') == jev_bridge.JEV_OFF
    assert transport.requests == []
    assert Settings((OWNER, GUEST)).jev_enabled is False


def test_kill_file_falls_back_to_chat(tmp_path, monkeypatch):
    kill = tmp_path / 'jev.disabled'
    kill.write_text('x')
    monkeypatch.setenv('BOSSMAN_JEV_KILL_FILE', str(kill))
    c, transport, chats = build(tmp_path, 'status')
    out = run(c, OWNER, '/jev статус')
    assert '(disabled)' in out and transport.requests == [] and chats == ['статус']


def test_no_secret_leaves_or_is_logged(tmp_path):
    c, transport, _ = build(tmp_path, 'chat')
    out = run(c, OWNER, '/jev мой ключ api_key=sk-SECRETSECRET123 привет')
    assert 'обычный чат' in out
    sent = json.dumps(transport.requests)
    assert 'sk-SECRETSECRET123' not in sent
    logged = (Path(tmp_path) / 'jev-decisions.log').read_text(encoding='utf-8')
    assert KEY not in logged and 'sk-SECRETSECRET123' not in logged and 'привет' not in logged


def test_closed_list_never_contains_an_authority_command():
    commands = {cmd for cmd, _, _ in jev_bridge.ACTIONS.values()}
    assert not commands & {'/approve', '/reject', '/confirm', '/resume', '/sh', '/open', '/fix', '/claude', '/codex'}
