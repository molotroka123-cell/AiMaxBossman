"""Real user paths against a disposable server; no API mocks or model claims.

Required gate: unavailable Chromium is a failure, never a silent skip. The
browser makes all changes. API reads and FFmpeg independently verify effects.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from .browser_support import click_in_preview
from .test_ux2_thinking_pane import _launch

ROOT = Path(__file__).resolve().parents[2]
SERVE = '''import os,sys,pathlib
from bcc.api import create_app
from bcc.config import Settings
from bossman_shared import fable_budget
root,data,port=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]),int(sys.argv[3])
fable_budget.LEDGER_PATH=data/'test-budget.json'
app=create_app(Settings(data_dir=data,ui_dir=root/'command-center'/'ui'),announce_token=False,start_workers=True)
fd=os.open(data/'test-login-token',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:f.write(app.state.svc.auth.token)
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning',timeout_graceful_shutdown=2)
'''


class EditorServer:
    def __init__(self, folder: Path):
        self.data = folder
        folder.mkdir(parents=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}'
        self.process = None

    def start(self):
        env = dict(os.environ, BCC_DATA_DIR=str(self.data), PYTHONPATH=os.pathsep.join(
            map(str, [ROOT, ROOT / 'bossman-core', ROOT / 'command-center'])))
        env['BOSSMAN_EVIDENCE_KEY_FILE'] = str(self.data / 'test-evidence.key')
        env['BOSSMAN_REAL_WORKLOAD_ROOT'] = str(self.data / 'test-telemetry')
        self.process = subprocess.Popen([sys.executable, '-c', SERVE, str(ROOT),
                                         str(self.data), str(self.port)],
                                        env=env, cwd=ROOT, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        until = time.monotonic() + 25
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < until:
                assert self.process.poll() is None, 'Disposable BCC server exited'
                try:
                    if client.get(self.url + '/').status_code == 200:
                        return self
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
        raise AssertionError('Disposable BCC server did not become ready')

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def restart(self):
        before = self.process.pid
        self.stop()
        self.start()
        assert self.process.pid != before


@pytest.fixture
def editor_server(tmp_path):
    server = EditorServer(tmp_path / 'server')
    try:
        yield server.start()
    finally:
        server.stop()


def evidence_dir(tmp_path):
    path = Path(os.environ.get('BOSSMAN_EDITOR_EVIDENCE_DIR') or tmp_path / 'evidence')
    path.mkdir(parents=True, exist_ok=True)
    return path


def login(page, server):
    page.goto(server.url + '/', wait_until='domcontentloaded')
    page.locator('#login-token').fill((server.data / 'test-login-token').read_text())
    page.locator('#login-submit').click()
    page.locator('#shell:not([hidden])').wait_for()


def snapshot(context, url):
    response = context.request.get(url)
    assert response.status == 200, response.text()
    return response.json()


def play_preview_to_end(page):
    """Only real transport buttons change playback; DOM reads verify it."""
    page.wait_for_function("""() => {
        const v = document.querySelector('.vs-preview video');
        return v && v.readyState >= 1 && v.videoWidth > 0 && !v.error;
    }""", timeout=15000)
    page.locator('.vs-transport').get_by_role('button', name='│◀', exact=True).click()
    page.locator('.vs-transport').get_by_role('button', name='▶', exact=True).click()
    page.wait_for_function("""() => {
        const v = document.querySelector('.vs-preview video');
        return v && v.currentTime > .05 && !v.error;
    }""", timeout=15000)
    page.wait_for_function("""() => {
        const v = document.querySelector('.vs-preview video');
        return v && v.ended && v.currentTime >= .9 && !v.error;
    }""", timeout=15000)
    return page.locator('.vs-preview video').evaluate("""v => ({
        current_time: v.currentTime, duration: v.duration, ended: v.ended,
        ready_state: v.readyState, width: v.videoWidth, height: v.videoHeight
    })""")


def change(page, suffix, action):
    with page.expect_response(lambda r: r.url.split('?')[0].endswith(suffix)
                              and r.request.method in ('POST', 'PUT'), timeout=30000) as seen:
        action()
    response = seen.value
    assert response.status == 200, response.text()
    return response.json()


@pytest.mark.timeout(180)
def test_video_ui_import_trim_undo_preview_export_restart(editor_server, tmp_path):
    server = editor_server
    output = evidence_dir(tmp_path)
    fixture = tmp_path / 'red-audio.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                    'color=red:size=320x180:rate=30:duration=2', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000:duration=2', '-c:v', 'libx264',
                    '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(fixture)],
                   check=True, timeout=20)
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = None
        try:
            context = browser.new_context(viewport={'width': 1720, 'height': 1100}, accept_downloads=True)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            login(page, server)
            page.goto(server.url + '/#/video-studio')
            page.get_by_role('button', name='＋ Новый проект', exact=True).click()
            page.get_by_label('Название', exact=True).fill('Video user acceptance')
            page.get_by_role('button', name='Применить', exact=True).click()
            page.locator('.vs-library input[type=file]').set_input_files(fixture)
            page.locator('.vs-media-card').first.wait_for(timeout=30000)
            page.locator('.vs-media-card').first.dblclick()
            page.locator('.vs-clip').first.click()
            pid = re.search(r'project_id=([^&]+)', page.url).group(1)
            project_url = server.url + '/api/video-studio/projects/' + pid
            field = page.get_by_label(re.compile(r'^(Source out, s|Конец исходника, с)$'))
            change(page, '/commands', lambda: (field.fill('1'), field.press('Tab')))
            def clip():
                return snapshot(context, project_url)['sequences'][0]['tracks'][0]['clips'][0]
            assert clip()['source_out'] == 1_000_000
            change(page, '/commands', lambda: page.locator('.vs-studio').press('Control+z'))
            assert clip()['source_out'] == 2_000_000
            change(page, '/commands', lambda: page.locator('.vs-studio').press('Control+Shift+z'))
            assert clip()['source_out'] == 1_000_000
            page.locator('.vs-preview-actions').get_by_role('button', name='Создать preview', exact=True).click()
            page.locator('.vs-preview video').wait_for(timeout=60000)
            expect(page.locator('.vs-job a[download]')).to_have_count(1, timeout=60000)
            playback = [play_preview_to_end(page)]
            page.get_by_role('button', name='Экспорт', exact=True).first.click()
            queued = change(page, '/exports', lambda: page.locator('dialog').get_by_role(
                'button', name='Применить', exact=True).click())
            expect(page.locator('.vs-job a[download]')).to_have_count(2, timeout=60000)
            job = snapshot(context, server.url + '/api/video-studio/exports/' + queued['job_id'])
            assert job['status'] == 'completed' and job['verification']['decoded']
            with page.expect_download() as downloaded:
                page.locator('.vs-job a[download][href=' + json.dumps(job['output_url']) + ']').click()
            video = output / 'verified-ui-export.mp4'
            downloaded.value.save_as(video)
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format',
                '-show_streams', '-of', 'json', str(video)], text=True, timeout=20))
            assert abs(float(probe['format']['duration']) - 1) <= .08
            assert {'audio', 'video'} <= {stream['codec_type'] for stream in probe['streams']}
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', str(video),
                            '-f', 'null', '-'], check=True, timeout=30)
            before = snapshot(context, project_url)
            server.restart()
            page.reload()
            page.locator('.vs-clip').first.wait_for(timeout=15000)
            expect(page.locator('.vs-job a[download]')).to_have_count(2)
            assert snapshot(context, project_url) == before
            playback.append(play_preview_to_end(page))
            assert errors == [], errors
            page.screenshot(path=str(output / 'video-user-path.png'), full_page=True)
            (output / 'video-result.json').write_text(json.dumps({'status': 'PASS',
                'kind': 'REAL_BROWSER_TESTER_NOT_LOCAL_MODEL', 'restart': 'FRESH_PROCESS',
                'job': job, 'ffprobe': probe, 'playback': playback, 'page_errors': errors}, ensure_ascii=False, indent=2))
        except Exception:
            if page is not None:
                page.screenshot(path=str(output / 'video-failure.png'), full_page=True)
            raise
        finally:
            browser.close()


@pytest.mark.timeout(120)
def test_web_ui_edit_download_restart_and_create_second_project(editor_server, tmp_path):
    server = editor_server
    output = evidence_dir(tmp_path)
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = None
        try:
            context = browser.new_context(viewport={'width': 1600, 'height': 1100}, accept_downloads=True)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            login(page, server)
            page.goto(server.url + '/#/web_designer')
            page.get_by_placeholder('Название проекта, например «Кофейня Север»').fill('Web user acceptance')
            page.get_by_role('button', name='Открыть проект', exact=True).click()
            page.locator('iframe.bd-frame').wait_for()
            pid = re.search(r'/projects/(\d+)/preview', page.locator('iframe.bd-frame').get_attribute('src')).group(1)
            project_url = server.url + '/api/web-designer/projects/' + pid
            html = '<!DOCTYPE html><html><head><title>QA</title><script type="application/ld+json">{"name":"preserve"}</script></head><body><!--retain--><h1>Before</h1><p><strong>keep</strong></p></body></html>'
            change(page, '/code', lambda: page.locator('textarea.bd-code').fill(html))
            frame = page.frame_locator('iframe.bd-frame')
            expect(frame.locator('h1')).to_have_text('Before')
            click_in_preview(page, 'h1')
            row = page.locator('.bd-row').filter(has=page.locator('label').filter(has_text=re.compile(r'^Текст$')))
            row.locator('input[type=text]').fill('Verified Web heading')
            change(page, '/edit', lambda: row.get_by_role('button', name='Применить', exact=True).click())
            expect(frame.locator('h1')).to_have_text('Verified Web heading')
            click_in_preview(page, 'h1')
            font = page.locator('.bd-row').filter(has=page.locator('label').filter(has_text=re.compile(r'^Кегль$'))).locator('input[type=number]').first
            change(page, '/edit', lambda: (font.fill('28'), font.press('Tab')))
            page.get_by_label('Размер экрана превью', exact=True).select_option('mobile')
            page.wait_for_function("() => document.querySelector('iframe.bd-frame').style.width === '390px'")
            saved = snapshot(context, project_url)
            for marker in ['<!DOCTYPE html>', '<!--retain-->', '<strong>keep</strong>', 'application/ld+json', 'Verified Web heading', 'font-size: 28px']:
                assert marker in saved['code'], marker
            with page.expect_download() as downloaded:
                page.get_by_role('button', name='Скачать HTML', exact=True).click()
            html_file = output / 'verified-ui-site.html'
            downloaded.value.save_as(html_file)
            assert html_file.read_text() == saved['code']
            server.restart()
            page.reload()
            expect(page.frame_locator('iframe.bd-frame').locator('h1')).to_have_text('Verified Web heading')
            assert snapshot(context, project_url) == saved
            assert page.get_attribute('iframe.bd-frame', 'sandbox') == 'allow-scripts'
            page.screenshot(path=str(output / 'web-user-path.png'), full_page=True)
            # Regression: + Project must not silently select the first old project.
            page.get_by_role('button', name='+ Проект', exact=True).click()
            name = page.get_by_placeholder('Название проекта, например «Кофейня Север»')
            expect(name).to_be_visible(timeout=8000)
            name.fill('Second independent website')
            page.get_by_role('button', name='Открыть проект', exact=True).click()
            page.locator('iframe.bd-frame').wait_for()
            second = re.search(r'/projects/(\d+)/preview', page.locator('iframe.bd-frame').get_attribute('src')).group(1)
            assert second != pid and snapshot(context, project_url) == saved
            assert errors == [], errors
            (output / 'web-result.json').write_text(json.dumps({'status': 'PASS',
                'kind': 'REAL_BROWSER_TESTER_NOT_LOCAL_MODEL', 'restart': 'FRESH_PROCESS',
                'first_project': pid, 'second_project': second, 'page_errors': errors}, indent=2))
        except Exception:
            if page is not None:
                page.screenshot(path=str(output / 'web-failure.png'), full_page=True)
            raise
        finally:
            browser.close()
