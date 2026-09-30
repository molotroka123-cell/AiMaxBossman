"""Line B workers: single writer under the lease, isolated clone, strict envelopes, read-only reviewers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from .autonomy_fakes import (Act, FakeExecutor, ScriptedCLIs, done, git, install_fake_clis, kinds, make_broker,
                             make_goal, make_repo, of)
from bcc.autonomy import workers as W
from bcc.autonomy.goals import GoalStore
from bcc.autonomy.journal import Journal
from bcc.autonomy.lease import EngineeringLease


@pytest.fixture
def env(tmp_path, monkeypatch):
    install_fake_clis(monkeypatch)
    repo = make_repo(tmp_path)
    root = tmp_path / "autonomy"
    journal = Journal(root)
    GoalStore(root, journal).create(make_goal())
    executor = FakeExecutor()
    return {"repo": repo, "base": git(repo, "rev-parse", "HEAD"), "tmp": root / "cycles" / "G-1", "root": root,
            "journal": journal, "lease": EngineeringLease(root, journal=journal), "executor": executor,
            "broker": make_broker(root, journal, executor)}


def writer(env, agent="claude", runner=None, lease=None, hands=None, journal=None, goal=None, **kw):
    return W.WriterSession(goal=goal or make_goal(), agent=agent, source_repo=env["repo"], base_sha=env["base"],
                           session_dir=env["tmp"] / f"s-{agent}", lease=lease or env["lease"],
                           hands=hands or env["broker"], journal=journal or env["journal"],
                           runner=runner or ScriptedCLIs(), timeout_s=30, **kw)


def test_roles_are_seeded_and_reproducible():
    a = W.assign_roles("JEFF-0042", 7)
    assert a == W.assign_roles("JEFF-0042", 7)
    assert a["review_order"] == ["claude", "codex"]
    writers = {W.assign_roles(f"G-{i}", 7)["writer"] for i in range(20)}
    assert writers == {"claude", "codex"}


def test_path_scope():
    allowed = ("docs/**", "command-center/tests/test_identity_*.py")
    assert W.path_allowed("docs/a/b.md", allowed)
    assert W.path_allowed("command-center/tests/test_identity_x.py", allowed)
    assert not W.path_allowed("command-center/bcc/runtime.py", allowed)
    assert not W.path_allowed("docs/../secrets.txt", allowed)
    assert not W.path_allowed(".git/config", ("**",))


def test_envelope_is_the_last_valid_json_object():
    text = 'noise {"status": "bogus"} then {"status": "done", "summary": "a"} and {"status": "failed", "summary": "b"}'
    assert W.extract_envelope(text)["status"] == "failed"
    assert W.extract_envelope("no json here") is None
    assert W.extract_envelope('{"status": "weird", "summary": "x"}') is None


def test_hand_requests_are_parsed_strictly():
    good = {"goal_id": "G-1", "requested_by": "claude", "action": "run_tests", "target": "isolated_worktree",
            "arguments": {"suite": "x"}, "expected_evidence": ["exit_code"], "risk_class": "low"}
    env = {"status": "need_hands", "summary": "", "hand_requests": [
        good,
        {**good, "goal_id": "OTHER"},
        {**good, "requested_by": "codex"},
        {**good, "sudo": True},
        {**good, "risk_class": "high"},
        {**good, "action": "rm -rf /"},
        "not a dict",
    ]}
    ok, rejected = W.parse_hand_requests(env, goal_id="G-1", agent="claude")
    assert len(ok) == 1 and ok[0].requested_by == "claude" and ok[0].timeout_s == 120
    reasons = [r["reason"] for r in rejected]
    assert any("goal_id" in r for r in reasons) and any("requested_by" in r for r in reasons)
    assert any("unknown fields" in r for r in reasons) and any("rollback" in r for r in reasons)
    assert any("invalid action" in r for r in reasons) and any("not an object" in r for r in reasons)
    many = {"status": "need_hands", "summary": "", "hand_requests": [good] * 12}
    ok, rejected = W.parse_hand_requests(many, goal_id="G-1", agent="claude")
    assert len(ok) == W.MAX_HAND_REQUESTS_PER_TURN and rejected


async def test_writer_commits_in_isolated_clone_and_journals_transcript(env, monkeypatch):
    monkeypatch.setenv("SOME_SERVICE_API_KEY", "x")
    monkeypatch.setenv("BOSSMAN_ANYTHING", "x")
    lease, journal, clis = env["lease"], env["journal"], ScriptedCLIs()
    res = await writer(env, lease=lease, journal=journal, runner=clis).run()
    assert res.status == "ok", res.summary
    assert res.sha and res.sha != env["base"] and res.changed_paths == ["docs/README.md"]
    assert len(res.diff_sha256) == 64
    assert git(env["repo"], "rev-parse", "HEAD") == env["base"]          # the source repo is untouched
    assert lease.current() is None
    assert [k for k in kinds(journal) if k.startswith("lease.")] == ["lease.acquired", "lease.released"]
    turn = of(journal, "worker_turn")[0]
    assert turn["transcript_sha256"] == res.transcripts[0]["sha256"]
    assert Path(res.transcripts[0]["path"]).is_file()
    assert "SOME_SERVICE_API_KEY" not in clis.envs[0] and "BOSSMAN_ANYTHING" not in clis.envs[0]
    msg = git(Path(res.worktree), "log", "-1", "--format=%an|%B")
    assert msg.startswith("BOSSMAN|") and "Worker: claude" in msg
    manifest = json.loads((env["tmp"] / "s-claude" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["allowed_paths"] == ["docs/**"] and manifest["output_schema"]["required"] == ["status", "summary"]


async def test_second_writer_is_refused_while_lease_is_held(env):
    lease = env["lease"]
    lease.acquire("OTHER-1", "someone", 60)
    clis = ScriptedCLIs()
    res = await writer(env, lease=lease, runner=clis).run()
    assert res.status == "lease_busy" and clis.calls == []


async def test_timeout_kills_and_releases(env):
    lease = env["lease"]
    res = await writer(env, lease=lease, runner=ScriptedCLIs(writer=lambda c: Act(timed_out=True))).run()
    assert res.status == "timeout" and res.sha is None and lease.current() is None


async def test_scope_violation_is_flagged(env):
    clis = ScriptedCLIs(writer=lambda c: Act(text=done(), edits={"command-center/bcc/runtime.py": "X = 2\n"}))
    res = await writer(env, runner=clis).run()
    assert res.status == "violation" and res.violations[0].startswith("scope:command-center/bcc/runtime.py")


async def test_tampered_git_is_never_committed(env):
    clis = ScriptedCLIs(writer=lambda c: Act(text=done(), edits={"docs/README.md": "x\n",
                                                                  ".git/hooks/pre-commit": "evil\n"}))
    res = await writer(env, runner=clis).run()
    assert res.status == "violation" and "tampered_git" in res.violations and res.sha is None


async def test_malformed_output_is_not_success(env):
    res = await writer(env, runner=ScriptedCLIs(writer=lambda c: Act(text="I did it!",
                                                                    edits={"docs/README.md": "y\n"}))).run()
    assert res.status == "malformed" and res.sha is None


async def test_no_change_is_reported(env):
    res = await writer(env, runner=ScriptedCLIs(writer=lambda c: Act(text=done()))).run()
    assert res.status == "no_change"


async def test_worker_that_commits_itself_is_still_a_candidate(env):
    def script(call):
        (call.cwd / "docs" / "README.md").write_text("self-committed\n", encoding="utf-8")
        git(call.cwd, "-c", "user.name=w", "-c", "user.email=w@w", "commit", "-qam", "by the worker")
        return Act(text=done())

    res = await writer(env, agent="codex", runner=ScriptedCLIs(writer=script)).run()
    assert res.status == "ok" and res.changed_paths == ["docs/README.md"]


async def test_hand_requests_are_routed_to_the_broker_never_direct(env):
    turns = []

    def script(call):
        turns.append(call.prompt)
        if len(turns) == 1:
            req = {"goal_id": "G-1", "requested_by": call.agent, "action": "run_tests", "target": "isolated_worktree",
                   "arguments": {"suite": "acceptance", "worktree": str(call.cwd),
                                 "acceptance_tests": ["pytest:command-center/tests/test_a.py"]},
                   "expected_evidence": ["exit_code"], "risk_class": "low", "rollback": "none: read-only"}
            bad = {**req, "goal_id": "G-2"}
            return Act(text=json.dumps({"status": "need_hands", "summary": "need tests",
                                        "hand_requests": [req, bad]}))
        return Act(text=done(), edits={"docs/README.md": "fixed\n"})

    journal = env["journal"]
    res = await writer(env, agent="codex", runner=ScriptedCLIs(writer=script)).run()
    assert res.status == "ok" and res.turns_used == 2
    reqs = [e["request"] for e in of(journal, "hand.request")]
    assert [(r["goal_id"], r["requested_by"], r["action"]) for r in reqs] == [("G-1", "codex", "run_tests")]
    assert [r["ok"] for r in of(journal, "hand.result")] == [True] and len(env["executor"].calls) == 1
    assert "Results of your previous hand requests" in turns[1]
    assert of(journal, "hand_requests_rejected")[0]["rejected"][0]["reason"] == "goal_id outside the active goal"


async def test_writer_turn_budget_is_respected(env):
    res = await writer(env, runner=ScriptedCLIs(), turns_left=lambda: 0).run()
    assert res.status == "budget"


async def test_reviewer_is_read_only_at_exact_sha(env):
    w = await writer(env).run()
    clis, journal = ScriptedCLIs(), env["journal"]
    for agent in ("claude", "codex"):
        sess = W.ReviewerSession(goal=make_goal(), reviewer=agent, source_worktree=Path(w.worktree), sha=w.sha,
                                 diff_sha256=w.diff_sha256, evidence_sha256="e" * 64, evidence=[],
                                 session_dir=env["tmp"] / f"r-{agent}", journal=journal, runner=clis)
        out = await sess.run()
        assert out.status == "ok" and w.sha in out.text
        assert git(sess.checkout, "rev-parse", "HEAD") == w.sha
    claude_argv = W.cli_argv("claude", "reviewer", cwd=env["tmp"], last_message=env["tmp"] / "m")
    assert "plan" in claude_argv and "Edit" not in claude_argv[claude_argv.index("--allowedTools") + 1]
    codex_argv = W.cli_argv("codex", "reviewer", cwd=env["tmp"], last_message=env["tmp"] / "m")
    assert codex_argv[codex_argv.index("--sandbox") + 1] == "read-only"
    assert all(c.role == "reviewer" for c in clis.calls)


async def test_reviewer_that_writes_or_times_out_is_unusable(env):
    w = await writer(env).run()
    for act, status in ((Act(text="{}", edits={"docs/README.md": "sneaky\n"}), "wrote"),
                        (Act(timed_out=True), "timeout")):
        sess = W.ReviewerSession(goal=make_goal(), reviewer="codex", source_worktree=Path(w.worktree), sha=w.sha,
                                 diff_sha256=w.diff_sha256, evidence_sha256="e" * 64, evidence=[],
                                 session_dir=env["tmp"] / f"r-{status}", journal=env["journal"],
                                 runner=ScriptedCLIs(reviewer=lambda c, a=act: a))
        assert (await sess.run()).status == status


def _diff(path: str, old: str, new: str) -> str:
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n")


async def test_nemotron_diff_is_applied_by_bossman_inside_scope(env):
    async def chat(messages):
        assert "docs/README.md" in messages[1]["content"]
        return {"text": "```diff\n" + _diff("docs/README.md", "# docs", "# docs v2") + "```", "tokens_in": 900,
                "tokens_out": 80, "model": "nvidia/nemotron-3-ultra-550b-a55b:free"}

    goal = make_goal(paths=("docs/README.md",), tier="docs_tests")
    res = await writer(env, agent="nemotron", goal=goal, nemotron=W.NemotronWriter(chat, model="n")).run()
    assert res.status == "ok" and res.changed_paths == ["docs/README.md"] and res.tokens_in == 900
    assert (Path(res.worktree) / "docs" / "README.md").read_text(encoding="utf-8") == "# docs v2\n"


async def test_nemotron_diff_outside_scope_or_garbage_is_refused(env):
    async def outside(messages):
        return {"text": "```diff\n" + _diff("command-center/bcc/runtime.py", "X = 1", "X = 2") + "```"}

    async def garbage(messages):
        return {"text": "sure, here is the change: make it better"}

    goal = make_goal(paths=("docs/README.md",), tier="docs_tests")
    r1 = await writer(env, agent="nemotron", goal=goal, nemotron=W.NemotronWriter(outside, model="n")).run()
    assert r1.status == "violation" and r1.violations[0].startswith("scope:")
    env["tmp"] = env["tmp"] / "second"
    r2 = await writer(env, agent="nemotron", goal=goal, nemotron=W.NemotronWriter(garbage, model="n")).run()
    assert r2.status == "malformed"


def test_manifest_hash_is_stable():
    m = W.TaskManifest(task_id="t", goal_id="g", role="writer", agent="claude", branch="b", worktree="w",
                       base_sha="0", problem="p", desired_result="d", acceptance_tests=("a",),
                       allowed_paths=("docs/**",), constraints=(), timeout_s=10)
    assert m.sha256() == W.TaskManifest(**m.as_dict()).sha256()
    assert "hand_requests" in m.prompt()
