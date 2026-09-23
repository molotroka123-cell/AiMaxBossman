"""The local coding sidecar end to end: real sidecar process, real HTTP model
server (the DETERMINISTIC TEST MODEL — labelled as such in every response),
real disposable git repository, real OpenHandsClient evidence derivation.

What this proves: the protocol, the tool loop, the scope/`.git` refusals, the
tests-before-finish rule, the test guard and process-tree hygiene. What it
cannot prove: that a real local model picks good edits — that is the owner-run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from bossman.apprentice import local_sidecar as ls
from bossman.apprentice.openhands_client import OpenHandsClient, OpenHandsError, OpenHandsRequest
from bossman.apprentice.proc_tree import run_tree
from bossman.apprentice.scripted_model import MARKER, serve_in_thread


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    (root / "calc.py").write_bytes(b"def add(a, b):\r\n    return a - b\r\n")   # CRLF on purpose
    (root / "test_calc.py").write_text(textwrap.dedent("""
        import unittest
        from calc import add
        class T(unittest.TestCase):
            def test_add(self):
                self.assertEqual(add(2, 3), 5)
        """), encoding="utf-8")
    (root / "SECRET.txt").write_text("owner only\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def serve(turns, name="t"):
    server, url = serve_in_thread({"name": name, "turns": turns})
    return server, url, f"{MARKER}-{name}"


def client_for(url, model, *extra):
    return OpenHandsClient(command=[sys.executable, "-m", "bossman.apprentice.local_sidecar",
                                    "--endpoint", url, "--model", model, *extra])


FIX = [
    {"tool": "read_file", "args": {"path": "calc.py"}},
    {"tool": "edit_file", "args": {"path": "calc.py", "old": "return a - b", "new": "return a + b"}},
    {"tool": "run_tests", "args": {"paths": ["test_calc.py"], "runner": "unittest"}},
    {"tool": "finish", "args": {"summary": "add fixed"}},
]


def test_handshake_proves_protocol_endpoint_and_a_real_tool_call():
    server, url, model = serve(FIX)
    try:
        resp = client_for(url, model).handshake(timeout_seconds=60)
    finally:
        server.shutdown()
    assert resp["status"] == "ready" and resp["executor"] == "bossman-local-sidecar"
    assert resp["tool_call_ok"] is True and resp["deterministic_test_model"] is True
    assert "run_tests" in resp["tools"]


def test_handshake_fails_when_the_model_is_not_served_or_endpoint_is_down():
    server, url, _model = serve(FIX)
    try:
        with pytest.raises(OpenHandsError, match="not ready"):
            client_for(url, "some-other-model").handshake(timeout_seconds=60)
    finally:
        server.shutdown()
    with pytest.raises(OpenHandsError, match="not ready"):
        client_for("http://127.0.0.1:9", "m").handshake(timeout_seconds=60)


def test_a_scripted_fix_goes_through_tools_tests_and_independent_diff(repo):
    server, url, model = serve(FIX)
    try:
        result = client_for(url, model).run(OpenHandsRequest(
            "fix add", repo, ("calc.py",), ("SECRET.txt",), timeout_seconds=120,
            context={"profile": {"name": "TOOL_FIRST", "require_tests_before_finish": True}}))
    finally:
        server.shutdown()
    assert result.status == "completed", result.sidecar
    assert result.changed_files == ("calc.py",)
    assert "+    return a + b" in result.diff.replace("\r", "")
    # the edit kept the file's CRLF endings: only one line changed
    assert (repo / "calc.py").read_bytes() == b"def add(a, b):\r\n    return a + b\r\n"
    assert result.sidecar["tests"]["passed"] is True and result.sidecar["tests"]["runner"] == "unittest"
    assert result.sidecar["deterministic_test_model"] is True and result.sidecar["stop_reason"] == "finished"


def test_finish_before_tests_is_refused_when_the_profile_requires_tests(repo):
    turns = [FIX[1], {"tool": "finish", "args": {"summary": "too early"}}, FIX[2], FIX[3]]
    server, url, model = serve(turns)
    try:
        result = client_for(url, model).run(OpenHandsRequest(
            "fix add", repo, ("calc.py",), timeout_seconds=120,
            context={"profile": {"require_tests_before_finish": True}}))
    finally:
        server.shutdown()
    calls = [(c["tool"], c["ok"]) for c in result.sidecar["tool_calls"]]
    assert ("finish", False) in calls and calls[-1] == ("finish", True)
    assert result.status == "completed"
    # negative control: without the rule an early finish is accepted
    git(repo, "checkout", "--", "calc.py")
    server, url, model = serve(turns)
    try:
        result = client_for(url, model).run(OpenHandsRequest("fix add", repo, ("calc.py",), timeout_seconds=120))
    finally:
        server.shutdown()
    assert [c["tool"] for c in result.sidecar["tool_calls"]] == ["edit_file", "finish"]


def test_a_recipe_required_check_must_be_green_before_finish(repo):
    recipe = {"id": "R-add", "symptom": "add wrong", "required_check": {"tool": "run_tests",
                                                                         "args": {"paths": ["test_calc.py"]}}}
    turns = [FIX[1], {"tool": "finish", "args": {"summary": "x"}}, FIX[2], FIX[3]]
    server, url, model = serve(turns)
    try:
        result = client_for(url, model).run(OpenHandsRequest(
            "fix add", repo, ("calc.py",), timeout_seconds=120, context={"recipes": [recipe]}))
    finally:
        server.shutdown()
    assert result.status == "completed" and result.sidecar["recipes_applied"] == ["R-add"]
    assert ("finish", False) in [(c["tool"], c["ok"]) for c in result.sidecar["tool_calls"]]


def test_scope_protected_and_git_are_refused_inside_the_loop(repo):
    turns = [{"tool": "write_file", "args": {"path": "SECRET.txt", "content": "x"}},
             {"tool": "write_file", "args": {"path": "other.py", "content": "x"}},
             {"tool": "read_file", "args": {"path": ".git/config"}},
             {"tool": "read_file", "args": {"path": "../outside.txt"}},
             {"tool": "finish", "args": {"summary": "nothing"}}]
    server, url, model = serve(turns)
    try:
        result = client_for(url, model).run(OpenHandsRequest(
            "x", repo, ("calc.py",), ("SECRET.txt",), timeout_seconds=120))
    finally:
        server.shutdown()
    assert [c["ok"] for c in result.sidecar["tool_calls"]] == [False, False, False, False, True]
    assert result.changed_files == ()


@pytest.mark.parametrize("runner", ["unittest", "pytest"])
def test_the_test_guard_refuses_reading_owner_files_outside_the_workspace(repo, tmp_path, runner):
    if runner == "pytest" and not ls._pytest_available():
        pytest.skip("pytest not installed in this runtime")
    owner_doc = tmp_path / "Documents" / "passport.txt"
    owner_doc.parent.mkdir()
    owner_doc.write_text("PERSONAL", encoding="utf-8")
    (repo / "test_peek.py").write_text(textwrap.dedent(f"""
        import unittest
        class T(unittest.TestCase):
            def test_peek(self):
                with open({str(owner_doc)!r}) as f:
                    self.assertEqual(f.read(), "PERSONAL")
        """), encoding="utf-8")
    ws = ls.Workspace(repo, ["."], [])
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    res = ls.tool_run_tests(ws, {"paths": ["test_peek.py"], "runner": runner}, scratch=scratch,
                            deadline=time.monotonic() + 60, test_timeout=60)
    assert res["passed"] is False and "sidecar guard" in res["output_tail"]
    # positive control: reading inside the workspace works under the same guard
    (repo / "test_inside.py").write_text(textwrap.dedent("""
        import unittest, pathlib
        class T(unittest.TestCase):
            def test_inside(self):
                self.assertIn("add", pathlib.Path("calc.py").read_text())
        """), encoding="utf-8")
    res = ls.tool_run_tests(ws, {"paths": ["test_inside.py"], "runner": runner}, scratch=scratch,
                            deadline=time.monotonic() + 60, test_timeout=60)
    assert res["passed"] is True, res["output_tail"]


# The Windows archive runs an *embeddable* Python: its ._pth file makes the
# interpreter ignore PYTHONPATH and keep the current directory off sys.path.
# `python -I` reproduces exactly that on any OS (isolated: -E -P -s). Found by
# the installed-archive coding path on Windows CI (owner-experience, f25d6fd4):
# `ModuleNotFoundError: No module named 'test_calc'` for the student AND for
# Bossman's independent verification — and, worse, the guard (sitecustomize
# via PYTHONPATH) was never loaded there.
EMBEDDED_LIKE = [sys.executable, "-I"]


@pytest.mark.parametrize("runner", ["unittest", "pytest"])
def test_workspace_tests_import_under_an_embedded_like_interpreter(repo, tmp_path, monkeypatch, runner):
    if runner == "pytest" and not ls._pytest_available():
        pytest.skip("pytest not installed in this runtime")
    monkeypatch.setattr(ls, "_interpreter", lambda: list(EMBEDDED_LIKE))
    (repo / "test_inside.py").write_text(textwrap.dedent("""
        import unittest
        import calc
        class T(unittest.TestCase):
            def test_inside(self):
                self.assertTrue(hasattr(calc, "add"))
        """), encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    res = ls.tool_run_tests(ls.Workspace(repo, ["."], []), {"paths": ["test_inside.py"], "runner": runner},
                            scratch=scratch, deadline=time.monotonic() + 60, test_timeout=60)
    assert res["passed"] is True, res["output_tail"]


@pytest.mark.parametrize("runner", ["unittest", "pytest"])
def test_the_guard_is_active_under_an_embedded_like_interpreter(repo, tmp_path, monkeypatch, runner):
    if runner == "pytest" and not ls._pytest_available():
        pytest.skip("pytest not installed in this runtime")
    monkeypatch.setattr(ls, "_interpreter", lambda: list(EMBEDDED_LIKE))
    owner_doc = tmp_path / "Documents" / "passport.txt"
    owner_doc.parent.mkdir()
    owner_doc.write_text("PERSONAL", encoding="utf-8")
    (repo / "test_peek.py").write_text(textwrap.dedent(f"""
        import unittest
        class T(unittest.TestCase):
            def test_peek(self):
                with open({str(owner_doc)!r}) as f:
                    self.assertEqual(f.read(), "PERSONAL")
        """), encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    res = ls.tool_run_tests(ls.Workspace(repo, ["."], []), {"paths": ["test_peek.py"], "runner": runner},
                            scratch=scratch, deadline=time.monotonic() + 60, test_timeout=60)
    assert res["passed"] is False and "sidecar guard" in res["output_tail"], res["output_tail"]


def test_the_test_process_does_not_inherit_owner_secrets(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-should-not-leak")
    (repo / "test_env.py").write_text(textwrap.dedent("""
        import os, unittest
        class T(unittest.TestCase):
            def test_env(self):
                self.assertNotIn("OPENROUTER_API_KEY", os.environ)
                self.assertTrue(os.environ["HOME"].startswith(os.environ["BOSSMAN_SIDECAR_SCRATCH"]))
        """), encoding="utf-8")
    scratch = tmp_path / "s"
    scratch.mkdir()
    res = ls.tool_run_tests(ls.Workspace(repo, ["."], []), {"paths": ["test_env.py"], "runner": "unittest"},
                            scratch=scratch, deadline=time.monotonic() + 60, test_timeout=60)
    assert res["passed"] is True, res["output_tail"]


def _alive(pid: int) -> bool:
    try:
        import psutil
    except ImportError:  # pragma: no cover
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_timeout_kills_the_grandchild_too(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    code = textwrap.dedent(f"""
        import subprocess, sys, time, pathlib
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        pathlib.Path({str(pidfile)!r}).write_text(str(p.pid))
        time.sleep(120)
        """)
    res = run_tree([sys.executable, "-c", code], timeout=3, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert res.timed_out is True
    grandchild = int(pidfile.read_text())
    for _ in range(50):
        if not _alive(grandchild):
            break
        time.sleep(0.1)
    assert not _alive(grandchild), "grandchild survived the timeout (orphan)"


def test_a_normally_finished_child_does_not_leave_its_grandchild_running(tmp_path):
    pidfile = tmp_path / "gc.pid"
    code = textwrap.dedent(f"""
        import subprocess, sys, pathlib
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pathlib.Path({str(pidfile)!r}).write_text(str(p.pid))
        """)
    res = run_tree([sys.executable, "-c", code], timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert res.timed_out is False and res.returncode == 0
    grandchild = int(pidfile.read_text())
    for _ in range(50):
        if not _alive(grandchild):
            break
        time.sleep(0.1)
    assert not _alive(grandchild)


def test_the_sidecar_stdout_carries_exactly_one_json_line(repo):
    server, url, model = serve(FIX)
    try:
        proc = subprocess.run([sys.executable, "-m", "bossman.apprentice.local_sidecar", "--endpoint", url,
                               "--model", model], input=json.dumps({
                                   "schema": "bossman.openhands.v1", "instruction": "fix", "workspace": str(repo),
                                   "allowed_paths": ["calc.py"], "timeout_seconds": 60}),
                              capture_output=True, text=True, timeout=120)
    finally:
        server.shutdown()
    lines = [x for x in proc.stdout.splitlines() if x.strip()]
    assert len(lines) == 1 and json.loads(lines[0])["status"] == "completed", proc.stdout + proc.stderr


def test_a_windows_command_with_a_quoted_path_with_spaces_splits_into_a_usable_executable():
    from bossman.apprentice.openhands_client import split_command
    cmd = r'"C:\Owner acceptance with spaces\BOSSMAN\runtime\python.exe" -I -m bossman.apprentice.local_sidecar --model m'
    parts = split_command(cmd, windows=True)
    assert parts[0] == r"C:\Owner acceptance with spaces\BOSSMAN\runtime\python.exe"
    assert parts[1:] == ("-I", "-m", "bossman.apprentice.local_sidecar", "--model", "m")
    # unquoted tokens and backslashes are untouched; POSIX splitting unchanged
    assert split_command(r"C:\py\python.exe -m x", windows=True) == (r"C:\py\python.exe", "-m", "x")
    assert split_command("'/opt/my py/python' -m x", windows=False) == ("/opt/my py/python", "-m", "x")


def test_tool_choice_is_required_and_falls_back_to_auto_on_a_400():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            return

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body["tool_choice"])
            if body["tool_choice"] == "required":
                self.send_response(400); self.end_headers(); return
            data = json.dumps({"choices": [{"message": {"role": "assistant", "content": "x"}}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        m = ls.Model(f"http://127.0.0.1:{srv.server_address[1]}", "m", None)
        m.chat([{"role": "user", "content": "hi"}], tools=ls.TOOL_SPECS[:1], timeout=10)
        m.chat([{"role": "user", "content": "hi"}], tools=ls.TOOL_SPECS[:1], timeout=10)
    finally:
        srv.shutdown()
    assert seen == ["required", "auto", "auto"]   # tried required once, then remembered auto
