"""Music Studio: honest service state and the Start/Stop lifecycle.

The owner saw "ConnectError: All connection attempts failed" because nothing told
him ACE-Step was not installed / not started. These tests use a REAL local fake
ACE-Step process (a tiny HTTP server with the upstream /health envelope) instead of
mocks, so sockets, the child process and the tree kill are all exercised. No real
model is claimed here: REAL_LOCAL_MODEL needs the hardware run in docs/music.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import psutil
import pytest

from bcc.features import music_studio as music

FAKE_SERVER = textwrap.dedent('''\
    import argparse, json, os, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args, _ = parser.parse_known_args()
    dump = os.environ.get("FAKE_ACESTEP_ENVDUMP")
    if dump:
        with open(dump, "w", encoding="utf-8") as fh:
            json.dump(dict(os.environ), fh)
    if os.environ.get("FAKE_ACESTEP_CRASH"):
        print("boom: fake CUDA out of memory", flush=True)
        sys.exit(3)
    time.sleep(float(os.environ.get("FAKE_ACESTEP_BIND_AFTER", "0")))
    born = time.time()
    ready_after = float(os.environ.get("FAKE_ACESTEP_READY_AFTER", "0"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_error(404)
                return
            body = {"data": {"status": "ok", "service": "ACE-Step API", "version": "1.0",
                             "models_initialized": time.time() - born >= ready_after,
                             "llm_initialized": True, "loaded_model": "acestep-v15-turbo",
                             "loaded_lm_model": None},
                    "code": 200, "error": None, "timestamp": 1, "extra": None}
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    HTTPServer((args.host, args.port), Handler).serve_forever()
''')


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def port_listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(autouse=True)
def clean_music_state(monkeypatch):
    """No test may see another test's child process or the real machine's install."""
    monkeypatch.delenv("BOSSMAN_ACESTEP_PYTHON", raising=False)
    monkeypatch.delenv("BOSSMAN_ACESTEP_DIR", raising=False)
    monkeypatch.delenv("BOSSMAN_ACESTEP_API_KEY", raising=False)
    music._service = None
    music._last_exit = None
    yield
    rec = music._service
    if rec is not None:
        with contextlib.suppress(Exception):
            music._kill_tree(rec.proc, grace=2.0)
    music._service = None
    music._last_exit = None


@pytest.fixture
def no_install(tmp_path, monkeypatch):
    port = free_port()
    monkeypatch.setenv("BOSSMAN_ACESTEP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(music, "_install_candidates", lambda: [tmp_path / "nothing-here"])
    return SimpleNamespace(port=port)


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    """A complete 'installation' whose api_server.py is the tiny fake above."""
    root = tmp_path / "acestep"
    script = root / "repo" / "acestep" / "api_server.py"
    script.parent.mkdir(parents=True)
    script.write_text(FAKE_SERVER, encoding="utf-8")
    port = free_port()
    monkeypatch.setenv("BOSSMAN_ACESTEP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("BOSSMAN_ACESTEP_PYTHON", sys.executable)
    monkeypatch.setattr(music, "_install_candidates", lambda: [root])
    dump = tmp_path / "child-env.json"
    monkeypatch.setenv("FAKE_ACESTEP_ENVDUMP", str(dump))
    return SimpleNamespace(root=root, port=port, dump=dump, script=script)


class ForeignServer:
    """Something on the port that Bossman did not start. `body` answers GET /health."""

    def __init__(self, body: bytes):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *args):
                pass

        self.body = body
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def foreign(monkeypatch):
    servers = []

    def make(body: bytes) -> ForeignServer:
        server = ForeignServer(body)
        servers.append(server)
        monkeypatch.setenv("BOSSMAN_ACESTEP_URL", f"http://127.0.0.1:{server.port}")
        return server

    yield make
    for server in servers:
        server.close()


async def get_health(env) -> dict:
    response = await env.client.get("/api/music/health")
    assert response.status_code == 200, response.text
    return response.json()


async def wait_status(env, wanted: set[str], timeout: float = 30.0) -> tuple[dict, list[str]]:
    """Poll /health until its status is in `wanted`; also return every status seen."""
    deadline = time.monotonic() + timeout
    seen: list[str] = []
    body: dict = {}
    while time.monotonic() < deadline:
        body = await get_health(env)
        if not seen or seen[-1] != body["status"]:
            seen.append(body["status"])
        if body["status"] in wanted:
            return body, seen
        await asyncio.sleep(0.25)
    raise AssertionError(f"status never reached {wanted}; saw {seen}; last={body}")


# --------------------------------------------------------------------------- health states

async def test_not_installed_is_named_and_actionable(env, no_install):
    body = await get_health(env)
    assert body["status"] == "NOT_INSTALLED"
    assert body["installed"] is False and body["can_start"] is False
    assert body["action"] == "install"
    assert "не установлен" in body["reason"]
    assert "MUSIC_STUDIO.md" in body["remedy"] and body["install_command"]
    # The raw transport error must not be what the owner reads.
    assert "ConnectError" not in json.dumps(body, ensure_ascii=False)
    assert "All connection attempts failed" not in json.dumps(body, ensure_ascii=False)


async def test_installed_but_not_started_offers_start(env, fake_install):
    body = await get_health(env)
    assert body["status"] == "NOT_RUNNING"
    assert body["installed"] is True and body["can_start"] is True
    assert body["action"] == "start"
    assert "не запущен" in body["reason"]
    assert "Запустить ACE-Step" in body["remedy"]
    assert "ConnectError" not in json.dumps(body, ensure_ascii=False)


async def test_incomplete_install_names_what_is_missing(env, tmp_path, monkeypatch):
    root = tmp_path / "half"
    (root / "repo" / "acestep").mkdir(parents=True)
    (root / "repo" / "acestep" / "api_server.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("BOSSMAN_ACESTEP_URL", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setattr(music, "_install_candidates", lambda: [root])
    body = await get_health(env)
    assert body["status"] == "NOT_INSTALLED"
    assert "venv" in body["reason"]


async def test_full_lifecycle_not_running_starting_not_ready_ready_stop(env, fake_install, monkeypatch):
    monkeypatch.setenv("FAKE_ACESTEP_BIND_AFTER", "3")
    monkeypatch.setenv("FAKE_ACESTEP_READY_AFTER", "2")
    assert (await get_health(env))["status"] == "NOT_RUNNING"

    response = await env.client.post("/api/music/service/start")
    assert response.status_code == 200, response.text
    started = response.json()
    assert started["ok"] is True and started["started"] is True and started["pid"]
    assert started["status"] == "STARTING"

    ready, seen = await wait_status(env, {"READY"}, timeout=40)
    assert seen[0] == "STARTING", seen            # process alive, port not open yet
    assert "NOT_READY" in seen, seen              # port open, models not initialised yet
    assert ready["running"] is True and ready["pid"] == started["pid"]
    assert ready["loaded_model"] == "acestep-v15-turbo"

    second = (await env.client.post("/api/music/service/start")).json()
    assert second["started"] is False and second["already_running"] is True
    assert second["pid"] == started["pid"]

    stopped = (await env.client.post("/api/music/service/stop")).json()
    assert stopped["ok"] is True and stopped["stopped"] is True
    assert not psutil.pid_exists(started["pid"])
    assert not port_listening(fake_install.port)
    assert (await get_health(env))["status"] == "NOT_RUNNING"


async def test_generic_ok_json_on_the_port_is_not_ready(env, foreign):
    """Negative control: the old probe called ANY 2xx 'READY'."""
    foreign(json.dumps({"hello": "world"}).encode())
    body = await get_health(env)
    assert body["status"] == "NOT_READY"
    assert "ACE-Step" in body["reason"] and "не похож" in body["reason"]


async def test_acestep_with_models_loading_is_not_ready_then_ready(env, foreign):
    envelope = {"data": {"status": "ok", "models_initialized": False, "llm_initialized": False},
                "code": 200, "error": None}
    server = foreign(json.dumps(envelope).encode())
    body = await get_health(env)
    assert body["status"] == "NOT_READY"
    assert "модел" in body["reason"]
    envelope["data"].update(models_initialized=True, llm_initialized=True,
                            loaded_model="acestep-v15-turbo")
    server.body = json.dumps(envelope).encode()
    body = await get_health(env)
    assert body["status"] == "READY" and body["loaded_model"] == "acestep-v15-turbo"


# --------------------------------------------------------------------------- start / stop safety

async def test_start_refused_when_not_installed(env, no_install):
    response = await env.client.post("/api/music/service/start")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "MUSIC_NOT_INSTALLED"
    assert "MUSIC_STUDIO.md" in error["hint"]
    assert music._service is None


async def test_start_refused_when_port_held_by_a_foreign_process_and_stop_does_not_touch_it(env, foreign, tmp_path, monkeypatch):
    server = foreign(b"{}")
    root = tmp_path / "acestep"
    script = root / "repo" / "acestep" / "api_server.py"
    script.parent.mkdir(parents=True)
    script.write_text(FAKE_SERVER, encoding="utf-8")
    monkeypatch.setenv("BOSSMAN_ACESTEP_PYTHON", sys.executable)
    monkeypatch.setattr(music, "_install_candidates", lambda: [root])
    response = await env.client.post("/api/music/service/start")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MUSIC_PORT_BUSY"
    assert music._service is None
    stopped = (await env.client.post("/api/music/service/stop")).json()
    assert stopped["stopped"] is False and stopped["owned"] is False and stopped["port_busy"] is True
    assert port_listening(server.port), "the foreign process must be left alone"


async def test_start_and_health_refuse_a_non_loopback_url(env, fake_install, monkeypatch):
    monkeypatch.setenv("BOSSMAN_ACESTEP_URL", "http://10.1.2.3:8001")
    body = await get_health(env)
    assert body["status"] == "NOT_CONFIGURED" and "loopback" in body["reason"]
    assert body["can_start"] is False
    response = await env.client.post("/api/music/service/start")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MUSIC_NOT_LOOPBACK"
    assert music._service is None


async def test_child_environment_is_scrubbed_and_gfx_override_absent(env, fake_install, monkeypatch):
    monkeypatch.setenv("BCC_SECRET_PROBE", "must-not-leak")
    monkeypatch.setenv("SOME_SERVICE_API_KEY", "must-not-leak")
    monkeypatch.setenv("BOSSMAN_ACESTEP_API_KEY", "local-key")
    monkeypatch.delenv("HSA_OVERRIDE_GFX_VERSION", raising=False)
    started = (await env.client.post("/api/music/service/start")).json()
    assert started["ok"] is True
    deadline = time.monotonic() + 15
    while not fake_install.dump.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    child_env = json.loads(fake_install.dump.read_text(encoding="utf-8"))
    names = set(child_env)
    assert "BCC_SECRET_PROBE" not in names and "SOME_SERVICE_API_KEY" not in names
    assert "BOSSMAN_ACESTEP_API_KEY" not in names
    assert "ACESTEP_API_KEY" in names            # the service is protected by the same local key
    assert {"ACESTEP_NO_INIT", "ACESTEP_LM_BACKEND", "MIOPEN_FIND_MODE"} <= names
    # 4B LM crashed natively on the owner's gfx1151 and costs +8.4 GB: 1.7B is the default.
    assert child_env["ACESTEP_LM_MODEL_PATH"] == "acestep-5Hz-lm-1.7B"
    # gfx1151 works natively; forcing the RDNA3 gfx1100 override (upstream .bat) is wrong here.
    assert "HSA_OVERRIDE_GFX_VERSION" not in names
    # The recorded command line must not carry the key (it is written to disk and to the log).
    assert "local-key" not in " ".join(started["command"])


async def test_crashing_service_reports_exit_and_log(env, fake_install, monkeypatch):
    monkeypatch.setenv("FAKE_ACESTEP_CRASH", "1")
    response = await env.client.post("/api/music/service/start")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False and body["reason"] == "exited" and body["exit_code"] == 3
    assert any("boom" in line for line in body["log_tail"])
    health = await get_health(env)
    assert health["status"] == "NOT_RUNNING"
    assert "завершил" in health["reason"] and "3" in health["reason"]
    assert any("boom" in line for line in health["log_tail"])


# --------------------------------------------------------------------------- generation errors

async def test_generate_without_service_is_an_actionable_russian_error(env, no_install):
    response = await env.client.post("/api/music/generate", json={"prompt": "dark phonk instrumental"})
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "MUSIC_NOT_INSTALLED"
    assert "ConnectError" not in json.dumps(error, ensure_ascii=False)
    assert "не установлен" in error["message"] and error["hint"]


async def test_generate_with_installed_but_stopped_service_points_to_start(env, fake_install):
    response = await env.client.post("/api/music/generate", json={"prompt": "dark phonk instrumental"})
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "MUSIC_NOT_RUNNING"
    assert "Запустить ACE-Step" in error["hint"]
    assert "ConnectError" not in json.dumps(error, ensure_ascii=False)


# --------------------------------------------------------------------------- log readability

def test_log_tail_is_readable_after_a_native_crash(tmp_path):
    log = tmp_path / "acestep.log"
    frame = "0x00007FF85347{:04X}, C:\\venv\\torch_cpu.dll(0x00007FF852BA0000) + 0x8D3011 byte(s)\n"
    lines = ["[bossman] запуск\n",
             "Loading shards:   0%|  | 0/2\rLoading shards:  50%|#####| 1/2Exception Code: 0xC0000005\n",
             *(frame.format(i) for i in range(300)),
             "config api_key=sk-secretsecretsecret leaked\n"]
    log.write_bytes("".join(lines).encode("utf-8"))
    tail = music._log_tail(log, 10)
    assert any("Exception Code: 0xC0000005" in row for row in tail)
    assert not any(row.startswith("0x0000") for row in tail), "native frames are noise"
    assert not any("0/2" in row for row in tail), "only the last redraw of a progress bar"
    assert "sk-secretsecretsecret" not in " ".join(tail)
