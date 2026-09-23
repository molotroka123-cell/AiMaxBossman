"""`bossman review` / `bossman rate`: a run id is an id, never a piece of a URL path (review P2 b)."""
import pytest
from bcc.terminal_cli import cli
from bcc.terminal_cli.records import EXIT_USAGE


@pytest.mark.parametrize('argv', [
    ['review', '../x'], ['review', '..%2Fx', '--run'], ['review', 'a/b'], ['review', 'x?y=1'],
    ['rate', '../../api/tasks', 'bad', 'причина'], ['rate', 'a b', 'good'], ['rate', 'x' * 41, 'good'],
])
def test_a_run_id_that_is_not_an_id_is_refused_before_any_request(argv, monkeypatch):
    called = []
    monkeypatch.setattr(cli, 'connect', lambda args: called.append(args) or pytest.fail('must not connect'))
    assert cli.main(argv) == EXIT_USAGE and called == []


def test_a_real_run_id_reaches_the_backend(monkeypatch):
    seen = []

    class Client:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, path, **kw):
            seen.append(('GET', path))
            return {'review': {'verdict': 'GOOD', 'score': 8, 'summary': 'ok', 'defects': []}, 'feedback': None}
        def post(self, path, body=None, **kw):
            seen.append(('POST', path, body))
            return {'feedback': {'verdict': 'bad', 'reason': 'хвост', 'vision_verdict': 'GOOD', 'agreed_with_vision': False}}
    monkeypatch.setattr(cli, 'connect', lambda args: Client())
    assert cli.main(['review', '9ff118ea65cb425482974066b198ea6d']) == 0
    assert cli.main(['rate', 'run_abc-1', 'bad', 'хвост']) == 0
    assert seen == [('GET', '/api/studio/runs/9ff118ea65cb425482974066b198ea6d/review'),
                    ('POST', '/api/studio/runs/run_abc-1/feedback', {'verdict': 'bad', 'reason': 'хвост'})]
