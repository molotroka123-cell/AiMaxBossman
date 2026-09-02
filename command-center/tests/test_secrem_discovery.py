"""SECREM F-017 (+ BUG-005) — discovery extra_urls и taskxchange result.

F-017: POST /api/models/discover extra_urls раньше зондировал ЛЮБОЙ URL
(169.254.169.254, metadata.google.internal, file://…); GET /taskxchange/result
делал mkdir/чтение по app_id с `..` вне APPS_DIR.

BUG-005: discover() против порта, который принимает соединение и молчит,
обязан вернуться в пределах таймаута зонда и закрыть клиентское соединение.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from bcc import discovery
from bcc.discovery import discover
from bcc.features import apps as apps_mod
from bcc.features import task_exchange as tx


# ------------------------------------------------------------ F-017: extra_urls

def _recording_transport(seen: list[str]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "m"}]})
    return httpx.MockTransport(handle)


BLOCKED = [
    "http://169.254.169.254/latest/meta-data",        # AWS/GCP metadata (link-local)
    "http://metadata.google.internal/computeMetadata/v1",
    "http://[fe80::1]:8080/v1",                       # IPv6 link-local
    "file:///etc/passwd",                             # не http(s)
    "ftp://127.0.0.1/v1",
    "http://user:pw@127.0.0.1:8080/v1",               # userinfo
    "http://224.0.0.1:8080/v1",                       # multicast
    "http://0.0.0.0:8080/v1",                         # unspecified
]


async def test_extra_urls_link_local_and_non_http_are_never_probed(tmp_path):
    seen: list[str] = []
    result = await discover(extra_urls=BLOCKED, endpoints=[], model_dirs=[str(tmp_path)],
                            transport=_recording_transport(seen))
    assert seen == [], f"запрещённые URL ушли в сеть: {seen}"
    assert result["online"] == 0
    # отказ виден в ответе честно, а не молча выброшен
    assert len(result["endpoints"]) == len(BLOCKED)
    for r in result["endpoints"]:
        assert r["ok"] is False and r.get("rejected") is True, r
        assert "отклон" in r["detail"], r


async def test_extra_urls_loopback_and_private_are_probed(tmp_path):
    """Discovery легитимно опрашивает локальные сервера моделей: loopback и RFC1918
    разрешены — блокируется только link-local/metadata и не-http."""
    seen: list[str] = []
    allowed = ["http://127.0.0.1:8080/v1", "http://192.168.1.50:8080/v1",
               "http://localhost:1234/v1", "http://[::1]:8080/v1"]
    result = await discover(extra_urls=allowed, endpoints=[], model_dirs=[str(tmp_path)],
                            transport=_recording_transport(seen))
    assert result["online"] == len(allowed)
    assert len(seen) == len(allowed)


async def test_extra_url_hostname_resolving_to_link_local_is_blocked(tmp_path, monkeypatch):
    """Имя, которое резолвится в 169.254.x.x, — тот же metadata-SSRF через DNS."""
    import socket

    def fake_getaddrinfo(host, *a, **kw):
        if host == "meta.example.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    seen: list[str] = []
    result = await discover(extra_urls=["http://meta.example.test/v1"], endpoints=[],
                            model_dirs=[str(tmp_path)], transport=_recording_transport(seen))
    assert seen == []
    assert result["endpoints"][0]["rejected"] is True


# ------------------------------------------------------------ F-017: taskxchange result

@pytest.fixture
def xenv(tmp_path, monkeypatch):
    monkeypatch.setattr(apps_mod, "APPS_DIR", tmp_path / "apps")
    monkeypatch.setattr(tx, "APPS_DIR", tmp_path / "apps")
    monkeypatch.setattr(apps_mod, "_cache", {"at": 0.0, "apps": []})
    d = tmp_path / "apps" / "good-app"
    d.mkdir(parents=True)
    (d / "app.manifest.yaml").write_text(
        "id: good-app\nname: good-app\nversion: '1.0'\ndefault_port: 8931\n"
        "permissions:\n  local.compute: auto\n", encoding="utf-8")
    return tmp_path


def _dirs_outside_apps(tmp_path: Path) -> set[str]:
    """Все каталоги под tmp_path, НЕ лежащие внутри apps/ (побег traversal)."""
    apps = (tmp_path / "apps").resolve()
    out = set()
    for p in tmp_path.rglob("*"):
        if p.is_dir() and apps not in p.resolve().parents and p.resolve() != apps:
            out.add(str(p))
    return out


async def test_result_route_rejects_traversal_and_creates_nothing(xenv, env):
    from fastapi import HTTPException

    before = _dirs_outside_apps(xenv)
    for bad_app in ("../../evil", "..", "/tmp/x", "a/b", "good-app/../evil",
                    "..\\evil", "%2e%2e%2f", ""):
        with pytest.raises(HTTPException) as ei:
            await tx.result(bad_app, "t1")
        assert ei.value.status_code in (400, 404), bad_app
    # task_id с побегом тоже не должен уходить в ФС
    for bad_task in ("../../../etc/passwd", "..", "x/y", "..%2f"):
        with pytest.raises(HTTPException) as ei:
            await tx.result("good-app", bad_task)
        assert ei.value.status_code in (400, 404), bad_task
    assert _dirs_outside_apps(xenv) == before
    assert not (xenv / "evil").exists() and not (xenv.parent / "evil").exists()

    # через HTTP: закодированный traversal → 4xx, ничего не создано
    r = await env.client.get("/api/taskxchange/result/%2e%2e%2f%2e%2e%2fevil/t1")
    assert r.status_code in (400, 404), r.text
    r = await env.client.get("/api/taskxchange/result/good-app/%2e%2e%2f%2e%2e%2fx")
    assert r.status_code in (400, 404), r.text
    assert _dirs_outside_apps(xenv) == before


async def test_result_route_still_serves_known_app(xenv):
    from fastapi import HTTPException

    root = tx.exchange_root("good-app") / "completed"
    root.mkdir(parents=True)
    (root / "t1.json").write_text('{"task_id": "t1", "status": "COMPLETED"}', encoding="utf-8")
    assert (await tx.result("good-app", "t1"))["status"] == "COMPLETED"
    with pytest.raises(HTTPException) as ei:
        await tx.result("good-app", "missing")
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        await tx.result("unknown-app", "t1")        # не в реестре → 404 до mkdir
    assert ei.value.status_code == 404
    assert not (xenv / "apps" / "unknown-app").exists()


# ------------------------------------------------------------ BUG-005: молчащий порт

async def _silent_server():
    """Сервер, который принимает соединение и молчит (как форвардер WSL2).

    Handler-задачи и reader'ы запоминаются, чтобы тест мог (а) проверить, что
    клиент закрыл соединение, и (б) корректно завершить сервер: на Python ≥ 3.12
    Server.wait_closed() ждёт закрытия ВСЕХ соединений, а молчащий handler
    транспорт не закрывает никогда.
    """
    handlers: list[asyncio.Task] = []
    readers: list[asyncio.StreamReader] = []

    async def silent(reader, writer):
        handlers.append(asyncio.current_task())
        readers.append(reader)
        await asyncio.sleep(30)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    return server, handlers, readers


async def _close_silent(server, handlers):
    server.close()
    for t in handlers:
        t.cancel()            # отменённый handler → streams закрывает транспорт
    await asyncio.wait_for(server.wait_closed(), 5)


async def test_silent_accept_port_returns_within_bound_and_is_diagnosed(monkeypatch):
    """discover() против «принял и молчит» возвращается в пределах таймаута зонда,
    диагноз — «занят другим процессом», клиентское соединение закрыто."""
    monkeypatch.setattr(discovery, "PROBE_TIMEOUT", 1.0)
    server, handlers, readers = await _silent_server()
    port = server.sockets[0].getsockname()[1]
    try:
        t0 = time.perf_counter()
        result = await asyncio.wait_for(
            discover(endpoints=[("занятый", f"http://127.0.0.1:{port}/v1")], model_dirs=[]),
            timeout=5.0)
        elapsed = time.perf_counter() - t0
        detail = result["endpoints"][0]["detail"]
        assert result["online"] == 0
        assert "занят другим процессом" in detail and str(port) in detail, detail
        assert elapsed < 5.0, elapsed
        # клиент HTTP-зонда и TCP-проб закрыли соединения: сервер видит EOF
        assert readers, "сервер не получил ни одного соединения"
        for reader in readers:
            data = await asyncio.wait_for(reader.read(), 2.0)
            assert data == b"" or reader.at_eof()
    finally:
        await _close_silent(server, handlers)


async def test_probe_and_port_state_are_bounded_by_wait_for(monkeypatch):
    """Каждое сетевое ожидание в discovery ограничено wait_for — даже если
    таймаут ужат до долей секунды, результат приходит быстро."""
    monkeypatch.setattr(discovery, "PROBE_TIMEOUT", 0.3)
    monkeypatch.setattr(discovery, "PORT_TIMEOUT", 0.3)
    server, handlers, _ = await _silent_server()
    port = server.sockets[0].getsockname()[1]
    try:
        t0 = time.perf_counter()
        r = await discovery._probe("x", f"http://127.0.0.1:{port}/v1")
        assert r["ok"] is False
        assert time.perf_counter() - t0 < 2.0
        assert await asyncio.wait_for(discovery._port_state(f"http://127.0.0.1:{port}/v1"),
                                      2.0) is True
    finally:
        await _close_silent(server, handlers)
