"""Downloaded diagnostics from an isolated task; no owner profile or real model."""
from __future__ import annotations

import json

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_chat_ux_browser import (chat, fake_llm, _open, _pick, _send, _wait_finished)  # noqa: F401

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


@pytest.mark.parametrize('broken_events', [False, True])
def test_download_technical_log_is_redacted_and_reports_partial_results(chat, tmp_path, broken_events):
    from playwright.sync_api import sync_playwright

    srv, _ = chat
    errors = []
    marker = 'PRIVATE-PROMPT-CANARY-technical-export'
    with sync_playwright() as pw:
        browser, page = _open(pw, srv, errors)
        try:
            _pick(page, 'Заглушка')
            _send(page, marker)
            _wait_finished(page)
            if broken_events:
                page.route('**/api/tasks/*/events?**', lambda route: route.fulfill(
                    status=200, content_type='application/json', body='{"events":null}'))
            page.click('#chat-more')
            with page.expect_download() as received:
                page.get_by_role('menuitem', name='Скачать технические логи выполнения').click()
            destination = tmp_path / 'diagnostic-download.json'
            received.value.save_as(destination)
            content = destination.read_text(encoding='utf-8')
            data = json.loads(content)
            assert data['schema'] == 'bossman.technical-log.v1'
            assert data['coverage']['exported_task_count'] == 1
            assert data['coverage']['partial'] is broken_events
            assert data['coverage']['run_event_count'] > 0
            assert data['turns'][0]['task_id'] > 0
            assert data['turns'][0]['run_ids']
            turn = data['turns'][0]
            assert all(event['run_id'] in turn['run_ids'] for event in turn['run_events'])
            assert all(event['ts'].endswith('Z') for event in turn['run_events'])
            assert data['redaction']['omitted_field_count'] > 0
            assert marker not in content
            assert 'Привет из заглушки' not in content
            if broken_events:
                assert any(f['code'] == 'INVALID_RESPONSE' for f in data['coverage']['failures'])
            else:
                assert data['coverage']['task_event_count'] > 0
                assert data['coverage']['failures'] == []
                assert all(event['ts'].endswith('Z') for event in turn['task_events'])
                assert all(event.get('task_id') in (None, turn['task_id']) for event in turn['task_events'])
                assert all(event.get('run_id') in (None, *turn['run_ids']) for event in turn['task_events'])
        finally:
            browser.close()
    assert errors == [], errors
