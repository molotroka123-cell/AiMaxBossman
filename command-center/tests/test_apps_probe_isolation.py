"""Опрос карточек приложений: одно медленное или кривое приложение не держит и не гасит остальные.

Воспроизведено 2026-10-08 на 1eac8ac8 (до правки):

* здоровье и метрики спрашивались ПОДРЯД, у каждого запроса свой PROBE_TIMEOUT (1.2 с).
  Настоящий loopback-сервер: health отвечает через 1.0 с, metrics висит — `_probe` держал
  2.21 с (полностью зависшее приложение — 1.21 с: после таймаута health метрики не спрашивались);
* `ui.order: 'first'` в ОДНОМ манифесте — `int()` бросал ValueError внутри `_collect_fresh`,
  и `/api/apps` падал целиком: пропадали карточки ВСЕХ приложений;
* холодный разбор десяти настоящих манифестов (yaml) — ~31 мс синхронно в потоке цикла
  событий, при каждом изменении файла.

Проверки здесь детерминированы (без порогов по времени): одновременность доказывается тем,
что health отвечает ТОЛЬКО когда запрос метрик уже пришёл.
"""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from bcc import portcheck
from bcc.features import apps as apps_mod


@pytest.fixture
def fresh_apps(tmp_path, monkeypatch):
    root = tmp_path / "apps"
    root.mkdir()
    monkeypatch.setattr(apps_mod, "APPS_DIR", root)
    monkeypatch.setattr(apps_mod, "_cache", {"at": 0.0, "apps": []})
    monkeypatch.setattr(apps_mod, "_described", {})
    return root


def _manifest(root, app_id: str, order: str) -> None:
    d = root / app_id
    d.mkdir()
    (d / "app.manifest.yaml").write_text(
        f"id: {app_id}\nname: {app_id}\nui:\n  order: {order}\n", encoding="utf-8")


# ------------------------------------------------------------------ health ∥ metrics

APP = {"id": "x", "port": 8999, "health_path": "/health", "metrics_path": "/metrics"}


async def test_health_and_metrics_are_asked_at_the_same_time():
    metrics_arrived = asyncio.Event()
    overlapped: list[bool] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/metrics":
            metrics_arrived.set()
            return httpx.Response(200, json={"queue": 2})
        try:
            await asyncio.wait_for(metrics_arrived.wait(), 1.0)
            overlapped.append(True)
        except asyncio.TimeoutError:
            overlapped.append(False)          # прежний код: метрики ждали конца health
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await apps_mod._probe(APP, client)
    assert overlapped == [True], "метрики спрашиваются только после ответа health"
    # Форма результата прежняя.
    assert out == {"reachable": True, "status": "LIVE", "detail": "",
                   "health": {"status": "ok"}, "metrics": {"queue": 2}}


async def test_real_client_keeps_one_timeout_per_request_and_overlaps_them(monkeypatch):
    """Настоящий сокет и настоящий `_probe_client`: health отвечает, только увидев запрос
    метрик; метрики не отвечают никогда. Последовательный опрос получал ReadTimeout на health
    (STOPPED). Параллельный — LIVE, а зависшие метрики обрезает их собственный таймаут."""
    monkeypatch.setattr(apps_mod, "PROBE_TIMEOUT", 0.5)
    monkeypatch.setattr(portcheck, "_cache", None)
    metrics_arrived = asyncio.Event()
    release = asyncio.Event()
    writers: list[asyncio.StreamWriter] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writers.append(writer)
        try:
            line = await reader.readline()
            while (await reader.readline()) not in (b"\r\n", b""):
                pass
            if b"/metrics" in line:
                metrics_arrived.set()
                await release.wait()              # зависшие метрики
                return
            try:
                await asyncio.wait_for(metrics_arrived.wait(), 3.0)
            except asyncio.TimeoutError:
                await release.wait()              # метрик не было — health тоже не отвечает
                return
            body = b'{"status": "ok"}'
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
                         b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        out = await apps_mod._probe({**APP, "port": port})
    finally:
        release.set()
        server.close()
        for w in writers:
            w.close()
    assert out["status"] == "LIVE", out
    assert out["reachable"] is True and out["metrics"] == {}


async def test_a_down_health_still_hides_metrics_that_did_answer():
    """Негативный контроль: параллельный запрос метрик не делает недоступное приложение
    «полуживым» — при ошибке health результат тот же, что и раньше."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"queue": 9})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await apps_mod._probe(APP, client)
    assert out == {"reachable": False, "status": "STOPPED",
                   "detail": "ConnectError: приложение не отвечает на http://127.0.0.1:8999",
                   "health": {}, "metrics": {}}


@pytest.mark.parametrize("metrics_reply", ["error", "not-json", "http-500"])
async def test_broken_metrics_never_spoil_a_healthy_card(metrics_reply):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if metrics_reply == "error":
            raise httpx.ReadTimeout("hung", request=request)
        if metrics_reply == "not-json":
            return httpx.Response(200, text="<html>")
        return httpx.Response(500, json={"queue": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await apps_mod._probe(APP, client)
    assert out == {"reachable": True, "status": "LIVE", "detail": "",
                   "health": {"status": "ok"}, "metrics": {}}


async def test_without_a_metrics_path_only_health_is_asked():
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await apps_mod._probe({**APP, "metrics_path": ""}, client)
    assert asked == ["/health"] and out["status"] == "LIVE" and out["metrics"] == {}


# ------------------------------------------------------------------ один кривой ui.order

@pytest.mark.parametrize("bad", ["'first'", "[1, 2]", ".inf"])
async def test_one_bad_ui_order_moves_only_that_card_to_the_end(fresh_apps, bad):
    _manifest(fresh_apps, "good", "5")
    _manifest(fresh_apps, "texty", "'3'")         # строка-число по-прежнему работает
    _manifest(fresh_apps, "broken", bad)
    apps = await apps_mod.collect(force=True)
    assert [(a["id"], a["order"]) for a in apps] == [("texty", 3), ("good", 5), ("broken", 99)]


# ------------------------------------------------------------------ разбор вне цикла событий

async def test_manifest_parse_runs_off_the_event_loop_and_stays_cached(fresh_apps, monkeypatch):
    for i in range(3):
        _manifest(fresh_apps, f"app{i}", str(i + 1))
    loop_thread = threading.get_ident()
    parsed_on: list[int] = []
    real = apps_mod.yaml.safe_load

    def recording(*a, **kw):
        parsed_on.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(apps_mod.yaml, "safe_load", recording)
    first = await apps_mod.collect(force=True)
    assert [a["id"] for a in first] == ["app0", "app1", "app2"]
    assert len(parsed_on) == 3
    assert loop_thread not in parsed_on, "yaml разбирается в потоке цикла событий"
    await apps_mod.collect(force=True)
    assert len(parsed_on) == 3, "файлы не менялись — повторного разбора быть не должно"
