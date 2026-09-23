"""Coding path from the INSTALLED product, on real files and processes.

Run with the release's Python ``-I`` (``runtime\\python.exe -I app-support\\coding_path_owner.py``)
or from a checkout. What it does, all in a private temporary data dir:

  1. starts the DETERMINISTIC TEST MODEL (bossman.apprentice.scripted_model) —
     every record it touches is labelled deterministic_test_model=true;
  2. creates a disposable git repository with a real seeded defect and a
     unittest that fails on it;
  3. launches the installed backend (python -I -m bcc.app) with
     BOSSMAN_OPENHANDS_COMMAND pointing at the shipped local sidecar;
  4. logs in with the owner token, allows the repo root, checks
     /api/coding-tasks/readiness (a REAL handshake must pass), saves an agent,
     submits the coding task with verify_tests, waits for the terminal record;
  5. asserts: completed, the host-derived diff fixes the defect, Bossman's own
     verification passed, the owner repository is unchanged, the sandbox was
     removed, the model flag is set;
  6. negative control: a scripted "liar" that finishes without fixing must end
     failed by Bossman's verification, not completed;
  7. stops everything and checks no sidecar/model process is left.

Exit 0 = PASS (plumbing proven with a TEST model — not model quality), 1 = FAIL.
The JSON report never contains the owner token.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

FIX = [
    {"tool": "read_file", "args": {"path": "calc.py"}},
    {"tool": "edit_file", "args": {"path": "calc.py", "old": "return a - b", "new": "return a + b"}},
    {"tool": "run_tests", "args": {"paths": ["test_calc.py"], "runner": "unittest"}},
    {"tool": "finish", "args": {"summary": "add() fixed; unittest green"}},
]
LIAR = [{"tool": "finish", "args": {"summary": "done, trust me"}}]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8", timeout=60).stdout


def make_repo(parent: Path) -> Path:
    repo = parent / "owner repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "owner@example.invalid")
    _git(repo, "config", "user.name", "owner")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
    (repo / "test_calc.py").write_bytes(textwrap.dedent("""
        import unittest
        from calc import add
        class T(unittest.TestCase):
            def test_add(self):
                self.assertEqual(add(2, 3), 5)
        """).encode("utf-8"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seeded defect")
    return repo


def start_model(work: Path, name: str, turns: list) -> tuple[subprocess.Popen, str, str]:
    script = work / f"script-{name}.json"
    script.write_text(json.dumps({"name": name, "turns": turns}), encoding="utf-8")
    port_file = work / f"model-{name}.port"
    proc = subprocess.Popen([sys.executable, "-I", "-m", "bossman.apprentice.scripted_model",
                             "--script", str(script), "--port-file", str(port_file)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not port_file.exists():
        if proc.poll() is not None:
            raise RuntimeError("deterministic test model exited before listening")
        time.sleep(0.1)
    if not port_file.exists():
        raise RuntimeError("deterministic test model did not start")
    return proc, f"http://127.0.0.1:{port_file.read_text().strip()}", f"DETERMINISTIC-TEST-MODEL-{name}"


def sidecar_command(url: str, model: str) -> str:
    parts = [sys.executable, "-I", "-m", "bossman.apprentice.local_sidecar", "--endpoint", url, "--model", model]
    if os.name == "nt":
        return " ".join(f'"{p}"' if " " in p else p for p in parts)
    return " ".join(shlex.quote(p) for p in parts)


def _stop(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def run_case(owner, data: Path, repo: Path, url: str, model: str, *, verify: bool, report: dict, label: str,
             with_agent: bool = True) -> dict:
    os.environ["BOSSMAN_OPENHANDS_COMMAND"] = sidecar_command(url, model)
    port = owner.free_port()
    log_path = data / f"server-{label}.log"
    with log_path.open("wb") as log:
        server = owner.launch(data, port, log)
        client = None
        try:
            client, _identity = owner.connect(server, data, port)
            client.timeout = 180
            client.post("/api/terminal/roots", json={"roots": [str(repo.parent)]}).raise_for_status()
            ready = client.get("/api/coding-tasks/readiness").json()
            report[f"{label}_readiness"] = {k: ready.get(k) for k in ("available", "reason", "handshake")}
            if not ready.get("available"):
                raise RuntimeError(f"coding path not ready: {ready.get('reason')}")
            body = {"instruction": "add() returns a difference; fix it and prove it with the test",
                    "source_repo": str(repo), "allowed_paths": ["calc.py"], "timeout_seconds": 300,
                    "use_memory": False}
            if with_agent:
                agent = client.post("/api/agents", json={
                    "name": f"TOOL_FIRST-{label}", "role": "coding-path check", "system_prompt": "Tools first.",
                    "tools": ["read_file", "edit_file", "run_tests", "finish"], "max_steps": 12,
                    "permissions": {"require_tests_before_finish": True, "use_memory": False}})
                agent.raise_for_status()
                body["agent_id"] = agent.json()["id"]
            if verify:
                body["verify_tests"] = ["test_calc.py"]
            created = client.post("/api/coding-tasks", json=body)
            created.raise_for_status()
            task_id = created.json()["id"]
            deadline = time.monotonic() + 400
            record = {}
            while time.monotonic() < deadline:
                record = client.get(f"/api/coding-tasks/{task_id}").json()
                if record.get("status") in ("completed", "failed", "blocked"):
                    break
                time.sleep(0.5)
            return record
        finally:
            if client is not None:
                client.close()
            owner.stop(server)


def utf8_console() -> None:
    """`-I` ignores PYTHONUTF8/PYTHONIOENCODING: on a cp1252 Windows console the
    first Cyrillic reason would crash the runner (the run-132 class of defect)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None) -> int:
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="")
    args = ap.parse_args(argv)
    from bcc import owner_acceptance as owner  # the installed product's own launcher

    report: dict = {"check": "coding_path_installed", "python": sys.executable,
                    "model": "DETERMINISTIC-TEST-MODEL", "model_kind": "MOCK_MODEL", "verdict": "FAIL", "checks": {}}
    models = []
    try:
        with tempfile.TemporaryDirectory(prefix="bossman coding path ", ignore_cleanup_errors=True) as folder:
            work = Path(folder)
            repo = make_repo(work / "repos")
            before = (repo / "calc.py").read_bytes()
            data = work / "data"
            data.mkdir()
            proc, url, model = start_model(work, "fix", FIX)
            models.append(proc)
            rec = run_case(owner, data, repo, url, model, verify=True, report=report, label="fix")
            report["fix_record"] = {k: rec.get(k) for k in ("status", "error", "changed_files", "verification",
                                                             "sandbox_cleanup", "agent", "outcome")}
            report["fix_record"]["sidecar"] = {k: (rec.get("sidecar") or {}).get(k) for k in (
                "executor", "model", "deterministic_test_model", "model_kind", "stop_reason", "steps", "tests", "profile")}
            c = report["checks"]
            c["completed"] = rec.get("status") == "completed"
            c["diff_fixes_defect"] = "+    return a + b" in (rec.get("diff") or "")
            c["host_verification_passed"] = bool((rec.get("verification") or {}).get("passed"))
            c["owner_repo_unchanged"] = (repo / "calc.py").read_bytes() == before
            c["sandbox_removed"] = bool((rec.get("sandbox_cleanup") or {}).get("removed"))
            c["labelled_test_model"] = bool((rec.get("sidecar") or {}).get("deterministic_test_model")) and \
                (rec.get("sidecar") or {}).get("model_kind") == "MOCK_MODEL"
            _stop(proc)

            proc, url, model = start_model(work, "liar", LIAR)
            models.append(proc)
            data2 = work / "data2"
            data2.mkdir()
            # No profile: nothing in the sidecar stops the early finish, so only
            # Bossman's own verification can refuse it — that is what is measured.
            rec2 = run_case(owner, data2, repo, url, model, verify=True, report=report, label="liar",
                            with_agent=False)
            report["liar_record"] = {k: rec2.get(k) for k in ("status", "error", "verification")}
            report["liar_record"]["sidecar_status"] = (rec2.get("sidecar") or {}).get("status")
            c["negative_control_liar_failed"] = rec2.get("status") == "failed" and \
                (rec2.get("sidecar") or {}).get("status") == "completed"
            _stop(proc)
            report["verdict"] = "PASS" if all(c.values()) else "FAIL"
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"[:800]
    finally:
        for proc in models:
            _stop(proc)
        report["leftover_model_processes"] = [p.pid for p in models if p.poll() is None]
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    print(f"BOSSMAN_CODING_PATH={report['verdict']} (MOCK_MODEL — plumbing, not model quality)")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
