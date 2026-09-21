"""Music preset regressions: real UI/API, controlled unavailable health response.

A preset edits a form; it must not wait for ACE-Step or claim music generation.
The installed Windows profile executes this module against its actual archive.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright

from .test_web_designer_first_click_ui import live, pytestmark  # noqa: F401
from .test_editors_user_acceptance import editor_server, login  # noqa: F401
from .test_ux2_thinking_pane import _launch

HEALTH_PATH = '**/api/music/health'


def _open_music(page, live):
    # This is an explicit transport fixture, NOT evidence of a live generator.
    page.route(HEALTH_PATH, lambda route: route.fulfill(status=200, json={
        'status': 'NOT_CONFIGURED', 'reason': 'Acceptance fixture: generator unavailable'}))
    login(page, live)
    response = page.request.get(live.url + '/api/music/presets')
    assert response.ok, response.text()
    presets = response.json()['items']
    assert len(presets) >= 2
    page.goto(live.url + '/#/music-studio')
    expect(page.get_by_role('button', name=presets[0]['label'], exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Сгенерировать трек', exact=True)).to_be_disabled()
    return presets


def _hold_health(page):
    page.unroute(HEALTH_PATH)
    held = []
    # Leave subsequent probes pending deliberately. Local form edits still
    # have to finish; fulfilling them immediately would hide the old defect.
    page.route(HEALTH_PATH, lambda route: held.append(route))
    return held


def _close(browser, held):
    for route in held:
        try:
            route.abort()
        except Error:
            pass  # The test page may already have closed; teardown only.
    browser.close()


def test_style_selection_does_not_wait_for_generator_health(live, tmp_path):
    with sync_playwright() as pw:
        browser = _launch(pw)
        held = []
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors, mutations = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            presets = _open_music(page, live)
            held = _hold_health(page)
            page.on('request', lambda req: mutations.append(req.url)
                    if '/api/music/' in req.url and req.method not in {'GET', 'HEAD'} else None)
            page.get_by_label('Длина, сек', exact=True).fill('77')
            page.get_by_label('Lyrics / instrumental', exact=True).fill('Keep these fixture lyrics')
            checked = []
            for preset in presets[1:]:
                button = page.get_by_role('button', name=preset['label'], exact=True)
                button.click()
                expect(button).to_have_attribute('aria-pressed', 'true', timeout=1000)
                expect(page.get_by_label('Описание', exact=True)).to_have_value(preset['prompt'], timeout=1000)
                expect(page.get_by_label('BPM', exact=True)).to_have_value(str(preset['bpm']), timeout=1000)
                expect(page.locator('#view button[aria-pressed="true"]')).to_have_count(1)
                expect(page.get_by_label('Длина, сек', exact=True)).to_have_value('77')
                expect(page.get_by_label('Lyrics / instrumental', exact=True)).to_have_value('Keep these fixture lyrics')
                expect(page.get_by_role('button', name='Сгенерировать трек', exact=True)).to_be_disabled()
                checked.append(preset['id'])
            assert not errors, errors
            assert not mutations, 'Selecting a style must not generate/save music: ' + repr(mutations)
            proof = Path(os.environ.get('BOSSMAN_EDITOR_EVIDENCE_DIR') or tmp_path)
            proof.mkdir(parents=True, exist_ok=True)
            (proof / 'music-presets-ui.json').write_text(json.dumps({
                'source_sha': os.environ.get('BCC_ACCEPTANCE_SOURCE_SHA'),
                'archive_sha256': os.environ.get('BOSSMAN_ARCHIVE_SHA256'),
                'harness_sha': os.environ.get('BOSSMAN_HARNESS_SHA'),
                'status': 'PASS', 'real_browser': True, 'health': 'EXPLICIT_UNAVAILABLE_TRANSPORT_FIXTURE',
                'live_music_generation': False, 'presets_checked': checked,
                'generator_probes_held': len(held), 'music_mutations': mutations, 'page_errors': errors,
            }, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            _close(browser, held)


def test_manual_prompt_and_bpm_clear_selected_style_immediately(live):
    with sync_playwright() as pw:
        browser = _launch(pw)
        held = []
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            presets = _open_music(page, live)
            held = _hold_health(page)
            button = page.get_by_role('button', name=presets[1]['label'], exact=True)
            button.click()
            expect(button).to_have_attribute('aria-pressed', 'true', timeout=1000)
            page.get_by_label('Описание', exact=True).fill('Custom fixture style')
            expect(page.locator('#view button[aria-pressed="true"]')).to_have_count(0, timeout=1000)
            expect(button).to_have_attribute('title', 'Выбрать стиль ' + presets[1]['label'])
            button.click()
            expect(button).to_have_attribute('aria-pressed', 'true', timeout=1000)
            page.get_by_label('BPM', exact=True).fill('123')
            expect(page.locator('#view button[aria-pressed="true"]')).to_have_count(0, timeout=1000)
            expect(page.get_by_role('button', name='Сгенерировать трек', exact=True)).to_be_disabled()
        finally:
            _close(browser, held)
