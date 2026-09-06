"""Real isolated Chromium, no account/network/model: component correctness only."""
import os
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from bossman.apprentice.models import AppIdentity, PlanStep, SemanticTarget
from bossman.computer_operator.models import ActionKind
from bossman.computer_operator.adapters.playwright_browser import (
    PlaywrightBrowserObserver, PlaywrightBrowserActuator, _wait_ms,
)


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as pw:
        path = os.getenv('BOSSMAN_TEST_CHROMIUM')
        browser = pw.chromium.launch(headless=True, **({'executable_path': path} if path else {}))
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    context.route('**/*', lambda route: route.abort())
    page = context.new_page()
    yield page
    context.close()


def step(kind, name='Go', **kw):
    return PlanStep('step-1', kind, AppIdentity(app='Chromium'),
                    target=SemanticTarget('button', name), **kw)


def test_real_wall_time_and_fresh_state(page):
    page.set_content('<title>Fixture</title><button>Go</button>')
    observer = PlaywrightBrowserObserver(page)
    before = time.time()
    obs = observer.observe()
    assert before <= obs.created_at <= time.time()
    assert observer.is_current(obs)
    page.get_by_role('button').evaluate('(el) => el.disabled = true')
    assert not observer.is_current(obs), 'same URL/title is not current widget state'


def test_changed_checkbox_invalidates(page):
    page.set_content('<label><input type=checkbox>Consent</label>')
    observer = PlaywrightBrowserObserver(page)
    obs = observer.observe()
    page.get_by_role('checkbox').check()
    assert not observer.is_current(obs)


def test_changed_input_and_mutated_observation_invalidates(page):
    page.set_content('<label for=p>Prompt</label><input id=p>')
    observer = PlaywrightBrowserObserver(page)
    obs = observer.observe()
    assert obs.ui_tree['elements'][0]['name'] == 'Prompt'
    assert observer.is_current(obs)
    obs.ui_tree['elements'][0]['value'] = 'forged'
    assert not observer.is_current(obs)
    obs = observer.observe()
    page.get_by_role('textbox', name='Prompt').fill('new')
    assert not observer.is_current(obs)


def test_old_generation_never_current(page):
    page.set_content('<button>Go</button>')
    observer = PlaywrightBrowserObserver(page)
    old = observer.observe()
    observer.observe()
    assert not observer.is_current(old)


def test_reads_are_bounded_not_per_element(page):
    page.set_content(''.join(f'<button>Button {i}</button>' for i in range(100)))
    observer = PlaywrightBrowserObserver(page)
    obs = observer.observe()
    assert len(obs.ui_tree['elements']) == 60
    assert observer.last_metrics['browser_read_calls'] == 12


def test_sensitive_autocomplete_is_not_copied(page):
    page.set_content('<input aria-label="Code" autocomplete="one-time-code" value="123456">')
    obs = PlaywrightBrowserObserver(page).observe()
    assert obs.sensitive
    assert '123456' not in str(obs.ui_tree)


def test_ambiguous_target_refused(page):
    page.set_content('<button>Go</button><button>Go</button>')
    with pytest.raises(RuntimeError, match='ambiguous'):
        PlaywrightBrowserActuator(page).act(step(ActionKind.CLICK), None)


def test_substring_does_not_select_unrelated_button(page):
    page.set_content('<button>Go and pay</button>')
    with pytest.raises(RuntimeError, match='missing'):
        PlaywrightBrowserActuator(page).act(step(ActionKind.CLICK), None)


def test_double_click_really_dispatches_dblclick(page):
    page.set_content('<button ondblclick="this.textContent=\'Double\'">Go</button>')
    PlaywrightBrowserActuator(page).act(step(ActionKind.DOUBLE_CLICK), None)
    assert page.get_by_role('button').inner_text() == 'Double'


def test_condition_wait_never_calls_fixed_sleep(page, monkeypatch):
    page.set_content('<button>Ready</button>')
    monkeypatch.setattr(page, 'wait_for_timeout', lambda *_: pytest.fail('fixed sleep'))
    target = step(ActionKind.WAIT, args={'until': {'role': 'button', 'name': 'Ready', 'state': 'enabled'}, 'timeout_ms': 1000})
    assert 'condition observed' in PlaywrightBrowserActuator(page).act(target, None)['detail']


def test_wait_delayed_control_and_verify(page):
    page.set_content('<button disabled id=b>Ready</button><script>setTimeout(()=>b.disabled=false, 20)</script>')
    target = step(ActionKind.WAIT, args={'until': {'role': 'button', 'name': 'Ready', 'state': 'enabled'}, 'timeout_ms': 1000})
    PlaywrightBrowserActuator(page).act(target, None)
    assert page.get_by_role('button').is_enabled()


def test_fake_screenshot_is_no_longer_reported_success(page):
    with pytest.raises(RuntimeError, match='not implemented'):
        PlaywrightBrowserActuator(page).act(step(ActionKind.TAKE_SCREENSHOT), None)


@pytest.mark.parametrize('value', [True, 0, -1, 30_001, float('nan'), '50'])
def test_invalid_duration_is_rejected(value):
    with pytest.raises(ValueError):
        _wait_ms(value)
