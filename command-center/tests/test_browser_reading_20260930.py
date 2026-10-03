"""Браузер как «листалка интернета»: живая проба 2026-09-30 нашла три дыры чтения.

D1. Снимок делался сразу после `domcontentloaded`: страница, подгружающая текст
    по fetch уже ПОСЛЕ него, читалась пустой (quotes.toscrape.com/scroll: 78
    знаков, через три секунды 1488). Модель видела «Loading…» и отвечала по нему.
D2. Текст обрезался на TEXT_LIMIT знаков, а подсказка отсылала к `browser.read_dom`
    «с уточняющим запросом», у которого не было ни одного параметра: всё за
    первыми 6000 знаками и вся ленивая подгрузка были недостижимы.
D3. Страница 404/500 приходила как пустой снимок: код ответа нигде не назывался.

Реальный Chromium + локальный http.server (на 127.0.0.1 — под owner-override
BCC_BROWSER_ALLOW_PRIVATE, как и остальные браузерные тесты). Без Chromium
тесты с браузером пропускаются; чистые тесты отрисовки идут всегда.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bcc.features import tools_browser
from bcc.tools import ToolContext
from bcc.v2.browser_control import BrowserManager, BrowserPolicy

from .browser_support import chromium_available, reason as browser_reason

needs_browser = pytest.mark.skipif(not chromium_available(), reason=browser_reason())

LATE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>late</title></head>
<body><div id="o">Loading...</div>
<script>fetch('/data').then(r => r.text()).then(t => { document.getElementById('o').textContent = t; });
</script></body></html>"""

LAZY_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>lazy</title>
<style>.it{height:320px;border-bottom:1px solid #ccc}</style></head><body><div id="feed"></div>
<script>
let n = 0;
function more() { for (let i = 0; i < 10; i++) { n++; const d = document.createElement('div');
  d.className = 'it'; d.textContent = 'LAZY-ITEM-' + String(n).padStart(3, '0');
  document.getElementById('feed').appendChild(d); } }
more();
window.addEventListener('scroll', () => {
  if (window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 80) more();
});
</script></body></html>"""

LONG_TEXT = "\n".join(f"<p>Абзац {i:04d} " + "слово " * 8 + "</p>" for i in range(400))
LONG_PAGE = f"<!doctype html><html><head><meta charset='utf-8'><title>long</title></head><body>{LONG_TEXT}</body></html>"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path == "/late":
            self._send(200, LATE_PAGE)
        elif self.path == "/data":
            time.sleep(0.6)
            self._send(200, "LATE-CONTENT-READY", "text/plain; charset=utf-8")
        elif self.path == "/lazy":
            self._send(200, LAZY_PAGE)
        elif self.path == "/long":
            self._send(200, LONG_PAGE)
        elif self.path == "/ok":
            self._send(200, "<html><head><title>ok</title></head><body><p>всё хорошо</p></body></html>")
        else:
            self._send(404, "<html><head><title>gone</title></head><body>Not here</body></html>")


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
async def mgr(tmp_path):
    manager = BrowserManager(tmp_path / "browser")
    await manager.start(1, BrowserPolicy.from_dict({}), headless=True)
    try:
        yield manager
    finally:
        await manager.stop(1)
        await manager.close()


# ------------------------------------------------------------------- D1


@needs_browser
async def test_navigate_waits_for_text_that_arrives_after_domcontentloaded(mgr, site):
    snap = await mgr.navigate(1, site + "/late", actor="agent", approved=True)
    assert "LATE-CONTENT-READY" in snap["text"], snap["text"]


# ------------------------------------------------------------------- D3


@needs_browser
async def test_error_page_reports_its_http_status(mgr, site):
    gone = await mgr.navigate(1, site + "/nope", actor="agent", approved=True)
    assert gone["http_status"] == 404
    assert "HTTP: 404" in tools_browser._render(gone).content

    # Негативный контроль: нормальная страница строки об ошибке не получает.
    fine = await mgr.navigate(1, site + "/ok", actor="agent", approved=True)
    assert fine["http_status"] == 200
    assert "HTTP:" not in tools_browser._render(fine).content


# ------------------------------------------------------------------- D2


def _snapshot_of(text: str) -> dict:
    return {"url": "http://x/long", "title": "long", "text": text, "interactive": []}


def test_long_page_is_read_in_chunks_and_the_hint_names_the_exact_next_call():
    text = "".join(f"[{i:05d}]" for i in range(3000))          # 21 000 знаков, каждый кусок узнаваем
    first = tools_browser._render(_snapshot_of(text))
    assert first.truncated is True
    assert f'"offset": {tools_browser.TEXT_LIMIT}' in first.more
    assert "[00000]" in first.content and "[02999]" not in first.content

    second = tools_browser._render(_snapshot_of(text), offset=tools_browser.TEXT_LIMIT)
    assert "[00857]" not in second.content                     # конец первого куска уже не показан
    assert text[tools_browser.TEXT_LIMIT:tools_browser.TEXT_LIMIT + 14] in second.content
    assert "знаки 6000.." in second.content and "из 21000" in second.content

    last = tools_browser._render(_snapshot_of(text), offset=3 * tools_browser.TEXT_LIMIT)
    assert last.truncated is False and last.more == ""
    assert "[02999]" in last.content                           # конец страницы достижим


def test_short_page_render_is_unchanged_and_offset_is_clamped():
    snap = _snapshot_of("коротко")
    plain = tools_browser._render(snap)
    assert "Текст страницы:\nкоротко" in plain.content and plain.truncated is False
    beyond = tools_browser._render(snap, offset=10**9)          # не падает и не врёт
    assert beyond.truncated is False


class _RecordingManager:
    def __init__(self, text: str) -> None:
        self.text, self.calls = text, []

    async def snapshot(self, session_id, *, actor="agent", approved=False, **kw):
        self.calls.append(kw)
        start = kw.get("text_offset", 0)
        return {"session_id": session_id, "url": "http://x/", "title": "t",
                "text": self.text[start:start + 20000], "text_offset": start,
                "text_total": len(self.text), "interactive": []}


async def test_read_dom_tool_passes_scroll_and_offset_and_survives_garbage(env, monkeypatch):
    text = "".join(f"[{i:05d}]" for i in range(3000))
    fake = _RecordingManager(text)
    monkeypatch.setattr(tools_browser, "_mgr", lambda svc: fake)

    async def fixed_session(ctx, args):
        return 7
    monkeypatch.setattr(tools_browser, "_session_for", fixed_session)
    ctx = ToolContext(svc=env.svc, task={"id": 1}, run_id=1, agent={})

    out = await tools_browser._read_dom({"offset": 6000, "scroll": 3}, ctx)
    assert fake.calls[-1] == {"scroll_screens": 3, "text_offset": 6000}
    assert "знаки 6000.." in out.content

    out = await tools_browser._read_dom({"offset": "много", "scroll": "вниз"}, ctx)
    assert fake.calls[-1] == {"scroll_screens": 0, "text_offset": 0}
    assert out.error is False and "[00000]" in out.content

    await tools_browser._read_dom({"scroll": 999}, ctx)        # потолок, а не 999 экранов
    assert fake.calls[-1]["scroll_screens"] == 10

    spec = tools_browser.REGISTRY.get("browser.read_dom")
    assert {"offset", "scroll"} <= set(spec.input_schema)


@needs_browser
async def test_scroll_triggers_lazy_loading_that_a_plain_read_never_sees(mgr, site):
    first = await mgr.navigate(1, site + "/lazy", actor="agent", approved=True)
    assert "LAZY-ITEM-010" in first["text"] and "LAZY-ITEM-011" not in first["text"]

    again = await mgr.snapshot(1, actor="agent", approved=True)
    assert "LAZY-ITEM-011" not in again["text"], "без прокрутки подгрузки нет"

    scrolled = await mgr.snapshot(1, actor="agent", approved=True, scroll_screens=6)
    assert "LAZY-ITEM-011" in scrolled["text"], scrolled["text"][-200:]


@needs_browser
async def test_page_longer_than_the_snapshot_cap_is_fully_reachable_through_offset(mgr, site):
    """Снимок держит до 20000 знаков; страница длиннее читается окнами, а не
    обрезается навсегда: `text_offset` режет окно ещё внутри страницы."""
    snap = await mgr.navigate(1, site + "/long", actor="agent", approved=True)
    assert snap["text_total"] > 20000, "фикстура должна быть длиннее потолка снимка"
    seen, offset, guard = "", 0, 0
    while True:
        snap = await mgr.snapshot(1, actor="agent", approved=True, text_offset=offset)
        part = tools_browser._render(snap)
        seen += part.content
        if not part.truncated:
            break
        offset += tools_browser.TEXT_LIMIT
        guard += 1
        assert guard < 20
    assert "Абзац 0000" in seen and "Абзац 0399" in seen
    assert f"из {snap['text_total']}" in seen
