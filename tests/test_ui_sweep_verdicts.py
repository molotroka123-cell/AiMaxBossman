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


def test_explicit_registry_refresh_is_awaited_without_tracking_background_gets():
    assert sweep._tracked_request('GET', 'http://127.0.0.1:123/api/apps?refresh=true')
    assert not sweep._tracked_request('GET', 'http://127.0.0.1:123/api/apps')
    assert not sweep._tracked_request('GET', 'http://127.0.0.1:123/api/tasks')
    assert sweep._tracked_request('POST', 'http://127.0.0.1:123/api/tasks')


def _controls_of(html, after=None, with_closed=False):
    playwright = pytest.importorskip('playwright.sync_api')
    from bcc.browser_runtime import chromium_executable
    exe = chromium_executable(preinstalled='/opt/pw-browsers/chromium')
    if exe is None:
        pytest.skip('Chromium is not installed')
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=exe)
        try:
            page = browser.new_page()
            page.set_content(html)
            if after:
                page.evaluate(after)
            found = sweep._visible_controls(page)
            return [(c['label'], c.get('closed', False)) for c in found] if with_closed else [c['label'] for c in found]
        finally:
            browser.close()


_DISCLOSURE = ('<div id="view"><button>Always</button>'
               '<details id="list"><summary>All nodes</summary>'
               '<button title="leaf detail">Leaf</button></details></div>')


def test_buttons_inside_a_closed_details_are_reported_as_closed_not_dropped():
    # Sweep metric v2: a control in a closed <details> is listed with closed=True (the sweep records it
    # as skipped_closed_details) instead of being clicked (v1) or silently dropped (4398e6e7).
    assert _controls_of(_DISCLOSURE, with_closed=True) == [('Always', False), ('leaf detail', True)]
    assert sweep.SWEEP_METRIC_VERSION == 2


def test_buttons_inside_an_opened_details_are_still_swept():
    assert _controls_of(_DISCLOSURE, "document.getElementById('list').open = true", with_closed=True) == [
        ('Always', False), ('Leaf', False)]
