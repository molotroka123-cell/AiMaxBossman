"""tools/context_probe.py yields to the owner (owner rule 23.10; incident: the probe held ollama ~10 min while the
owner's pult chat waited and the phone showed «нет связи»). No network: every HTTP path is faked."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import context_probe as cp  # noqa: E402

# The writer's side, loaded by path from this checkout (as the backend writes the marker).
_spec = importlib.util.spec_from_file_location("owner_activity_under_test",
                                               ROOT / "command-center" / "bcc" / "owner_activity.py")
oa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oa)

TARGET = 2000


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    # post_json is patched with raising=False so that, on code without the gate, the old direct urlopen call hits
    # no_network below: the test then fails because the probe really tried to reach ollama, not on a missing name.
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path))

    def no_network(*a, **k):
        raise AssertionError("network used in a unit test")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    return tmp_path


def _answering_send(calls: list):
    """Fake ollama /api/generate: echoes the canary, reports the whole prompt as evaluated."""
    def send(url, body, timeout, should_abort=None):
        calls.append(url)
        prompt = json.loads(body)["prompt"]
        canary = re.search(r"ПАМЯТИ: ([A-Z0-9]{10})\.", prompt).group(1)
        return {"response": canary, "prompt_eval_count": TARGET}
    return send


def _args(*extra):
    return ["--model", "gemma4-31b-uncensored:latest", "--target", str(TARGET), *extra]


def test_probe_refuses_to_start_while_owner_active(data_dir, monkeypatch, capsys):
    calls: list = []
    monkeypatch.setattr(cp, "post_json", _answering_send(calls), raising=False)
    oa.mark("chat_send", data_dir=data_dir, ttl=600)
    assert cp.main(_args()) == 3
    assert calls == [], "nothing may reach ollama while the owner is active"
    assert "owner active" in capsys.readouterr().err
    assert not (data_dir / "context-probes").exists()


def test_probe_runs_when_owner_idle(data_dir, monkeypatch):
    calls: list = []
    monkeypatch.setattr(cp, "post_json", _answering_send(calls), raising=False)
    oa.mark("chat_send", data_dir=data_dir, ttl=600, now=time.time() - 3600)     # an old, expired marker
    assert cp.main(_args()) == 0
    assert calls == ["http://127.0.0.1:11434/api/generate"]
    receipts = list((data_dir / "context-probes").glob("*.json"))
    assert len(receipts) == 1 and json.loads(receipts[0].read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_probe_checks_again_right_before_sending(data_dir, monkeypatch):
    calls: list = []
    monkeypatch.setattr(cp, "post_json", _answering_send(calls), raising=False)
    real_build = cp.build_prompt

    def slow_build(target, canary):          # the owner writes while the big prompt is being built
        oa.mark("chat_send", data_dir=data_dir, ttl=600)
        return real_build(target, canary)

    monkeypatch.setattr(cp, "build_prompt", slow_build)
    assert cp.main(_args()) == 3
    assert calls == []


def test_wait_flag_waits_until_the_owner_is_idle(data_dir, monkeypatch):
    calls: list = []
    monkeypatch.setattr(cp, "post_json", _answering_send(calls), raising=False)
    monkeypatch.setattr(cp, "GATE_POLL_S", 0.05)
    oa.mark("chat_send", data_dir=data_dir, ttl=0.4)
    t0 = time.time()
    assert cp.main(_args("--wait")) == 0
    assert time.time() - t0 >= 0.3 and len(calls) == 1


def test_wait_flag_gives_up_after_max_wait(data_dir, monkeypatch):
    calls: list = []
    monkeypatch.setattr(cp, "post_json", _answering_send(calls), raising=False)
    monkeypatch.setattr(cp, "GATE_POLL_S", 0.05)
    oa.mark("chat_send", data_dir=data_dir, ttl=600)
    assert cp.main(_args("--wait", "--max-wait-s", "0.2")) == 3
    assert calls == []


class _BlockingConn:
    """http.client.HTTPConnection stand-in: the response never comes until the socket is closed."""
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.closed = threading.Event()
        self.sock = None
        _BlockingConn.instances.append(self)

    def request(self, method, path, body=None, headers=None):
        self.sent = (method, path)

    def getresponse(self):
        if not self.closed.wait(10):
            raise AssertionError("never aborted")
        raise OSError("connection closed by the client")

    def close(self):
        self.closed.set()


def test_in_flight_request_is_dropped_when_the_owner_arrives(monkeypatch):
    _BlockingConn.instances.clear()
    monkeypatch.setattr(cp.http.client, "HTTPConnection", _BlockingConn)
    polls = {"n": 0}

    def owner_arrives() -> bool:
        polls["n"] += 1
        return polls["n"] >= 2

    t0 = time.time()
    with pytest.raises(cp.OwnerPreempted):
        cp.post_json("http://127.0.0.1:11434/api/generate", b"{}", 1800, owner_arrives, poll_s=0.05)
    conn = _BlockingConn.instances[0]
    assert conn.sent == ("POST", "/api/generate") and conn.closed.is_set()
    assert time.time() - t0 < 5, "the owner does not wait for the probe's request to finish"


def test_preempted_probe_exits_3_without_wait(data_dir, monkeypatch):
    def preempted(url, body, timeout, should_abort=None):
        raise cp.OwnerPreempted("owner became active")

    monkeypatch.setattr(cp, "post_json", preempted, raising=False)
    assert cp.main(_args()) == 3
    assert not (data_dir / "context-probes").exists()
