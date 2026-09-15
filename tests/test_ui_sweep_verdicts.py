"""Prevent successful-looking UI sweep evidence without an observed outcome."""
import importlib.util
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location('ui_sweep_verdicts',
    Path(__file__).resolve().parents[1] / 'scripts' / 'ui_acceptance_sweep.py')
sweep = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sweep
spec.loader.exec_module(sweep)


def classify(**overrides):
    state = dict(failures=[], console=[], page_errors=[], requests=[], responses=[],
                 new_dialogs=set(), new_toasts=set(), validation=None,
                 dom_changed=False, url_changed=False)
    state.update(overrides)
    return sweep._classify(**state)[0]


def test_no_new_visible_dialog_or_dom_change_is_not_feature_opening():
    assert classify() == 'dead'
    assert classify(new_dialogs={'Create project'}) == 'opens_feature'


def test_request_needs_successful_response_and_does_not_claim_effect():
    assert classify(requests=['POST /api/tasks']) == 'error'
    assert classify(requests=['POST /api/tasks'], responses=['200 POST /api/tasks']) == 'request_accepted'
    assert classify(requests=['POST /api/tasks'], failures=['500 /api/tasks']) == 'error'
    assert classify(responses=['200 POST /first'], incomplete_requests=['POST /second']) == 'error'


@pytest.mark.parametrize('override', [dict(new_toasts=set()), dict(requests=['POST /api/tasks']),
    dict(page_errors=['TypeError']), dict(console=['Error: unrelated']), dict(validation=None)])
def test_expected_refusal_requires_all_independent_observations(override):
    evidence = dict(validation=('Команда пустая', 'Error: Команда пустая'),
        console=['Error: Команда пустая\n at onSubmit'], new_toasts={'Команда пустая'})
    assert classify(**evidence) == 'input_refused'
    evidence.update(override)
    assert classify(**evidence) == 'error'


def test_native_dialog_is_observed_cancellation_not_completed_mutation():
    assert classify(native_dialogs=['prompt: Collection name']) == 'dialog_opened'
    assert classify(native_dialogs=['prompt: Collection name'], page_errors=['TypeError']) == 'error'


def test_refresh_and_explicit_initial_state_do_not_claim_changes():
    assert classify(read_responses=['200 GET /api/status']) == 'refresh_observed'
    assert classify(unchanged_state={'selected': 'aria-selected=true'}) == 'already_selected'
    assert classify(unchanged_state={'empty_attachments': '0 files'}) == 'already_empty'
    assert classify(unchanged_state={}) == 'dead'
    assert classify(unchanged_state={'selected': 'aria-selected=true'}, console=['failure']) == 'error'


@pytest.mark.parametrize('payload, expected', [
    ({'ok': True, 'ready': True}, None),
    ({'ok': False, 'ready': False, 'reason': 'exited'}, 'exited'),
    ({'ok': False, 'ready': False, 'reason': 'not_ready'}, 'not_ready'),
    ({'ok': True, 'ready': False}, 'readiness_not_confirmed'),
    ({'ok': False, 'reason': 'private child log'}, 'readiness_not_confirmed'),
    (None, 'readiness_not_confirmed'),
])
def test_app_start_2xx_requires_actual_readiness_without_leaking_logs(payload, expected):
    assert sweep._app_start_problem(payload) == expected
