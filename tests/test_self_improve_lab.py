"""tools/self_improve_lab.py against a stub of the Bossman coding-tasks API.

The real sidecar is not runnable here, so a small HTTP server imitates the product
routes the lab drives (identity, readiness, lab agents, coding tasks, coding recipes).
The stub acts on the clean copy it is handed exactly like a student would: it edits the
code, drops files, and the lab must derive everything else — clean copies, budgets,
the hidden verifier, outcomes — itself. The hidden verifier is REAL (unittest in a
subprocess), so a "fix" passes only when the code is actually fixed.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "self_improve_lab.py"


def _load():
    spec = importlib.util.spec_from_file_location("self_improve_lab_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lab = _load()
TOKEN = "stub-token"
VARIANT_IDS = {v: i + 1 for i, v in enumerate(lab.VARIANTS)}
ID_VARIANT = {i: v for v, i in VARIANT_IDS.items()}


# ------------------------------------------------------------------ student behaviours
def _git(repo, *args, input_text=None):
    return subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=repo, input=input_text,
                          text=True, capture_output=True, check=True).stdout


def _target(repo: Path) -> Path:
    for rel in ("invoicekit/money.py", "stockkit/weight.py"):
        if (repo / rel).is_file():
            return repo / rel
    raise AssertionError("unknown synthetic repo")


def _fix(repo: Path, marker: str) -> None:
    path = _target(repo)
    src = path.read_text(encoding="utf-8")
    old = '    cleaned = text.strip().replace(" ", "")\n'
    assert old in src
    new = ('    cleaned = "".join(text.split())\n'
           '    if cleaned.count(",") == 1 and "." not in cleaned:\n'
           '        cleaned = cleaned.replace(",", ".")\n')
    path.write_text(src.replace(old, new), encoding="utf-8", newline="\n")
    (repo / "tests" / "test_regression_comma.py").write_text(
        "import unittest\n\n\nclass R(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
        encoding="utf-8", newline="\n")
    (repo / "tests" / f"_dropped_by_{marker}.txt").write_text("student scratch\n", encoding="utf-8")


def _wrong(repo: Path, marker: str) -> None:
    path = _target(repo)
    path.write_text(path.read_text(encoding="utf-8") + "\n# looked at it\n", encoding="utf-8", newline="\n")
    (repo / "tests" / f"_dropped_by_{marker}.txt").write_text("student scratch\n", encoding="utf-8")


# ------------------------------------------------------------------ the stub product
class Stub:
    def __init__(self, behaviours: dict[str, str], *, started_at: str = "2026-09-22T10:00:00"):
        self.behaviours = behaviours
        self.started_at = started_at
        self.tasks: dict[str, dict] = {}
        self.submissions: list[dict] = []
        self.recipes: list[dict] = []
        self.polls: dict[str, int] = {}
        self.stop_on_first_poll: tuple[str, Path] | None = None    # (behaviour key, STOP path)
        self.lock = threading.Lock()

    def key(self, agent_id, use_memory) -> str:
        variant = ID_VARIANT.get(agent_id, "?")
        specific = f"{variant}:{'on' if use_memory else 'off'}"
        return specific if specific in self.behaviours else variant

    def submit(self, body: dict) -> dict:
        repo = Path(body["source_repo"])
        listing = sorted(str(p.relative_to(repo)).replace("\\", "/") for p in repo.rglob("*")
                         if ".git" not in p.parts)
        vdir = repo.parent
        hidden_seen = any(p.name.startswith("hidden_") for p in repo.rglob("*")) or \
            (vdir / "verifier" / "verifier-private").exists()
        key = self.key(body.get("agent_id"), body.get("use_memory"))
        behaviour = self.behaviours.get(key, "fix")
        with self.lock:
            tid = f"t{len(self.tasks) + 1:011d}"
        marker = tid
        if behaviour in ("fix", "leak", "slow"):
            _fix(repo, marker)
        elif behaviour == "wrong":
            _wrong(repo, marker)
        diff = ""
        if behaviour in ("fix", "wrong", "leak", "slow"):
            _git(repo, "add", "--all")
            diff = _git(repo, "diff", "--cached", "--binary")
        recalled = bool(body.get("use_memory")) and bool(self.recipes) or behaviour == "leak"
        record = {"id": tid, "status": "running", "agent_id": body.get("agent_id"),
                  "instruction": body["instruction"], "changed_files": [], "diff": ""}
        final = {**record, "status": "blocked" if behaviour == "blocked" else "completed",
                 "error": "scope" if behaviour == "blocked" else "", "diff": diff,
                 "changed_files": [l[6:] for l in diff.splitlines() if l.startswith("+++ b/")],
                 "duration_seconds": 0.1,
                 "sidecar": {"status": "completed", "summary": "stub", "tests": [], "notes": "",
                             "steps": 3, "stop_reason": "finished", "recipes_applied": []},
                 "memory": {"recalled": recalled,
                            "recipe_ids": [r["id"] for r in self.recipes] if recalled else []}}
        self.tasks[tid] = {"behaviour": behaviour, "record": record, "final": final, "key": key}
        self.submissions.append({"key": key, "body": body, "listing": listing, "hidden_seen": hidden_seen})
        return record

    def poll(self, tid: str) -> dict:
        task = self.tasks[tid]
        self.polls[tid] = self.polls.get(tid, 0) + 1
        if self.stop_on_first_poll and task["key"] == self.stop_on_first_poll[0]:
            self.stop_on_first_poll[1].write_text("stop", encoding="utf-8")
            self.stop_on_first_poll = None
            return task["record"]
        if task["behaviour"] in ("hang", "slow") and not task.get("release"):
            return task["record"]
        return task["final"]


def _handler(stub: Stub):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def _authed(self):
            if self.headers.get("X-BCC-Token") != TOKEN:
                self._send(401, {"message": "no"})
                return False
            return True

        def do_GET(self):
            if not self._authed():
                return
            path = self.path.split("?")[0]
            if path == "/api/identity":
                return self._send(200, {"app": "stub", "version": "0", "started_at": stub.started_at})
            if path == "/api/coding-tasks/readiness":
                return self._send(200, {"available": True})
            if path == "/api/coding-recipes":
                return self._send(200, {"items": stub.recipes})
            if path.startswith("/api/coding-tasks/"):
                tid = path.rsplit("/", 1)[1]
                if tid not in stub.tasks:
                    return self._send(404, {"message": "нет"})
                return self._send(200, stub.poll(tid))
            self._send(404, {"message": path})

        def do_POST(self):
            if not self._authed():
                return
            path, body = self.path, self._body()
            if path == "/api/lab-agents/ensure":
                return self._send(200, {"agents": [
                    {"id": i, "name": f"LAB · {v}", "variant": v, "use_memory": v == "MEMORY"}
                    for v, i in VARIANT_IDS.items()]})
            if path == "/api/coding-tasks":
                return self._send(200, stub.submit(body))
            if path.endswith("/cancel"):
                return self._send(404, {"message": "cancel not supported"})
            if path == "/api/coding-recipes":
                stub.recipes.append(body["recipe"])
                return self._send(200, {"recipe_id": body["recipe"]["id"], "lesson_id": "coach-lesson:stub",
                                        "status": "VERIFIED"})
            if path == "/api/coding-recipes/match":
                return self._send(200, {"items": stub.recipes, "memory_hit": bool(stub.recipes)})
            self._send(404, {"message": path})
    return H


@pytest.fixture
def served():
    servers = []

    def start(stub: Stub) -> str:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(stub))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"
    yield start
    for srv in servers:
        srv.shutdown()


def _args(base, out, *extra):
    return ["--base-url", base, "--token", TOKEN, "--out", str(out), "--poll-interval", "0.05", *extra]


def _state(out) -> dict:
    return json.loads((out / "lab-state.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ pure functions
def test_sample_case_files_are_the_builtin_cases():
    for key, name in (("sample", "decimal_comma_invoice.json"), ("sample-transfer", "decimal_comma_stock_transfer.json")):
        on_disk = json.loads((REPO / "tools" / "self_improve_cases" / name).read_text(encoding="utf-8"))
        assert on_disk == lab.BUILTIN_CASES[key]
        assert lab.validate_case(on_disk) == []


def test_classification_teacher_patch_is_never_a_student_pass():
    c = lab.classify_outcome
    assert c(blocked=False, timed_out=False, verifier_passed=True, interventions=[]) == lab.STUDENT_UNASSISTED_PASS
    assert c(blocked=False, timed_out=False, verifier_passed=True,
             interventions=[{"kind": "hint"}]) == lab.STUDENT_COACHED_PASS
    for kind in ("teacher_patch", "cloud_fix"):
        out = c(blocked=False, timed_out=False, verifier_passed=True, interventions=[{"kind": "hint"}, {"kind": kind}])
        assert out == lab.TEACHER_PATCH and not lab.is_student_pass(out)
    assert c(blocked=False, timed_out=False, verifier_passed=False, interventions=[{"kind": "teacher_patch"}]) == lab.FAIL
    assert c(blocked=False, timed_out=True, verifier_passed=True) == lab.TIMEOUT
    assert c(blocked=True, timed_out=True, verifier_passed=True) == lab.BLOCKED
    s = lab.summarize({"A": {"outcome": lab.TEACHER_PATCH}, "B": {"outcome": lab.STUDENT_COACHED_PASS}})
    assert s["student_unassisted_passes"] == 0 and s["student_coached_passes"] == 1
    assert s["teacher_patches"] == 1 and s["weights"] == "WEIGHTS_UNCHANGED"


def test_gain_verdict():
    P, F = lab.STUDENT_UNASSISTED_PASS, lab.FAIL
    assert lab.gain_verdict(P, P) == lab.NO_MEASURED_GAIN
    assert lab.gain_verdict(F, F) == lab.NO_MEASURED_GAIN
    assert lab.gain_verdict(F, lab.TIMEOUT) == lab.NO_MEASURED_GAIN
    assert lab.gain_verdict(F, P) == lab.MEASURED_GAIN
    assert lab.gain_verdict(P, F) == lab.MEASURED_REGRESSION
    assert lab.gain_verdict(lab.TEACHER_PATCH, lab.TEACHER_PATCH) == lab.NO_MEASURED_GAIN
    assert lab.gain_verdict(None, P) == lab.INSUFFICIENT_EVIDENCE
    assert lab.gain_verdict(lab.BLOCKED, P) == lab.INSUFFICIENT_EVIDENCE


def test_verifier_env_is_minimal(tmp_path, monkeypatch):
    monkeypatch.setenv("BCC_TOKEN", "secret-token-value")
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    env = lab.build_verifier_env(tmp_path / "repo", tmp_path / "scratch")
    assert "BCC_TOKEN" not in env and env["PYTHONPATH"] == str(tmp_path / "repo")
    assert not any("secret-token-value" in v for v in env.values())


# ------------------------------------------------------------------ compare
def test_compare_clean_copy_per_variant_and_independent_verifier(tmp_path, served, monkeypatch):
    monkeypatch.setenv("LAB_PROBE_SECRET", "must-not-reach-the-verifier")
    stub = Stub({"RAW": "fix", "TOOL_FIRST": "wrong", "MEMORY": "fix"})
    out = tmp_path / "out"
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW,TOOL_FIRST,MEMORY",
                   *_args(served(stub), out)])
    assert rc == lab.EXIT_OK
    st = _state(out)["compare"]
    outcomes = {v: r["outcome"] for v, r in st["results"].items()}
    assert outcomes == {"RAW": lab.STUDENT_UNASSISTED_PASS, "TOOL_FIRST": lab.FAIL,
                        "MEMORY": lab.STUDENT_UNASSISTED_PASS}
    # clean copy: what variant 1 dropped is absent in variant 2 and 3
    assert len(stub.submissions) == 3
    for sub in stub.submissions:
        assert not [f for f in sub["listing"] if "_dropped_by_" in f], sub["listing"]
        assert "test_regression_comma.py" not in " ".join(sub["listing"])
        assert sub["hidden_seen"] is False                 # verifier did not exist for the student
    assert len({s["body"]["source_repo"] for s in stub.submissions}) == 3
    for r in st["results"].values():
        assert r["student_copy_removed"] and not Path(r["student_copy"]).exists()
    # memory policy of the controlled comparison
    assert [s["body"]["use_memory"] for s in stub.submissions] == [False, False, True]
    # the verifier ran for real, with a minimal env
    raw = st["results"]["RAW"]["verifier"]
    assert raw["passed"] and raw["exit_code"] == 0 and raw["hidden_visible_to_student"] is False
    assert "LAB_PROBE_SECRET" not in raw["env_keys"]
    assert st["results"]["TOOL_FIRST"]["verifier"]["reason"] == "VERIFIER_FAILED"
    report = json.loads((out / "compare-report.json").read_text(encoding="utf-8"))
    assert report["weights"] == "WEIGHTS_UNCHANGED"
    assert (out / "compare-report.md").is_file()


def test_timeout_is_enforced_and_the_next_variant_proceeds(tmp_path, served):
    stub = Stub({"RAW": "hang", "TOOL_FIRST": "fix"})
    out = tmp_path / "out"
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW,TOOL_FIRST",
                   "--budget-minutes", "0.02", *_args(served(stub), out)])
    assert rc == lab.EXIT_OK
    st = _state(out)["compare"]
    raw = st["results"]["RAW"]
    assert raw["outcome"] == lab.TIMEOUT and raw["timed_out"] is True
    assert raw["budget_seconds"] == pytest.approx(1.2)
    assert raw["wall_seconds"] < 10                      # not silently extended
    assert raw["cancel_status"] == 404 and raw["server_task_status_at_timeout"] == "running"
    assert (out / "work" / st["run_id"] / "RAW" / "task-record-at-timeout.json").is_file()
    assert not Path(raw["student_copy"]).exists()        # restored before the next variant
    assert st["results"]["TOOL_FIRST"]["outcome"] == lab.STUDENT_UNASSISTED_PASS


def test_stop_file_before_start_submits_nothing(tmp_path, served):
    stub = Stub({})
    out = tmp_path / "out"
    out.mkdir()
    (out / "STOP").write_text("stop", encoding="utf-8")
    rc = lab.main(["compare", "--case", "sample", *_args(served(stub), out)])
    assert rc == lab.EXIT_STOPPED
    assert stub.submissions == []


def test_stop_between_polls_then_resume_skips_completed(tmp_path, served):
    stub = Stub({"RAW": "fix", "TOOL_FIRST": "slow"})
    out = tmp_path / "out"
    stub.stop_on_first_poll = ("TOOL_FIRST", out / "STOP")
    base = served(stub)
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW,TOOL_FIRST",
                   "--budget-minutes", "5", *_args(base, out)])
    assert rc == lab.EXIT_STOPPED
    st = _state(out)["compare"]
    assert st["results"]["RAW"]["status"] == "done"
    tf = st["results"]["TOOL_FIRST"]
    assert tf["status"] == "in_progress" and tf["task_id"]
    deadline = tf["deadline_epoch"]

    (out / "STOP").unlink()
    for task in stub.tasks.values():
        task["release"] = True
    rc = lab.main(["compare", "--case", "sample", "--budget-minutes", "90", *_args(base, out)])
    assert rc == lab.EXIT_OK
    keys = [s["key"] for s in stub.submissions]
    assert keys == ["RAW", "TOOL_FIRST"]                # nothing resubmitted
    st = _state(out)["compare"]
    assert st["results"]["TOOL_FIRST"]["deadline_epoch"] == deadline   # budget never extended
    assert st["budget_seconds"] == 300
    assert st["results"]["TOOL_FIRST"]["outcome"] == lab.STUDENT_UNASSISTED_PASS


def test_memory_leak_into_a_control_variant_is_blocked(tmp_path, served):
    stub = Stub({"RAW": "leak"})
    out = tmp_path / "out"
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW", *_args(served(stub), out)])
    assert rc == lab.EXIT_OK
    raw = _state(out)["compare"]["results"]["RAW"]
    assert raw["outcome"] == lab.BLOCKED and "MEMORY_LEAK_IN_CONTROL" in raw["block_reason"]


def test_product_blocked_task_is_blocked_not_fail(tmp_path, served):
    stub = Stub({"RAW": "blocked"})
    out = tmp_path / "out"
    assert lab.main(["compare", "--case", "sample", "--variants", "RAW", *_args(served(stub), out)]) == 0
    assert _state(out)["compare"]["results"]["RAW"]["outcome"] == lab.BLOCKED


# ------------------------------------------------------------------ verifier independence
def test_case_file_inside_the_source_repo_is_refused(tmp_path):
    repo = tmp_path / "repo"
    lab.make_synthetic_repo("invoicekit", repo)
    case = dict(lab.BUILTIN_CASES["sample"], source_repo=str(repo))
    case_path = repo / "case.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(lab.UsageError, match="hidden verifier"):
        lab.resolve_source(case, case_path.resolve(), tmp_path / "work")
    outside = tmp_path / "cases" / "case.json"
    outside.parent.mkdir()
    outside.write_text(json.dumps(case), encoding="utf-8")
    src, sha = lab.resolve_source(case, outside.resolve(), tmp_path / "work")
    assert src == repo.resolve() and len(sha) == 40


def test_verifier_refuses_when_the_student_could_see_it(tmp_path):
    src = tmp_path / "src"
    sha = lab.make_synthetic_repo("invoicekit", src)
    student = tmp_path / "student"
    lab.clone_clean(src, sha, student)
    _fix(student, "s")
    _git(student, "add", "--all")
    diff = _git(student, "diff", "--cached")
    clean = lab.run_hidden_verifier(case=lab.BUILTIN_CASES["sample"], case_path=None, src=src, baseline=sha,
                                    diff=diff, vdir=tmp_path / "v0", student_dir=student)
    assert clean["passed"] is True and clean["hidden_visible_to_student"] is False
    (student / "tests" / "hidden_test_money.py").write_text("x", encoding="utf-8")
    res = lab.run_hidden_verifier(case=lab.BUILTIN_CASES["sample"], case_path=None, src=src, baseline=sha,
                                  diff=diff, vdir=tmp_path / "v", student_dir=student)
    assert res["passed"] is False and res["reason"] == "VERIFIER_VISIBLE_TO_STUDENT"


def test_verifier_on_baseline_fails_and_on_fix_passes(tmp_path):
    """Negative control for the seeded defect: the hidden tests are red on the baseline."""
    src = tmp_path / "src"
    sha = lab.make_synthetic_repo("invoicekit", src)
    work = tmp_path / "w"
    lab.clone_clean(src, sha, work)
    _wrong(work, "x")
    _git(work, "add", "--all")
    bad = lab.run_hidden_verifier(case=lab.BUILTIN_CASES["sample"], case_path=None, src=src, baseline=sha,
                                  diff=_git(work, "diff", "--cached"), vdir=tmp_path / "v1", student_dir=None)
    assert bad["passed"] is False and bad["reason"] == "VERIFIER_FAILED"
    lab.clone_clean(src, sha, work)
    _fix(work, "y")
    _git(work, "add", "--all")
    good = lab.run_hidden_verifier(case=lab.BUILTIN_CASES["sample"], case_path=None, src=src, baseline=sha,
                                   diff=_git(work, "diff", "--cached"), vdir=tmp_path / "v2", student_dir=None)
    assert good["passed"] is True, good.get("output_tail")
    # a diff touching a protected path is a scope violation, not a pass
    lab.clone_clean(src, sha, work)
    _fix(work, "z")
    (work / "README.md").write_text("changed\n", encoding="utf-8")
    _git(work, "add", "--all")
    scoped = lab.run_hidden_verifier(case=lab.BUILTIN_CASES["sample"], case_path=None, src=src, baseline=sha,
                                     diff=_git(work, "diff", "--cached"), vdir=tmp_path / "v3", student_dir=None)
    assert scoped["passed"] is False and scoped["reason"] == "SCOPE_VIOLATION"


# ------------------------------------------------------------------ lesson / transfer
def test_teacher_patch_does_not_become_a_student_lesson(tmp_path, served):
    stub = Stub({"RAW": "fix"})
    out = tmp_path / "out"
    base = served(stub)
    assert lab.main(["compare", "--case", "sample", "--variants", "RAW", *_args(base, out)]) == 0
    assert lab.main(["intervene", "--variant", "RAW", "--kind", "cloud_fix", "--note", "Claude fixed it",
                     "--out", str(out)]) == 0
    st = _state(out)["compare"]
    assert st["results"]["RAW"]["outcome"] == lab.TEACHER_PATCH
    assert st["summary"]["student_unassisted_passes"] == 0 and st["summary"]["teacher_patches"] == 1
    assert lab.main(["lesson", "--case", "sample", *_args(base, out)]) == lab.EXIT_NO_PASS
    assert stub.recipes == []


def test_lesson_restart_check_and_transfer(tmp_path, served):
    stub = Stub({"MEMORY": "fix", "MEMORY:off": "fix", "MEMORY:on": "fix"})
    out = tmp_path / "out"
    base = served(stub)
    assert lab.main(["compare", "--case", "sample", "--variants", "MEMORY", *_args(base, out)]) == 0
    assert _state(out)["compare"]["snapshot_recipe_ids"] == []
    assert lab.main(["lesson", "--case", "sample", *_args(base, out)]) == 0
    assert len(stub.recipes) == 1
    recipe = stub.recipes[0]
    assert recipe["status"] == "VERIFIED" and recipe["provenance"]["assistance_level"] == "none"
    assert {s["tool"] for s in recipe["steps"]} <= set(lab.ALLOWED_RECIPE_TOOLS)
    assert all(set(s) == {"tool", "args"} for s in recipe["steps"])
    assert recipe["steps"][-1] == {"tool": "run_tests", "args": {"paths": ["tests"]}}
    assert any(s["tool"] == "edit_file" and s["args"]["path"] == "invoicekit/money.py" for s in recipe["steps"])

    # no restart yet -> BLOCKED precondition, nothing submitted
    before = len(stub.submissions)
    assert lab.main(["transfer", "--case", "sample-transfer", *_args(base, out)]) == lab.EXIT_BLOCKED
    assert len(stub.submissions) == before
    stub.started_at = "2026-09-22T11:00:00"            # Bossman restarted
    rc = lab.main(["transfer", "--case", "sample-transfer", "--json", *_args(base, out)])
    assert rc == lab.EXIT_OK
    tr = _state(out)["transfer"]
    assert tr["restart_verified"] is True
    assert [s["body"]["use_memory"] for s in stub.submissions[before:]] == [False, True]
    assert tr["memory_hit"]["expected_recipe_hit"] is True
    assert tr["solve"]["CONTROL"]["student_pass"] and tr["solve"]["MEMORY"]["student_pass"]
    assert tr["verdict"] == lab.NO_MEASURED_GAIN      # both solved: memory did not change the outcome
    assert tr["weights"] == "WEIGHTS_UNCHANGED"


def test_transfer_gain_when_only_memory_solves(tmp_path, served):
    stub = Stub({"MEMORY": "fix", "MEMORY:off": "wrong", "MEMORY:on": "fix"})
    out = tmp_path / "out"
    base = served(stub)
    assert lab.main(["compare", "--case", "sample", "--variants", "MEMORY", *_args(base, out)]) == 0
    assert lab.main(["lesson", "--case", "sample", *_args(base, out)]) == 0
    stub.started_at = "later"
    assert lab.main(["transfer", "--case", "sample-transfer", *_args(base, out)]) == 0
    tr = _state(out)["transfer"]
    assert tr["results"]["CONTROL"]["outcome"] == lab.FAIL
    assert tr["results"]["MEMORY"]["outcome"] == lab.STUDENT_UNASSISTED_PASS
    assert tr["verdict"] == lab.MEASURED_GAIN


def test_transfer_refuses_the_compared_case(tmp_path, served):
    stub = Stub({"MEMORY": "fix"})
    out = tmp_path / "out"
    base = served(stub)
    assert lab.main(["compare", "--case", "sample", "--variants", "MEMORY", *_args(base, out)]) == 0
    assert lab.main(["lesson", "--case", "sample", *_args(base, out)]) == 0
    assert lab.main(["transfer", "--case", "sample", *_args(base, out)]) == lab.EXIT_USAGE


# ------------------------------------------------------------------ plumbing
def test_unreachable_api_and_bad_token(tmp_path, served):
    out = tmp_path / "out"
    assert lab.main(["compare", "--case", "sample", "--base-url", "http://127.0.0.1:9",
                     "--token", TOKEN, "--out", str(out)]) == lab.EXIT_API
    base = served(Stub({}))
    assert lab.main(["compare", "--case", "sample", "--base-url", base, "--token", "wrong",
                     "--out", str(out)]) == lab.EXIT_API


def test_token_from_data_dir(tmp_path, served):
    data = tmp_path / "data"
    data.mkdir()
    (data / "token").write_text(TOKEN + "\n", encoding="utf-8")
    stub = Stub({"RAW": "fix"})
    out = tmp_path / "out"
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW", "--base-url", served(stub),
                   "--data-dir", str(data), "--out", str(out), "--poll-interval", "0.05"])
    assert rc == 0


def test_steps_from_diff_use_only_allowed_tools(tmp_path):
    src = tmp_path / "src"
    sha = lab.make_synthetic_repo("invoicekit", src)
    work = tmp_path / "w"
    lab.clone_clean(src, sha, work)
    _fix(work, "m")
    _git(work, "add", "--all")
    steps, notes = lab.steps_from_diff(_git(work, "diff", "--cached"), ["tests"])
    tools = [s["tool"] for s in steps]
    assert set(tools) <= set(lab.ALLOWED_RECIPE_TOOLS)
    assert "write_file" in tools and "edit_file" in tools and tools[-1] == "run_tests"
    assert notes == []


def test_synthetic_repo_is_deterministic(tmp_path):
    a = lab.make_synthetic_repo("stockkit", tmp_path / "a")
    b = lab.make_synthetic_repo("stockkit", tmp_path / "b")
    assert a == b
    assert lab.make_synthetic_repo("stockkit", tmp_path / "a") == a       # reused, not rebuilt
