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


SERVICE_START_PATH = '**/api/music/service/start'
GENERATE_PATH = '**/api/music/generate'
NOT_INSTALLED = {
    'status': 'NOT_INSTALLED', 'provider': 'ACE-Step 1.5', 'installed': False, 'can_start': False,
    'action': 'install', 'owned': False, 'running': False,
    'reason': 'ACE-Step 1.5 не установлен на этом ПК (не найден каталог установки); по адресу http://127.0.0.1:8001 никто не отвечает.',
    'remedy': 'Установите ACE-Step один раз: команда ниже или раздел «Установка и запуск на ПК владельца» в docs/music/MUSIC_STUDIO.md.',
    'install_command': r'python tools\music_studio_install.py install',
}
NOT_RUNNING = {
    'status': 'NOT_RUNNING', 'provider': 'ACE-Step 1.5', 'installed': True, 'can_start': True,
    'action': 'start', 'owned': False, 'running': False,
    'reason': 'ACE-Step установлен, но не запущен: по адресу http://127.0.0.1:8001 никто не отвечает.',
    'remedy': 'Нажмите «Запустить ACE-Step» на этой странице.',
}
STARTING = {**NOT_RUNNING, 'status': 'STARTING', 'can_start': False, 'action': 'wait', 'owned': True,
            'running': True, 'pid': 4242, 'reason': 'ACE-Step запускается (pid 4242, 12 с): сервис ещё не открыл порт 8001.',
            'log_tail': ['Loading checkpoint shards: 50%']}
NOT_READY = {**STARTING, 'status': 'NOT_READY',
             'reason': 'ACE-Step запущен, но модели ещё загружаются — генерация пока невозможна.'}
READY = {'status': 'READY', 'provider': 'ACE-Step 1.5', 'installed': True, 'can_start': False, 'action': None,
         'owned': True, 'running': True, 'pid': 4242, 'reason': 'ACE-Step 1.5 запущен и готов к генерации.',
         'remedy': '', 'loaded_model': 'acestep-v15-turbo', 'loaded_lm_model': 'acestep-5Hz-lm-1.7B'}


def _open_with_health(page, live, payloads):
    """Each /api/music/health request answers the next payload (the last one repeats);
    a callable gets the request number and decides."""
    calls = []

    def answer(route):
        calls.append(1)
        payload = payloads(len(calls)) if callable(payloads) else payloads[min(len(calls), len(payloads)) - 1]
        route.fulfill(status=200, json=payload)

    page.route(HEALTH_PATH, answer)
    login(page, live)
    page.goto(live.url + '/#/music-studio')
    expect(page.get_by_role('button', name='Phonk', exact=True)).to_be_visible()
    return calls


def test_not_installed_says_so_in_russian_and_gives_the_install_step(live):
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            _open_with_health(page, live, [NOT_INSTALLED])
            view = page.locator('#view')
            expect(view).to_contain_text('не установлен')
            expect(view).to_contain_text('Не установлен')                     # the badge
            expect(view).to_contain_text(r'python tools\music_studio_install.py install')
            expect(view).not_to_contain_text('ConnectError')
            expect(view).not_to_contain_text('All connection attempts failed')
            expect(page.get_by_role('button', name='Запустить ACE-Step', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='Сгенерировать трек', exact=True)).to_be_disabled()
        finally:
            browser.close()


def test_start_button_starts_the_service_once_and_never_generates(live):
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            posts, generates = [], []
            page.route(SERVICE_START_PATH, lambda route: (posts.append(route.request.method), route.fulfill(
                status=200, json={'ok': True, 'started': True, 'pid': 4242, 'status': 'STARTING',
                                  'message': 'ACE-Step запускается (pid 4242).'}))[1])
            page.route(GENERATE_PATH, lambda route: (generates.append(1), route.abort())[1])
            _open_with_health(page, live, lambda _n: STARTING if posts else NOT_RUNNING)
            expect(page.locator('#view')).to_contain_text('не запущен')
            start = page.get_by_role('button', name='Запустить ACE-Step', exact=True)
            expect(start).to_be_enabled()
            expect(page.get_by_role('button', name='Сгенерировать трек', exact=True)).to_be_disabled()
            start.dblclick()                               # a double click must still be ONE start
            expect(page.locator('#view')).to_contain_text('запускается', timeout=8000)
            assert posts == ['POST'], posts
            assert not generates, 'starting the service must not queue a track'
        finally:
            browser.close()


def test_page_follows_the_service_from_starting_to_ready_by_itself(live):
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            calls = _open_with_health(page, live, [STARTING, NOT_READY, READY])
            generate = page.get_by_role('button', name='Сгенерировать трек', exact=True)
            expect(page.locator('#view')).to_contain_text('запускается')
            expect(generate).to_be_disabled()
            # No click: the page re-checks on its own while the service is starting.
            expect(page.locator('#view')).to_contain_text('модели ещё загружаются', timeout=12000)
            expect(generate).to_be_disabled()
            expect(page.locator('#view')).to_contain_text('Готов', timeout=12000)
            expect(generate).to_be_enabled()
            expect(page.locator('#view')).to_contain_text('acestep-5Hz-lm-1.7B')
            assert len(calls) >= 3
        finally:
            browser.close()


def test_ready_generate_queues_exactly_one_task_on_double_click(live):
    """UX-010 (audit 2026-09-25): a double click used to queue two provider tasks."""
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            generates = []
            page.route(GENERATE_PATH, lambda route: (generates.append(1), route.fulfill(
                status=200, json={'task_id': 'task-fixture-1', 'status': 'queued', 'provider': 'ACE-Step 1.5'}))[1])
            _open_with_health(page, live, [READY])
            generate = page.get_by_role('button', name='Сгенерировать трек', exact=True)
            expect(generate).to_be_enabled()
            generate.dblclick()
            expect(page.locator('#view')).to_contain_text('Task: task-fixture-1', timeout=8000)
            assert generates == [1], generates
        finally:
            browser.close()


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
