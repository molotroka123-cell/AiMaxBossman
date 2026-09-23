"""tools/jev_shadow_owner.py — owner runner CLI contract, offline.

Default mode must make NO network calls; missing key → OWNER_REQUIRED (2);
key present without --execute → READY_NOT_EXECUTED (3); --execute against a
MOCK Jev on 127.0.0.1 → probe + 10 cases; contract mismatch → FAIL (1);
rejected key → OWNER_REQUIRED (2). No paid calls anywhere.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "tools" / "jev_shadow_owner.py"
FAKE_KEY = "jev-FAKE-owner-runner-key-0000"      # ci-secret-scan: allow (test canary)


def _load():
    spec = importlib.util.spec_from_file_location("jev_shadow_owner", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


runner = _load()


def _chromium() -> bool:
    try:
        from bcc.browser_runtime import chromium_executable
    except ImportError:
        return False
    return chromium_executable(preinstalled="/opt/pw-browsers/chromium") is not None


def _clean_env(monkeypatch, tmp_path):
    for name in ("BOSSMAN_JEV_API_KEY", "TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY", "BOSSMAN_JEV_ENABLED",
                 "BOSSMAN_JEV_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BOSSMAN_JEV_KILL_FILE", str(tmp_path / "jev.disabled"))


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("network call in a mode that must not touch the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_no_key_is_owner_required_exit_2_without_network(monkeypatch, tmp_path, no_network, capsys):
    _clean_env(monkeypatch, tmp_path)
    assert runner.main(["--out", str(tmp_path / "o")]) == 2
    report = json.loads((tmp_path / "o" / "jev-shadow-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "OWNER_REQUIRED" and report["keys"]["jev"] is False
    assert "OWNER_REQUIRED" in capsys.readouterr().out


def test_key_without_execute_is_ready_exit_3_without_network(monkeypatch, tmp_path, no_network):
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BOSSMAN_JEV_API_KEY", FAKE_KEY)
    assert runner.main(["--out", str(tmp_path / "o")]) == 3
    blob = (tmp_path / "o" / "jev-shadow-report.json").read_text(encoding="utf-8")
    assert FAKE_KEY not in blob and json.loads(blob)["status"] == "READY_NOT_EXECUTED"
    assert FAKE_KEY not in (tmp_path / "o" / "jev-shadow-report.md").read_text(encoding="utf-8")


def test_isolated_subprocess_like_the_bundle(tmp_path):
    """python -I, UTF-8 console, exit code 2 when no key — exactly how Owner-Run calls it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BOSSMAN_JEV", "TYPESAFE"))}
    env["BOSSMAN_JEV_KILL_FILE"] = str(tmp_path / "jev.disabled")
    proc = subprocess.run([sys.executable, "-I", str(RUNNER), "--out", str(tmp_path / "o")],
                          capture_output=True, env=env, timeout=120)
    assert proc.returncode == 2, proc.stderr.decode("utf-8", "replace")
    assert "OWNER_REQUIRED" in proc.stdout.decode("utf-8")


class _Mock:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.requests = []
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mock.requests.append({"auth": self.headers.get("Authorization"), "body": body})
                if mock.mode == "401":
                    self.send_response(401)
                    self.end_headers()
                    return
                if mock.mode == "mismatch":
                    payload = {"result": "DONE"}                   # a different API shape
                else:
                    answers = {}
                    for name, q in body["questions"].items():
                        ids = list(q["criteria"])
                        pick = "DONE" if "DONE" in ids and name == "operation" and len(ids) == 2 else ids[0]
                        answers[name] = {"choice": pick, "confidence": 0.8,
                                         "probabilities": {i: float(i == pick) for i in ids}}
                    payload = {"model": "jev-mock", "answers": answers,
                               "usage": {"input_tokens": 100, "output_tokens": 5}}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/systemone"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.parametrize("mode,code,contract", [("401", 2, "KEY_REJECTED"), ("mismatch", 1, "CONTRACT_MISMATCH")])
def test_execute_probe_fails_closed(monkeypatch, tmp_path, mode, code, contract):
    _clean_env(monkeypatch, tmp_path)
    mock = _Mock(mode)
    try:
        monkeypatch.setenv("BOSSMAN_JEV_API_KEY", FAKE_KEY)
        monkeypatch.setenv("BOSSMAN_JEV_ENDPOINT", mock.url)
        assert runner.main(["--execute", "--out", str(tmp_path / "o")]) == code
        report = json.loads((tmp_path / "o" / "jev-shadow-report.json").read_text(encoding="utf-8"))
        assert report["contract"] == contract and "cases" not in report      # no benchmark after a bad probe
        assert len(mock.requests) == 1                                      # exactly one cheap probe
    finally:
        mock.close()


@pytest.mark.skipif(not _chromium(), reason="Chromium/Playwright недоступен для BrowserManager")
def test_execute_full_shadow_benchmark_against_mock(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "0")                    # restored after the test
    mock = _Mock("ok")
    try:
        monkeypatch.setenv("BOSSMAN_JEV_API_KEY", FAKE_KEY)
        monkeypatch.setenv("BOSSMAN_JEV_ENDPOINT", mock.url)
        monkeypatch.setenv("BOSSMAN_JEV_PRICE_PER_1K_INPUT_USD", "0.01")
        assert runner.main(["--execute", "--out", str(tmp_path / "o")]) == 0
        blob = (tmp_path / "o" / "jev-shadow-report.json").read_text(encoding="utf-8")
        assert FAKE_KEY not in blob
        report = json.loads(blob)
        assert report["contract"] == "VERIFIED" and report["status"] == "PASS"
        s = report["summary"]
        assert s["safety_cases_ok"] == "4/4" and s["functional_success"] == "6/6"
        assert s["model_calls"] + report["probe"]["calls"] == len(mock.requests) and s["model_calls"] >= 10
        assert s["input_tokens"] > 0 and s["estimated_cost_usd"] is not None
        assert s["phase2"]["candidate"] == "INSUFFICIENT_EVIDENCE"            # 17 steps < 30
        for key in ("case_time_ms", "jev_latency_ms", "browser_ops", "agreement_exact", "fallback_rate"):
            assert key in s
        assert all(r["auth"] == f"Bearer {FAKE_KEY}" for r in mock.requests)
        # iframe/canvas case: escalated before any model call
        unsupported = next(c for c in report["cases"] if c["case"] == "8_iframe_canvas")
        assert unsupported["model_calls"] == 0 and unsupported["safety_ok"] is True
    finally:
        mock.close()


def test_summary_marks_cost_unknown_not_zero():
    cfg = type("C", (), {"price_per_call_usd": None, "price_per_1k_input_usd": None,
                         "price_per_1k_output_usd": None})()
    from bcc.jev.client import estimate_cost
    s = runner.summarize({"cases": [], "client": {"stats": {"calls": 3, "input_tokens": 10}}}, cfg, estimate_cost)
    assert s["estimated_cost_usd"] is None and s["phase2"]["candidate"] == "INSUFFICIENT_EVIDENCE"
    assert s["safety_all_ok"] is False                                      # no safety evidence ≠ safe
