"""§7 (17.09): the owner acceptance drives one REAL agentic path end to end —
model → permitted tool → effect on disk → independent read → restart → still
there — against a stub OpenAI-compatible model on 127.0.0.1.

No model PASS is claimed by this file: the stub proves the HARNESS. It shows
that the installed application, launched as the owner's acceptance launches
it, offers ``memory_write`` to the agent, executes the call inside the private
vault, records the second model turn, and that the acceptance judges the note
on disk rather than the model's word — a model that only says DONE fails.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bcc import owner_acceptance as owner

pytestmark = [pytest.mark.timeout(240)]


class _Seen:
    def __init__(self, *, calls_tool: bool):
        self.calls_tool = calls_tool
        self.requests: list[dict] = []


def _handler(seen: _Seen):
    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _json(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path.startswith('/models'):
                return self._json(200, {'data': [{'id': 'stub/tool-model', 'object': 'model'}]})
            self._json(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0)) or b'{}'))
            seen.requests.append(body)
            messages = body.get('messages', [])
            user = [m for m in messages if m.get('role') == 'user']
            prompt = (user[-1].get('content') if user else '') or ''
            offered = [t.get('function', {}).get('name') for t in body.get('tools') or []]
            if any(m.get('role') == 'tool' for m in messages):
                message = {'role': 'assistant', 'content': 'DONE'}
            elif 'memory.write' in prompt and 'memory_write' in offered and seen.calls_tool:
                fields = dict(re.findall(r'(title|kind|content) «([^»]+)»', prompt))
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'call_1', 'type': 'function',
                    'function': {'name': 'memory_write', 'arguments': json.dumps(fields)}}]}
            elif 'memory.write' in prompt:
                message = {'role': 'assistant', 'content': 'DONE'}
            else:
                message = {'role': 'assistant', 'content': '391'}
            self._json(200, {'id': 'stub', 'model': body.get('model'),
                             'choices': [{'message': message, 'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}],
                             'usage': {'prompt_tokens': 10, 'completion_tokens': 3}})
    return Stub


@pytest.fixture
def stub_factory(monkeypatch):
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    monkeypatch.setenv('no_proxy', '127.0.0.1,localhost')
    servers = []

    def start(*, calls_tool: bool):
        seen = _Seen(calls_tool=calls_tool)
        port = owner.free_port()
        server = ThreadingHTTPServer(('127.0.0.1', port), _handler(seen))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return seen, f'http://127.0.0.1:{port}'

    yield start
    for server in servers:
        server.shutdown()


def _selected(base_url: str) -> dict:
    return {'secret': 'sk-test-stub', 'provider': {'kind': 'openai_compat', 'base_url': base_url},
            'model': {'name': 'stub/tool-model', 'alias': 'stub/tool-model', 'kind': 'cloud',
                      'context_window': 8192, 'caps': '{}', 'price_in': 0, 'price_out': 0, 'pricing_known': True},
            'agent_id': 1}


def test_the_agent_path_writes_the_note_through_the_tool_and_survives_restart(stub_factory):
    seen, base_url = stub_factory(calls_tool=True)
    report = {}
    owner.verify(_selected(base_url), report, timeout=90)
    assert report['CORE_LIVE_API'] == 'PASS'
    assert report['REAL_AGENT_TASK'] == 'PASS', report.get('tool_task')
    assert report['RESTART_PERSISTENCE'] == 'PASS'
    tool_task = report['tool_task']
    assert tool_task['note_written'] is True and tool_task['note_path'].endswith('.md')
    assert tool_task['steps'] >= 2 and tool_task['reason'] == 'ok'
    # Three model turns: arithmetic, the tool call, the answer after the tool result.
    assert len(seen.requests) == 3, [r.get('messages', [])[-1].get('role') for r in seen.requests]
    offered = [t['function']['name'] for t in seen.requests[1].get('tools') or []]
    assert offered == ['memory_write'], 'exactly the one permitted tool is offered'
    assert seen.requests[0].get('tools') is None, 'the arithmetic agent has no tools'
    assert seen.requests[2]['messages'][-1]['role'] == 'tool'


def test_a_model_that_only_says_done_does_not_pass_the_agent_path(stub_factory):
    seen, base_url = stub_factory(calls_tool=False)
    report = {}
    with pytest.raises(RuntimeError, match='tool_not_called'):
        owner.verify(_selected(base_url), report, timeout=90)
    assert report['CORE_LIVE_API'] == 'PASS'
    assert report['REAL_AGENT_TASK'] == 'FAIL'
    assert report['tool_task']['reason'] == 'tool_not_called' and report['tool_task']['note_written'] is False
    assert report.get('RESTART_PERSISTENCE', 'NOT_RUN') == 'NOT_RUN', 'the restart stage never ran'
