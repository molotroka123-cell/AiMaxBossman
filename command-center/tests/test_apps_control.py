"""Запуск и остановка приложений из дашборда: тесты про порождение процессов.

Здесь почти нет подмен. Модуль порождает настоящие процессы в системе, поэтому
подменённый Popen доказывал бы только то, что подмена работает: приложение
запускается настоящим `python -m ...`, слушает настоящий порт и настоящим же
образом умирает. Подменяются только выдержки времени — чтобы тест про «не
поднялось» не ждал полминуты.
"""
from __future__ import annotations

import inspect
import json
import socket
import sys
import time
from pathlib import Path

import httpx
import pytest

from bcc.features import apps as apps_mod
from bcc.features import apps_control as ctl

# ------------------------------------------------------------------ фальшивые приложения

# Настоящий дочерний процесс: слушает порт, отвечает на /health и попутно
# сохраняет свои argv, окружение и cwd — по этому слепку тест проверяет, что в
# командную строку не попало ничего из запроса, а секреты ядра — в окружение.
SERVER_CLI = '''
import http.server, json, os, sys

HERE = os.path.abspath(__file__)
APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    with open(os.path.join(APP_DIR, "spawn.json"), "w", encoding="utf-8") as fh:
        json.dump({"argv": sys.argv, "env": dict(os.environ), "cwd": os.getcwd()}, fh)
    if len(sys.argv) < 2 or sys.argv[1] != "serve":
        print("ожидалась команда serve", flush=True)
        return 2
    port = int(os.environ.get("APP_PORT", "0"))
    print("fake app listening on", port, flush=True)
    http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

# Живой процесс, который никогда не откроет порт: проверка «не поднялось».
# Первая строка нарочно печатает секрет — хвост журнала уходит в браузер.
HUNG_CLI = '''
import time

print("api_key=SUPERSECRET123", flush=True)
print("не могу открыть базу данных, жду вечно", flush=True)
time.sleep(120)
'''

# Процесс, который падает сразу: проверка «завершилось с кодом».
BROKEN_CLI = '''
import sys

print("boom: конфигурация не найдена", flush=True)
raise SystemExit(3)
'''


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def make_app(root: Path, app_id: str, *, package: str = "fakepkg", port: int | None = None,
             cli: str = SERVER_CLI, script: str | None = None) -> tuple[Path, int]:
    """Приложение с настоящей раскладкой пакета: src/<package>/cli.py + pyproject."""
    port = port or _free_port()
    script = script or app_id
    app_dir = root / app_id
    pkg_dir = app_dir / "src" / package
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "cli.py").write_text(cli, encoding="utf-8")
    (app_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{app_id}"\nversion = "0.1"\n\n'
        f'[project.scripts]\n{script} = "{package}.cli:main"\n', encoding="utf-8")
    (app_dir / "app.manifest.yaml").write_text(
        f"id: {app_id}\nname: {app_id}\nversion: '1.0'\ndefault_port: {port}\n"
        f"entrypoints:\n  cli: {script}\n"
        f"ui:\n  health_path: /health\n  order: 1\n", encoding="utf-8")
    return app_dir, port


# ------------------------------------------------------------------ фикстуры

@pytest.fixture
def apps_root(tmp_path, monkeypatch):
    """Каталог приложений живёт в tmp: настоящие приложения владельца тесты
    не запускают ни при каких условиях."""
    root = tmp_path / "apps-under-test"
    root.mkdir()
    monkeypatch.setattr(apps_mod, "APPS_DIR", root)
    monkeypatch.setattr(apps_mod, "_cache", {"at": 0.0, "apps": []})
    monkeypatch.setattr(ctl, "READY_TIMEOUT", 8.0)
    return root


@pytest.fixture(autouse=True)
def no_orphans():
    """Ни один тест не имеет права оставить после себя живой процесс."""
    yield
    for rec in list(ctl._processes.values()):
        try:
            rec.proc.kill()
            rec.proc.wait(timeout=5)
        except Exception:                      # noqa: BLE001 — уборка не должна ронять прогон
            pass
        ctl._forget(rec)
    ctl._processes.clear()


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv(ctl.FLAG, "1")


async def _get(url: str) -> httpx.Response:
    """Локальный адрес без прокси — тем же приёмом, что и _probe в apps.py."""
    async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
        return await client.get(url)


def _spawn_dump(app_dir: Path, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    path = app_dir / "spawn.json"
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        time.sleep(0.05)
    raise AssertionError("дочерний процесс не оставил слепка запуска")


# ------------------------------------------------------------------ флаг

async def test_start_refused_while_flag_off_and_nothing_is_spawned(env, apps_root, monkeypatch):
    """С выключенным флагом запуск отклонён, процесс не порождён, порт свободен."""
    monkeypatch.delenv(ctl.FLAG, raising=False)
    _, port = make_app(apps_root, "fake-app")

    res = await env.client.post("/api/apps/fake-app/start")

    assert res.status_code == 409
    assert ctl.FLAG in res.json()["error"]["hint"]
    assert ctl._processes == {}
    assert ctl.port_busy(port) is False


async def test_stop_refused_while_flag_off(env, apps_root, monkeypatch):
    """Гасить процессы без разрешения владельца тоже нельзя."""
    monkeypatch.delenv(ctl.FLAG, raising=False)
    make_app(apps_root, "fake-app")

    res = await env.client.post("/api/apps/fake-app/stop")

    assert res.status_code == 409


async def test_process_endpoint_answers_honestly_while_flag_off(env, apps_root, monkeypatch):
    """Чтение состояния доступно всегда: человек должен видеть «выключено» до нажатия."""
    monkeypatch.delenv(ctl.FLAG, raising=False)
    make_app(apps_root, "fake-app")

    res = await env.client.get("/api/apps/fake-app/process")

    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is False
    assert body["running"] is False and body["owned"] is False


# ------------------------------------------------------------------ идентификатор

@pytest.mark.parametrize("app_id", [
    "..", "../..", "../fake-app", "fake-app/../fake-app", "..\\..",
    "/etc", "/tmp/fake-app", "fake-app\x00", "", "unknown-app",
])
def test_traversal_and_unknown_ids_resolve_to_nothing(apps_root, app_id):
    """Ни одна форма обхода каталогов не находит приложения: поиск идёт по
    словарю обнаруженных манифестов, а не по склейке пути с вводом."""
    make_app(apps_root, "fake-app")
    assert ctl.find_app_dir("fake-app") is not None      # контроль: настоящий id находится
    assert ctl.find_app_dir(app_id) is None


async def test_foreign_ids_are_rejected_by_the_endpoint_without_spawning(env, apps_root, flag_on):
    """Запрос с чужим или подставленным id отклонён, и ни один процесс не порождён."""
    make_app(apps_root, "fake-app")

    for path in ("/api/apps/unknown-app/start",
                 "/api/apps/%2E%2E/start",
                 "/api/apps/%2E%2E%2Ffake-app/start",
                 "/api/apps/%2Fetc/start"):
        res = await env.client.post(path)
        assert res.status_code >= 400, path

    assert ctl._processes == {}


def test_app_dir_must_match_manifest_id(apps_root):
    """Каталог, чьё имя разошлось с id в манифесте, не запускается: гадать,
    что здесь приложение, модуль порождения процессов не имеет права."""
    app_dir, _ = make_app(apps_root, "fake-app")
    (app_dir / "app.manifest.yaml").write_text(
        "id: other-app\nname: other\nversion: '1.0'\ndefault_port: 8999\n", encoding="utf-8")

    assert ctl.find_app_dir("fake-app") is None
    assert ctl.find_app_dir("other-app") is None


# ------------------------------------------------------------------ запуск

async def test_start_gives_a_live_process_visible_in_process_endpoint(env, apps_root, flag_on):
    """Запуск даёт живой отвечающий процесс, и он виден в GET /process."""
    _, port = make_app(apps_root, "fake-app")

    res = await env.client.post("/api/apps/fake-app/start")
    body = res.json()

    assert res.status_code == 200 and body["ok"] is True
    assert body["started"] is True and body["ready"] is True
    pid = body["pid"]
    assert pid and ctl._processes["fake-app"].proc.poll() is None

    health = await _get(f"http://127.0.0.1:{port}/health")
    assert health.status_code == 200

    info = (await env.client.get("/api/apps/fake-app/process")).json()
    assert info["owned"] is True and info["running"] is True
    assert info["pid"] == pid
    assert info["uptime_seconds"] >= 0
    assert info["port"] == port and info["port_busy"] is True


async def test_second_start_does_not_spawn_a_second_process(env, apps_root, flag_on):
    """Повторный запуск честно отвечает «уже запущено» и не плодит второй процесс."""
    make_app(apps_root, "fake-app")

    first = (await env.client.post("/api/apps/fake-app/start")).json()
    assert first["started"] is True
    proc = ctl._processes["fake-app"].proc

    second = (await env.client.post("/api/apps/fake-app/start")).json()

    assert second["already_running"] is True and second["started"] is False
    assert second["pid"] == first["pid"]
    assert "уже запущено" in second["message"]
    assert ctl._processes["fake-app"].proc is proc      # тот же объект, второго нет
    assert len(ctl._processes) == 1


async def test_log_keeps_child_output_and_start_writes_command(env, apps_root, flag_on):
    """Вывод процесса не теряется: он лежит в data_dir/apps/<id>.log."""
    make_app(apps_root, "fake-app")

    body = (await env.client.post("/api/apps/fake-app/start")).json()

    log = Path(body["log_path"])
    assert log == Path(env.settings.data_dir) / "apps" / "fake-app.log"
    text = log.read_text(encoding="utf-8")
    assert "запуск:" in text and "fake app listening on" in text


# ------------------------------------------------------------------ argv и секреты

async def test_nothing_from_the_request_reaches_argv(env, apps_root, flag_on, monkeypatch):
    """В argv только интерпретатор, модуль из раскладки пакета и `serve`;
    идентификатора из запроса там нет, а секреты ядра не уходят в окружение."""
    monkeypatch.setenv("BOSSMAN_TEST_API_KEY", "SUPERSECRET123")
    app_dir, _ = make_app(apps_root, "fake-app", package="fakepkg")

    body = (await env.client.post("/api/apps/fake-app/start")).json()
    assert body["ok"] is True

    assert body["command"] == [sys.executable, "-m", "fakepkg.cli", "serve"]
    assert not any("fake-app" in part for part in body["command"])
    dump = _spawn_dump(app_dir)
    # argv[0] дочернему процессу подставляет сам интерпретатор (путь к cli.py),
    # поэтому проверяем то, что передали мы: всё после него.
    assert dump["argv"][1:] == ["serve"]
    assert Path(dump["cwd"]).resolve() == app_dir.resolve()

    assert "BOSSMAN_TEST_API_KEY" not in dump["env"]
    assert not any("SUPERSECRET123" in v for v in dump["env"].values())

    source = inspect.getsource(ctl)
    assert "shell=True" not in source                   # запуск только argv-списком


def test_manual_command_uses_module_not_a_missing_console_script(apps_root):
    """Запасная команда для владельца исполнима: консольного скрипта у него нет."""
    make_app(apps_root, "fake-app", package="fakepkg")

    command = ctl.command_for("fake-app")

    assert command["manual"] == "cd apps/fake-app && PYTHONPATH=src python -m fakepkg.cli serve"
    assert "fake-app serve" not in command["manual"]


def test_log_tail_masks_secrets(tmp_path):
    """Секрет, напечатанный приложением, не уходит в ответ ручки как есть."""
    path = tmp_path / "app.log"
    path.write_text("api_key=SUPERSECRET123\nвсё хорошо\ntoken: abcdef123456\n",
                    encoding="utf-8")

    tail = ctl.log_tail(path)

    assert not any("SUPERSECRET123" in row for row in tail)
    assert not any("abcdef123456" in row for row in tail)
    assert "всё хорошо" in tail


# ------------------------------------------------------------------ остановка

async def test_stop_kills_our_process_and_frees_the_port(env, apps_root, flag_on):
    """Остановка гасит наш процесс: порт освобождён, запись из реестра ушла."""
    _, port = make_app(apps_root, "fake-app")
    started = (await env.client.post("/api/apps/fake-app/start")).json()
    proc = ctl._processes["fake-app"].proc

    body = (await env.client.post("/api/apps/fake-app/stop")).json()

    assert body["stopped"] is True and body["owned"] is True
    assert body["pid"] == started["pid"]
    assert proc.poll() is not None
    assert "fake-app" not in ctl._processes
    assert ctl.port_busy(port) is False

    info = (await env.client.get("/api/apps/fake-app/process")).json()
    assert info["running"] is False and info["owned"] is False


async def test_foreign_server_on_the_port_is_neither_adopted_nor_stopped(env, apps_root, flag_on):
    """Чужой сервер на порту приложения не считается нашим: запуск отклонён,
    остановка его не трогает."""
    port = _free_port()
    make_app(apps_root, "fake-app", port=port)
    foreign = socket.socket()
    foreign.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    foreign.bind(("127.0.0.1", port))
    foreign.listen(5)
    try:
        start = await env.client.post("/api/apps/fake-app/start")
        assert start.status_code == 409
        assert "не запускал" in start.json()["error"]["message"]
        assert ctl._processes == {}

        stop = (await env.client.post("/api/apps/fake-app/stop")).json()
        assert stop["stopped"] is False and stop["owned"] is False
        assert stop["port_busy"] is True

        assert ctl.port_busy(port) is True          # чужой слушатель на месте
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            pass
    finally:
        foreign.close()


# ------------------------------------------------------------------ неудачный запуск

async def test_app_that_never_answers_reports_failure_with_log_tail(env, apps_root, flag_on,
                                                                    monkeypatch):
    """Приложение, которое не поднялось, даёт внятный отказ с хвостом журнала,
    а не молчание до бесконечности."""
    monkeypatch.setattr(ctl, "READY_TIMEOUT", 1.5)
    make_app(apps_root, "hung-app", cli=HUNG_CLI)

    body = (await env.client.post("/api/apps/hung-app/start")).json()

    assert body["ok"] is False and body["ready"] is False
    assert body["reason"] == "not_ready"
    assert "не ответило" in body["message"]
    assert body["log_tail"], "хвост журнала пуст — владельцу не на что смотреть"
    assert any("не могу открыть базу данных" in row for row in body["log_tail"])
    assert not any("SUPERSECRET123" in row for row in body["log_tail"])


async def test_app_that_exits_immediately_reports_exit_code(env, apps_root, flag_on):
    """Упавшее сразу приложение отдаёт код возврата и свои последние строки."""
    make_app(apps_root, "broken-app", cli=BROKEN_CLI)

    body = (await env.client.post("/api/apps/broken-app/start")).json()

    assert body["ok"] is False and body["reason"] == "exited"
    assert body["exit_code"] == 3
    assert any("boom" in row for row in body["log_tail"])
    assert "broken-app" not in ctl._processes            # мёртвый процесс не наш

    info = (await env.client.get("/api/apps/broken-app/process")).json()
    assert info["running"] is False


async def test_app_without_entrypoint_is_refused_before_spawning(env, apps_root, flag_on):
    """Приложение, для которого команду вывести не из чего, не запускается вслепую."""
    app_dir, _ = make_app(apps_root, "fake-app")
    (app_dir / "pyproject.toml").write_text('[project]\nname = "fake-app"\nversion = "0.1"\n',
                                            encoding="utf-8")

    res = await env.client.post("/api/apps/fake-app/start")

    assert res.status_code == 409
    assert "модуль запуска" in res.json()["error"]["message"]
    assert ctl._processes == {}


# ------------------------------------------------------------------ журнал

def test_trim_log_keeps_the_file_bounded_in_place(tmp_path, monkeypatch):
    """Журнал ограничен по размеру и урезается НА МЕСТЕ: подмена файла оборвала
    бы дескриптор работающего приложения."""
    monkeypatch.setattr(ctl, "LOG_MAX_BYTES", 4096)
    path = tmp_path / "app.log"
    path.write_bytes(b"x" * 20000 + "\nхвост\n".encode())
    inode = path.stat().st_ino

    ctl.trim_log(path)

    assert path.stat().st_size <= 4096
    assert path.stat().st_ino == inode
    assert "хвост" in path.read_text(encoding="utf-8")
