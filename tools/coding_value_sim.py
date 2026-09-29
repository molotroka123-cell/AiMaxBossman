"""Two synthetic coding jobs through the real Bossman CMD/API coding path.

No money, customer repository, credential, apply, push, or deployment is used.
The dollar labels are hypothetical job values, not earnings. The sidecar must
pass a real model handshake; mock models are allowed only with --allow-mock.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
PACKAGES = (ROOT / "command-center", ROOT / "bossman-core")
CHILD_PYTHON = sys.executable
CHILD_PACKAGES = PACKAGES
for package in reversed(PACKAGES):
    sys.path.insert(0, str(package))

from bcc import owner_acceptance as owner  # noqa: E402
from bcc.auth import _restrict_to_owner  # noqa: E402


CASES = {
    "medium": {
        "value_usd": 10,
        "source": "exporter.py",
        "test": "test_exporter.py",
        "instruction": ("Add --csv PATH to exporter.py. It must write the existing rows "
                        "with sku,description,qty columns in that order using correct CSV quoting. "
                        "Without --csv, preserve the existing stdout. Edit only exporter.py; run the test."),
        "files": {
            "exporter.py": '''ROWS = [("A-01", "paper, blue", 2), ("A-02", "cable", 1)]


def render():
    return "\\n".join(f"{sku}: {description} x{qty}" for sku, description, qty in ROWS)


if __name__ == "__main__":
    print(render())
''',
            "test_exporter.py": '''import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from exporter import render


class ExporterTests(unittest.TestCase):
    def test_existing_stdout(self):
        p = subprocess.run([sys.executable, "exporter.py"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), render())

    def test_csv_flag(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.csv"
            p = subprocess.run([sys.executable, "exporter.py", "--csv", str(out)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            with out.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows, [
                {"sku": "A-01", "description": "paper, blue", "qty": "2"},
                {"sku": "A-02", "description": "cable", "qty": "1"},
            ])


if __name__ == "__main__":
    unittest.main()
''',
        },
    },
    "hard": {
        "value_usd": 20,
        "source": "invoice.py",
        "test": "test_invoice.py",
        "instruction": ("Fix vat_total(lines) in invoice.py. Each line is (id, amount), where "
                        "amount may use a decimal comma. Compute 21% VAT per line with Decimal "
                        "ROUND_HALF_UP to cents, then sum; negative refunds must subtract. "
                        "Duplicate ids and invalid amounts must raise ValueError. Return a two-decimal "
                        "string. Edit only invoice.py and run the test."),
        "files": {
            "invoice.py": '''def vat_total(lines):
    total = 0.0
    for _line_id, amount in lines:
        total += float(amount) * 0.21
    return f"{round(total, 2):.2f}"
''',
            "test_invoice.py": '''import unittest
from invoice import vat_total


class InvoiceTests(unittest.TestCase):
    def test_round_each_line(self):
        self.assertEqual(vat_total([("a", "0.025"), ("b", "0.025")]), "0.02")

    def test_decimal_comma_and_refund(self):
        self.assertEqual(vat_total([("a", "10,00"), ("b", "-1,00")]), "1.89")

    def test_duplicate_is_refused(self):
        with self.assertRaises(ValueError):
            vat_total([("a", "1"), ("a", "2")])

    def test_invalid_is_refused(self):
        with self.assertRaises(ValueError):
            vat_total([("a", "oops")])


if __name__ == "__main__":
    unittest.main()
''',
        },
    },
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8", timeout=30).stdout


def make_repo(parent: Path, label: str) -> Path:
    case = CASES[label]
    repo = parent / label
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "sim@example.invalid")
    _git(repo, "config", "user.name", "synthetic simulation")
    _git(repo, "config", "core.autocrlf", "false")
    for name, content in case["files"].items():
        (repo / name).write_text(content, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "synthetic starting defect")
    return repo


def _python_module(module: str, *args: str) -> list[str]:
    if not CHILD_PACKAGES:
        return [CHILD_PYTHON, "-I", "-m", module, *args]
    bootstrap = ("import sys,runpy;sys.path[:0]="
                 + repr([str(p) for p in CHILD_PACKAGES])
                 + ";sys.argv=['" + module + "']+sys.argv[1:];"
                 + "runpy.run_module('" + module + "',run_name='__main__')")
    return [CHILD_PYTHON, "-I", "-c", bootstrap, *args]


def _sidecar_command(endpoint: str, model: str, max_steps: int) -> str:
    args = _python_module("bossman.apprentice.local_sidecar", "--endpoint", endpoint,
                          "--model", model, "--max-steps", str(max_steps))
    return subprocess.list2cmdline(args) if os.name == "nt" else __import__("shlex").join(args)


def _launch(data: Path, port: int, log) -> subprocess.Popen:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "DATABASE_URL", "BCC_UI_DIR", "BCC_TOKEN_STDOUT"):
        env.pop(key, None)
    env["BCC_DATA_DIR"] = str(data)
    return subprocess.Popen(_python_module("bcc.app", "--host", "127.0.0.1", "--port", str(port)),
                            cwd=data, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)


def _host_verify(repo: Path, diff: str, case: dict, parent: Path) -> dict:
    """Fresh clone and a verifier outside Bossman's sidecar and task record."""
    parent.mkdir(parents=True, exist_ok=True)
    candidate = parent / "host-candidate"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(repo), str(candidate)],
                   check=True, capture_output=True, timeout=30)
    patch = parent / "candidate.patch"
    patch.write_text(diff, encoding="utf-8", newline="\n")
    apply = subprocess.run(["git", "-C", str(candidate), "apply", "--check", str(patch)],
                           capture_output=True, text=True, timeout=30)
    if apply.returncode:
        return {"passed": False, "reason": "patch_does_not_apply"}
    subprocess.run(["git", "-C", str(candidate), "apply", str(patch)], check=True,
                   capture_output=True, timeout=30)
    changed = _git(candidate, "diff", "--name-only").splitlines()
    if changed != [case["source"]]:
        return {"passed": False, "reason": "unexpected_changed_files", "changed": changed}
    checks = []
    for _ in range(3):
        p = subprocess.run([sys.executable, "-m", "unittest", case["test"]], cwd=candidate,
                           capture_output=True, text=True, timeout=30)
        checks.append(p.returncode == 0)
    hidden = _hidden_check(candidate, case)
    return {"passed": all(checks) and hidden, "three_green": checks,
            "hidden_check": hidden, "changed": changed,
            "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest()}


def _hidden_check(candidate: Path, case: dict) -> bool:
    if case["source"] == "exporter.py":
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "quoted.csv"
            run = subprocess.run([sys.executable, "-I", "exporter.py", "--csv", str(output)],
                                 cwd=candidate, capture_output=True, timeout=20)
            if run.returncode or not output.is_file():
                return False
            with output.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                return reader.fieldnames == ["sku", "description", "qty"] and list(reader) == [
                    {"sku": "A-01", "description": "paper, blue", "qty": "2"},
                    {"sku": "A-02", "description": "cable", "qty": "1"}]
    code = ("import sys; sys.path.insert(0, " + repr(str(candidate)) + "); "
            "from invoice import vat_total; "
            "assert vat_total([('a','0.025'),('b','0.025')]) == '0.02'; "
            "assert vat_total([('a','-0.025')]) == '-0.01'; "
            "assert vat_total([('a','10,00'),('b','-1,00')]) == '1.89'")
    run = subprocess.run([sys.executable, "-I", "-c", code], cwd=candidate,
                         capture_output=True, timeout=20)
    return run.returncode == 0


def _save(path: Path, report: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _restrict_to_owner(temporary)
    temporary.replace(path)
    _restrict_to_owner(path)


def run_case(label: str, repo: Path, data: Path, task_timeout: int, allow_mock: bool) -> dict:
    case = CASES[label]
    baseline = subprocess.run([sys.executable, "-m", "unittest", case["test"]],
                              cwd=repo, capture_output=True, timeout=30)
    if baseline.returncode == 0:
        raise RuntimeError(f"{label} baseline unexpectedly green")
    before = _git(repo, "status", "--porcelain")
    port = owner.free_port()
    with (data / f"backend-{label}.log").open("wb") as log:
        server = _launch(data, port, log)
        client = None
        try:
            client, _ = owner.connect(server, data, port)
            client.timeout = 180
            client.post("/api/terminal/roots", json={"roots": [str(repo.parent)]}).raise_for_status()
            readiness = client.get("/api/coding-tasks/readiness").json()
            if not readiness.get("available"):
                return {"status": "BLOCKED", "reason": str(readiness.get("reason"))[:300],
                        "readiness": readiness}
            cli = _python_module("bcc.terminal_cli", "code", case["instruction"], "--repo", str(repo),
                                 "--allow", case["source"], "--protect", case["test"],
                                 "--verify", case["test"], "--no-memory", "--timeout", str(task_timeout),
                                 "--url", f"http://127.0.0.1:{port}", "--data-dir", str(data),
                                 "--output-format", "json")
            started = time.monotonic()
            proc = subprocess.run(cli, cwd=data, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=task_timeout + 30)
            duration = round(time.monotonic() - started, 2)
            try:
                output = json.loads(proc.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                output = {"raw_stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]}
            task_id = output.get("coding_task_id")
            record = client.get(f"/api/coding-tasks/{task_id}").json() if task_id else {}
            diff = record.get("diff") or ""
            host = _host_verify(repo, diff, case, data / label) if diff else {"passed": False, "reason": "no_diff"}
            unchanged = _git(repo, "status", "--porcelain") == before
            sidecar = record.get("sidecar") or {}
            ok = (proc.returncode == 0 and record.get("status") == "completed"
                  and bool((record.get("verification") or {}).get("passed"))
                  and host.get("passed") and unchanged
                  and bool((record.get("sandbox_cleanup") or {}).get("removed"))
                  and (allow_mock or (sidecar.get("model_kind") == "REAL_MODEL"
                                      and not sidecar.get("deterministic_test_model"))))
            return {"status": "PASS" if ok else "FAIL", "value_usd_hypothetical": case["value_usd"],
                    "duration_seconds": duration, "cli_exit": proc.returncode,
                    "task_id": task_id, "bossman_status": record.get("status"),
                    "bossman_verification": record.get("verification"), "host_verification": host,
                    "owner_repo_unchanged": unchanged, "sandbox_cleanup": record.get("sandbox_cleanup"),
                    "model": sidecar.get("model"), "model_kind": sidecar.get("model_kind"),
                    "deterministic_test_model": sidecar.get("deterministic_test_model"),
                    "sidecar_stop_reason": sidecar.get("stop_reason"),
                    "sidecar_steps": sidecar.get("steps"),
                    "sidecar_tool_calls": sidecar.get("tool_calls"),
                    "cli": output, "error": str(record.get("error") or proc.stderr[-300:])[:500]}
        finally:
            if client is not None:
                client.close()
            owner.stop(server)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="bossman-fast-qwen36-35b-a3b-q5:latest")
    parser.add_argument("--output", type=Path, help="private checkpoint directory")
    parser.add_argument("--installed-python", type=Path,
                        help="use this installed Bossman runtime for backend, CLI, and sidecar")
    parser.add_argument("--task-timeout", type=int, default=420, help="seconds per case (30..7200)")
    parser.add_argument("--allow-mock", action="store_true", help="plumbing test only")
    args = parser.parse_args(argv)
    if not 30 <= args.task_timeout <= 7200:
        parser.error("--task-timeout must be between 30 and 7200")
    endpoint = urlsplit(args.endpoint)
    if endpoint.scheme != "http" or endpoint.hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("--endpoint must be a local HTTP loopback server")
    global CHILD_PYTHON, CHILD_PACKAGES
    installed_sha = None
    if args.installed_python:
        if not args.installed_python.is_file():
            parser.error("--installed-python does not exist")
        CHILD_PYTHON, CHILD_PACKAGES = str(args.installed_python.resolve()), ()
        probe = subprocess.run([CHILD_PYTHON, "-I", "-c",
                                "import json,pathlib,bcc; p=pathlib.Path(bcc.__file__).with_name('_build.json'); "
                                "print(p.read_text(encoding='utf-8'))"], capture_output=True,
                               text=True, encoding="utf-8", timeout=15)
        if probe.returncode:
            parser.error("installed runtime has no readable source manifest")
        try:
            manifest = json.loads(probe.stdout)
            installed_sha = manifest["source_sha"]
            if manifest.get("source_dirty") is not False or not re.fullmatch(r"[0-9a-f]{40}", installed_sha):
                raise ValueError("invalid installed identity")
        except (ValueError, KeyError, TypeError):
            parser.error("installed runtime has an invalid source identity")
    if "DETERMINISTIC-TEST-MODEL" in args.model and not args.allow_mock:
        parser.error("mock model requires --allow-mock and cannot prove autonomy")
    home = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Bossman" / "owner-run" / "coding-value-sim"
    output = args.output or home / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + os.urandom(3).hex())
    output.mkdir(parents=True, exist_ok=False)
    _restrict_to_owner(output)
    original = os.environ.get("BOSSMAN_OPENHANDS_COMMAND")
    os.environ["BOSSMAN_OPENHANDS_COMMAND"] = _sidecar_command(args.endpoint, args.model, 36)
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True).stdout.strip()
    report = {"schema": "bossman.coding_value_sim.v1", "source_sha": sha,
              "source_worktree": str(ROOT), "started_at": datetime.now(timezone.utc).isoformat(),
              "execution_runtime": CHILD_PYTHON, "installed_source_sha": installed_sha,
              "model_requested": args.model,
              "model_kind_requested": ("MOCK_MODEL" if "DETERMINISTIC-TEST-MODEL" in args.model
                                       else "REAL_MODEL"),
              "money_moved": False, "customer_data_used": False, "cases": {}, "verdict": "INCOMPLETE"}
    _save(output / "checkpoint.json", report)
    try:
        repos = output / "synthetic-repos"
        repos.mkdir()
        for label in ("medium", "hard"):
            repo = make_repo(repos, label)
            data = output / f"private-bcc-{label}"
            data.mkdir()
            _restrict_to_owner(data)
            try:
                report["cases"][label] = run_case(label, repo, data, args.task_timeout, args.allow_mock)
            except Exception as exc:  # retain the first checkpoint and continue the other case
                report["cases"][label] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"[:500]}
            _save(output / "checkpoint.json", report)
        report["verdict"] = (("PLUMBING_PASS" if args.allow_mock else "PASS")
                             if all(v.get("status") == "PASS" for v in report["cases"].values())
                             else "BLOCKED" if any(v.get("status") == "BLOCKED" for v in report["cases"].values())
                             else "FAIL")
    finally:
        if original is None:
            os.environ.pop("BOSSMAN_OPENHANDS_COMMAND", None)
        else:
            os.environ["BOSSMAN_OPENHANDS_COMMAND"] = original
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _save(output / "checkpoint.json", report)
    print(json.dumps({"verdict": report["verdict"], "checkpoint": str(output / "checkpoint.json"),
                      "cases": {k: v.get("status") for k, v in report["cases"].items()}}, ensure_ascii=False))
    return 0 if report["verdict"] in ("PASS", "PLUMBING_PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
