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
        self.on_poll = None                                       # callable(task) run on each poll
        self.sidecar_default: dict = {}                           # merged into every final sidecar
        self.sidecar_extra: dict[str, dict] = {}                  # per behaviour key
        self.handshake: dict | None = None                        # readiness handshake, if any
        self.agent_fairness: dict[str, dict] = {}                 # per variant fairness fingerprint
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
                             "steps": 3, "stop_reason": "finished", "recipes_applied": [],
                             **self.sidecar_default, **self.sidecar_extra.get(key, {})},
                 "memory": {"recalled": recalled,
                            "recipe_ids": [r["id"] for r in self.recipes] if recalled else []}}
        self.tasks[tid] = {"behaviour": behaviour, "record": record, "final": final, "key": key}
        self.submissions.append({"key": key, "body": body, "listing": listing, "hidden_seen": hidden_seen})
        return record

    def poll(self, tid: str) -> dict:
        task = self.tasks[tid]
        self.polls[tid] = self.polls.get(tid, 0) + 1
        if self.on_poll is not None:
            self.on_poll(task)
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
                return self._send(200, {"available": True, **({"handshake": stub.handshake}
                                                              if stub.handshake else {})})
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
                    {"id": i, "name": f"LAB · {v}", "variant": v, "use_memory": v == "MEMORY",
                     **({"fairness": stub.agent_fairness[v]} if v in stub.agent_fairness else {})}
                    for v, i in VARIANT_IDS.items()],
                    # the real product lists observers separately; the lab must never map them
                    "observers": [{"id": 100 + n, "name": f"LAB · {o}", "observer": o, "variant": None}
                                  for n, o in enumerate(lab.OBSERVER_PROFILES)]})
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


# The Windows archive runs an *embeddable* Python: its ._pth file makes the interpreter
# ignore PYTHONPATH (and every PYTHON* variable) and keeps the cwd off sys.path.
# `python -I` reproduces that isolation on any OS. The hidden tests import the student's
# repository, so under that interpreter the verifier must put the repo on sys.path
# itself — otherwise every verification fails for the wrong reason.
EMBEDDED_LIKE = [__import__("sys").executable, "-I"]


def test_hidden_verifier_works_under_an_embedded_like_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "_interpreter", lambda: list(EMBEDDED_LIKE))
    src = tmp_path / "src"
    sha = lab.make_synthetic_repo("invoicekit", src)
    work = tmp_path / "w"
    lab.clone_clean(src, sha, work)
    _fix(work, "e")
    _git(work, "add", "--all")
    good = lab.run_hidden_verifier(case=lab.BUILTIN_CASES["sample"], case_path=None, src=src, baseline=sha,
                                   diff=_git(work, "diff", "--cached"), vdir=tmp_path / "v1", student_dir=None)
    assert good["passed"] is True, good.get("output_tail")
    # negative control under the same interpreter: an unfixed repo still fails, and
    # for the RIGHT reason (the seeded defect's ValueError), not an import failure
    lab.clone_clean(src, sha, work)
    _wrong(work, "f")
    _git(work, "add", "--all")
    bad = lab.run_hidden_verifier(case=lab.BUILTIN_CASES["sample"], case_path=None, src=src, baseline=sha,
                                  diff=_git(work, "diff", "--cached"), vdir=tmp_path / "v2", student_dir=None)
    assert bad["passed"] is False and bad["reason"] == "VERIFIER_FAILED"
    tail = bad["output_tail"]
    assert "ModuleNotFoundError" not in tail and "ImportError" not in tail, tail
    assert "ValueError" in tail and "Ran 6 tests" in tail, tail


def test_a_hint_logged_from_another_terminal_during_compare_is_not_lost(tmp_path, served):
    """Claude Code logs `intervene` from a second process while `compare` is polling.
    The running compare must not overwrite it: the pass is COACHED, not UNASSISTED."""
    stub = Stub({"RAW": "slow"})
    out = tmp_path / "out"
    base = served(stub)
    logged = {}

    def on_poll(task):
        if not logged:
            logged["rc"] = lab.main(["intervene", "--variant", "RAW", "--kind", "hint",
                                     "--note", "посмотри на разделитель", "--out", str(out)])
            task["release"] = True

    stub.on_poll = on_poll
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW", *_args(base, out)])
    assert rc == lab.EXIT_OK and logged["rc"] == lab.EXIT_OK
    raw = _state(out)["compare"]["results"]["RAW"]
    assert [i["note"] for i in raw["interventions"]] == ["посмотри на разделитель"]
    assert raw["outcome"] == lab.STUDENT_COACHED_PASS


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


# ------------------------------------------------------------------ observers through the product path
REAL_MODEL = "Qwen3.8-27B-UD-Q5_K_M.gguf"
SIDECAR_REAL = {"model": REAL_MODEL, "executor": "bossman-local-sidecar", "endpoint": "http://127.0.0.1:8081/v1",
                "model_kind": "REAL_MODEL"}
TRANSCRIPT = [
    {"step": 1, "tool": "read_file", "ok": True, "t": 1.0, "sig": "r1", "path": "invoicekit/money.py"},
    {"step": 2, "tool": "write_file", "ok": True, "t": 2.0, "sig": "w1", "path": "tests/test_regression_comma.py"},
    {"step": 3, "tool": "run_tests", "ok": False, "t": 3.0, "sig": "t1", "paths": ["tests"], "passed": False},
    {"step": 4, "tool": "edit_file", "ok": True, "t": 4.0, "sig": "e1", "path": "invoicekit/money.py"},
    {"step": 5, "tool": "run_tests", "ok": True, "t": 5.0, "sig": "t2", "paths": ["tests"], "passed": True},
    {"step": 6, "tool": "finish", "ok": True, "t": 6.0, "sig": "f1"}]


def test_compare_attaches_auditor_ux_reproducer_and_fairness(tmp_path, served):
    stub = Stub({"RAW": "fix", "TOOL_FIRST": "wrong"})
    stub.sidecar_default = dict(SIDECAR_REAL, tool_calls=TRANSCRIPT, tool_calls_total=len(TRANSCRIPT),
                                summary="parse_amount в invoicekit/money.py понимает запятую")
    stub.handshake = {"ok": True, "model": REAL_MODEL, "executor": "bossman-local-sidecar",
                      "endpoint": "http://127.0.0.1:8081/v1", "model_kind": "REAL_MODEL"}
    out = tmp_path / "out"
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW,TOOL_FIRST", "--context-tokens", "32768",
                   *_args(served(stub), out)])
    assert rc == lab.EXIT_OK
    st = _state(out)["compare"]
    raw = st["results"]["RAW"]
    # the stub's regression test is `assertTrue(True)`: it checks nothing and passes on the baseline
    found = {f["detector"] for f in raw["audit"]["findings"]}
    assert {"TEST_CHECKS_NOTHING", "FAKE_REPRODUCER"} <= found
    assert raw["reproducer"]["verdict"] == "PASSES_ON_BASELINE"
    assert raw["outcome"] == lab.STUDENT_UNASSISTED_PASS          # observers never change the outcome
    assert raw["ux"]["evidence"] == "SIDECAR_RECORD" and raw["ux"]["time_to_reproducer_s"] == 3.0
    assert raw["ux"]["time_to_verified_result_s"] is not None
    assert st["results"]["TOOL_FIRST"]["ux"]["time_to_verified_result_s"] is None
    assert raw["fairness"]["model"] == REAL_MODEL and raw["fairness"]["quant"] == "UD-Q5_K_M"
    assert st["fairness"]["verdict"] == lab.VALID_COMPARISON and st["fairness"]["context_tokens"] == 32768
    report = json.loads((out / "compare-report.json").read_text(encoding="utf-8"))
    assert report["metrics"]["main"]["student_passes"] == 1
    assert report["metrics"]["main"]["verified_useful_work_per_hour"] > 0
    assert "status" not in report                                 # a valid comparison is not an error
    md = (out / "compare-report.md").read_text(encoding="utf-8")
    assert "UX_OBSERVER" in md and "CLAUDE_AUDITOR" in md and "VALID_COMPARISON" in md


def test_a_timeout_is_observed_as_unknown_and_the_cycle_goes_on(tmp_path, served):
    stub = Stub({"RAW": "hang", "TOOL_FIRST": "fix", "MEMORY": "fix"})
    stub.sidecar_default = dict(SIDECAR_REAL)
    out = tmp_path / "out"
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW,TOOL_FIRST,MEMORY",
                   "--budget-minutes", "0.02", *_args(served(stub), out)])
    assert rc == lab.EXIT_OK
    st = _state(out)["compare"]
    assert [st["results"][v]["outcome"] for v in ("RAW", "TOOL_FIRST", "MEMORY")] == \
        [lab.TIMEOUT, lab.STUDENT_UNASSISTED_PASS, lab.STUDENT_UNASSISTED_PASS]
    raw = st["results"]["RAW"]
    assert raw["ux"]["evidence"] == lab.INSUFFICIENT_EVIDENCE and raw["ux"]["tool_calls"] is None
    assert raw["reproducer"] is None and raw["fairness"]["model"] is None
    assert st["fairness"]["verdict"] == lab.VALID_COMPARISON
    assert st["fairness"]["variants_without_model_evidence"] == ["RAW"]


def test_observer_profiles_are_refused_as_variants(tmp_path, served):
    stub = Stub({})
    out = tmp_path / "out"
    for name in lab.OBSERVER_PROFILES:
        rc = lab.main(["compare", "--case", "sample", "--variants", f"RAW,{name}", *_args(served(stub), out)])
        assert rc == lab.EXIT_USAGE
    assert stub.submissions == []
    ok = lab.ensure_agents(lab.Api(served(stub), TOKEN))
    assert set(ok) == set(lab.VARIANTS) and not set(ok) & set(lab.OBSERVER_PROFILES)


def test_a_different_model_in_one_variant_invalidates_the_comparison(tmp_path, served):
    stub = Stub({"RAW": "fix", "TOOL_FIRST": "fix", "MEMORY": "fix"})
    stub.sidecar_default = dict(SIDECAR_REAL)
    stub.sidecar_extra = {"TOOL_FIRST": {"model": "openai_gpt-oss-120b-MXFP4_MOE-00001-of-00002.gguf",
                                         "endpoint": "http://127.0.0.1:8083/v1"}}
    out = tmp_path / "out"
    base = served(stub)
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW,TOOL_FIRST,MEMORY", *_args(base, out)])
    assert rc == lab.EXIT_INVALID_COMPARISON
    st = _state(out)["compare"]
    assert len(stub.submissions) == 3                               # the cycle still finished every variant
    assert st["fairness"]["verdict"] == lab.INVALID_COMPARISON
    assert {"model", "endpoint", "quant"} <= {m["field"] for m in st["fairness"]["mismatches"]}
    report = json.loads((out / "compare-report.json").read_text(encoding="utf-8"))
    assert report["status"] == lab.INVALID_COMPARISON
    assert lab.main(["lesson", "--case", "sample", *_args(base, out)]) == lab.EXIT_INVALID_COMPARISON
    assert stub.recipes == []


def test_student_rows_with_different_budgets_are_refused_before_any_run(tmp_path, served):
    stub = Stub({"RAW": "fix", "MEMORY": "fix"})
    same = {"tools": ["read_file", "edit_file", "run_tests"], "max_steps": 40, "max_tokens": 4096, "model_id": 3}
    stub.agent_fairness = {"RAW": same, "MEMORY": {**same, "max_steps": 80}}
    out = tmp_path / "out"
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW,MEMORY", *_args(served(stub), out)])
    assert rc == lab.EXIT_INVALID_COMPARISON and stub.submissions == []
    # negative control: identical rows run
    stub.agent_fairness = {"RAW": same, "MEMORY": same}
    rc = lab.main(["compare", "--case", "sample", "--variants", "RAW,MEMORY", "--fresh",
                   *_args(served(stub), tmp_path / "out2")])
    assert rc == lab.EXIT_OK and len(stub.submissions) == 2


def test_intervene_levels_from_the_terminal(tmp_path, served):
    stub = Stub({"RAW": "fix", "TOOL_FIRST": "fix", "MEMORY": "fix", "RED_TEAM": "wrong"})
    out = tmp_path / "out"
    base = served(stub)
    assert lab.main(["compare", "--case", "sample", "--variants", "RAW,TOOL_FIRST,MEMORY,RED_TEAM",
                     *_args(base, out)]) == 0
    st = _state(out)["compare"]
    raw_task = st["results"]["RAW"]["task_id"]

    def iv(*extra):
        return lab.main(["intervene", "--out", str(out), "--json", *extra])
    assert iv("--agent", "RAW", "--level", "2", "--hint", "посмотри на десятичный разделитель",
              "--task", raw_task, "--student-response", "прочитал money.py и поправил разбор") == 0
    assert iv("--agent", "TOOL_FIRST", "--level", "5", "--hint", "учитель сам написал патч") == 0
    assert iv("--agent", "MEMORY", "--level", "0", "--note", "наблюдал") == 0
    assert iv("--agent", "RED_TEAM", "--level", "3", "--hint", "введи '1 234,50'") == 0
    # refusals: wrong task, no hint for coaching, an observer, a contradictory kind/level
    assert iv("--agent", "RAW", "--level", "2", "--hint", "x", "--task", "t999") == lab.EXIT_USAGE
    assert iv("--agent", "RAW", "--level", "3") == lab.EXIT_USAGE
    assert iv("--agent", "CLAUDE_AUDITOR", "--level", "1", "--hint", "x") == lab.EXIT_USAGE
    assert iv("--agent", "RAW", "--level", "2", "--kind", "teacher_patch", "--hint", "x") == lab.EXIT_USAGE
    assert iv("--agent", "RAW") == lab.EXIT_USAGE
    st = _state(out)["compare"]
    res = st["results"]
    assert res["RAW"]["outcome"] == lab.STUDENT_COACHED_PASS
    assert res["TOOL_FIRST"]["outcome"] == lab.TEACHER_PATCH
    assert res["MEMORY"]["outcome"] == lab.STUDENT_UNASSISTED_PASS          # level 0 is not help
    assert res["RED_TEAM"]["outcome"] == lab.FAIL
    entry = res["RAW"]["interventions"][0]
    assert entry["level"] == 2 and entry["task_id"] == raw_task and entry["agent"] == "LAB · RAW"
    assert entry["student_response"].startswith("прочитал") and entry["logged_after_finish"] is True
    assert entry["verified_effect"] == "VERIFIED_PASS_AFTER_INTERVENTION"
    assert res["TOOL_FIRST"]["interventions"][0]["verified_effect"] == "TEACHER_PATCH_NOT_STUDENT_SUCCESS"
    assert res["RED_TEAM"]["interventions"][0]["verified_effect"] == "NO_VERIFIED_EFFECT"
    summary = st["summary"]
    assert summary["student_unassisted_passes"] == 1 and summary["student_coached_passes"] == 1
    assert summary["teacher_patches"] == 1 and summary["teacher_interventions"] == 3
    journal = (out / "interventions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(journal) == 4                                      # refused calls wrote nothing
    # the lesson takes the unassisted pass; the teacher patch never becomes a student lesson
    assert lab.main(["lesson", "--case", "sample", *_args(base, out)]) == 0
    assert stub.recipes[-1]["provenance"]["variant"] == "MEMORY"


def test_lesson_records_the_observed_path_and_failed_approaches(tmp_path, served):
    stub = Stub({"RAW": "wrong", "MEMORY": "fix"})
    stub.sidecar_default = dict(SIDECAR_REAL, tool_calls=TRANSCRIPT, tool_calls_total=len(TRANSCRIPT))
    out = tmp_path / "out"
    base = served(stub)
    assert lab.main(["compare", "--case", "sample", "--variants", "RAW,MEMORY", *_args(base, out)]) == 0
    assert lab.main(["intervene", "--agent", "MEMORY", "--level", "1", "--hint", "что проверяет тест?",
                     "--out", str(out)]) == 0
    assert lab.main(["lesson", "--case", "sample", *_args(base, out)]) == 0
    recipe = stub.recipes[-1]
    prov = recipe["provenance"]
    assert prov["assistance_level"] == "hint" and prov["teacher_level"] == "LEVEL_1"
    assert prov["model"] == REAL_MODEL and prov["quant"] == "UD-Q5_K_M"
    assert "bossman-local-sidecar" in prov["runtime"] and "http://127.0.0.1:8081/v1" in prov["runtime"]
    assert prov["code_refs"] == ["invoicekit/money.py"]
    assert "tests/test_regression_comma.py" in prov["test_refs"] and "tests" in prov["test_refs"]
    assert prov["diagnostic_sequence"][:3] == ["read_file invoicekit/money.py -> ok",
                                               "write_file tests/test_regression_comma.py -> ok",
                                               "run_tests tests -> red"]
    assert any(f.startswith("вариант RAW:") and "VERIFIER_FAILED" in f for f in recipe["failed_approaches"])
    assert all(isinstance(v, (str, int, float, list)) for v in prov.values())
    report = json.loads((out / "lesson-report.json").read_text(encoding="utf-8"))
    fields = report["lesson_fields"]
    assert set(fields) >= {"symptom", "root_cause", "failed_approaches", "diagnostic_sequence",
                           "successful_strategy", "required_test", "applicability", "counterexample",
                           "code_refs", "test_refs", "evidence_refs", "model", "runtime", "teacher_assistance"}
    assert report["weights"] == "WEIGHTS_UNCHANGED"


def test_transfer_reports_whether_the_lesson_was_applied(tmp_path, served):
    rid = lab.BUILTIN_CASES["sample"]["lesson_template"]["id"]
    stub = Stub({"MEMORY": "fix", "MEMORY:off": "fix", "MEMORY:on": "fix"})
    stub.sidecar_extra = {"MEMORY:on": {"recipes_applied": [rid]}}
    out = tmp_path / "out"
    base = served(stub)
    assert lab.main(["compare", "--case", "sample", "--variants", "MEMORY", *_args(base, out)]) == 0
    assert lab.main(["lesson", "--case", "sample", *_args(base, out)]) == 0
    stub.started_at = "after-restart"
    assert lab.main(["transfer", "--case", "sample-transfer", *_args(base, out)]) == 0
    tr = _state(out)["transfer"]
    assert tr["memory_hit"]["expected_recipe_hit"] is True
    assert tr["lesson_applied"]["verdict"] == "APPLIED_CORRECTLY"
    assert tr["changes"]["solve"] == {"control": True, "memory": True} and tr["changes"]["evidence"] == "n=1"
    assert tr["verdict"] == lab.NO_MEASURED_GAIN and tr["weights"] == "WEIGHTS_UNCHANGED"
