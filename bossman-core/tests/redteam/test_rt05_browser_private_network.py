"""RT-05: браузер обязан подчиняться той же политике назначения, что и HTTP.

`browser.open` проверял только схему и `domain_risk`, поэтому синтетическая
служба на 127.0.0.1 открывалась, и её содержимое читалось со страницы. Это пивот
во внутреннюю сеть: у HTTP-инструмента запрет loopback/RFC1918/link-local/
metadata давно есть, а у браузера его не было.

Сеть здесь только синтетическая — локальный HTTP-сервер на эфемерном порту.
Наружу тесты не ходят. Проверка политики не требует запуска Chromium: браузерный
маршрутизатор и `browser.open` спрашивают одну и ту же функцию.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bossman.toolkit.browser import destination_refusal

CANARY = "CANARY-PRIVATE-SERVICE"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        body = f"<html><body>{CANARY}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                           # тишина в отчёте
        pass


@pytest.fixture()
def private_service():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/",
    "http://localhost:8080/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]:8080/",
    "http://0.0.0.0:8080/",
    "http://metadata.google.internal/",
])
def test_private_and_metadata_destinations_are_refused(url):
    assert destination_refusal(url), f"{url} прошёл политику назначения"


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/hostname",
                                 "chrome://settings"])
def test_non_web_schemes_are_refused(url):
    assert destination_refusal(url)


def test_browser_internal_pages_are_not_treated_as_destinations():
    assert destination_refusal("about:blank") == ""


def test_the_live_private_service_is_refused(private_service):
    """Служба действительно поднята и отвечает — и всё равно закрыта."""
    assert destination_refusal(f"http://127.0.0.1:{private_service}/")
    assert destination_refusal(f"http://localhost:{private_service}/")


def test_an_exact_owner_allowlisted_origin_is_permitted(private_service, monkeypatch):
    """Разрешение выдаётся РОВНО службе host:port, а не «localhost вообще»."""
    monkeypatch.setenv("BOSSMAN_BROWSER_ALLOW_ORIGINS", f"127.0.0.1:{private_service}")
    assert destination_refusal(f"http://127.0.0.1:{private_service}/") == ""
    # другой порт того же хоста — по-прежнему отказ
    assert destination_refusal(f"http://127.0.0.1:{private_service + 1}/")
    # и другой приватный хост тоже
    assert destination_refusal("http://10.0.0.5/")


def test_allowlisting_one_service_does_not_open_the_whole_loopback(private_service,
                                                                   monkeypatch):
    monkeypatch.setenv("BOSSMAN_BROWSER_ALLOW_ORIGINS", f"127.0.0.1:{private_service}")
    assert destination_refusal("http://127.0.0.1:22/")
    assert destination_refusal("http://localhost:5432/")
