"""Telegram calls, END TO END WITHOUT TELEGRAM: a REAL backend process, the REAL `bossman call` CLI, the REAL calls worker.

``BOSSMAN_CALLS_MODE=offline_test`` (set by this harness, never by the API) makes the worker use a fake Telethon client and
the loopback line with a scripted interlocutor: the whole product flow runs — credentials -> login -> contacts -> peer ->
enable -> dial -> live states -> events -> history -> summary in memory -> draft tasks -> STOP / UNKNOWN / global STOP ->
restart — and every record says loopback. Evidence level: LOOPBACK. It proves our plumbing, guards and secret hygiene; it is
NOT a Telegram call (the label «ТЕСТ БЕЗ TELEGRAM» is asserted everywhere it must appear).

The four tests share one backend (module scope) and run in file order; each finishes well inside the CI timeout.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil
import pytest

API_ID = 1234567
API_HASH = "0123456789abcdef0123456789abcdef"
PHONE = "+70000000000"               # the fixed test phone of the offline client
CODE = "12345"                       # its fixed login code
SESSION_MARK = "OFFLINE-TEST-SESSION"
PEER_ID = 222000222                  # the synthetic second account
CALLS = "/api/telegram/calls"
SECRET_PATTERNS = (API_HASH, PHONE, PHONE.lstrip("+"), SESSION_MARK)

LEDGER: list[tuple[str, str]] = []   # (where, text) of everything the owner-facing surfaces ever returned


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(data: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p and os.path.isdir(p))
    env["BCC_DATA_DIR"] = str(data)
    env["BCC_TOKEN_STDOUT"] = "0"
    env["BOSSMAN_CALLS_MODE"] = "offline_test"
    env["PYTHONIOENCODING"] = "utf-8"
    for name in ("NO_COLOR", "BOSSMAN_URL", "DATABASE_URL", "BOSSMAN_TELEGRAM_CALLS_HOME", "BOSSMAN_VAULT_KEY"):
        env.pop(name, None)
    return env


@dataclass
class Backend:
    proc: subprocess.Popen
    url: str
    data: Path
    token: str
    api: httpx.Client
    log: object
    name: str = "backend"

    @property
    def calls_home(self) -> Path:
        return self.data / "telegram-calls"

    def workers(self) -> list[psutil.Process]:
        try:
            kids = psutil.Process(self.proc.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            return []
        found = [k for k in kids if "bcc.telegram_calls" in " ".join(_cmdline(k))]
        # A Windows venv `python.exe` is a launcher that spawns the real interpreter with the same command line:
        # one worker is one process tree, so the launcher's child is not a second worker.
        pids = {k.pid for k in found}
        return [k for k in found if k.ppid() not in pids]


def _cmdline(p: psutil.Process) -> list[str]:
    try:
        return p.cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def start_backend(data: Path, name: str = "backend") -> Backend:
    data.mkdir(parents=True, exist_ok=True)
    for stale in ("backend.lock", "desktop.lock"):
        (data / stale).unlink(missing_ok=True)
    port = _free_port()
    log = (data.parent / f"{name}.log").open("wb")
    proc = subprocess.Popen([sys.executable, "-m", "bcc.app", "--host", "127.0.0.1", "--port", str(port)], cwd=str(data),
                            env=_env(data), stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.close()
            pytest.fail("backend exited: " + (data.parent / f"{name}.log").read_text(encoding="utf-8", errors="replace")[-3000:])
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
    api = httpx.Client(base_url=url, headers={"X-BCC-Token": token}, trust_env=False, timeout=60)
    return Backend(proc=proc, url=url, data=data, token=token, api=api, log=log, name=name)


def stop_backend(b: Backend) -> None:
    b.api.close()
    b.proc.terminate()
    try:
        b.proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        b.proc.kill()
        b.proc.wait(timeout=10)
    b.log.close()
    for worker in b.workers():
        worker.kill()


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    root = tmp_path_factory.mktemp("calls-e2e")
    b = start_backend(root / "data")
    b.root = root                                     # type: ignore[attr-defined]
    yield b
    stop_backend(b)


def cli(b: Backend, *args: str, stdin: str | None = None, timeout: float = 150) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "bcc.terminal_cli", *args, "--url", b.url, "--data-dir", str(b.data)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=_env(b.data), input=stdin)
    LEDGER.append((f"cli {' '.join(args[:3])}", proc.stdout + proc.stderr))
    return proc


def records(proc: subprocess.CompletedProcess) -> list[dict]:
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip().startswith("{")]


def api(b: Backend, method: str, path: str, **kw) -> httpx.Response:
    r = b.api.request(method, path, **kw)
    LEDGER.append((f"api {method} {path}", r.text))
    return r


def status(b: Backend) -> dict:
    r = api(b, "GET", f"{CALLS}/status")
    assert r.status_code == 200, r.text
    return r.json()


def until(fn, *, timeout: float = 60.0, interval: float = 0.25, what: str = "condition"):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what} (last: {last!r})")


def history(b: Backend, limit: int = 20) -> list[dict]:
    return api(b, "GET", f"{CALLS}/history", params={"limit": limit}).json()["items"]


def wait_active(b: Backend) -> dict:
    return until(lambda: (status(b).get("call") or {}).get("state") == "active" and status(b)["call"], what="an active call")


def wait_no_call(b: Backend, *, outcome: str | None = None) -> dict:
    def done():
        st = status(b)
        last = st.get("last_call") or {}
        return st if not st.get("call") and last and (outcome is None or last.get("outcome") == outcome) else None
    return until(done, timeout=40, what=f"the call to end ({outcome})")


def modes_ok(path: Path) -> bool:
    if os.name == "nt":
        return True
    return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


# ============================================================================ 1. the owner's story


def test_setup_call_history_memory_and_drafts_through_the_real_cli(backend):
    b = backend
    # -- nothing runs at server start, and the first look is calm and complete
    st = status(b)
    assert st["mode"] == "offline_test" and st["test_label"] is True
    assert st["enabled"] is False and st["peer"] is None and st["account"]["state"] == "no_credentials"
    assert st["worker"]["running"] is False and b.workers() == [], "no worker before an owner action"
    assert not (b.calls_home / "credentials.enc").exists() and not (b.calls_home / "worker.log").exists()
    first = cli(b, "call", "status", "--json")
    rec = records(first)[-1]
    assert first.returncode == 0 and rec["type"] == "call_status" and rec["account"]["state"] == "no_credentials"
    human = cli(b, "call", "status")
    assert "ТЕСТ БЕЗ TELEGRAM" in human.stdout + human.stderr and "не сохранены" in human.stdout + human.stderr

    # -- secrets are refused in argv, before anything happens
    for argv in (["--api-hash", API_HASH], ["--phone", PHONE], [str(API_ID), API_HASH]):
        refused = cli(b, "call", "setup", *argv)
        assert refused.returncode == 2 and API_HASH not in refused.stdout + refused.stderr and PHONE not in refused.stdout + refused.stderr
    assert not (b.calls_home / "credentials.enc").exists()

    # -- setup: api_id, api_hash, phone, code (hidden prompt equivalent: --stdin)
    setup = cli(b, "call", "setup", "--stdin", "--json", stdin="\n".join([str(API_ID), API_HASH, PHONE, CODE]) + "\n")
    assert setup.returncode == 0, (setup.stdout, setup.stderr)
    last = records(setup)[-1]
    assert last["type"] == "call_setup" and last["stages"] == ["credentials_saved", "code_sent", "code_accepted", "connected"]
    assert last["account"]["state"] == "ready" and last["account"]["phone"].startswith("+••••")
    creds = b.calls_home / "credentials.enc"
    blob = creds.read_bytes()
    assert API_HASH.encode() not in blob and SESSION_MARK.encode() not in blob, "credentials are encrypted at rest"
    assert (b.data / "secret.key").is_file() and modes_ok(creds) and modes_ok(b.data / "secret.key") and modes_ok(b.calls_home)
    assert len(b.workers()) == 1, "the worker started on the owner's login"

    # -- contacts, then the ONE peer with its own confirmation; enabling is separate
    contacts = records(cli(b, "call", "contacts", "--json"))[-1]
    assert [c["id"] for c in contacts["contacts"]] == [PEER_ID]
    no_confirm = cli(b, "call", "peer", "set", str(PEER_ID))
    assert no_confirm.returncode == 2 and status(b)["peer"] is None
    picked = cli(b, "call", "peer", "set", str(PEER_ID), "--confirm", "--json")
    assert picked.returncode == 0 and records(picked)[-1]["peer"]["user_id"] == PEER_ID
    st = status(b)
    assert st["peer"]["user_id"] == PEER_ID and st["enabled"] is False, "choosing the peer does not switch calls on"

    # -- calls are OFF by default: the dial is refused with a stable code and nothing dials
    off = cli(b, "call", "dial", "--json")
    assert off.returncode == 5 and records(off)[-1]["code"] == "NOT_ENABLED" and records(off)[-1]["kind"] == "blocked"
    assert history(b) == []
    # -- a dial request has NO peer parameter: unknown fields are refused by the API and by the CLI
    for body in ({"peer": 5}, {"user_id": 5}, {"username": "x"}, {"peer_user_id": 5}):
        assert api(b, "POST", f"{CALLS}/call", json=body).status_code == 422, body
    assert cli(b, "call", "dial", "--peer", "5").returncode == 2 and cli(b, "call", "dial", "5").returncode == 2
    assert history(b) == [] and status(b)["call"] is None

    # -- enable, then a real call to the synthetic second account, followed to its end
    assert cli(b, "call", "enable", "--json").returncode == 0 and status(b)["enabled"] is True
    dial = cli(b, "call", "dial", "--wait", "--json", timeout=170)
    rows = records(dial)
    assert dial.returncode == 0, (dial.stdout[-1500:], dial.stderr[-1500:])
    assert [r["type"] for r in rows][0] == "call_dial" and rows[0]["test_label"] is True and rows[0]["transport"] == "loopback"
    kinds = {r.get("kind") for r in rows if r["type"] == "call_event"}
    assert {"state", "turn", "metric", "phase"} <= kinds, kinds
    result = rows[-1]
    assert result["type"] == "call_result" and result["outcome"] == "completed" and result["test_label"] is True
    assert result["latency"]["n"] >= 1 and result["latency"]["p50"] is not None and result["latency"]["p95"] is not None
    assert set(result["models"]) >= {"stt", "llm", "tts"}
    assert not any("text" in r or "transcript" in r for r in rows), "the event stream is text-free"

    # -- history: loopback is labelled; nothing about the conversation except a short summary
    wait_no_call(b, outcome="completed")
    entry = until(lambda: next(iter(history(b)), None) if (next(iter(history(b)), {}) or {}).get("postcall") else None, what="the post-call step")
    call_id = entry["call_id"]
    assert entry["transport"] == "loopback" and entry["outcome"] == "completed" and entry["recorded_audio"] is False
    assert entry["summary"]["text"] and "transcript" not in entry
    assert entry["postcall"]["memory"]["status"] == "not_saved", "by default nothing is written to Bossman memory or tasks"
    assert entry["postcall"]["draft_tasks"] == []
    shown = cli(b, "call", "history")
    assert call_id in shown.stdout and "ТЕСТ БЕЗ TELEGRAM" in shown.stdout

    # -- the owner's click: memory. Not configured yet -> a clear refusal, the call record is untouched
    unconfigured = cli(b, "call", "save-memory", call_id, "--json")
    assert unconfigured.returncode == 5 and records(unconfigured)[-1]["code"] == "MEMORY_NOT_CONFIGURED"
    vault = b.root / "vault"
    vault.mkdir()
    assert api(b, "POST", "/api/memory/config", json={"root": str(vault)}).status_code == 200
    saved = cli(b, "call", "save-memory", call_id, "--json")
    assert saved.returncode == 0, (saved.stdout, saved.stderr)
    note = next(vault.rglob(f"telegram-call-{call_id}.md"))
    body = note.read_text(encoding="utf-8")
    assert f"call-{call_id}" in body and "telegram-call" in body and "kind: session" in body
    assert "без Telegram" in body, "a loopback call is labelled in the note"
    for secret in SECRET_PATTERNS:
        assert secret not in body
    before = (note.stat().st_mtime_ns, note.read_bytes())
    again = cli(b, "call", "save-memory", call_id, "--json")
    assert again.returncode == 0 and records(again)[-1]["memory"]["status"] == "exists", "idempotent: same file name"
    assert (note.stat().st_mtime_ns, note.read_bytes()) == before and len(list(vault.rglob("telegram-call-*.md"))) == 1
    assert history(b)[0]["postcall"]["memory"]["status"] in ("written", "exists")

    # -- draft tasks: none proposed by the scripted brain -> refused; then a real proposal -> DRAFTS only, never run
    none = cli(b, "call", "draft-tasks", call_id, "--json")
    assert none.returncode == 5 and records(none)[-1]["code"] == "NO_PROPOSED_TASKS"
    hist_file = b.calls_home / "history.jsonl"
    lines = [json.loads(x) for x in hist_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    for item in lines:
        if item["call_id"] == call_id:
            item["summary"]["agreed_tasks"] = ["Проверить отчёт по продажам", "Написать поставщику"]
    hist_file.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    drafts = cli(b, "call", "draft-tasks", call_id, "--json")
    assert drafts.returncode == 0, (drafts.stdout, drafts.stderr)
    ids = records(drafts)[-1]["drafts"]["ids"]
    assert len(ids) == 2
    for tid in ids:
        task = api(b, "GET", f"/api/tasks/{tid}").json()
        assert task["task"]["status"] == "draft" and task["task"]["agent_id"] is None and task["runs"] == []
        assert task["task"]["prompt"].startswith(f"[Telegram call {call_id}]")
        assert (task["task"].get("meta") or {}).get("client_request_id", "").startswith(f"call-{call_id}-t")
    again = cli(b, "call", "draft-tasks", call_id, "--json")
    assert records(again)[-1]["drafts"]["ids"] == ids, "idempotent per call-<id>-t<n>"
    running = api(b, "GET", "/api/tasks", params={"status": "queued,running,waiting_approval"}).json()
    assert not [t for t in running if t["id"] in ids], "a draft is never enqueued or started"


# ============================================================================ 2. STOP, UNKNOWN, global STOP


def test_stop_no_redial_unknown_needs_confirm_and_global_stop(backend):
    b = backend
    assert status(b)["enabled"] is True and status(b)["peer"]["user_id"] == PEER_ID
    calls_before = len(history(b))

    # -- STOP in the middle of a call: file first, call ended, dialing blocked, no redial
    assert api(b, "POST", f"{CALLS}/call", json={}).status_code == 200
    wait_active(b)
    stopped = cli(b, "call", "stop", "--json")
    rec = records(stopped)[-1]
    assert stopped.returncode == 0 and rec["persisted"] is True and rec["terminated"] is False
    assert (b.calls_home / "STOP").is_file(), "the durable STOP file"
    after = wait_no_call(b, outcome="stopped")
    assert after["stop"]["call"] is True and after["stop"]["active"] is True
    assert len(history(b)) == calls_before + 1
    blocked = cli(b, "call", "dial", "--json")
    assert blocked.returncode == 5 and records(blocked)[-1]["code"] == "STOP_ACTIVE"
    assert api(b, "POST", f"{CALLS}/call", json={}).status_code == 409
    assert len(history(b)) == calls_before + 1, "no automatic redial after a STOP"
    assert cli(b, "call", "resume", "--json").returncode == 0 and not (b.calls_home / "STOP").exists()
    assert status(b)["uncertain_previous"] is False, "a clean STOP is not an uncertain outcome"

    # -- the worker dies in the middle of a call: outcome UNKNOWN, next dial needs the explicit confirmation
    assert api(b, "PUT", f"{CALLS}/settings", json={"auto_save_to_bossman_memory": True}).status_code == 200
    assert api(b, "POST", f"{CALLS}/call", json={}).status_code == 200
    wait_active(b)
    workers = b.workers()
    assert len(workers) == 1
    workers[0].kill()
    unknown = wait_no_call(b, outcome="unknown")
    assert unknown["uncertain_previous"] is True and unknown["last_call"]["synthesized"] is True
    assert unknown["last_error"]["code"] and unknown["last_error"]["message"] and unknown["last_error"]["hint"]
    n = len(history(b))
    refused = cli(b, "call", "dial", "--json")
    assert refused.returncode == 5 and records(refused)[-1]["code"] == "UNCERTAIN_PREVIOUS_CALL"
    assert api(b, "POST", f"{CALLS}/call", json={}).status_code == 409
    assert len(history(b)) == n and b.workers() == [], "nothing redialled, the worker was not even restarted"
    confirmed = cli(b, "call", "dial", "--confirm-unknown", "--wait", "--json", timeout=170)
    assert confirmed.returncode == 0, (confirmed.stdout[-1200:], confirmed.stderr[-1200:])
    assert records(confirmed)[-1]["outcome"] == "completed"
    assert status(b)["uncertain_previous"] is False
    entry = until(lambda: next((h for h in history(b) if h["outcome"] == "completed" and (h.get("postcall") or {}).get("auto")), None),
                  what="the automatic post-call step (owner opt-in)")
    assert entry["postcall"]["memory"]["status"] in ("written", "exists")
    assert api(b, "PUT", f"{CALLS}/settings", json={"auto_save_to_bossman_memory": False}).status_code == 200

    # -- the global STOP (bus event computer.stop) ends calls too and blocks dialing until the owner resumes
    assert api(b, "POST", "/api/computer/stop").status_code == 200
    st = until(lambda: (lambda s: s if s["stop"]["call"] else None)(status(b)), timeout=20, what="the calls STOP set by computer.stop")
    assert st["stop"]["global"] is True
    glob = cli(b, "call", "dial", "--json")
    assert glob.returncode == 5 and records(glob)[-1]["code"] == "STOP_ACTIVE"
    assert api(b, "POST", "/api/computer/resume").status_code == 200
    st = status(b)
    assert st["stop"]["global"] is False and st["stop"]["call"] is True, "resuming the computer does not resume calls"
    assert cli(b, "call", "dial", "--json").returncode == 5
    resumed = api(b, "POST", f"{CALLS}/resume").json()
    assert resumed["stop_flag"] is False and resumed["global_stop"] is False
    assert status(b)["stop"]["active"] is False

    # -- `bossman stop --all` stops the call explicitly as its own step
    stop_all = cli(b, "stop", "--all", "--json")
    stop_rec = [r for r in records(stop_all) if r["type"] == "stop_all"][-1]
    assert stop_rec["calls"]["status"] == "stopped" and stop_rec["result"]["computer"]["persisted"] is True
    assert (b.calls_home / "STOP").is_file()
    api(b, "POST", "/api/computer/resume")
    assert cli(b, "call", "resume").returncode == 0


# ============================================================================ 3. self-test and doctor


def test_selftest_and_doctor_are_local_and_labelled(backend):
    b = backend
    selftest = cli(b, "call", "selftest", "basic", "--json", timeout=170)
    assert selftest.returncode == 0, (selftest.stdout[-1500:], selftest.stderr[-800:])
    rec = records(selftest)[-1]
    assert rec["type"] == "call_selftest" and rec["verdict"] == "PASS" and rec["label"] == "ТЕСТ БЕЗ TELEGRAM"
    assert rec["evidence_level"] == "loopback" and [r["scenario"] for r in rec["results"]] == ["basic"]
    doctor = cli(b, "call", "doctor", "--json")
    assert doctor.returncode == 0, doctor.stdout
    rows = {r["check"]: r for r in records(doctor)[-1]["rows"]}
    assert all(r["status"] in ("PASS", "WARN", "BLOCKED") for r in rows.values())
    assert not [r for r in rows.values() if r["status"] == "BLOCKED"]
    assert any("Права" in name and r["status"] == "PASS" for name, r in rows.items())
    assert any("Ключ" in name and r["status"] == "PASS" for name, r in rows.items())
    assert any("Журнал" in name for name in rows) and any("Резервн" in name for name in rows)
    assert all("remedy" in r and "detail" in r for r in rows.values())
    api_doctor = api(b, "POST", f"{CALLS}/doctor")
    assert api_doctor.status_code == 200 and api_doctor.json()["verdict"] in ("PASS", "WARN")


# ============================================================================ 4. restart, lost key, secret scan


def test_restart_keeps_the_vault_stop_and_uncertainty_and_a_lost_key_is_reported(backend):
    b = backend
    # an uncertain state and a STOP that must survive the restart
    assert api(b, "POST", f"{CALLS}/call", json={}).status_code == 200
    wait_active(b)
    b.workers()[0].kill()
    wait_no_call(b, outcome="unknown")
    assert cli(b, "call", "stop", "--json").returncode == 0
    snapshot = b.root / "restart-data"
    stop_backend(b)
    shutil.copytree(b.data, snapshot)
    lost_key = b.root / "lost-key-data"
    shutil.copytree(b.data, lost_key)

    restarted = start_backend(snapshot, "restarted")
    try:
        st = status(restarted)
        assert st["account"]["state"] == "ready" and st["account"]["unreadable"] is False, "the Vault key survived the restart"
        assert st["stop"]["call"] is True, "STOP survived the restart"
        assert st["uncertain_previous"] is True, "an uncertain outcome survived the restart"
        assert st["peer"]["user_id"] == PEER_ID and st["worker"]["running"] is False
        assert cli(restarted, "call", "dial", "--json").returncode == 5
        assert restarted.workers() == []
    finally:
        stop_backend(restarted)

    (lost_key / "secret.key").unlink()
    broken = start_backend(lost_key, "lost-key")
    try:
        st = status(broken)
        assert st["account"]["unreadable"] is True and st["account"]["state"] == "error" and st["account"]["has_session"] is False
        doctor = records(cli(broken, "call", "doctor", "--json"))[-1]
        vault_row = next(r for r in doctor["rows"] if "Ключ" in r["check"])
        assert vault_row["status"] == "BLOCKED" and vault_row["remedy"]
        assert cli(broken, "call", "doctor").returncode == 5
        step = cli(broken, "call", "setup", "--stdin", "--json", stdin="\n".join([str(API_ID), API_HASH, PHONE, CODE]) + "\n")
        assert step.returncode == 0, (step.stdout, step.stderr)      # the owner can simply connect again with the new key
        assert status(broken)["account"]["state"] == "ready"
    finally:
        stop_backend(broken)


def test_secrets_never_appear_in_any_response_log_file_or_the_database(backend):
    b = backend
    seen = 0
    for where, text in LEDGER:
        seen += 1
        for secret in SECRET_PATTERNS:
            assert secret not in text, f"{secret!r} leaked in {where}"
    assert seen > 60, "the ledger covers the whole run"
    root = b.root
    files = [p for p in root.rglob("*") if p.is_file()]
    checked = 0
    for path in files:
        if path.name in ("credentials.enc", "secret.key", "token") or path.suffix in (".db", ".sqlite", ".wal", ".shm") or "-wal" in path.name:
            if path.suffix in (".db",) or "bcc.db" in path.name:
                data = path.read_bytes()
                for secret in (API_HASH, SESSION_MARK):
                    assert secret.encode() not in data, f"{secret!r} in {path.name}"
            continue
        if path.stat().st_size > 20_000_000:
            continue
        data = path.read_bytes()
        checked += 1
        for secret in SECRET_PATTERNS:
            assert secret.encode() not in data, f"{secret!r} found in {path.relative_to(root)}"
    assert checked > 10
    names = {p.name for p in files}
    assert {"worker.log", "history.jsonl", "state.json", "config.json"} <= names, names
    for name in ("worker.log", "history.jsonl", "state.json", "config.json", "credentials.enc"):
        for path in (p for p in files if p.name == name and "telegram-calls" in p.parts):
            assert modes_ok(path), f"{path} is readable by group/other"
    for path in (p for p in files if p.name == "worker.log"):
        assert path.stat().st_size < 600_000, "the worker log is size-capped"
