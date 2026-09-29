"""RC19 audit P1-5: a companion started before a Command Center restart is still seen.

``_PROC`` lives only in the server's memory. After a restart the companion
kept polling (its own process group), the settings page said "stopped", Stop
did nothing, and token rotation/revocation claimed the old token no longer
polled. A real child process holding the real poller lock stands in for the
orphan here; no Telegram, no network.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from bcc.features import telegram_settings as ts
from bcc.secrets import Vault

from .test_telegram_settings import TOKEN, body, models_transport

ROOT = Path(__file__).resolve().parents[1]
ORPHAN = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from bcc.telegram_companion.store import single_instance
with single_instance(Path(sys.argv[2])):
    print("ready", flush=True)
    time.sleep(120)
"""


@pytest.fixture
def tg(tmp_path, monkeypatch):
    path = tmp_path / "tg" / "config.json"
    monkeypatch.setenv("BOSSMAN_TELEGRAM_CONFIG", str(path))
    monkeypatch.setattr(ts, "MODELS_TRANSPORT", models_transport())
    monkeypatch.setattr(ts, "TELEGRAM_TRANSPORT", httpx.MockTransport(
        lambda r: pytest.fail("Telegram must not be called")))
    monkeypatch.setattr(ts, "_PROC", {"proc": None, "started": None, "log": None})
    yield path


@pytest.fixture
def orphan(tg):
    """A companion launched by a previous server: holds the lock, unknown to _PROC."""
    proc = subprocess.Popen([sys.executable, "-c", ORPHAN, str(ROOT), str(tg.parent)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "ready", proc.stderr.read()
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


async def test_status_reports_a_companion_started_before_a_restart(env, orphan):
    status = (await env.client.get("/api/telegram/status")).json()
    assert status["state"] == "running" and status["managed"] is False
    import psutil
    # A venv python.exe on Windows is a launcher: the lock holder is its child.
    family = {orphan.pid, *(c.pid for c in psutil.Process(orphan.pid).children(recursive=True))}
    assert status["pid"] in family


async def test_stop_stops_the_orphan_and_start_never_duplicates_it(env, tg, orphan, monkeypatch):
    await env.client.put("/api/telegram/settings", json=body())
    monkeypatch.setattr(ts, "_command", lambda path: pytest.fail("second companion launched"))
    assert (await env.client.post("/api/telegram/start")).json()["managed"] is False
    status = (await env.client.post("/api/telegram/stop")).json()
    assert orphan.wait(timeout=10) is not None
    assert status["state"] == "stopped"


async def test_token_rotation_refuses_while_an_unstoppable_poller_holds_the_token(env, tg, orphan):
    await env.client.put("/api/telegram/settings", json=body())
    # poller.json that does not prove the holder (creation time mismatch): never kill blindly.
    info = tg.parent / "poller.json"
    data = json.loads(info.read_text(encoding="utf-8"))
    info.write_text(json.dumps({**data, "created": 1.0}), encoding="utf-8")
    new_token = "654321" + "987:" + "Gy" * 18       # fixture shape only
    r = await env.client.post("/api/telegram/token", json={"bot_token": new_token})
    assert r.status_code == 409
    assert orphan.poll() is None
    home = tg.parent
    saved = json.loads(Vault(home).decrypt((home / "credentials.enc").read_text(encoding="utf-8")))
    assert saved["bot_token"] == TOKEN


async def test_revocation_stops_the_orphan_that_polls_the_old_token(env, tg, orphan):
    await env.client.put("/api/telegram/settings", json=body())
    r = (await env.client.delete("/api/telegram/token")).json()
    assert r["revoked"] is True and r["poller_running"] is False
    deadline = time.monotonic() + 10
    while orphan.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert orphan.poll() is not None
