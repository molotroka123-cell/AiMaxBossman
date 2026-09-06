"""Real editor UI against BCC and files, not owner-PC/local-model acceptance.

No optional browser skip: install Chromium for this dedicated acceptance gate.
Only fixture preparation and native queue pumping use direct component calls.
"""
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect
from .test_ux2_thinking_pane import live, _login

pytestmark = pytest.mark.timeout(180)


@pytest.fixture
def page(live):
    with sync_playwright() as pw:
        path = os.getenv('BOSSMAN_TEST_CHROMIUM')
        browser = pw.chromium.launch(**({'executable_path': path} if path else {}))
        context = browser.new_context(viewport={'width': 1600, 'height': 1100})
        page = context.new_page()
        _login(page, live)
        yield page
        context.close()
        browser.close()


def create_web(page, name):
    page.get_by_placeholder('Название проекта, например «Кофейня Север»').fill(name)
    page.get_by_role('button', name='Открыть проект', exact=True).click()
    expect(page.locator('textarea.bd-code')).to_be_visible(timeout=15000)


def test_web_create_save_reload_and_create_second_project(page, live):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live.url + '/#/web_designer')
    create_web(page, 'Web baseline A')
    html = '<!doctype html><html><body><h1>Saved owner text</h1><p>Keep sibling</p></body></html>'
    editor = page.locator('textarea.bd-code')
    editor.fill(html)
    with page.expect_response(lambda r: '/web-designer/projects/' in r.url and r.url.endswith('/code') and r.request.method == 'PUT') as saved:
        editor.press('Control+s')
    assert saved.value.ok
    page.reload()
    expect(page.locator('textarea.bd-code')).to_have_value(html, timeout=15000)
    expect(page.frame_locator('iframe.bd-frame').locator('h1')).to_have_text('Saved owner text')
    assert page.locator('iframe.bd-frame').get_attribute('sandbox') == 'allow-scripts'
    page.get_by_role('button', name='+ Проект', exact=True).click()
    create_web(page, 'Web baseline B')
    selector = page.locator('select').filter(has=page.locator('option', has_text='Web baseline A')).first
    first_id = selector.locator('option', has_text='Web baseline A').get_attribute('value')
    selector.select_option(first_id)
    expect(page.locator('textarea.bd-code')).to_have_value(html, timeout=15000)
    assert errors == []


def test_web_failed_save_keeps_draft_and_refuses_project_switch(page, live):
    page.goto(live.url + '/#/web_designer')
    create_web(page, 'Draft A')
    first_url = page.url
    page.get_by_role('button', name='+ Проект', exact=True).click()
    create_web(page, 'Draft B')
    second_url = page.url
    page.route('**/api/web-designer/projects/*/code', lambda route:
        route.fulfill(status=409, json={'detail': 'fixture concurrent update'})
        if route.request.method == 'PUT' else route.continue_())
    draft = '<h1>Unsaved draft must survive</h1>'
    page.locator('textarea.bd-code').fill(draft)
    selector = page.locator('select').filter(has=page.locator('option', has_text='Draft A')).first
    first_id = selector.locator('option', has_text='Draft A').get_attribute('value')
    with page.expect_response(lambda r: r.status == 409):
        selector.select_option(first_id)
    expect(page.locator('textarea.bd-code')).to_have_value(draft)
    assert page.url == second_url and page.url != first_url


async def run_native_job(svc, task_id):
    import sqlalchemy as sa
    from bcc.db import task_runs
    async with svc.db.session() as session:
        run = (await session.execute(sa.select(task_runs).where(task_runs.c.task_id == task_id)
                                     .order_by(task_runs.c.id.desc()))).mappings().first()
    assert run
    await svc.engine.execute(run['id'])


def test_video_create_import_edit_undo_export_and_reopen(page, live, tmp_path):
    ffmpeg = shutil.which('ffmpeg')
    assert ffmpeg and shutil.which('ffprobe'), 'Real media tools required, not optional PASS'
    source = tmp_path / 'editor-source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=25:d=0.4',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(source)], check=True, timeout=30)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live.url + '/#/video-studio')
    page.locator('.vs-empty button').click()
    page.get_by_role('textbox', name='Название', exact=True).fill('Editor baseline')
    page.locator('dialog form button[type=submit]').click()
    expect(page.locator('.vs-library')).to_be_visible(timeout=15000)
    page.locator('.vs-library input[type=file]').set_input_files(str(source))
    card = page.locator('.vs-media-card').first
    expect(card).to_be_visible(timeout=15000)
    card.dblclick()
    clip = page.locator('.vs-clip').first
    expect(clip).to_be_visible(timeout=15000)
    clip.click()
    page.locator('.vs-studio').press('Delete')
    expect(page.locator('.vs-clip')).to_have_count(0)
    page.locator('.vs-studio').press('Control+z')
    expect(page.locator('.vs-clip')).to_have_count(1)
    page.locator('.vs-header').get_by_role('button', name='Экспорт', exact=True).click()
    page.get_by_role('spinbutton', name='Ширина', exact=True).fill('160')
    page.get_by_role('spinbutton', name='Высота', exact=True).fill('90')
    with page.expect_response(lambda r: r.url.endswith('/api/video-studio/exports') and r.request.method == 'POST') as exported:
        page.locator('dialog form button[type=submit]').click()
    assert exported.value.ok
    job = exported.value.json()
    # LiveServer deliberately disables autonomous workers. Execute the real
    # native queue item (no mocked renderer/result and no model/API spending).
    asyncio.run_coroutine_threadsafe(run_native_job(live.svc, job['task_id']), live.loop).result(timeout=90)
    status = page.request.get(live.url + '/api/video-studio/exports/' + job['job_id'])
    assert status.ok
    result = status.json()
    assert result['status'] == 'completed' and result['verification']['passed'], result
    expect(page.locator('.vs-jobs')).to_contain_text('completed', timeout=15000)
    download = page.request.get(live.url + result['output_url'])
    assert download.ok
    output = tmp_path / 'verified-export.mp4'
    output.write_bytes(download.body())
    subprocess.run([ffmpeg, '-v', 'error', '-i', str(output), '-f', 'null', '-'], check=True, timeout=30)
    page.reload()
    expect(page.locator('.vs-clip')).to_have_count(1, timeout=15000)
    assert errors == []
