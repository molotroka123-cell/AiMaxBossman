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
from playwright.sync_api import TimeoutError as PWTimeout

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


BROWSER_CODEC_PROBE = """() => {
    const v = document.createElement('video');
    const specs = {
        'video/mp4': 'video/mp4',
        'video/mp4 avc1.42E01E': 'video/mp4; codecs="avc1.42E01E"',
        'video/mp4 avc1.64001E+mp4a.40.2': 'video/mp4; codecs="avc1.64001E, mp4a.40.2"',
        'video/mp4 mp4a.40.2': 'video/mp4; codecs="mp4a.40.2"',
        'video/mp4 av01.0.05M.08': 'video/mp4; codecs="av01.0.05M.08"',
        'video/webm': 'video/webm',
        'video/webm vp9+opus': 'video/webm; codecs="vp9, opus"',
    };
    const out = {user_agent: navigator.userAgent};
    for (const [key, spec] of Object.entries(specs)) out[key] = v.canPlayType(spec);
    return out;
}"""


def browser_codec_support(page):
    """Наблюдение, а не решение.

    Этот тест НЕ выбирает формат preview. Формат выбирает продукт
    (`previewFormat` в `video_studio.js`), потому что кнопкой «Создать preview»
    пользуется владелец, а не тест: зелёный тест, который сам подобрал себе
    проигрываемый контейнер, закрывал бы путь, оставшийся сломанным у владельца.

    Ответы браузера всё равно попадают в evidence — чтобы PASS на WebM никогда
    не читался как «здесь играет H.264», и чтобы результат одной сборки
    браузера не выдавался за поведение всех сборок.
    """
    return page.evaluate(BROWSER_CODEC_PROBE)


# Дневник самого проигрывателя: события, вызовы play/pause/load и подмены
# <video> в документе. Нужен ровно для одного: если превью снова встанет, в
# отчёте будет видно ЧТО его остановило (кто и когда вызвал pause, отклонился
# ли промис play, или узел просто выбросили перерисовкой) — а не только то,
# что оно стоит. Диагностика обязана быть немой: любой её собственный сбой
# гасится, иначе она сама станет ошибкой страницы.
PLAYBACK_TRACE = r"""
try {
  window.__vsTrace = [];
  const t0 = performance.now();
  let seq = 0;
  const idOf = el => { if (!el.__vsId) el.__vsId = ++seq; return el.__vsId; };
  const log = (kind, detail) => {
    try {
      if (window.__vsTrace.length < 400) {
        window.__vsTrace.push({ t: Number((performance.now() - t0).toFixed(1)), kind, ...detail });
      }
    } catch (ignored) { /* дневник молчит, а не падает */ }
  };
  const where = () => (new Error().stack || '').split('\n').slice(3, 5).map(s => s.trim()).join(' <- ');
  const snap = el => ({ id: idOf(el), ct: el.currentTime, paused: el.paused,
                        rs: el.readyState, connected: el.isConnected });
  for (const name of ['loadstart', 'loadedmetadata', 'play', 'playing', 'pause', 'waiting',
                      'stalled', 'emptied', 'abort', 'ended', 'seeking', 'seeked', 'error',
                      'timeupdate']) {
    document.addEventListener(name, e => {
      if (e.target instanceof HTMLMediaElement) log('event:' + name, snap(e.target));
    }, true);
  }
  const proto = HTMLMediaElement.prototype;
  for (const method of ['play', 'pause', 'load']) {
    const original = proto[method];
    proto[method] = function (...args) {
      log('call:' + method, { ...snap(this), from: where() });
      const result = original.apply(this, args);
      if (method === 'play' && result && result.catch) {
        result.then(() => log('play:resolved', snap(this)),
                    err => log('play:rejected', { ...snap(this), error: String(err) }));
      }
      return result;
    };
  }
  const currentTime = Object.getOwnPropertyDescriptor(proto, 'currentTime');
  Object.defineProperty(proto, 'currentTime', { ...currentTime, set(value) {
    log('set:currentTime', { id: idOf(this), to: value, from: where() });
    currentTime.set.call(this, value);
  } });
  new MutationObserver(records => {
    for (const record of records) {
      for (const [key, nodes] of [['removed', record.removedNodes], ['added', record.addedNodes]]) {
        for (const node of nodes) {
          const found = node instanceof HTMLMediaElement ? [node]
              : (node.querySelectorAll ? node.querySelectorAll('video') : []);
          for (const v of found) log('dom:' + key, snap(v));
        }
      }
    }
  }).observe(document, { childList: true, subtree: true });
} catch (ignored) { /* без дневника тест всё равно идёт */ }
"""

MEDIA_STATE = """() => {
    const v = document.querySelector('.vs-preview video');
    if (!v) return {element: null};
    const codes = {1: 'ABORTED', 2: 'NETWORK', 3: 'DECODE', 4: 'SRC_NOT_SUPPORTED'};
    return {src: v.currentSrc || v.src, ready_state: v.readyState, network_state: v.networkState,
            paused: v.paused, ended: v.ended, current_time: v.currentTime, duration: v.duration,
            seeking: v.seeking, width: v.videoWidth, height: v.videoHeight,
            error: v.error ? {code: v.error.code, name: codes[v.error.code] || '?',
                              message: v.error.message} : null};
}"""


def media_wait(page, stage, expression):
    """Ждать состояние проигрывателя, а при неудаче сказать ЧТО именно не так.

    Голый `TimeoutError: 15000ms exceeded` не отличает «не начал играть» от
    «браузер не умеет этот поток» и от «файл не отдался»: по такому отчёту
    чинить нечего, и первая же мысль — поднять таймаут, то есть спрятать
    причину. Здесь в сообщение уходит настоящее состояние медиа-элемента:
    readyState, networkState, MediaError с расшифровкой кода, реальный URL и
    HTTP-статус этого URL.

    Одного состояния мало: «paused: true, ended: false, error: null» говорит,
    что кто-то остановил рабочий поток, но не говорит кто. Поэтому туда же
    уходит хвост дневника проигрывателя (`PLAYBACK_TRACE`): вызовы play/pause,
    судьба промиса play() и подмены самого <video> в документе.
    """
    try:
        page.wait_for_function(expression, timeout=15000)
    except PWTimeout as exc:
        state = page.evaluate(MEDIA_STATE)
        src = (state or {}).get('src')
        if src:
            try:
                probe = page.request.get(src)
                state['http'] = {'status': probe.status,
                                 'content_type': probe.headers.get('content-type'),
                                 'bytes': len(probe.body())}
            except Exception as probe_error:  # noqa: BLE001 — диагностика, не приёмка
                state['http'] = f'unreadable: {probe_error}'
        try:
            state['trace'] = page.evaluate('() => (window.__vsTrace || []).slice(-40)')
        except Exception as trace_error:  # noqa: BLE001 — диагностика, не приёмка
            state['trace'] = f'unreadable: {trace_error}'
        raise AssertionError(
            f'preview playback stalled at [{stage}]: {json.dumps(state, ensure_ascii=False)}'
        ) from exc


def render_preview(page, previous_src=None):
    """Только штатная кнопка владельца: ни API, ни page.evaluate, ни reload.

    Раньше здесь была развилка: mp4 — кнопкой, webm — прямым вызовом
    `/exports` через `page.evaluate` с последующим `page.reload()`. Она давала
    зелёный результат на пути, которым владелец не ходит: настоящая кнопка
    вызывает `startExport(true)`, а тот контейнер не называл и всегда получал
    mp4. Пока формат подбирал тест, у владельца путь оставался сломанным.
    Теперь формат подбирает продукт, и трогать здесь нечего, кроме кнопки.
    """
    page.locator('.vs-preview-actions').get_by_role(
        'button', name='Создать preview', exact=True).click()
    page.locator('.vs-preview video').wait_for(timeout=60000)
    if previous_src is not None:
        # Не дать зачесть уже доигравший ПРЕЖНИЙ элемент за новый прогон.
        page.wait_for_function(
            """previous => {
                const v = document.querySelector('.vs-preview video');
                const src = v && (v.currentSrc || v.src);
                return Boolean(src) && src !== previous;
            }""", arg=previous_src, timeout=60000)
    return page.locator('.vs-preview video').evaluate('v => v.currentSrc || v.src')


def played_format(page, context, server, pid, evidence_path):
    """Какой контейнер/кодек ПРОДУКТ действительно собрал и браузер проиграл.

    Читается из завершённой preview-задачи и перепроверяется независимым
    ffprobe по тем самым байтам, которые загрузил <video>. Это не пожелание
    теста, а протокол выбора, сделанного продуктом: PASS на webm нельзя
    прочитать как «здесь играет H.264», и наоборот.
    """
    played = page.locator('.vs-preview video').evaluate('v => v.currentSrc || v.src')
    jobs = snapshot(context, f'{server.url}/api/video-studio/projects/{pid}/exports')['jobs']
    previews = [job for job in jobs
                if job.get('preview') and job['status'] == 'completed' and job.get('output_url')]
    assert previews, jobs
    job = next((j for j in previews if played.endswith(j['output_url'])), None)
    assert job is not None, (played, [j['output_url'] for j in previews])
    response = context.request.get(server.url + job['output_url'])
    assert response.status == 200, response.text()
    evidence_path.write_bytes(response.body())
    probe = json.loads(subprocess.check_output(
        ['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json',
         str(evidence_path)], text=True, timeout=20))
    meta = job['verification']['metadata']
    return {'played_url': played, 'http_content_type': response.headers.get('content-type'),
            'job_container': meta['format'], 'job_video_codec': meta['video_codec'],
            'job_audio_codec': meta['audio_codec'], 'bytes': job['verification']['bytes'],
            'sha256': job['verification']['sha256'],
            'ffprobe_container': probe['format']['format_name'],
            'ffprobe_codecs': {s['codec_type']: s['codec_name'] for s in probe['streams']}}


def play_preview_to_end(page):
    """Only real transport buttons change playback; DOM reads verify it.

    Посреди воспроизведения страница перерисовывается — тоже настоящей
    кнопкой владельца. `paint()` в студии зовёт не только он: его зовут
    завершение задачи рендера, событие сервера через `refresh()`, любой
    перехваченный сбой и переключение раскладки/языка/пространства. Пока
    <video> строился заново на каждый `paint()`, такая перерисовка молча
    обрывала воспроизведение: узел выбрасывался играющим, новый вставал на
    playhead и оставался на паузе — «paused: true, ended: false, error: null».
    Совпадёт ли это по времени само, зависит от везения, поэтому здесь
    перерисовка вызывается явно и всегда.
    """
    media_wait(page, 'metadata', """() => {
        const v = document.querySelector('.vs-preview video');
        return v && v.readyState >= 1 && v.videoWidth > 0 && !v.error;
    }""")
    page.locator('.vs-transport').get_by_role('button', name='│◀', exact=True).click()
    page.locator('.vs-transport').get_by_role('button', name='▶', exact=True).click()
    media_wait(page, 'playback started', """() => {
        const v = document.querySelector('.vs-preview video');
        return v && v.currentTime > .05 && !v.error;
    }""")
    page.get_by_role('button', name='Сбросить раскладку', exact=True).click()
    media_wait(page, 'played to end', """() => {
        const v = document.querySelector('.vs-preview video');
        return v && v.ended && v.currentTime >= .9 && !v.error;
    }""")
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
            page.add_init_script(PLAYBACK_TRACE)
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
            codec_support = browser_codec_support(page)
            preview_src = render_preview(page)
            expect(page.locator('.vs-job a[download]')).to_have_count(1, timeout=60000)
            playback = [play_preview_to_end(page)]
            formats = [played_format(page, context, server, pid, output / 'played-preview.bin')]
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
            # Пережившее перезапуск preview играет тем же транспортом...
            playback.append(play_preview_to_end(page))
            # ...и ТА ЖЕ кнопка в свежем процессе собирает и играет новое.
            preview_src = render_preview(page, previous_src=preview_src)
            expect(page.locator('.vs-job a[download]')).to_have_count(3, timeout=60000)
            playback.append(play_preview_to_end(page))
            formats.append(played_format(page, context, server, pid,
                                         output / 'played-preview-after-restart.bin'))
            assert errors == [], errors
            page.screenshot(path=str(output / 'video-user-path.png'), full_page=True)
            (output / 'video-result.json').write_text(json.dumps({'status': 'PASS',
                'kind': 'REAL_BROWSER_TESTER_NOT_LOCAL_MODEL', 'restart': 'FRESH_PROCESS',
                # Контейнер/кодеки, которые ПРОДУКТ выбрал сам и которые
                # действительно проигрались (до и после перезапуска), плюс
                # полный ответ браузера про кодеки: PASS на webm не означает,
                # что здесь играет H.264, и обратного из него читать нельзя.
                'played_container': formats[0]['ffprobe_container'],
                'played_video_codec': formats[0]['ffprobe_codecs'].get('video'),
                'played_audio_codec': formats[0]['ffprobe_codecs'].get('audio'),
                'preview_formats': formats, 'browser_codec_support': codec_support,
                'preview_format_chosen_by': 'product (video_studio.js previewFormat)',
                # Каждый прогон проигрывания пережил перерисовку страницы
                # («Сбросить раскладку» в подвале → `paint()`): PASS означает
                # «доиграло, несмотря на перерисовку», а не «повезло со временем».
                'repaint_during_playback': 'Сбросить раскладку (Editor.paint)',
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
            frame.locator('h1').click()
            row = page.locator('.bd-row').filter(has=page.locator('label').filter(has_text=re.compile(r'^Текст$')))
            row.locator('input[type=text]').fill('Verified Web heading')
            change(page, '/edit', lambda: row.get_by_role('button', name='Применить', exact=True).click())
            expect(frame.locator('h1')).to_have_text('Verified Web heading')
            frame.locator('h1').click()
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
