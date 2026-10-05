"""HandBroker: sole privileged path; policy first, lease for writers, artifacts hashed, journaled."""
from __future__ import annotations

import hashlib
import sys
import time
from types import SimpleNamespace

import pytest

from bcc.autonomy.hands import ExecOutcome, HandBroker, SubprocessExecutor, request_hash
from bcc.autonomy.journal import Journal
from bcc.autonomy.lease import EngineeringLease
from bcc.autonomy.policy import Policy, Scope
from bcc.autonomy.types import Budget, Goal, HandRequest

OK = SimpleNamespace(ok=True, reason="pinned")
GOAL = Goal(goal_id="JEFF-0042", problem="p", desired_result="d", constraints=(),
            acceptance_tests=("pytest:tests/x.py",), budget=Budget(max_minutes=30, max_agent_turns=3),
            risk_tier="docs_tests", target_metric="identity.leaks", protected_metrics=())


def req(action="run_tests", evidence=("exit_code", "stdout"), timeout=30, **args):
    return HandRequest(goal_id="JEFF-0042", requested_by="claude", action=action, target="worktree",
                       arguments=args, expected_evidence=evidence, risk_class="low", timeout_s=timeout,
                       rollback="git restore .")


@pytest.fixture
def wt(tmp_path):
    w = tmp_path / "wt"
    (w / "docs").mkdir(parents=True)
    return w


def broker(wt, tmp_path, executor=None, **kw):
    pol = Policy(Scope(worktree=wt), constitution_status=kw.pop("status", OK), level=kw.pop("level", "L2"))
    return HandBroker(pol, Journal(tmp_path / "j"), executor, goals=kw.pop("goals", GOAL), **kw)


class FakeExec:
    def __init__(self, outcome=None, boom=False):
        self.calls = []
        self.outcome = outcome or ExecOutcome(0, b"ok\n", b"")
        self.boom = boom

    def __call__(self, r, scope):
        self.calls.append(r)
        if self.boom:
            raise RuntimeError("executor exploded")
        return self.outcome


def test_refused_request_never_reaches_the_executor(wt, tmp_path):
    ex = FakeExec()
    b = broker(wt, tmp_path, ex)
    res = b.execute(req(argv=["bash", "-c", "echo hi"]))
    assert not res.ok and res.exit_code is None and "shell" in res.refused_reason and ex.calls == []
    res = b.execute(req(action="send_message"))
    assert not res.ok and "external message" in res.refused_reason
    kinds = [e["kind"] for e in b.journal.entries()]
    assert kinds == ["hand.request", "hand.refused", "hand.request", "hand.refused"]
    assert b.journal.entries(kind="hand.refused")[1]["payload"]["needs_user"] is True


def test_unknown_goal_and_blocked_constitution_are_refused(wt, tmp_path):
    ex = FakeExec()
    assert not broker(wt, tmp_path, ex, goals=None).execute(req(argv=["pytest"])).ok
    res = broker(wt, tmp_path / "b", ex, status=SimpleNamespace(ok=False, reason="mismatch")).execute(
        req(argv=["pytest"]))
    assert "BLOCKED" in res.refused_reason and ex.calls == []


def test_allowed_request_records_artifact_hashes(wt, tmp_path):
    ex = FakeExec(ExecOutcome(0, b"12 passed\n", b"warn\n"))
    b = broker(wt, tmp_path, ex)
    r = req(argv=["pytest", "-q"])
    res = b.execute(r)
    assert res.ok and res.exit_code == 0 and res.request_hash == request_hash(r)
    assert res.artifacts["stdout"] == hashlib.sha256(b"12 passed\n").hexdigest()
    assert res.artifacts["stderr"] == hashlib.sha256(b"warn\n").hexdigest()
    last = b.journal.entries()[-1]
    assert last["kind"] == "hand.result" and last["payload"]["request_hash"] == res.request_hash
    assert b.journal.verify().ok


def test_missing_evidence_and_nonzero_exit_are_not_ok(wt, tmp_path):
    b = broker(wt, tmp_path, FakeExec(ExecOutcome(0, b"", b"")))
    res = b.execute(req(argv=["pytest"], evidence=("exit_code", "report_path")))
    assert not res.ok and "missing evidence" in res.refused_reason
    b = broker(wt, tmp_path / "2", FakeExec(ExecOutcome(3, b"", b"")))
    assert not b.execute(req(argv=["pytest"])).ok


def test_executor_crash_is_evidence_not_a_crash(wt, tmp_path):
    res = broker(wt, tmp_path, FakeExec(boom=True)).execute(req(argv=["pytest"]))
    assert not res.ok and "executor exploded" in res.refused_reason


def test_writers_need_the_lease_for_the_same_goal(wt, tmp_path):
    lz = EngineeringLease(tmp_path / "lease")
    ex = FakeExec()
    b = broker(wt, tmp_path, ex, lease=lz)
    cmd = dict(action="run_command", argv=["git", "status"])
    assert "lease" in b.execute(req(**cmd)).refused_reason
    assert b.execute(req(action="read_file", path="docs/a.md", evidence=())).ok        # reads need no lease
    assert b.execute(req(argv=["pytest"])).ok                                          # test runs need no lease
    t = lz.acquire("JEFF-0001", "codex", ttl_s=60)
    assert not b.execute(req(**cmd)).ok                                                # other goal's lease
    lz.release(t)
    t = lz.acquire("JEFF-0042", "claude", ttl_s=60)
    assert b.execute(req(**cmd)).ok
    lz.release(t)


def test_secrets_in_arguments_and_output_are_redacted_in_the_journal(wt, tmp_path):
    token = "sk-" + "Z" * 30
    b = broker(wt, tmp_path, FakeExec(ExecOutcome(0, f"key {token}".encode(), b"")))
    b.execute(req(argv=["pytest", f"--token={token}"]))
    assert token not in b.journal.path.read_text(encoding="utf-8")


# ------------------------------------------------------------ default executor, real processes

def real(wt, tmp_path, **kw):
    return broker(wt, tmp_path, SubprocessExecutor(env_factory=lambda: {"PATH": "", "SYSTEMROOT": "C:\\Windows"}
                                                    if sys.platform == "win32" else {"PATH": ""}), **kw)


def test_default_executor_runs_argv_in_the_worktree(wt, tmp_path):
    (wt / "probe.py").write_text("import os, pathlib\npathlib.Path('report.txt').write_text(os.getcwd())\n"
                                 "print('hello')\n")
    b = real(wt, tmp_path)
    res = b.execute(req(argv=[sys.executable, "probe.py"], report_path="report.txt",
                        evidence=("exit_code", "stdout", "report_path")))
    assert res.ok, res
    assert (wt / "report.txt").read_text() == str(wt.resolve())
    assert res.artifacts["report_path"] == hashlib.sha256((wt / "report.txt").read_bytes()).hexdigest()


def test_default_executor_timeout_kills_the_process_group(wt, tmp_path):
    (wt / "slow.py").write_text("import time\ntime.sleep(60)\n")
    t0 = time.monotonic()
    res = real(wt, tmp_path).execute(req(argv=[sys.executable, "slow.py"], timeout=1))
    assert not res.ok and res.exit_code is None and res.refused_reason == "timed out"
    assert time.monotonic() - t0 < 30


def test_default_executor_confines_cwd_and_writes(wt, tmp_path):
    b = real(wt, tmp_path)
    assert not b.execute(req(argv=["pytest"], cwd="..")).ok
    res = b.execute(req(action="write_file", path="docs/new.md", content="# hi\n", evidence=("written",)))
    assert res.ok and (wt / "docs" / "new.md").read_text() == "# hi\n"
    res = b.execute(req(action="read_file", path="docs/new.md", evidence=("stdout",)))
    assert res.ok and res.artifacts["stdout"] == hashlib.sha256(b"# hi\n").hexdigest()
    assert not b.execute(req(action="write_file", path="../evil.md", content="x")).ok
    assert not (wt.parent / "evil.md").exists()


def test_default_executor_does_not_leak_secret_env(wt, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-value")
    (wt / "env.py").write_text("import os\nprint('OPENAI_API_KEY' in os.environ)\n")
    b = broker(wt, tmp_path, SubprocessExecutor())
    res = b.execute(req(argv=[sys.executable, "env.py"]))
    assert res.ok
    tail = b.journal.entries(kind="hand.result")[-1]["payload"]["stdout_tail"]
    assert tail.strip() == "False"
