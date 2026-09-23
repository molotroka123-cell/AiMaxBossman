"""Bossman 1.2 terminal — end to end against a REAL local backend process.

The model is the DETERMINISTIC TEST MODEL (bossman.apprentice.scripted_model),
labelled MOCK_MODEL: these runs prove the plumbing (API -> engine -> tools ->
events -> terminal), never model quality. The backend is launched the way the
shipped tools launch it (`python -m bcc.app`, private data dir); the terminal
client runs as a separate process, non-TTY, exactly as Claude Code runs it.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

scripted = pytest.importorskip("bossman.apprentice.scripted_model",
                               reason="bossman-core (scripted test model) not installed")
pytest.importorskip("rich", reason="rich is a runtime dependency of the terminal client")

MOCK_MODEL = "MOCK_MODEL"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(data: Path) -> dict:
    env = os.environ.copy()
    # The child imports exactly what this test process imports (checkout or
    # installed wheel), never a different copy of the product.
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p and os.path.isdir(p))
    env["BCC_DATA_DIR"] = str(data)
    env["BCC_TOKEN_STDOUT"] = "0"
    for name in ("NO_COLOR", "BOSSMAN_URL", "DATABASE_URL"):
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    data = tmp_path_factory.mktemp("bossman-terminal-data")
    port = _free_port()
    log = (data / "server.log").open("wb")
    proc = subprocess.Popen([sys.executable, "-m", "bcc.app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=str(data), env=_env(data), stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.close()
            pytest.fail("backend exited: " + (data / "server.log").read_text(encoding="utf-8", errors="replace")[-3000:])
        try:
            if httpx.get(url + "/health/live", timeout=1, trust_env=False).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("backend did not start")
    token = (data / "token").read_text(encoding="utf-8").strip()
    client = httpx.Client(base_url=url, headers={"X-BCC-Token": token}, trust_env=False, timeout=30)
    servers = []
    yield {"url": url, "data": data, "token": token, "api": client, "servers": servers}
    client.close()
    for s in servers:
        s.shutdown()
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


def _agent(backend, name: str, turns: list, *, tools: list[str], max_steps: int = 4) -> dict:
    """A provider + model + agent served by one scripted (MOCK) model."""
    server, base = scripted.serve_in_thread({"name": name, "turns": turns})
    backend["servers"].append(server)
    api = backend["api"]
    prov = api.post("/api/providers", json={"name": f"mock {name}", "kind": "openai_compat",
                                            "base_url": base}).json()
    model = api.post("/api/models", json={"provider_id": prov["id"], "name": scripted.model_id(
        {"name": name}), "alias": f"mock-{name}", "context_window": 4096}).json()
    agent = api.post("/api/agents", json={"name": f"agent-{name}", "model_id": model["id"],
                                          "tools": tools, "max_steps": max_steps}).json()
    return agent


def _cli(backend, *args: str, timeout: float = 120, stdin: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "bcc.terminal_cli", *args, "--url", backend["url"],
           "--data-dir", str(backend["data"])]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                          env=_env(backend["data"]), input=stdin)


def _jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _no_secrets(backend, *procs) -> None:
    for p in procs:
        for stream in (p.stdout, p.stderr):
            assert backend["token"] not in (stream or ""), "the auth token leaked into output"


def test_headless_stream_json_run_with_a_tool(backend):
    """`bossman -p` creates a task; stream-json carries init, tool_use,
    tool_result and result in that order; the MOCK model is labelled."""
    agent = _agent(backend, "chat", [
        {"tool": "memory_fact_search", "args": {"query": "терминал"}, "content": "Проверю факты.",
         "reasoning": "Сначала поищу факты о терминале."},
        {"content": "Готово: фактов о терминале нет.\nNext: добавить факт."},
    ], tools=["memory.fact.search"])
    p = _cli(backend, "-p", "Что ты знаешь о терминале?", "--output-format", "stream-json",
             "--agent", agent["name"])
    assert p.returncode == 0, (p.stdout[-3000:], p.stderr[-3000:])
    recs = _jsonl(p.stdout)
    types = [r["type"] for r in recs]
    assert types[0] == "system" and recs[0]["subtype"] == "init"
    assert recs[0]["model"]["model_kind"] == MOCK_MODEL
    for needed in ("tool_use", "tool_result", "result"):
        assert needed in types, types
    assert types.index("tool_use") < types.index("tool_result") < types.index("result")
    assert types[-1] == "result"
    res = recs[-1]
    assert res["task_state"] == "PASS" and res["exit_code"] == 0
    assert res["model_kind"] == MOCK_MODEL
    tool_use = next(r for r in recs if r["type"] == "tool_use")
    assert tool_use["name"] == "memory.fact.search" and tool_use["input"] == {"query": "терминал"}
    # thinking appears ONLY because the provider really returned reasoning
    thinking = [r for r in recs if r["type"] == "thinking"]
    assert thinking and "поищу факты" in thinking[0]["delta"]
    assert any(r["type"] == "assistant_message" and "фактов о терминале нет" in r["text"] for r in recs)
    assert all(r.get("v") == 1 for r in recs)
    _no_secrets(backend, p)

    # replay: the durable history of the same task, by cursor, without duplicates
    task_id = res["task_id"]
    ev = _cli(backend, "events", str(task_id), "--json")
    assert ev.returncode == 0, ev.stderr
    hist = _jsonl(ev.stdout)
    seqs = [r["seq"] for r in hist if "seq" in r]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    assert "tool_use" in [r["type"] for r in hist]
    assert not [r for r in hist if r["type"] == "thinking"], "reasoning is live-only, never stored"
    tail = hist[-1]
    assert tail["type"] == "cursor" and tail["task_state"] == "PASS"
    later = _cli(backend, "events", str(task_id), "--after", str(tail["cursor"]), "--json")
    assert [r["type"] for r in _jsonl(later.stdout)] == ["cursor"]

    result = _cli(backend, "result", str(task_id), "--json")
    assert _jsonl(result.stdout)[-1]["task_state"] == "PASS"
    _no_secrets(backend, ev, later, result)


def test_no_thinking_record_when_the_model_sends_no_reasoning(backend):
    agent = _agent(backend, "plain", [{"content": "Просто ответ."}], tools=[])
    p = _cli(backend, "-p", "Скажи что-нибудь", "--output-format", "stream-json", "--agent", agent["name"])
    assert p.returncode == 0, p.stderr[-2000:]
    assert not [r for r in _jsonl(p.stdout) if r["type"] == "thinking"]


def test_approval_required_with_fail_mode_exits_4_and_approves_nothing(backend):
    agent = _agent(backend, "ask", [
        {"tool": "memory_fact_add", "args": {"subject": "терминал", "predicate": "есть",
                                             "statement": "терминал есть"}},
        {"content": "не должно дойти"},
    ], tools=["memory.fact.add"])
    p = _cli(backend, "-p", "Запиши факт", "--output-format", "stream-json", "--agent", agent["name"],
             "--approval-mode", "fail")
    assert p.returncode == 4, (p.stdout[-2000:], p.stderr[-2000:])
    recs = _jsonl(p.stdout)
    assert any(r["type"] == "approval_required" for r in recs)
    res = recs[-1]
    assert res["type"] == "result" and res["task_state"] == "WAIT_APPROVAL"
    api = backend["api"]
    approvals = api.get("/api/approvals", params={"status": "all"}).json()
    mine = [a for a in approvals if a.get("task_id") == res["task_id"]]
    assert mine and all(a["status"] == "pending" for a in mine), "nothing may be approved"
    # the approval row is created a moment before the task flips its status
    for _ in range(50):
        status = api.get(f"/api/tasks/{res['task_id']}").json()["task"]["status"]
        if status != "running":
            break
        time.sleep(0.1)
    assert status == "waiting_approval"
    # without a TTY, approving needs an explicit --yes (never a silent auto-approve)
    refused = _cli(backend, "approve", str(mine[0]["id"]), "--json")
    assert refused.returncode == 2
    assert api.get("/api/approvals", params={"status": "pending"}).json()
    denied = _cli(backend, "deny", str(mine[0]["id"]), "--json")
    assert denied.returncode == 0 and _jsonl(denied.stdout)[-1]["status"] == "rejected"
    _no_secrets(backend, p, refused, denied)


def test_stop_cancels_a_running_task(backend):
    agent = _agent(backend, "slow", [{"content": "долго думаю", "delay_seconds": 60}], tools=[])
    cmd = [sys.executable, "-m", "bcc.terminal_cli", "-p", "Долгая задача", "--output-format",
           "stream-json", "--agent", agent["name"], "--url", backend["url"], "--data-dir", str(backend["data"])]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            env=_env(backend["data"]))
    api = backend["api"]
    task_id = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and task_id is None:
        for t in api.get("/api/tasks", params={"limit": 20}).json():
            if t.get("title") == "Долгая задача" and t.get("status") == "running":
                task_id = t["id"]
        time.sleep(0.2)
    assert task_id is not None, "the task never started running"
    stop = _cli(backend, "stop", str(task_id), "--json")
    assert stop.returncode == 0, stop.stderr
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 6, (out[-2000:], err[-2000:])
    assert _jsonl(out)[-1]["task_state"] == "STOPPED"
    assert api.get(f"/api/tasks/{task_id}").json()["task"]["status"] == "stopped"
    assert backend["token"] not in out + err


def test_idempotent_exec_input_file_gives_one_task(backend, tmp_path):
    agent = _agent(backend, "exec", [{"content": "принято"}], tools=[])
    spec = {"schema": "bossman.exec.v1", "prompt": "Проверка exec", "agent": agent["name"],
            "request_id": "cli-test-idempotent-0001", "approval_mode": "fail"}
    f = tmp_path / "задание с пробелом.json"
    f.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    first = _cli(backend, "exec", "--input-file", str(f))
    second = _cli(backend, "exec", "--input-file", str(f))
    assert first.returncode == 0 and second.returncode == 0, (first.stderr, second.stderr)
    r1, r2 = _jsonl(first.stdout), _jsonl(second.stdout)
    assert r1[-1]["task_id"] == r2[-1]["task_id"]
    assert any(r["type"] == "task" and r.get("subtype") == "replayed" for r in r2)
    runs = backend["api"].get(f"/api/tasks/{r1[-1]['task_id']}").json()["runs"]
    assert len(runs) == 1, "a repeated submit must not start a second run"
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": "other"}', encoding="utf-8")
    refused = _cli(backend, "exec", "--input-file", str(bad))
    assert refused.returncode == 2 and _jsonl(refused.stdout)[-1]["type"] == "error"


def test_non_tty_output_has_no_ansi_and_no_token(backend):
    agent = _agent(backend, "text", [{"content": "**Жирный** ответ\n1. **Шаг** — деталь"}], tools=[])
    p = _cli(backend, "-p", "Текстом", "--agent", agent["name"])
    assert p.returncode == 0, p.stderr
    assert "Жирный" in p.stdout
    status = _cli(backend, "status")
    for proc in (p, status):
        assert "\x1b" not in proc.stdout and "\x1b" not in proc.stderr
    st = _jsonl(_cli(backend, "status", "--json").stdout)[-1]
    assert st["type"] == "status" and st["bossman"]["data_dir"]
    _no_secrets(backend, p, status)


def test_keys_set_via_stdin_is_encrypted_and_never_printed(backend):
    """The key goes to the backend once (request body), is stored encrypted and
    only its mask is ever shown; argv keys are refused."""
    fake_key = "sk-ant-TESTKEY-terminal-" + "Q" * 24 + "WXYZ"
    catalog_server = _fake_anthropic(backend)
    p = _cli(backend, "keys", "set", "anthropic", "--stdin", "--base-url", catalog_server, "--json",
             stdin=fake_key + "\n")
    assert p.returncode == 0, (p.stdout, p.stderr)
    rec = _jsonl(p.stdout)[-1]
    assert rec["mask"] == "…WXYZ"
    assert "claude-test-model" in (rec.get("models") or [])
    assert fake_key not in p.stdout + p.stderr
    listing = _cli(backend, "keys", "--json")
    assert fake_key not in listing.stdout and "…WXYZ" in listing.stdout
    db_bytes = (backend["data"] / "bcc.db").read_bytes()
    assert fake_key.encode() not in db_bytes, "the key must be stored encrypted"
    refused = _cli(backend, "keys", "set", "anthropic", fake_key)
    assert refused.returncode == 2 and fake_key not in refused.stdout + refused.stderr
    events = backend["api"].get("/api/activity", params={"limit": 200}).text
    assert fake_key not in events


def _fake_anthropic(backend) -> str:
    """A local Anthropic-compatible /v1/models (key checked, never echoed)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            return

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") == "/v1/models" and self.headers.get("x-api-key", "").startswith("sk-ant-"):
                body = json.dumps({"data": [{"id": "claude-test-model"}]}).encode()
                self.send_response(200)
            else:
                body = b'{"error": "no"}'
                self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    backend["servers"].append(server)
    return f"http://127.0.0.1:{server.server_address[1]}"
