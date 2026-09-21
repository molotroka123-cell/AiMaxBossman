"""B4 (owner audit 2026-09-21, P1): навигация на файл → «Download is starting» → 500.

Регрессионная матрица на НАСТОЯЩЕМ Chromium через продуктовый путь
POST /api/browser/sessions/{id}/act. PASS каждого случая требует реального
файла на диске с совпавшим размером и sha256 — «статус 200» не доказательство.
"""
from __future__ import annotations

import hashlib
import http.server
import io
import socketserver
import threading
import zipfile
from pathlib import Path

import pytest

from .browser_support import chromium_available, reason

pytestmark = pytest.mark.skipif(not chromium_available(), reason=reason())

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]"
       b"/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
TXT = "BOSSMAN-B4 проверка загрузки\n".encode("utf-8")


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("inside.txt", "b4-zip-payload")
    return buf.getvalue()


ZIP = _zip()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401 — тихий тестовый сервер
        pass

    def _send(self, body: bytes, ctype: str, extra: dict | None = None, length: int | None = None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body) if length is None else length))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        p = self.path.split("?", 1)[0]
        if p == "/page.html":
            self._send("<html><head><title>B4 page</title></head><body><h1>Обычная страница</h1>"
                       "<a id='dl' href='/attach.txt'>скачать</a>"
                       "<a id='dlattr' download='named.txt' href='/plain.txt'>скачать2</a>"
                       "</body></html>".encode(), "text/html; charset=utf-8")
        elif p == "/doc.pdf":
            self._send(PDF, "application/pdf")
        elif p == "/attach.pdf":
            self._send(PDF, "application/pdf", {"Content-Disposition": 'attachment; filename="report.pdf"'})
        elif p == "/attach.txt":
            self._send(TXT, "text/plain; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="note.txt"'})
        elif p == "/plain.txt":
            self._send(TXT, "text/plain; charset=utf-8")
        elif p == "/bundle.zip":
            self._send(ZIP, "application/zip")
        elif p == "/tool.exe":
            self._send(b"MZ-not-really", "application/octet-stream",
                       {"Content-Disposition": 'attachment; filename="tool.exe"'})
        elif p == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/attach.pdf")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif p == "/evil":
            self._send(TXT, "application/octet-stream",
                       {"Content-Disposition": 'attachment; filename="..\..\CON.txt"'})
        elif p == "/stall.bin":
            # Заголовки есть, тела нет — сервер «завис».
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="stall.bin"')
            self.send_header("Content-Length", "1048576")
            self.end_headers()
            self.wfile.write(b"x" * 100)
            self.wfile.flush()
            import time as _t
            _t.sleep(8)
        elif p == "/broken.bin":
            # Объявлено 100 КБ, отдано 10 байт, соединение закрыто — обрыв.
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="broken.bin"')
            self.send_header("Content-Length", "102400")
            self.end_headers()
            self.wfile.write(b"0123456789")
            self.wfile.flush()
            self.connection.shutdown(2)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


async def _session(env) -> int:
    r = await env.client.post("/api/browser/sessions", json={})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


async def _act(env, sid, **body):
    return await env.client.post(f"/api/browser/sessions/{sid}/act", json=body)


def _assert_file(dl: dict, payload: bytes, name_suffix: str):
    assert dl["status"] == "saved", dl
    path = Path(dl["path"])
    assert path.is_file(), f"файла нет на диске: {path}"
    data = path.read_bytes()
    assert data == payload
    assert dl["bytes"] == len(payload)
    assert dl["sha256"] == hashlib.sha256(payload).hexdigest()
    assert path.name.endswith(name_suffix), path.name
    assert not path.with_name(path.name + ".part").exists()


async def test_b4_download_matrix_real_chromium(env, site):
    sid = await _session(env)
    try:
        # 1. обычная HTML — навигация, без загрузки
        r = await _act(env, sid, action="navigate", url=f"{site}/page.html", actor="human")
        assert r.status_code == 200, r.text
        assert "Обычная страница" in r.json()["text"] and "download" not in r.json()

        # 2. прямой PDF (headless Chromium отдаёт его загрузкой — ровно сценарий B4)
        r = await _act(env, sid, action="navigate", url=f"{site}/doc.pdf", actor="human")
        assert r.status_code == 200, r.text
        _assert_file(r.json()["download"], PDF, ".pdf")
        assert r.json()["download"]["mime"] == "application/pdf"
        assert "Файл скачан" in r.json()["message"]

        # 3. Content-Disposition: attachment
        r = await _act(env, sid, action="navigate", url=f"{site}/attach.pdf", actor="human")
        assert r.status_code == 200, r.text
        _assert_file(r.json()["download"], PDF, "report.pdf")

        # 4. редирект → загрузка (одноимённый файл не затирает прошлый)
        r = await _act(env, sid, action="navigate", url=f"{site}/redirect", actor="human")
        assert r.status_code == 200, r.text
        dl = r.json()["download"]
        _assert_file(dl, PDF, "report (1).pdf")

        # 5. маленький TXT (attachment) и 6. маленький ZIP
        r = await _act(env, sid, action="navigate", url=f"{site}/attach.txt", actor="human")
        _assert_file(r.json()["download"], TXT, "note.txt")
        r = await _act(env, sid, action="navigate", url=f"{site}/bundle.zip", actor="human")
        assert r.status_code == 200, r.text
        dl = r.json()["download"]
        _assert_file(dl, ZIP, ".zip")
        assert dl["mime"] == "application/zip"
        with zipfile.ZipFile(dl["path"]) as zf:
            assert zf.read("inside.txt") == b"b4-zip-payload"

        # 7. загрузка кликом по ссылке (обычный click и явное действие download)
        await _act(env, sid, action="navigate", url=f"{site}/page.html", actor="human")
        r = await _act(env, sid, action="click", selector="#dl", actor="human")
        assert r.status_code == 200, r.text
        _assert_file(r.json()["download"], TXT, "note (1).txt")
        r = await _act(env, sid, action="download", selector="#dlattr", actor="human")
        assert r.status_code == 200, r.text
        _assert_file(r.json()["download"], TXT, "named.txt")

        # 8. оборванная загрузка — честный отказ, НЕ 500 и НЕ «скачано»
        r = await _act(env, sid, action="navigate", url=f"{site}/broken.bin", actor="human")
        assert r.status_code == 422, r.text
        body = r.json()
        text = str(body)
        assert "не скачан" in text

        # 9. исполняемый файл — только в карантин
        r = await _act(env, sid, action="navigate", url=f"{site}/tool.exe", actor="human")
        assert r.status_code == 200, r.text
        dl = r.json()["download"]
        assert dl["quarantined"] is True and Path(dl["path"]).parent.name == "quarantine"

        # журнал сессии: всё, включая сорвавшуюся
        r = await env.client.get(f"/api/browser/sessions/{sid}/downloads")
        statuses = [d["status"] for d in r.json()]
        assert statuses.count("saved") == 8 and statuses.count("failed") == 1, statuses
        saved = [Path(d["path"]) for d in r.json() if d["status"] == "saved"]
        assert all(p.is_file() for p in saved)
        folder = saved[0].parent
        assert not list(folder.glob("broken*")), "оборванный файл остался на диске"
        assert not list(folder.rglob("*.part"))
    finally:
        await env.client.post(f"/api/browser/sessions/{sid}/stop")


async def test_b4_agent_download_needs_approval_and_saves_nothing(env, site):
    """Агент без одобрения: загрузка отменена, файла нет, 202 с approval_id.
    С одобренным approval_id — файл реально сохранён."""
    sid = await _session(env)
    try:
        url = f"{site}/attach.pdf"
        r = await _act(env, sid, action="navigate", url=url, actor="agent")
        assert r.status_code == 202, r.text
        aid = r.json()["error"]["approval_id"]
        assert "report.pdf" in r.json()["error"]["message"]
        r2 = await env.client.get(f"/api/browser/sessions/{sid}/downloads")
        assert [d["status"] for d in r2.json()] == ["needs_approval"]
        assert not any("path" in d for d in r2.json())
        # одобрение владельцем → повтор с approval_id
        dec = await env.client.post(f"/api/approvals/{aid}", json={"approve": True})
        assert dec.status_code in (200, 204), dec.text
        r3 = await _act(env, sid, action="navigate", url=url, actor="agent", approval_id=aid)
        assert r3.status_code == 200, r3.text
        _assert_file(r3.json()["download"], PDF, "report.pdf")
        # повтор того же approval_id не проходит (одноразовое)
        r4 = await _act(env, sid, action="navigate", url=url, actor="agent", approval_id=aid)
        assert r4.status_code == 202, r4.text
    finally:
        await env.client.post(f"/api/browser/sessions/{sid}/stop")


async def test_b4_agent_tool_path_open_refuses_then_download_tool_saves(env, site):
    """Модель: browser.open на файл → файл НЕ сохранён, подсказка про browser.download;
    browser.download (ASK) → подтверждение владельца → реальный файл, ровно один."""
    import sqlalchemy as sa

    from bcc.db import tool_calls as tool_calls_t

    from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools

    url = f"{site}/attach.pdf"
    adapter = ToolAdapter([
        ("tool", "browser_open", {"url": url}),
        ("tool", "browser_download", {"url": url}),
        ("text", "файл скачан"),
    ])
    stack = await _stack_with_tools(env, ["browser.open", "browser.download"],
                                    adapter=adapter, max_steps=6)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"browser.read": True, "browser.control": True}})
    assert await _run_task(env, stack["task"]["id"], timeout=90) == "waiting_approval"
    assert not list((env.svc.browser.data_dir / "downloads").rglob("*.pdf")), "файл без одобрения"
    first = adapter.seen_messages[1][-1]["content"]
    assert "НЕ сохранён" in first and "browser.download" in first

    appr = [a for a in (await env.client.get("/api/approvals")).json()
            if "browser.download" in a["preview"]]
    assert len(appr) == 1, appr
    await env.client.post(f"/api/approvals/{appr[0]['id']}", json={"approve": True, "by": "тест"})
    assert await _run_task(env, stack["task"]["id"], until=FINISHED, timeout=90) == "completed"

    result = adapter.seen_messages[2][-1]["content"]
    assert "Файл скачан: report.pdf" in result
    path = Path(result.split("Путь: ", 1)[1].splitlines()[0].strip())
    assert path.read_bytes() == PDF
    assert hashlib.sha256(PDF).hexdigest() in result
    async with env.svc.db.session() as s:
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(tool_calls_t))).fetchall()]
    assert [(r["tool"], r["status"]) for r in rows] == [("browser.open", "error"),
                                                        ("browser.download", "executed")]
    assert len(list(path.parent.glob("report*.pdf"))) == 1
    await env.svc.browser.close()


async def test_b4_timeout_cancel_and_hostile_filename(env, site, monkeypatch):
    """Зависшая загрузка → таймаут → 422 и ничего на диске; стоп сессии посреди
    загрузки → не «скачано»; имя с traversal/зарезервированным словом — обезврежено."""
    import asyncio

    monkeypatch.setenv("BCC_BROWSER_DOWNLOAD_TIMEOUT_S", "2")
    sid = await _session(env)
    folder = env.svc.browser.downloads_dir(sid)
    try:
        r = await _act(env, sid, action="navigate", url=f"{site}/stall.bin", actor="human")
        assert r.status_code == 422, r.text
        assert r.json()["error"]["download"]["status"] == "failed"
        assert not list(folder.rglob("stall*")) and not list(folder.rglob("*.part"))

        r = await _act(env, sid, action="navigate", url=f"{site}/evil", actor="human")
        assert r.status_code == 200, r.text
        p = Path(r.json()["download"]["path"])
        assert p.parent == folder and ".." not in p.name and p.name.startswith("_CON")
        assert p.read_bytes() == TXT
    finally:
        await env.client.post(f"/api/browser/sessions/{sid}/stop")

    # Отмена: сессию останавливают, пока загрузка висит.
    monkeypatch.setenv("BCC_BROWSER_DOWNLOAD_TIMEOUT_S", "30")
    sid = await _session(env)
    folder = env.svc.browser.downloads_dir(sid)
    pending = asyncio.create_task(_act(env, sid, action="navigate", url=f"{site}/stall.bin",
                                       actor="human"))
    await asyncio.sleep(1.5)
    await env.client.post(f"/api/browser/sessions/{sid}/stop")
    r = await asyncio.wait_for(pending, 30)
    assert r.status_code != 200, r.text
    assert "download" not in (r.json() if r.status_code == 200 else {})
    assert not list(folder.rglob("stall*"))
