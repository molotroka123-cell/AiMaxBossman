"""Offline safeguards only. These tests do not prove live model acceptance."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('live_openrouter_owner', Path(__file__).parents[1] / 'tools/live_openrouter_owner.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def model(name='vendor/model:free', **prices):
    return {'id': name, 'pricing': {'prompt': '0', 'completion': '0', **prices}}


@pytest.mark.parametrize('price', ['0.001', '-1', 'NaN', 'Infinity', '', None, True])
def test_rejects_nonzero_unknown_and_nonfinite_charges(price):
    assert live.free_models([model(request=price)]) == []


def test_requires_explicit_free_identity_and_both_prices():
    assert live.free_models([model('vendor/paid'), {'id': 'vendor/model:free', 'pricing': {'prompt': '0'}}]) == []


def test_deduplicates_and_bounds_selected_models():
    selected = live.free_models([model('c/c:free'), model('a/a:free'), model('b/b:free'), model('a/a:free')])
    assert [row['id'] for row in selected] == ['a/a:free', 'b/b:free']


def test_additional_charge_cannot_hide_behind_zero_token_prices():
    assert live.free_models([model(image='0.01')]) == []
    assert live.free_models([model(request='0', internal_reasoning='0')])


def test_malformed_catalog_entries_cannot_disable_valid_selection():
    assert live.free_models([None, [], {**model(), 'architecture': None}, model()]) == [model()]


def test_output_contract_rejects_plausible_but_wrong_answers():
    assert live.answer_ok('arithmetic', '391')
    assert not live.answer_ok('arithmetic', 'The answer is 391')
    assert live.answer_ok('structured_data', '{"city":"Prague","count":3}')
    assert not live.answer_ok('structured_data', '{"city":"Prague","count":3.0}')
    assert live.answer_ok('instruction_following', 'Прага\nБрно\nОстрава')
    assert not live.answer_ok('instruction_following', '1. Прага\n2. Брно\n3. Острава')


def test_recursive_redaction_of_exact_and_key_shaped_secrets():
    result = live.redact({'answer': ['private-value', 'sk-or-v1-test_credential']}, 'private-value')
    assert result == {'answer': ['[REDACTED]', '[REDACTED]']}


def test_missing_secret_never_imports_app_or_claims_pass(tmp_path, monkeypatch):
    monkeypatch.delenv('BOSSMAN_OPENROUTER_API_KEY', raising=False)
    monkeypatch.setenv('OPENROUTER_API_KEY', 'must-not-bootstrap')
    output, trajectories = tmp_path / 'report.json', tmp_path / 'examples.jsonl'
    assert live.main(['--output', str(output), '--trajectories', str(trajectories), '--expected-sha', 'a' * 40]) == 2
    report = json.loads(output.read_text())
    assert report['status'] == 'OWNER_REQUIRED'
    assert report['tasks'] == []
    assert report['training_weights_updated'] is False
    assert trajectories.read_text() == ''
    assert 'must-not-bootstrap' not in output.read_text()


@pytest.mark.parametrize('timeout', ['0', '181', 'nan', 'inf'])
def test_timeout_cannot_remove_attempt_bound(tmp_path, timeout):
    with pytest.raises(SystemExit):
        live.main(['--output', str(tmp_path / 'out.json'), '--expected-sha', 'a' * 40, '--timeout', timeout])


@pytest.mark.parametrize('task_status,error,answer,expected', [
    ('failed', 'ProviderError: HTTP 429 Rate limit exceeded: free-models-per-day', '', 'rate_limited'),
    ('failed', 'ProviderError: HTTP 404 No endpoints found for stub/alpha:free', '', 'model_unavailable'),
    ('failed', 'ProviderError: HTTP 401 Unauthorized: invalid API key', '', 'key_rejected'),
    ('failed', 'ProviderError: chat/completions: сервер вернул невалидный JSON', '', 'provider_error'),
    ('failed', None, '', 'failed_without_error_text'),
    ('running', None, '', 'timeout'),
    ('completed', None, '392', 'wrong_answer'),
])
def test_failure_reason_keeps_distinct_failures_distinct(task_status, error, answer, expected):
    """§3: отсутствующий ключ, 429, недоступная модель и неверный ответ — разные результаты."""
    runs = [{'error': error, 'status': task_status}]
    assert live.failure_reason(passed=False, task_status=task_status, runs=runs,
                               answer=answer, case='arithmetic') == expected


def test_failure_reason_never_says_ok_for_a_failed_task():
    """Негативный контроль: «ok» только когда прогон действительно принят."""
    assert live.failure_reason(passed=True, task_status='completed', runs=[], answer='391', case='arithmetic') == 'ok'
    # Верный ответ, но контракт нарушен (например, ненулевая цена): не ok и не wrong_answer.
    assert live.failure_reason(passed=False, task_status='completed', runs=[{'cost_usd': 0.01}],
                               answer='391', case='arithmetic') == 'contract_violation'
    # Никакой текст ошибки не должен превращать провал в успех.
    assert live.failure_reason(passed=False, task_status='failed', runs=[{'error': 'ok'}],
                               answer='391', case='arithmetic') != 'ok'
