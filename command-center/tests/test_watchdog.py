"""Сторож настольного запуска: находки на человеческом языке и тишина, когда всё хорошо.

Проверяем поведение, а не устройство: сторожу подкладывают настоящие следы
(файл-отметку с мёртвым процессом, журнал запусков) и настоящий локальный
сервер, а дальше смотрят, что он про них скажет.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from bcc import desktop
from bcc.features import watchdog

# Слова из кода и из журнала: владелец их не видел и видеть не должен.
# Проверяем именно человеческие поля находки; путь к файлу-улике (source) —
# это адрес, который владелец пересылает в поддержку, а не объяснение.
CODE_WORDS = ("lock", "pid", "lifetime", "browser-exit", "stale", "exit code",
              "json", "traceback", "127.0.0.1:0")

NORMAL_LOG = ("2026-09-03T10:00:00 start pid=4242 url=http://127.0.0.1:8800/\n"
              "2026-09-03T10:00:01 browser-argv /usr/bin/chromium --app=http://127.0.0.1:8800/\n"
              "2026-09-03T10:42:11 browser-exit code=0 lifetime=2530.4s url=http://127.0.0.1:8800/\n")


def _human(finding: dict) -> str:
    return " ".join([finding["problem"], finding["evidence"], finding["fix"]]).lower()


def _assert_speaks_human(finding: dict) -> None:
    text = _human(finding)
    for word in CODE_WORDS:
        assert word not in text, f"находка {finding['code']} говорит словом из кода: {word}"
    assert len(finding["problem"]) > 40 and finding["fix"], "находка обязана объяснять и советовать"
    assert finding["severity"] in ("critical", "warning", "info")


def _write_log(data_dir: Path, text: str) -> None:
    (Path(data_dir) / "desktop-run.log").write_text(text, encoding="utf-8")


def _write_marker(data_dir: Path, pid: int, port: int = 8800) -> None:
    """Ровно то, что пишет desktop.run на время жизни окна."""
    (Path(data_dir) / "desktop.lock").write_text(json.dumps({"pid": pid, "port": port}),
                                                 encoding="utf-8")


def _dead_pid() -> int:
    """Заведомо мёртвый процесс: запущен, дождались, забрали номер."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    assert not desktop._pid_alive(proc.pid)
    return proc.pid


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def serve():
    """Настоящий локальный HTTP-сервер: сторож обязан работать против сокета, а не мока."""
    servers: list[HTTPServer] = []

    def start(payload: dict) -> str:
        body = json.dumps(payload).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — имя задано BaseHTTPRequestHandler
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{srv.server_port}/"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def ours(serve):
    return serve({"app": desktop.APP_IDENTITY, "version": "test"})


def _findings(data_dir, base_url) -> list[dict]:
    from dataclasses import asdict
    return [asdict(f) for f in watchdog.collect(Path(data_dir), base_url)]


# ── находки ─────────────────────────────────────────────────────────────────

def test_healthy_state_finds_nothing(env, ours):
    """Нет отметки, журнал с нормальным запуском, сервер отвечает — сторож молчит."""
    _write_log(env.settings.data_dir, NORMAL_LOG)
    assert _findings(env.settings.data_dir, ours) == []


def test_abandoned_marker_of_killed_run_is_reported_in_plain_words(env, ours):
    """Отметка с мёртвым процессом — находка, и объясняется без слов из кода."""
    _write_log(env.settings.data_dir, NORMAL_LOG)
    _write_marker(env.settings.data_dir, _dead_pid())

    found = _findings(env.settings.data_dir, ours)
    assert [f["code"] for f in found] == ["previous_run_killed"]
    _assert_speaks_human(found[0])
    assert found[0]["source"].endswith("desktop.lock")   # владельцу есть что переслать


def test_live_window_marker_is_not_a_finding(env, ours):
    """Открытое прямо сейчас окно — не беда: живой процесс находкой не считается."""
    _write_log(env.settings.data_dir, NORMAL_LOG)
    _write_marker(env.settings.data_dir, os.getpid())
    assert _findings(env.settings.data_dir, ours) == []


def test_window_that_closed_instantly_is_reported(env, ours):
    """«Окно закрылось само» — находка про мгновенно закрывшееся окно."""
    _write_log(env.settings.data_dir,
               "2026-09-03T10:00:00 start pid=4242 url=http://127.0.0.1:8800/\n"
               "2026-09-03T10:00:00 browser-exit code=0 lifetime=0.0s url=http://127.0.0.1:8800/\n")

    found = _findings(env.settings.data_dir, ours)
    assert [f["code"] for f in found] == ["window_closed_instantly"]
    _assert_speaks_human(found[0])
    assert "окно" in found[0]["problem"].lower()
    assert found[0]["severity"] == "critical"


def test_start_without_outcome_is_reported(env, ours):
    """Запуск начался и ничего о себе не сказал — это оборванный запуск."""
    _write_log(env.settings.data_dir,
               "2026-09-03T10:00:00 start pid=4242 url=http://127.0.0.1:8800/\n")

    found = _findings(env.settings.data_dir, ours)
    assert [f["code"] for f in found] == ["start_without_outcome"]
    _assert_speaks_human(found[0])


def test_start_without_outcome_is_silent_while_window_is_open(env, ours):
    """Тот же журнал при живом окне — не находка: запуск ещё идёт."""
    _write_log(env.settings.data_dir,
               "2026-09-03T10:00:00 start pid=4242 url=http://127.0.0.1:8800/\n")
    _write_marker(env.settings.data_dir, os.getpid())
    assert _findings(env.settings.data_dir, ours) == []


def test_foreign_app_on_the_address_is_reported(env, serve):
    """На адресе отвечает не Command Center — открывать там окно нельзя."""
    _write_log(env.settings.data_dir, NORMAL_LOG)
    foreign = serve({"app": "some-other-app", "version": "9"})

    found = _findings(env.settings.data_dir, foreign)
    assert [f["code"] for f in found] == ["address_taken_by_other_app"]
    _assert_speaks_human(found[0])


def test_silent_address_is_reported(env):
    """По адресу не отвечает никто — ярлык откроет пустое окно."""
    _write_log(env.settings.data_dir, NORMAL_LOG)

    found = _findings(env.settings.data_dir, f"http://127.0.0.1:{_free_port()}/")
    assert [f["code"] for f in found] == ["address_silent"]
    _assert_speaks_human(found[0])


def test_window_that_never_opened_is_reported(env, ours):
    """«Открылась только командная строка»: браузер не запустился."""
    _write_log(env.settings.data_dir,
               "2026-09-03T10:00:00 start pid=4242 url=http://127.0.0.1:8800/\n"
               "2026-09-03T10:00:00 browser-launch-failed FileNotFoundError: chrome\n")

    found = _findings(env.settings.data_dir, ours)
    assert [f["code"] for f in found] == ["window_did_not_open"]
    _assert_speaks_human(found[0])


def test_missing_browser_is_reported(env, ours):
    """Браузера на компьютере нет — окну не в чем открыться."""
    _write_log(env.settings.data_dir,
               "2026-09-03T10:00:00 start pid=4242 url=http://127.0.0.1:8800/\n"
               "2026-09-03T10:00:00 exit code=2 no-browser-found\n")

    found = _findings(env.settings.data_dir, ours)
    assert [f["code"] for f in found] == ["browser_not_found"]
    _assert_speaks_human(found[0])


def test_server_that_did_not_start_is_reported(env, ours):
    """Внутренняя часть приложения не поднялась — открывать окно было незачем."""
    _write_log(env.settings.data_dir,
               "2026-09-03T10:00:00 start pid=4242 url=http://127.0.0.1:8800/\n"
               "2026-09-03T10:00:03 exit code=3 server-start-failed OSError: address in use\n")

    found = _findings(env.settings.data_dir, ours)
    assert [f["code"] for f in found] == ["server_did_not_start"]
    _assert_speaks_human(found[0])


def test_probe_ignores_proxy_from_environment(ours, monkeypatch):
    """Прокси из окружения не должен решать за сторожа, кто отвечает на локальном адресе."""
    dead_proxy = f"http://127.0.0.1:{_free_port()}"
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, dead_proxy)
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)

    assert watchdog._probe_port(ours) == "ours"


# ── флаг, tick и история ────────────────────────────────────────────────────

@pytest.fixture
def probe_spy(monkeypatch):
    """Считает обращения к адресу: при выключенном флаге их быть не должно."""
    calls: list[str] = []

    def fake(base_url: str, timeout: float = 1.5) -> str:
        calls.append(base_url)
        return "ours"

    monkeypatch.setattr(watchdog, "_probe_port", fake)
    return calls


async def test_tick_does_nothing_while_flag_is_off(env, probe_spy, monkeypatch):
    """Выключенный флаг = никакой фоновой работы: ни проверки адреса, ни записи."""
    monkeypatch.delenv(watchdog.FLAG, raising=False)
    _write_log(env.settings.data_dir, NORMAL_LOG)
    _write_marker(env.settings.data_dir, _dead_pid())

    await watchdog._tick(env.svc)

    assert probe_spy == []
    assert not (Path(env.settings.data_dir) / watchdog.HISTORY_NAME).exists()


async def test_tick_records_history_only_when_picture_changes(env, probe_spy, monkeypatch):
    """При включённом флаге проверка пишет историю, но не повторяет одно и то же."""
    monkeypatch.setenv(watchdog.FLAG, "1")
    _write_log(env.settings.data_dir, NORMAL_LOG)
    _write_marker(env.settings.data_dir, _dead_pid())

    await watchdog._tick(env.svc)
    await watchdog._tick(env.svc)
    entries = watchdog.read_history(env.settings.data_dir)
    assert len(entries) == 1 and entries[0]["codes"] == ["previous_run_killed"]

    (Path(env.settings.data_dir) / "desktop.lock").unlink()
    await watchdog._tick(env.svc)
    entries = watchdog.read_history(env.settings.data_dir)
    assert len(entries) == 2 and entries[-1]["codes"] == []   # выздоровление тоже видно


# ── ручки ───────────────────────────────────────────────────────────────────

async def test_endpoints_read_while_flag_is_off(env, probe_spy, monkeypatch):
    """Читающие ручки отвечают и при выключенном флаге, честно говоря об этом."""
    monkeypatch.delenv(watchdog.FLAG, raising=False)
    _write_log(env.settings.data_dir, NORMAL_LOG)
    _write_marker(env.settings.data_dir, _dead_pid())

    now = (await env.client.get("/api/watchdog")).json()
    assert now["enabled"] is False and now["healthy"] is False
    assert [f["code"] for f in now["findings"]] == ["previous_run_killed"]
    _assert_speaks_human(now["findings"][0])

    history = (await env.client.get("/api/watchdog/history")).json()
    assert history == {"enabled": False, "entries": [], "count": 0}


async def test_history_endpoint_shows_what_was_found_before(env, probe_spy, monkeypatch):
    """История доступна и после выключения флага: находки не пропадают."""
    monkeypatch.setenv(watchdog.FLAG, "1")
    _write_log(env.settings.data_dir,
               "2026-09-03T10:00:00 start pid=4242 url=http://127.0.0.1:8800/\n"
               "2026-09-03T10:00:00 browser-exit code=0 lifetime=1.2s url=http://127.0.0.1:8800/\n")
    await watchdog._tick(env.svc)

    monkeypatch.delenv(watchdog.FLAG, raising=False)
    history = (await env.client.get("/api/watchdog/history")).json()
    assert history["count"] == 1
    assert history["entries"][0]["codes"] == ["window_closed_instantly"]


async def test_healthy_state_endpoint_reports_nothing_to_fix(env, probe_spy, monkeypatch):
    """Здоровое приложение через ручку выглядит именно здоровым."""
    monkeypatch.delenv(watchdog.FLAG, raising=False)
    _write_log(env.settings.data_dir, NORMAL_LOG)

    now = (await env.client.get("/api/watchdog")).json()
    assert now["healthy"] is True and now["findings"] == []
