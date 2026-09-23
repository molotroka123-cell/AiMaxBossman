"""Owner-Run.cmd profile `self-improve-mvcr` (tools/owner_run_tomorrow.py).

The runner is copied into an app-support-like folder and driven as a real
subprocess under `python -I` — the way Owner-Run.cmd starts it — so nothing here
leans on the checkout, PYTHONPATH or the test's own sys.path. The sibling
scripts other agents own (model_fetch.py, mvcr_prepare.py, self_improve_lab.py)
are replaced by tiny stand-ins that log how they were called; Bossman is a tiny
HTTP server; the model is a deterministic OpenAI-compatible fake.

Measured, not assumed: stage order, plan without side effects, resume skipping
completed work and never repeating an external action, STOP killing a real
child AND grandchild, the Telegram poller lock (live → no second poller; stale
→ allowed), no re-download of REUSED models, missing siblings → NOT_RUN, and
the bundle shipping the profile's inputs.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil  # root CI installs it; the shipped runtime has it (command-center dependency)
import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "tools" / "owner_run_tomorrow.py"
BAKEOFF = REPO / "tools" / "model_bakeoff.py"
PY = sys.executable


def _load(path: Path, name: str = "owner_run_under_test"):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _wait(predicate, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


# ------------------------------------------------------------------ fixtures

FAKE_MODEL_FETCH = r'''
import json, os, sys, pathlib
log = pathlib.Path(os.environ["FAKE_LOG"]); log.mkdir(parents=True, exist_ok=True)
with (log / "model_fetch.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
sub = sys.argv[1]
if sub == "plan":
    plan = pathlib.Path(os.environ.get("FAKE_PLAN", ""))
    body = json.loads(plan.read_text(encoding="utf-8")) if plan.is_file() else {"profiles": []}
    print(json.dumps(body))
else:
    profiles = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--profile"]
    print(json.dumps({"status": "OK", "subcommand": sub, "profiles": profiles}))
'''

FAKE_MVCR = r'''
import json, os, subprocess, sys, time, pathlib
log = pathlib.Path(os.environ["FAKE_LOG"]); log.mkdir(parents=True, exist_ok=True)
with (log / "mvcr.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
mode = os.environ.get("FAKE_MVCR", "WAIT_APPROVAL")
if mode == "SLEEP":
    g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    (log / "tree.pids").write_text(f"{os.getpid()} {g.pid}", encoding="utf-8")
    time.sleep(120)
out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]); out.mkdir(parents=True, exist_ok=True)
print(json.dumps({"status": mode, "missing": ["passport"] if mode == "PARTIAL_MISSING_DATA" else [],
                  "submitted": mode == "SUBMITTED"}))
'''

FAKE_LAB = r'''
import json, os, subprocess, sys, time, pathlib
phase = sys.argv[1]
log = pathlib.Path(os.environ["FAKE_LOG"]); log.mkdir(parents=True, exist_ok=True)
with (log / f"lab-{phase}.count").open("a", encoding="utf-8") as fh:
    fh.write("x")
if os.environ.get("FAKE_LAB_SLEEP") == phase:
    g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    (log / "tree.pids").write_text(f"{os.getpid()} {g.pid}", encoding="utf-8")
    time.sleep(120)
print(json.dumps({"status": "PASS", "phase": phase, "summary": "fake " + phase}))
'''

HOLD_LOCK = r'''
import os, sys, time, pathlib
path = pathlib.Path(sys.argv[1]); path.parent.mkdir(parents=True, exist_ok=True)
fh = path.open("a+b")
if fh.seek(0, 2) == 0:
    fh.write(b"0"); fh.flush()
fh.seek(0)
if os.name == "nt":
    import msvcrt; msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl; fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
if len(sys.argv) > 2:
    pathlib.Path(sys.argv[2]).write_text(str(os.getpid()), encoding="utf-8")
time.sleep(float(os.environ.get("HOLD_SECONDS", "60")))
'''


@pytest.fixture
def env(tmp_path):
    e = dict(os.environ)
    for k in list(e):
        if k.startswith(("BOSSMAN_", "BCC_")):
            e.pop(k)
    e.update({"BCC_DATA_DIR": str(tmp_path / "data"), "LOCALAPPDATA": str(tmp_path / "local"),
              "BOSSMAN_MODEL_ENDPOINTS": "http://127.0.0.1:9", "FAKE_LOG": str(tmp_path / "fake-log"),
              "PYTHONUTF8": "1"})
    return e


@pytest.fixture
def home(tmp_path):
    """app-support with ONLY the runner (siblings are added per test)."""
    h = tmp_path / "bundle" / "app-support"
    h.mkdir(parents=True)
    shutil.copyfile(RUNNER, h / "owner_run_tomorrow.py")
    return h


def _add(home: Path, name: str, body: str) -> None:
    (home / name).write_text(body, encoding="utf-8")


def _full(home: Path) -> None:
    _add(home, "model_fetch.py", FAKE_MODEL_FETCH)
    _add(home, "model_profiles.json", json.dumps({"profiles": []}))
    _add(home, "mvcr_prepare.py", FAKE_MVCR)
    _add(home, "self_improve_lab.py", FAKE_LAB)
    shutil.copyfile(BAKEOFF, home / "model_bakeoff.py")
    (home / "self_improve_cases").mkdir()
    (home / "self_improve_cases" / "case-01.json").write_text('{"id": "case-01"}', encoding="utf-8")


def _cli(home: Path, env: dict, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-I", str(home / "owner_run_tomorrow.py"), *args], env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _popen(home: Path, env: dict, *args: str) -> subprocess.Popen:
    return subprocess.Popen([PY, "-I", str(home / "owner_run_tomorrow.py"), *args], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace")


def _state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


def _count(env: dict, phase: str) -> int:
    p = Path(env["FAKE_LOG"]) / f"lab-{phase}.count"
    return len(p.read_text(encoding="utf-8")) if p.is_file() else 0


def _fetch_calls(env: dict) -> list[list[str]]:
    p = Path(env["FAKE_LOG"]) / "model_fetch.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []


class _Bossman(http.server.BaseHTTPRequestHandler):
    started_at = 1000.0
    handshake: object = {"ok": True, "status": "ok"}

    def log_message(self, *a):  # noqa: D401 — quiet
        pass

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/api/identity":
            return self._json(200, {"app": "bossman-command-center", "started_at": type(self).started_at})
        if self.path == "/api/coding-tasks/readiness":
            # Real readiness names the allowed coding roots; the lab work dir must be inside one.
            body = {"available": True, "runtime": True, "sidecar_command": True, "reason": "",
                    "roots": list(type(self).roots)}
            if type(self).handshake is not None:
                body["handshake"] = type(self).handshake
            return self._json(200, body)
        if self.path == "/api/skill-catalog":
            return self._json(200, list(type(self).catalog))
        if self.path.startswith("/api/skill-catalog/select"):
            return self._json(200, [c for c in type(self).catalog if "debug" in c["id"]])
        return self._json(404, {})


@pytest.fixture
def bossman():
    import tempfile as _tf
    handler = type("Bossman", (_Bossman,), {"started_at": 1000.0, "handshake": {"ok": True},
                                            "roots": [str(Path(_tf.gettempdir()).resolve())],
                                            "catalog": [{"id": "obra-superpowers/systematic-debugging",
                                                         "status": "UNVERIFIED",
                                                         "grants": {"tools": [], "permissions": []}}]})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield handler, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


# ------------------------------------------------------------ deterministic model

FIXED = '''def moving_sum(xs, k):
    out = []
    for i in range(len(xs) - k + 1):
        out.append(sum(xs[i:i + k]))
    return out
'''


def _answer(messages: list[dict], good: bool) -> dict:
    if not good:
        return {"content": "I am not sure."}
    last = messages[-1]
    text = str(last.get("content") or "")
    if last.get("role") == "tool":
        return {"content": "", "tool_calls": [{"id": "c2", "type": "function", "function": {
            "name": "read_file", "arguments": json.dumps({"path": "command-center/bcc/health.py"})}}]}
    if "Find the bug" in text:
        return {"content": "```python\n" + FIXED + "```"}
    if "test_regression" in text:
        return {"content": "```python\n" + FIXED + "\ndef test_regression():\n"
                           "    assert moving_sum([1, 2, 3, 4], 2) == [3, 5, 7]\n```"}
    if "Repository map" in text:
        return {"content": "command-center/bcc/registry.py"}
    if "Run only the tests" in text:
        return {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "run_tests", "arguments": json.dumps({"path": "command-center/tests/test_health_model_components.py"})}}]}
    if "Return ONLY a JSON object" in text:
        return {"content": json.dumps({"file": "utils.py", "line": 4, "severity": "high", "fix": "range bound"})}
    if "rollback password" in text:
        return {"content": "AMBER-4471"}
    return {"content": "?"}


def _model_server(good: bool):
    class Model(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            self._json({"data": [{"id": "fake-deterministic"}]})

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            msg = {"role": "assistant", **_answer(body["messages"], good)}
            self._json({"choices": [{"message": msg, "finish_reason": "stop"}],
                        "usage": {"completion_tokens": 7}, "timings": {"predicted_per_second": 42.0}})

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# ----------------------------------------------------------------------- tests

def test_profile_stage_order_is_fixed_and_run_follows_it(home, env, tmp_path, bossman):
    mod = _load(RUNNER)
    assert mod.SI_STAGES == ("plan", "preflight", "bootstrap", "skills", "compare", "mvcr", "self-improve", "report")
    assert mod.SI_PHASES == ("explore", "compare", "lesson", "restart", "transfer")
    assert mod.step_keys() == ["preflight", "bootstrap", "skills", "compare", "mvcr", "self-improve:explore",
                               "self-improve:compare", "self-improve:lesson", "self-improve:restart",
                               "self-improve:transfer", "report"]
    _full(home)
    folder = tmp_path / "owner-docs"
    folder.mkdir()
    (tmp_path / "facts.json").write_text("{}", encoding="utf-8")
    run_dir = tmp_path / "run"
    _, base = bossman
    done = _cli(home, env, "self-improve-mvcr", "run", "--run-dir", str(run_dir), "--base-url", base, "--explore-repo", str(tmp_path),
                "--owner-folder", str(folder), "--facts", str(tmp_path / "facts.json"))
    state = _state(run_dir)
    started = [h["step"] for h in state["history"] if h["event"] == "started"]
    assert started == mod.step_keys(), done.stdout + done.stderr
    assert state["steps"]["mvcr"]["status"] == "OWNER_REQUIRED"          # WAIT_APPROVAL: owner submits
    assert state["steps"]["self-improve:restart"]["status"] == "OWNER_REQUIRED"   # no desktop.lock
    assert state["steps"]["self-improve:transfer"]["status"] == "BLOCKED"
    assert (run_dir / "report.md").is_file() and "НЕ на железе владельца" in (run_dir / "report.md").read_text(encoding="utf-8")
    assert state["steps"]["preflight"]["status"] == "FAIL"               # no doctor beside the runner
    assert done.returncode == 1


def test_plan_has_no_side_effects(home, env, tmp_path):
    _full(home)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({"profiles": [
        {"id": "main-q5", "status": "REUSED", "bytes": 20_000_000_000},
        {"id": "oss-120b", "status": "MISSING", "download_bytes": 63_000_000_000}]}), encoding="utf-8")
    env["FAKE_PLAN"] = str(plan_file)
    run_dir = tmp_path / "run"
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if "fake-log" not in p.parts)
    done = _cli(home, env, "self-improve-mvcr", "plan", "--run-dir", str(run_dir))
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if "fake-log" not in p.parts)
    assert done.returncode == 0, done.stderr
    assert before == after                                  # nothing created, not even the run dir
    assert not run_dir.exists()
    assert [c[0] for c in _fetch_calls(env)] == ["plan"]     # never fetch
    assert "oss-120b" in done.stdout and "main-q5" in done.stdout and "58.7 ГБ" in done.stdout
    assert "--allow-download" in done.stdout
    # JSON form, and the short `Owner-Run.cmd plan` alias, are the same plan.
    js = json.loads(_cli(home, env, "plan", "--run-dir", str(run_dir), "--json").stdout)
    assert [s["stage"] for s in js["stages"]] == list(_load(RUNNER).SI_STAGES)
    assert js["side_effects"] == "none" and not run_dir.exists()


def test_bootstrap_never_redownloads_reused_models(home, env, tmp_path):
    _full(home)
    plan_file = tmp_path / "plan.json"
    env["FAKE_PLAN"] = str(plan_file)
    run_dir = tmp_path / "run"
    plan_file.write_text(json.dumps({"profiles": [{"id": "main-q5", "status": "REUSED"},
                                                  {"id": "fast", "status": "PRESENT_OK"}]}), encoding="utf-8")
    done = _cli(home, env, "self-improve-mvcr", "bootstrap", "--run-dir", str(run_dir), "--allow-download",
                "--profile", "main-q5")
    rec = _state(run_dir)["steps"]["bootstrap"]
    assert rec["status"] == "PASS" and rec["facts"]["downloaded"] == [], done.stdout
    assert [c[0] for c in _fetch_calls(env)] == ["plan"]
    # Negative control: a MISSING profile IS fetched — and only that one, only with permission.
    plan_file.write_text(json.dumps({"profiles": [{"id": "main-q5", "status": "REUSED"},
                                                  {"id": "oss-120b", "status": "MISSING", "bytes": 10}]}),
                         encoding="utf-8")
    _cli(home, env, "self-improve-mvcr", "bootstrap", "--run-dir", str(run_dir))
    assert _state(run_dir)["steps"]["bootstrap"]["status"] == "OWNER_REQUIRED"
    assert "fetch" not in [c[0] for c in _fetch_calls(env)]
    _cli(home, env, "self-improve-mvcr", "bootstrap", "--run-dir", str(run_dir), "--allow-download",
         "--profile", "oss-120b")
    rec = _state(run_dir)["steps"]["bootstrap"]
    assert rec["status"] == "PASS" and rec["facts"]["downloaded"] == ["oss-120b"]
    fetches = [c for c in _fetch_calls(env) if c[0] == "fetch"]
    assert len(fetches) == 1 and "oss-120b" in fetches[0] and "main-q5" not in fetches[0]
    assert any(c[0] == "verify" for c in _fetch_calls(env))


def test_resume_skips_completed_and_never_repeats_external_actions(home, env, tmp_path, bossman):
    _full(home)
    handler, base = bossman
    run_dir = tmp_path / "run"
    env["FAKE_PLAN"] = str(tmp_path / "plan.json")
    (tmp_path / "plan.json").write_text(json.dumps({"profiles": [{"id": "m", "status": "REUSED"}]}), encoding="utf-8")
    _cli(home, env, "self-improve-mvcr", "bootstrap", "--run-dir", str(run_dir))
    _cli(home, env, "self-improve-mvcr", "self-improve", "--run-dir", str(run_dir), "--base-url", base, "--explore-repo", str(tmp_path))
    st = _state(run_dir)["steps"]
    assert [st[f"self-improve:{p}"]["status"] for p in ("explore", "compare", "lesson")] == ["PASS"] * 3
    assert st["self-improve:restart"]["status"] == "OWNER_REQUIRED"
    assert [_count(env, p) for p in ("explore", "compare", "lesson", "transfer")] == [1, 1, 1, 0]
    plans_before = len(_fetch_calls(env))

    _cli(home, env, "resume", "--run-dir", str(run_dir), "--base-url", base)
    state = _state(run_dir)
    skipped = {h["step"] for h in state["history"] if h["event"] == "skipped_done"}
    assert {"bootstrap", "self-improve:explore", "self-improve:compare", "self-improve:lesson"} <= skipped
    assert [_count(env, p) for p in ("explore", "compare", "lesson")] == [1, 1, 1]   # never repeated
    fetch_after = _fetch_calls(env)[plans_before:]
    assert all(c[0] != "fetch" for c in fetch_after)
    # bootstrap was skipped; only preflight's plan/runtime-check ran
    assert sorted(c[0] for c in fetch_after) == ["plan", "runtime-check"]

    # The owner restarted Bossman by hand: resume counts it by started_at and runs transfer once.
    handler.started_at = 2000.0
    _cli(home, env, "resume", "--run-dir", str(run_dir), "--base-url", base)
    st = _state(run_dir)["steps"]
    assert st["self-improve:restart"]["status"] == "PASS" and "started_at" in st["self-improve:restart"]["detail"]
    assert st["self-improve:transfer"]["status"] == "PASS" and _count(env, "transfer") == 1
    _cli(home, env, "resume", "--run-dir", str(run_dir), "--base-url", base)
    assert _count(env, "transfer") == 1


def test_stop_kills_the_child_and_grandchild_and_records_what_ran(home, env, tmp_path):
    _full(home)
    env["FAKE_MVCR"] = "SLEEP"
    folder = tmp_path / "docs"
    folder.mkdir()
    (tmp_path / "facts.json").write_text("{}", encoding="utf-8")
    run_dir = tmp_path / "run"
    runner = _popen(home, env, "self-improve-mvcr", "mvcr", "--run-dir", str(run_dir), "--owner-folder", str(folder),
                    "--facts", str(tmp_path / "facts.json"))
    pids = Path(env["FAKE_LOG"]) / "tree.pids"
    try:
        assert _wait(pids.is_file, 30), runner.stderr.read() if runner.poll() is not None else "no pids"
        child, grandchild = map(int, pids.read_text(encoding="utf-8").split())
        assert not _gone(child) and not _gone(grandchild)
        stop = _cli(home, env, "stop", "--run-dir", str(run_dir))
        assert stop.returncode == 0, stop.stdout + stop.stderr
        assert runner.wait(timeout=60) == 3
        assert _wait(lambda: _gone(child) and _gone(grandchild), 15), "orphan left behind"
    finally:
        if runner.poll() is None:
            runner.kill()
    report = json.loads((run_dir / "stop-report.json").read_text(encoding="utf-8"))
    assert report["runner_acknowledged"] is True and report["step"] == "mvcr"
    killed = {pid for k in report["killed"] for pid in k["killed"]}
    assert {child, grandchild} <= killed
    state = _state(run_dir)
    assert state["children"] == [] and state["steps"]["mvcr"]["interrupted"] == "STOP"
    assert not (run_dir / "runner.lock").exists() or not _load(RUNNER).lock_is_held(run_dir / "runner.lock")
    # mvcr is idempotent: resume re-enters it (and it finishes this time).
    env["FAKE_MVCR"] = "WAIT_APPROVAL"
    _cli(home, env, "self-improve-mvcr", "mvcr", "--run-dir", str(run_dir), "--owner-folder", str(folder),
         "--facts", str(tmp_path / "facts.json"))
    assert _state(run_dir)["steps"]["mvcr"]["status"] == "OWNER_REQUIRED"
    assert not (run_dir / "STOP").exists()


def test_interrupted_external_action_becomes_unknown_outcome_not_a_repeat(home, env, tmp_path, bossman):
    _full(home)
    _, base = bossman
    env["FAKE_LAB_SLEEP"] = "explore"
    run_dir = tmp_path / "run"
    runner = _popen(home, env, "self-improve-mvcr", "self-improve", "--run-dir", str(run_dir), "--base-url", base, "--explore-repo", str(tmp_path))
    try:
        assert _wait((Path(env["FAKE_LOG"]) / "tree.pids").is_file, 30)
        assert _cli(home, env, "stop", "--run-dir", str(run_dir)).returncode == 0
        assert runner.wait(timeout=60) == 3
    finally:
        if runner.poll() is None:
            runner.kill()
    env.pop("FAKE_LAB_SLEEP")
    _cli(home, env, "self-improve-mvcr", "self-improve", "--run-dir", str(run_dir), "--base-url", base, "--explore-repo", str(tmp_path))
    st = _state(run_dir)["steps"]
    assert st["self-improve:explore"]["status"] == "UNKNOWN_OUTCOME"
    assert st["self-improve:compare"]["status"] == "BLOCKED"
    assert _count(env, "explore") == 1                                   # not repeated by itself
    assert "--redo self-improve:explore" in json.loads((run_dir / "report.json").read_text(encoding="utf-8"))["next_command"]
    # Negative control: the owner's explicit --redo does repeat it.
    _cli(home, env, "self-improve-mvcr", "self-improve", "--run-dir", str(run_dir), "--base-url", base, "--explore-repo", str(tmp_path),
         "--redo", "self-improve:explore")
    assert _count(env, "explore") == 2
    assert _state(run_dir)["steps"]["self-improve:compare"]["status"] == "PASS"


def _poller_cmd(tmp_path: Path) -> tuple[list[str], Path]:
    script = tmp_path / "fake_poller.py"
    script.write_text(HOLD_LOCK, encoding="utf-8")
    marker = tmp_path / "poller-started.pid"
    return [PY, "-I", str(script), "{lock}", str(marker)], marker


def test_second_telegram_poller_is_not_started_while_a_live_one_holds_the_lock(tmp_path):
    mod = _load(RUNNER)
    tg = tmp_path / "telegram-companion"
    holder_script = tmp_path / "holder.py"
    holder_script.write_text(HOLD_LOCK, encoding="utf-8")
    ready = tmp_path / "holder.ready"
    holder = subprocess.Popen([PY, "-I", str(holder_script), str(tg / "poller.lock"), str(ready)])
    try:
        assert _wait(ready.is_file, 20)
        assert mod.telegram_poller_running(tg) is True
        cmd, marker = _poller_cmd(tmp_path)
        cmd = [str(tg / "poller.lock") if c == "{lock}" else c for c in cmd]
        for _ in range(2):   # re-running never starts a second one
            result = mod.ensure_telegram_poller(tg, cmd, start=True, log_path=tmp_path / "p.log",
                                                require_config=False)
            assert result["status"] == "PASS" and result["facts"]["started"] is False
            assert "второй не запускаю" in result["detail"]
        time.sleep(0.5)
        assert not marker.exists()
    finally:
        holder.kill()
        holder.wait()


def test_a_stale_poller_lock_does_not_block_the_start(tmp_path):
    """Negative control: a lock FILE without a living holder is not a running poller."""
    mod = _load(RUNNER)
    tg = tmp_path / "telegram-companion"
    tg.mkdir()
    (tg / "poller.lock").write_bytes(b"0")               # left by a crashed companion
    assert mod.telegram_poller_running(tg) is False
    cmd, marker = _poller_cmd(tmp_path)
    cmd = [str(tg / "poller.lock") if c == "{lock}" else c for c in cmd]
    result = mod.ensure_telegram_poller(tg, cmd, start=True, log_path=tmp_path / "p.log", require_config=False)
    try:
        assert result["status"] == "PASS" and result["facts"]["started"] is True
        assert result["facts"]["stale_lock"] is True and marker.is_file()
        again = mod.ensure_telegram_poller(tg, cmd, start=True, log_path=tmp_path / "p.log", require_config=False)
        assert again["facts"]["started"] is False       # idempotent: the one we started is now live
    finally:
        mod.kill_tree(result["facts"]["pid"])


def test_preflight_reports_a_live_poller_and_bossman_without_handshake(home, env, tmp_path, bossman):
    handler, base = bossman
    handler.handshake = None                             # available, but no proof of a sidecar handshake
    lock = Path(env["LOCALAPPDATA"]) / "Bossman" / "telegram-companion" / "poller.lock"
    holder_script = tmp_path / "holder.py"
    holder_script.write_text(HOLD_LOCK, encoding="utf-8")
    ready = tmp_path / "ready"
    holder = subprocess.Popen([PY, "-I", str(holder_script), str(lock), str(ready)])
    try:
        assert _wait(ready.is_file, 20)
        run_dir = tmp_path / "run"
        _cli(home, env, "self-improve-mvcr", "preflight", "--run-dir", str(run_dir), "--base-url", base,
             "--telegram", "start")
        checks = _state(run_dir)["steps"]["preflight"]["facts"]["checks"]
        assert checks["telegram"]["status"] == "PASS" and checks["telegram"]["facts"]["started"] is False
        assert checks["bossman"]["status"] == "OWNER_REQUIRED" and "рукопожат" in checks["bossman"]["detail"]
        assert checks["model_reuse"]["status"] == "NOT_RUN"
        # With a handshake record the same Bossman is ready.
        handler.handshake = {"ok": True}
        _cli(home, env, "self-improve-mvcr", "preflight", "--run-dir", str(run_dir), "--base-url", base)
        assert _state(run_dir)["steps"]["preflight"]["facts"]["checks"]["bossman"]["status"] == "PASS"
    finally:
        holder.kill()
        holder.wait()


def test_missing_sibling_scripts_are_not_run_never_pass(home, env, tmp_path, bossman):
    _, base = bossman
    folder = tmp_path / "docs"
    folder.mkdir()
    (tmp_path / "facts.json").write_text("{}", encoding="utf-8")
    run_dir = tmp_path / "run"
    done = _cli(home, env, "self-improve-mvcr", "run", "--run-dir", str(run_dir), "--base-url", base, "--explore-repo", str(tmp_path),
                "--owner-folder", str(folder), "--facts", str(tmp_path / "facts.json"))
    st = _state(run_dir)["steps"]
    for key in ("bootstrap", "compare", "mvcr", "self-improve:explore"):
        assert st[key]["status"] == "NOT_RUN", (key, st[key])
        assert "не найден" in st[key]["detail"]
    # `skills` reads the catalog from Bossman itself (no sibling script), so it may pass here.
    assert all(st[k]["status"] != "PASS" for k in st if k not in ("report", "skills"))
    assert done.returncode != 0
    assert json.loads((run_dir / "report.json").read_text(encoding="utf-8"))["overall"] != "PASS"


def test_mvcr_statuses_and_submission_is_a_failure(home, env, tmp_path):
    _full(home)
    folder = tmp_path / "docs"
    folder.mkdir()
    (tmp_path / "facts.json").write_text("{}", encoding="utf-8")
    run_dir = tmp_path / "run"
    expected = {"WAIT_APPROVAL": "OWNER_REQUIRED", "PARTIAL_MISSING_DATA": "OWNER_REQUIRED", "BLOCKED": "BLOCKED",
                "SUBMITTED": "FAIL"}
    for raw, ours in expected.items():
        env["FAKE_MVCR"] = raw
        _cli(home, env, "self-improve-mvcr", "mvcr", "--run-dir", str(run_dir), "--owner-folder", str(folder),
             "--facts", str(tmp_path / "facts.json"))
        assert _state(run_dir)["steps"]["mvcr"]["status"] == ours, raw
    calls = (Path(env["FAKE_LOG"]) / "mvcr.jsonl").read_text(encoding="utf-8").splitlines()
    assert all("--json" in json.loads(c) and "--owner-folder" in json.loads(c) for c in calls)
    # without inputs: the owner is asked, the script is not called
    _cli(home, env, "self-improve-mvcr", "mvcr", "--run-dir", str(run_dir))
    assert _state(run_dir)["steps"]["mvcr"]["status"] == "OWNER_REQUIRED"
    assert len((Path(env["FAKE_LOG"]) / "mvcr.jsonl").read_text(encoding="utf-8").splitlines()) == len(expected)


def test_a_second_runner_on_the_same_run_dir_is_refused(home, env, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    holder_script = tmp_path / "holder.py"
    holder_script.write_text(HOLD_LOCK, encoding="utf-8")
    ready = tmp_path / "ready"
    holder = subprocess.Popen([PY, "-I", str(holder_script), str(run_dir / "runner.lock"), str(ready)])
    try:
        assert _wait(ready.is_file, 20)
        done = _cli(home, env, "resume", "--run-dir", str(run_dir))
        assert done.returncode == 2 and "уже идёт прогон" in done.stdout
        assert not (run_dir / "state.json").exists()
    finally:
        holder.kill()
        holder.wait()


def test_bakeoff_grades_a_deterministic_model_by_execution(tmp_path):
    bake = _load(BAKEOFF, "model_bakeoff_under_test")
    good, good_url = _model_server(True)
    bad, bad_url = _model_server(False)
    try:
        assert bake.main(["--url", good_url + "/v1", "--tag", "good", "--out", str(tmp_path), "--json"]) == 0
        result = json.loads((tmp_path / "good.json").read_text(encoding="utf-8"))
        assert result["passed"] == 7 and result["failed"] == [], result
        assert bake.main(["--url", bad_url + "/v1", "--tag", "bad", "--out", str(tmp_path), "--json"]) == 0
        result = json.loads((tmp_path / "bad.json").read_text(encoding="utf-8"))
        assert result["passed"] == 0 and "A_bug" in result["failed"]
    finally:
        good.shutdown()
        bad.shutdown()


def test_compare_stage_runs_the_bakeoff_on_live_endpoints(home, env, tmp_path):
    _full(home)
    server, url = _model_server(True)
    run_dir = tmp_path / "run"
    try:
        _cli(home, env, "self-improve-mvcr", "compare", "--run-dir", str(run_dir), "--compare-endpoint", url,
             timeout=300)
    finally:
        server.shutdown()
    rec = _state(run_dir)["steps"]["compare"]
    assert rec["status"] == "PASS", rec
    summary = json.loads((run_dir / "compare" / "summary.json").read_text(encoding="utf-8"))
    assert summary["rows"][0]["passed"] == 7 and summary["rows"][0]["model"] == "fake-deterministic"
    # no live endpoint: the owner has to start one — never a PASS
    _cli(home, env, "self-improve-mvcr", "compare", "--run-dir", str(run_dir))
    assert _state(run_dir)["steps"]["compare"]["status"] == "OWNER_REQUIRED"


# ------------------------------------------------------------- bundle / launcher

FORBIDDEN = (re.compile(r"C:\\+Users\\+asd", re.I), re.compile(r"PYTHONPATH"), re.compile(r"wt-final"),
             re.compile(r"start-bossman-src", re.I), re.compile(r"owner-repair"))


def _hidden_paths(text: str) -> list[str]:
    return [rx.pattern for rx in FORBIDDEN if rx.search(text)]


def test_launcher_and_runner_have_no_hidden_checkout_dependency():
    sys.path.insert(0, str(REPO / "tools"))
    import build_windows_bundle as bundle
    launchers = bundle.launcher_files()
    texts = {"Owner-Run.cmd": launchers["Owner-Run.cmd"], "_env.cmd": launchers["app-support/_env.cmd"],
             "owner_run_tomorrow.py": RUNNER.read_text(encoding="utf-8"),
             "model_bakeoff.py": BAKEOFF.read_text(encoding="utf-8")}
    for name, text in texts.items():
        assert _hidden_paths(text) == [], name
    assert 'app-support\\owner_run_tomorrow.py" %*' in launchers["Owner-Run.cmd"]
    assert "self-improve-mvcr" in launchers["Owner-Run.cmd"]
    # negative control: the checker does see such a path
    assert _hidden_paths(r"set PYTHONPATH=C:\Users\asd\Bossman\wt-final") != []


def test_bundle_ships_the_profile_inputs_and_fails_loudly_without_them(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "tools"))
    import build_windows_bundle as bundle
    assert "docs/owner/OWNER_RUN_NEXT.md" in bundle.SELF_IMPROVE_DOCS
    assert "docs/owner/OWNER_RUN_NEXT.md" in bundle.self_improve_inputs()
    assert (REPO / "docs/owner/OWNER_RUN_NEXT.md").is_file()
    required = {name for _, name in bundle.SELF_IMPROVE_REQUIRED}
    assert required == {"model_fetch.py", "model_profiles.json", "mvcr_prepare.py", "self_improve_lab.py"}
    assert "tools/self_improve_cases/**" in bundle.self_improve_inputs()
    assert "model_bakeoff.py" in {name for _, name in bundle.SUPPORT_SCRIPTS}
    assert "owner_run_tomorrow.py" in {name for _, name in bundle.SUPPORT_SCRIPTS}

    root = tmp_path / "checkout"
    (root / "tools").mkdir(parents=True)
    monkeypatch.setattr(bundle, "ROOT", root)
    monkeypatch.setattr(bundle, "SELF_IMPROVE_REQUIRED",
                        tuple((root / "tools" / name, name) for _, name in bundle.SELF_IMPROVE_REQUIRED))
    monkeypatch.setattr(bundle, "SELF_IMPROVE_CASES_SOURCE", root / "tools" / "self_improve_cases")
    with pytest.raises(RuntimeError) as missing:
        bundle.install_self_improve(tmp_path / "out")
    for name in ("model_fetch.py", "model_profiles.json", "mvcr_prepare.py", "self_improve_lab.py",
                 "self_improve_cases", "OWNER_RUN_NEXT.md"):
        assert name in str(missing.value), name
    for _, name in bundle.SELF_IMPROVE_REQUIRED:
        (root / "tools" / name).write_text("# " + name, encoding="utf-8")
    (root / "tools" / "self_improve_cases" / "a").mkdir(parents=True)
    (root / "tools" / "self_improve_cases" / "a" / "case.json").write_text("{}", encoding="utf-8")
    (root / "docs" / "owner").mkdir(parents=True)
    (root / "docs" / "owner" / "OWNER_RUN_NEXT.md").write_text("# next", encoding="utf-8")
    shipped = bundle.install_self_improve(tmp_path / "out")["files"]
    assert "docs/owner/OWNER_RUN_NEXT.md" in shipped
    assert "app-support/self_improve_cases/a/case.json" in shipped
    assert (tmp_path / "out" / "app-support" / "model_fetch.py").is_file()
    assert (tmp_path / "out" / "docs" / "owner" / "OWNER_RUN_NEXT.md").is_file()


def test_owner_run_next_names_the_exact_commands_and_the_honest_scope():
    text = (REPO / "docs/owner/OWNER_RUN_NEXT.md").read_text(encoding="utf-8")
    for line in ("Owner-Run.cmd self-improve-mvcr plan", "Owner-Run.cmd self-improve-mvcr preflight",
                 "Owner-Run.cmd self-improve-mvcr bootstrap --allow-download --profile",
                 "Owner-Run.cmd stop", "Owner-Run.cmd resume", "--redo"):
        assert line in text, line
    assert "не запускался ни разу" in text and "OWNER_HARDWARE_CERTIFIED" in text


def test_summary_reads_the_real_model_fetch_plan_output(tmp_path):
    """Contract across the seam: the REAL tools/model_fetch.py plan output (not a
    guessed shape) is classified per file: a present file is REUSED and never
    downloaded, a missing pinned file is a download, an absent unpinned file is
    unknown (needs pin)."""
    import hashlib, json as _json, subprocess as _sp, sys as _sys
    repo = Path(__file__).resolve().parents[1]
    import importlib.util
    spec = importlib.util.spec_from_file_location("ort_contract", repo / "tools" / "owner_run_tomorrow.py")
    ort = importlib.util.module_from_spec(spec); spec.loader.exec_module(ort)
    data = b"GGUF-present"
    models = tmp_path / "models"; (models / "m").mkdir(parents=True)
    (models / "m" / "present.gguf").write_bytes(data)
    rt = {"engine": "llama.cpp", "version": "b10964", "version_match": "10964", "status": "UNPINNED",
          "status_reason": "t", "compatibility_notes": "t", "license": {"id": "MIT"},
          "version_regex": r"version:\s*b?(\d+)", "binary_candidates": []}
    def prof(pid, files, status):
        return {"id": pid, "kind": "model", "category": "llm_coding_agents", "role": "t", "runtime": {"ref": "rt"},
                "source": {"hf_repo": None, "revision": None}, "files": files, "optional": True, "status": status,
                "license": {"id": "MIT", "url": "https://example.invalid", "acceptance_required": False},
                "min_free_disk": {"bytes": 0 if status == "PINNED" else None, "basis": "t"},
                **({} if status == "PINNED" else {"status_reason": "t"})}
    manifest = {"schema_version": 1, "kind": "bossman.model_profiles", "runtimes": {"rt": rt}, "profiles": [
        prof("have", [{"id": "p", "dest_dir": "m", "name": "present.gguf", "size_bytes": len(data),
                       "sha256": hashlib.sha256(data).hexdigest(), "status": "PINNED", "url": "http://x.invalid/p"}], "PINNED"),
        prof("need", [{"id": "n", "dest_dir": "m", "name": "absent.gguf", "size_bytes": 10,
                       "sha256": "0" * 64, "status": "PINNED", "url": "http://x.invalid/n"}], "PINNED"),
        prof("unk", [{"id": "u", "dest_dir": "m", "name": "unk.gguf", "size_bytes": None, "sha256": None,
                      "status": "UNPINNED", "unpinned_reason": "t"}], "UNPINNED")]}
    mpath = tmp_path / "profiles.json"; mpath.write_text(_json.dumps(manifest))
    out = _sp.run([_sys.executable, str(repo / "tools" / "model_fetch.py"), "plan", "--manifest", str(mpath),
                   "--models-dir", str(models), "--margin-bytes", "0", "--json",
                   "--profile", "have", "--profile", "need", "--profile", "unk"],
                  capture_output=True, text=True, timeout=120)
    payload = _json.loads(out.stdout)
    assert payload.get("action") == "plan" and "profiles" in payload, out.stdout[:500]
    summary = ort.summarize_fetch_plan(payload)
    assert [r["id"] for r in summary["reuse"]] == ["have"]
    assert [r["id"] for r in summary["download"]] == ["need"] and summary["download_bytes"] == 10
    assert [r["id"] for r in summary["unknown"]] == ["unk"]


@pytest.mark.parametrize("variant,expected", [("happy", "WAIT_APPROVAL")])
def test_owner_run_understands_the_real_mvcr_prepare_output(tmp_path, variant, expected):
    """Contract across the seam: the REAL mvcr_prepare CLI (--json, synthetic
    offline fixtures) prints a status the owner-run maps, and nothing in it
    reads as a submission."""
    pytest.importorskip("pypdf", reason="mvcr_prepare needs pypdf")
    pytest.importorskip("sqlalchemy", reason="mvcr_prepare approvals need sqlalchemy (not in root-ci; runs in core-runtime)")
    pytest.importorskip("aiosqlite", reason="mvcr_prepare approvals need aiosqlite")
    import importlib.util, json as _json, subprocess as _sp, sys as _sys
    repo = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("ort_mvcr", repo / "tools" / "owner_run_tomorrow.py")
    ort = importlib.util.module_from_spec(spec); spec.loader.exec_module(ort)
    fspec = importlib.util.spec_from_file_location("mvcr_fx", repo / "tests" / "fixtures" / "mvcr" / "make_fixtures.py")
    fx = importlib.util.module_from_spec(fspec); _sys.modules["mvcr_fx"] = fx; fspec.loader.exec_module(fx)
    fixtures = fx.build(tmp_path / "fx", variant)
    owner = fx.build_owner_folder(tmp_path / "owner")
    facts = tmp_path / "facts.json"
    facts.write_text((repo / "tests" / "fixtures" / "mvcr" / "facts_synthetic.json").read_text(encoding="utf-8"),
                     encoding="utf-8")
    proc = _sp.run([_sys.executable, str(repo / "tools" / "mvcr_prepare.py"), "--owner-folder", str(owner),
                    "--facts", str(facts), "--out", str(tmp_path / "out"), "--offline-fixtures", str(fixtures),
                    "--json"], capture_output=True, text=True, encoding="utf-8", timeout=300)
    payload = ort._json_from(proc.stdout)
    assert isinstance(payload, dict), proc.stdout[-500:] + proc.stderr[-500:]
    raw = str(payload.get("status") or payload.get("final_status") or payload.get("verdict") or "").upper()
    assert raw == expected and raw in ort.MVCR_FINAL
    assert not ort._claims_submission(payload)



def test_lab_work_dir_outside_the_coding_roots_is_owner_required_not_a_401(home, env, tmp_path, bossman):
    """Negative control: the lab would get 403 from the product for a work dir
    outside the allowed coding roots; the runner says so up front."""
    handler, base = bossman
    handler.roots = ["/nonexistent-root-for-test"]
    _full(home)
    env["FAKE_PLAN"] = str(tmp_path / "plan.json")
    (tmp_path / "plan.json").write_text(json.dumps({"profiles": [{"id": "m", "status": "REUSED"}]}), encoding="utf-8")
    run_dir = tmp_path / "run-roots"
    _cli(home, env, "self-improve-mvcr", "bootstrap", "--run-dir", str(run_dir))
    _cli(home, env, "self-improve-mvcr", "self-improve", "--run-dir", str(run_dir), "--base-url", base,
         "--explore-repo", str(tmp_path))
    st = _state(run_dir)["steps"]
    assert st["self-improve:explore"]["status"] == "OWNER_REQUIRED"
    assert "вне разрешённых корней" in st["self-improve:explore"]["detail"]


def test_explore_without_a_repo_is_not_run_and_does_not_block_the_comparison(home, env, tmp_path, bossman):
    handler, base = bossman
    _full(home)
    env["FAKE_PLAN"] = str(tmp_path / "plan.json")
    (tmp_path / "plan.json").write_text(json.dumps({"profiles": [{"id": "m", "status": "REUSED"}]}), encoding="utf-8")
    run_dir = tmp_path / "run-noexplore"
    _cli(home, env, "self-improve-mvcr", "bootstrap", "--run-dir", str(run_dir))
    _cli(home, env, "self-improve-mvcr", "self-improve", "--run-dir", str(run_dir), "--base-url", base)
    st = _state(run_dir)["steps"]
    assert st["self-improve:explore"]["status"] == "NOT_RUN"
    assert st["self-improve:compare"]["status"] == "PASS"



def test_skills_stage_passes_on_a_real_catalog_and_fails_on_an_empty_one(home, env, tmp_path, bossman):
    handler, base = bossman
    run_dir = tmp_path / "run-skills"
    _cli(home, env, "self-improve-mvcr", "skills", "--run-dir", str(run_dir), "--base-url", base)
    st = _state(run_dir)["steps"]["skills"]
    assert st["status"] == "PASS" and "systematic-debugging" in st["detail"]
    handler.catalog = []                                   # negative control: nothing shipped
    run2 = tmp_path / "run-skills-empty"
    _cli(home, env, "self-improve-mvcr", "skills", "--run-dir", str(run2), "--base-url", base)
    assert _state(run2)["steps"]["skills"]["status"] == "FAIL"
