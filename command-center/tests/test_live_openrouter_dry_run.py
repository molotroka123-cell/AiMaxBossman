"""Путь «ключ есть → шесть задач → перезапуск → PASS» — насухо, против заглушки.

Почему этот тест существует. Вердикт FROZEN решает tools/live_openrouter_owner.py,
и до этого файла его главная функция exercise() не выполнялась НИГДЕ, кроме
Windows-прогона с настоящим ключом, — а ключа не было. Существующий
tests/test_live_openrouter_owner.py проверяет отбор бесплатных моделей, контракт
ответа и вычистку секрета, но не запуск изолированного приложения под -I, не вход
через настоящий UI, не composer, не перезапуск и не сохранность. Ровно этот путь
здесь и проходится — с локальной заглушкой вместо OpenRouter.

Чего тест НЕ утверждает: ничего о качестве настоящих бесплатных моделей и их
лимитах. Заглушка отвечает идеально; это проверка трубы, а не воды.

Негативный контроль обязателен: заглушка, ответившая 429 на одну задачу, обязана
дать FAIL, и запись обязана НАЗВАТЬ причину — иначе лимит бесплатной модели
неотличим от её глупости (§3: разные исходы — разные результаты).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from .browser_support import chromium_available, reason as browser_reason, required

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('_live_owner_under_test', ROOT / 'tools' / 'live_openrouter_owner.py')
live = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = live
_spec.loader.exec_module(live)

pytestmark = [pytest.mark.timeout(420),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]

MODELS = ['stub/alpha:free', 'stub/beta:free']
ANSWERS = {
    'Посчитай 17*23. Ответь только числом.': '391',
    'Return only valid JSON: an object with key city set to Prague and key count set to integer 3.':
        '{"city": "Prague", "count": 3}',
    'Напиши ровно три строки без нумерации: сначала Прага, затем Брно, затем Острава.':
        'Прага\nБрно\nОстрава',
}
CASE_OF = {prompt: case for case, prompt in live.TASKS}


def _catalog():
    return [{'id': name, 'context_length': 8192, 'pricing': {'prompt': '0', 'completion': '0'},
             'architecture': {'output_modalities': ['text']}} for name in MODELS]


class _Seen:
    def __init__(self):
        self.calls = 0
        self.auth: set[str] = set()
        self.fail_case = ''


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
                return self._json(200, {'data': _catalog()})
            self._json(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0)) or b'{}'))
            seen.calls += 1
            seen.auth.add(self.headers.get('Authorization', '')[:7])
            user = [m for m in body.get('messages', []) if m.get('role') == 'user']
            prompt = (user[-1].get('content') if user else '') or ''
            if seen.fail_case and CASE_OF.get(prompt) == seen.fail_case:
                return self._json(429, {'error': {'message': 'Rate limit exceeded: free-models-per-day', 'code': 429}})
            self._json(200, {'id': 'stub', 'model': body.get('model'),
                             'choices': [{'message': {'role': 'assistant', 'content': ANSWERS.get(prompt, '?')},
                                          'finish_reason': 'stop'}],
                             'usage': {'prompt_tokens': 12, 'completion_tokens': 4}})
    return Stub


@pytest.fixture
def stub(monkeypatch):
    """Заглушка модели на 127.0.0.1; BASE_URL инструмента указывает на неё.

    NO_PROXY нужен потому, что launch() копирует окружение в дочернее
    приложение, а в некоторых средах HTTPS_PROXY уводит даже 127.0.0.1.
    """
    from bcc import owner_acceptance as owner
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    monkeypatch.setenv('no_proxy', '127.0.0.1,localhost')
    seen = _Seen()
    port = owner.free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), _handler(seen))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(live, 'BASE_URL', f'http://127.0.0.1:{port}')
    try:
        yield seen
    finally:
        server.shutdown()


def _sha() -> str:
    return subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()


def _freeze_accepts(trajectories, sha) -> bool:
    """Тот же предикат, что в tools/astra6_freeze.py для live-model.json."""
    return (len(trajectories) == 6 and all(
        t.get('status') == 'PASS' and t.get('restart_persistence') == 'PASS'
        and t.get('source_sha') == sha and t.get('model') in MODELS for t in trajectories))


def test_the_installed_free_model_path_passes_end_to_end_against_a_stub(stub):
    sha = _sha()
    report, trajectories = {'identity': {'source_sha': sha}}, []
    for model in live.free_models(_catalog()):
        live.exercise(model, 'sk-or-v1-DUMMYDUMMY', SimpleNamespace(timeout=60.0), report, trajectories)

    assert len(trajectories) == 6
    assert all(t['status'] == 'PASS' and t['restart_persistence'] == 'PASS' for t in trajectories), trajectories
    assert all(t['reason'] == 'ok' and t['errors'] == [] for t in trajectories)
    assert _freeze_accepts(trajectories, sha), 'запись не прошла бы предикат astra6_freeze'
    # Ключ обязан доходить до провайдера как Bearer — иначе «PASS» был бы получен без модели.
    assert stub.auth == {'Bearer '}
    # Ровно по одному вызову на задачу: повторных попыток на исправной модели быть не должно.
    assert stub.calls == 6
    assert report['stage'] == 'restart_persistence'


def test_a_rate_limited_task_is_named_not_merely_failed(stub):
    """Негативный контроль первого теста и проверка §3 сразу."""
    sha = _sha()
    stub.fail_case = 'structured_data'
    report, trajectories = {'identity': {'source_sha': sha}}, []
    live.exercise(live.free_models(_catalog())[0], 'sk-or-v1-DUMMYDUMMY',
                  SimpleNamespace(timeout=60.0), report, trajectories)

    by_case = {t['case']: t for t in trajectories}
    assert by_case['structured_data']['status'] == 'FAIL'
    assert by_case['structured_data']['reason'] == 'rate_limited', by_case['structured_data']
    assert any('429' in e for e in by_case['structured_data']['errors']), by_case['structured_data']['errors']
    # Остальные задачи той же модели не пострадали — отказ точечный, а не «всё сломалось».
    assert by_case['arithmetic']['status'] == 'PASS' and by_case['instruction_following']['status'] == 'PASS'
    # Итог сводится к FAIL, а не к PASS — иначе первый тест был бы пуст.
    assert not _freeze_accepts(trajectories, sha)
