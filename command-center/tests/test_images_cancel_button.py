"""Кнопка «Стоп» в Images нажимается по-настоящему. Закрывает пробел BL-057.

BL-057 назвал причину, по которой кнопку не проверял никто: MockImageProvider
рисует мгновенно, задача завершается раньше клика. Условие закрытия — «управляемый
тестовый провайдер... не добавляй искусственные задержки в боевой код».

Здесь провайдер — настоящий ComfyUI-адаптер продукта, направленный на поддельный
ComfyUI-сервер под управлением теста (127.0.0.1). Он отвечает на /history «ещё не
готово», пока тест не отпустит задачу. Продукт не изменён ни на символ; адрес
приходит через окружение, которое EditorServer копирует и в процесс архива.

Что доказывается через браузер, по пунктам задания:
  * нужная задача действительно начала выполняться (её /prompt получен);
  * нажатие отменяет именно её — соседняя не тронута;
  * интерфейс показывает фактическое состояние;
  * частичный результат не объявлен готовым — даже когда «вычисление» позже
    заканчивается, отменённая задача ассет не получает;
  * повторный запуск работает.
Отдельно, для внешнего ComfyUI: отмена задачи Bossman НЕ зовёт /interrupt —
чужой расчёт не останавливается; различие двух вещей измерено, а не заявлено.

Негативный контроль: задача, которую никто не останавливал, остаётся running,
пока её держат, и завершается только после отпускания. Без него «Стоп сработал»
ничего не стоило бы — ровно то, чему научил BL-057.
"""
from __future__ import annotations

import json
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from .browser_support import chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401

pytestmark = [pytest.mark.timeout(300),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]


def _png(width=64, height=64) -> bytes:
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + b'\x00\x55\xaa' * width) * height)) + chunk(b'IEND', b''))


class ComfyStub:
    """Поддельный ComfyUI: держит задачу, пока тест не скажет «готово»."""

    def __init__(self):
        self.lock = threading.Lock()
        self.prompts: list[str] = []          # в порядке получения
        self.dims: dict[str, tuple[int, int]] = {}   # размер, который запросил workflow
        self.released: set[str] = set()
        self.interrupts = 0

    def release(self, *ids: str):
        with self.lock:
            self.released.update(ids or self.prompts)


def _handler(stub: ComfyStub):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _json(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/system_stats':
                return self._json(200, {'system': {'os': 'stub'}, 'devices': []})
            if path.startswith('/history/'):
                pid = path.rsplit('/', 1)[1]
                with stub.lock:
                    done = pid in stub.released
                if not done:
                    return self._json(200, {})
                return self._json(200, {pid: {'status': {'completed': True, 'status_str': 'success'},
                                              'outputs': {'9': {'images': [{'filename': f'{pid}.png', 'subfolder': '',
                                                                              'type': 'output'}]}}}})
            if path == '/view':
                # Провайдер сверяет размер PNG с запрошенным: отдаём ровно его.
                query = dict(part.split('=', 1) for part in self.path.split('?', 1)[1].split('&')) if '?' in self.path else {}
                pid = query.get('filename', '').rsplit('.', 1)[0]
                width, height = stub.dims.get(pid, (1024, 1024))
                data = _png(width, height)
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._json(404, {})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0) or 0))
            if self.path == '/prompt':
                workflow = (json.loads(raw or b'{}') or {}).get('prompt') or {}
                dims = next(((int(n['inputs']['width']), int(n['inputs']['height']))
                             for n in workflow.values() if isinstance(n, dict)
                             and isinstance(n.get('inputs'), dict)
                             and 'width' in n['inputs'] and 'height' in n['inputs']), (1024, 1024))
                with stub.lock:
                    pid = f'job-{len(stub.prompts) + 1}'
                    stub.prompts.append(pid)
                    stub.dims[pid] = dims
                return self._json(200, {'prompt_id': pid})
            if self.path == '/interrupt':
                with stub.lock:
                    stub.interrupts += 1
                return self._json(200, {})
            self._json(404, {})
    return Handler


@pytest.fixture
def comfy(monkeypatch):
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    stub = ComfyStub()
    server = ThreadingHTTPServer(('127.0.0.1', port), _handler(stub))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Читается продуктом в момент исполнения задачи; EditorServer копирует os.environ
    # в процесс сервера, поэтому порядок фикстур ниже — сначала заглушка.
    monkeypatch.setenv('BOSSMAN_COMFYUI_URL', f'http://127.0.0.1:{port}')
    monkeypatch.setenv('BOSSMAN_COMFYUI_CHECKPOINT', 'stub.safetensors')
    try:
        yield stub
    finally:
        server.shutdown()


@pytest.fixture
def live(comfy, editor_server):
    return editor_server


def _api(page, method, path, body=None):
    return page.evaluate("""async ([method, path, body]) => {
      const r = await fetch(path, {method, credentials: 'include',
        headers: {'Content-Type': 'application/json', 'X-BCC-CSRF': localStorage.getItem('bcc.csrf')},
        body: body === null ? undefined : JSON.stringify(body)});
      const text = await r.text();
      let data = null; try { data = JSON.parse(text); } catch (e) { data = text; }
      return {status: r.status, data};
    }""", [method, path, body])


def _job(page, job_id):
    return _api(page, 'GET', f'/api/images/jobs/{job_id}')['data']


def _create(page, prompt):
    reply = _api(page, 'POST', '/api/images/jobs', {'prompt': prompt, 'model_alias': 'comfyui', 'count': 1, 'width': 256, 'height': 256})
    assert reply['status'] < 300, reply
    return reply['data']['id']


def _assets_titled(page, title):
    items = _api(page, 'GET', '/api/images/assets?limit=200')['data']
    rows = items.get('items') if isinstance(items, dict) else items
    return [a for a in (rows or []) if a.get('title') == title]


def _wait(predicate, timeout=30.0, step=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def test_stop_cancels_the_running_job_only_and_a_late_result_is_not_adopted(live, comfy):
    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1600, 'height': 1100})
            _login(page, live)
            a = _create(page, 'STOP ME A')
            b = _create(page, 'NEIGHBOUR B')

            # Нужная задача действительно начала выполняться: её /prompt дошёл до провайдера.
            assert _wait(lambda: len(comfy.prompts) >= 1 and _job(page, a)['status'] == 'running'), \
                (comfy.prompts, _job(page, a))
            assert _job(page, b)['status'] in ('queued', 'running'), _job(page, b)

            page.goto(live.url + '/#/images')
            page.get_by_role('button', name='Очередь', exact=True).click()
            row_a = page.locator('.images-job-row', has_text=f'#{a}')
            row_b = page.locator('.images-job-row', has_text=f'#{b}')
            expect(row_a.get_by_role('button', name='Стоп', exact=True)).to_be_visible()
            expect(row_b).to_be_visible()

            with page.expect_response(lambda r: r.url.endswith(f'/api/images/jobs/{a}/cancel')
                                      and r.request.method == 'POST') as cancelled:
                row_a.get_by_role('button', name='Стоп', exact=True).click()
            assert cancelled.value.ok

            # Отменена именно она; сосед не тронут; чужой расчёт не прерван.
            assert _job(page, a)['status'] == 'cancelled'
            assert _job(page, b)['status'] in ('queued', 'running')
            assert comfy.interrupts == 0, 'отмена задачи Bossman остановила чужое вычисление в ComfyUI'

            # Интерфейс показывает фактическое состояние: A ушла из очереди, у B «Стоп» на месте.
            expect(page.locator('.images-job-row', has_text=f'#{a}')).to_have_count(0)
            expect(page.locator('.images-job-row', has_text=f'#{b}').get_by_role('button', name='Стоп', exact=True)).to_be_visible()

            # Сосед стартует СРАЗУ, не дожидаясь конца чужого расчёта отменённой A:
            # отмена освобождает воркер (BL-066). Измерено до починки: 9,9 с и
            # только после завершения A у провайдера; теперь — секунды.
            assert _wait(lambda: len(comfy.prompts) >= 2, timeout=10), \
                f'сосед не стартовал после отмены: {comfy.prompts}'
            assert comfy.interrupts == 0

            # «Вычисление» отменённой A позже заканчивается — результат НЕ усыновляется.
            comfy.release('job-1')
            time.sleep(2.5)                    # несколько опросов после готовности A
            assert _job(page, a)['status'] == 'cancelled'
            assert _assets_titled(page, 'STOP ME A') == [], 'отменённая задача получила ассет'
            comfy.release('job-2')
            assert _wait(lambda: _job(page, b)['status'] == 'completed', timeout=60), _job(page, b)
            assert len(_assets_titled(page, 'NEIGHBOUR B')) == 1, 'соседняя задача не дала результата'
            # Результат ОТКРЫВАЕТСЯ, а не только числится: файл ассета отдаётся и это PNG.
            asset = _assets_titled(page, 'NEIGHBOUR B')[0]
            head = page.evaluate("""async id => {
              const r = await fetch('/api/images/assets/' + id + '/file', {credentials: 'include'});
              const buf = new Uint8Array(await r.arrayBuffer());
              return {status: r.status, type: r.headers.get('content-type'), magic: Array.from(buf.slice(0, 4))};
            }""", asset['id'])
            assert head['status'] == 200 and head['magic'] == [0x89, 0x50, 0x4E, 0x47], head

            # Повторный запуск работает — с кнопки «Повторить» у отменённой задачи.
            page.get_by_role('button', name='Все', exact=True).click()
            row_a = page.locator('.images-job-row', has_text=f'#{a}')
            expect(row_a.get_by_role('button', name='Повторить', exact=True)).to_be_visible()
            with page.expect_response(lambda r: r.url.endswith(f'/api/images/jobs/{a}/retry')
                                      and r.request.method == 'POST') as retried:
                row_a.get_by_role('button', name='Повторить', exact=True).click()
            retry_id = retried.value.json()['id']
            assert retry_id != a
            assert _wait(lambda: len(comfy.prompts) >= 3, timeout=30), comfy.prompts
            comfy.release('job-3')
            assert _wait(lambda: _job(page, retry_id)['status'] == 'completed', timeout=60), _job(page, retry_id)
            assert len(_assets_titled(page, 'STOP ME A')) == 1
        finally:
            browser.close()


def test_a_job_nobody_stops_stays_running_until_released(live, comfy):
    """Негативный контроль: барьер настоящий. Иначе тест выше — пустой."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            job = _create(page, 'HELD JOB')
            assert _wait(lambda: _job(page, job)['status'] == 'running'), _job(page, job)
            time.sleep(3.0)
            assert _job(page, job)['status'] == 'running', 'провайдер не удержал задачу — барьер пустой'
            assert _assets_titled(page, 'HELD JOB') == []
            comfy.release()
            assert _wait(lambda: _job(page, job)['status'] == 'completed', timeout=60), _job(page, job)
            assert len(_assets_titled(page, 'HELD JOB')) == 1
        finally:
            browser.close()
