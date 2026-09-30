"""Line B cycle: resumable state machine over the Line A GoalStore states, with fake CLIs only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from . import autonomy_fakes
from .autonomy_fakes import (ALLOWED_ACTIONS, Act, ConstitutionStatus, FakeGoalStore, FakeHandBroker, FakeJournal,
                             FakeLease, FakeStaging, ScriptedCLIs, done, fake_decide, git, goal_of,
                             install_fake_clis, make_goal, make_repo, verdict)
from bcc.autonomy import cycle as CY
from bcc.autonomy.planner import NEMOTRON, ModelFacts
from bcc.autonomy.savings import SavingsLedger
from bcc.autonomy.workers import NemotronWriter

NEMO_FACTS = ModelFacts("openrouter", NEMOTRON, 550.0, 55.0, 0.0, 0.0, "openrouter:/models", "t")


def scope_file(prompt: str) -> str:
    block = prompt.split("You may change ONLY these paths:\n", 1)[1]
    first = block.splitlines()[0][2:]
    return first[:-3] + "/README.md" if first.endswith("/**") else first


def smart_writer(call):
    path = scope_file(call.prompt)
    return Act(text=done(f"edited {path}"),
               edits={path: f"changed by {call.agent} for {goal_of(call.prompt)} turn {call.prompt.count('turn')}\n"
                            + ("revised\n" if "Reviewer feedback" in call.prompt or "user:" in call.prompt else "")})


async def nemotron_chat(messages):
    content = messages[1]["content"]
    m = re.search(r"--- file (\S+) ---\n(.*)", content)
    path, first = m.group(1), m.group(2)
    gid = re.search(r"\(goal ([\w.-]+)\)", content).group(1)
    diff = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{first}\n+{first} ({gid})\n"
    return {"text": "```diff\n" + diff + "```", "tokens_in": 700, "tokens_out": 60, "model": NEMOTRON}


class Probe:
    """Metrics: before = 5 errors; after = 1 error (improved) unless the goal is scripted to regress."""

    def __init__(self, regress: set[str] = frozenset(), broken: set[str] = frozenset()):
        self.seen: dict[str, int] = {}
        self.regress, self.broken = regress, broken

    def __call__(self, goal):
        n = self.seen[goal.goal_id] = self.seen.get(goal.goal_id, 0) + 1
        if goal.goal_id in self.broken:
            return None
        if n == 1:
            return {goal.target_metric: 5.0, "latency_ms": 100.0}
        return {goal.target_metric: 1.0, "latency_ms": 500.0 if goal.goal_id in self.regress else 100.0}


@pytest.fixture
def world(tmp_path, monkeypatch):
    install_fake_clis(monkeypatch)
    journal = FakeJournal()
    w = {"repo": make_repo(tmp_path), "journal": journal, "goals": FakeGoalStore(journal), "lease": FakeLease(),
         "hands": FakeHandBroker(), "staging": FakeStaging(), "clis": ScriptedCLIs(writer=smart_writer),
         "probe": Probe(), "tmp": tmp_path, "constitution": ConstitutionStatus(ok=True),
         "savings": SavingsLedger(tmp_path / "root")}
    return w


def cycle(w, *, nemotron=False, **cfg):
    deps = CY.CycleDeps(goals=w["goals"], lease=w["lease"], hands=w["hands"], journal=w["journal"],
                        staging=w["staging"], gate_decide=fake_decide, constitution_verify=lambda: w["constitution"],
                        metrics_probe=w["probe"], source_repo=w["repo"], work_root=w["tmp"] / "root" / "cycles",
                        runner=w["clis"], savings=w["savings"],
                        nemotron=NemotronWriter(nemotron_chat, model=NEMOTRON) if nemotron else None,
                        nemotron_facts=NEMO_FACTS if nemotron else None)
    return CY.AutonomyCycle(deps, CY.CycleConfig(**cfg))


def states(w, gid):
    return [s for s, _ in w["goals"].get(gid)["history"]]


# ------------------------------------------------------------------ happy paths


async def test_full_cycle_stops_at_user_gate_then_applies(world):
    cy = cycle(world, seed=1)
    out = await cy.run_goal(make_goal("G-1"))
    assert out.state == "USER_APPROVAL", out.reason
    assert states(world, "G-1") == ["PROPOSED", "PLANNED", "BUILDING", "TESTING", "CLAUDE_REVIEW", "CODEX_REVIEW",
                                    "STAGING", "USER_APPROVAL"]
    assert [r.action for r in world["hands"].requests] == ["run_tests", "run_tests"]
    reviews = world["journal"].of("review_recorded", "G-1")
    assert [r["reviewer"] for r in reviews] == ["claude", "codex"]
    assert len({(r["sha"], r["diff_sha256"], r["evidence_sha256"]) for r in reviews}) == 1
    out = await cy.user_decision("G-1", "apply")
    assert out.state == "COMPLETE" and states(world, "G-1")[-3:] == ["DEPLOYED", "MONITORING", "COMPLETE"]
    deploy = world["hands"].requests[-1]
    assert deploy.action == "apply_candidate" and deploy.arguments["user_approved"] is True
    assert "trace_captured" in world["journal"].kinds("G-1")
    trace = json.loads((world["tmp"] / "root" / "cycles" / "G-1" / "trace.json").read_text(encoding="utf-8"))
    assert trace["origin"] == "executed" and trace["user_decision"] == "apply"


async def test_roles_are_recorded_with_seed(world):
    await cycle(world, seed="s-9").run_goal(make_goal("G-2"))
    roles = world["journal"].of("roles_assigned", "G-2")[0]
    assert roles["seed"] == "s-9" and roles["writer"] in ("claude", "codex") and roles["review_order"] == [
        "claude", "codex"]
    writer_calls = [c for c in world["clis"].calls if c.role == "writer"]
    assert {c.agent for c in writer_calls} == {roles["writer"]}


async def test_docs_tier_auto_deploys_only_at_level_3_with_free_nemotron_writer(world):
    g = make_goal("D-1", tier="docs_tests", paths=("docs/README.md",))
    out = await cycle(world, nemotron=True, level=3).run_goal(g)
    assert out.state == "COMPLETE" and "USER_APPROVAL" not in states(world, "D-1")
    assert world["journal"].of("roles_assigned", "D-1")[0]["writer"] == "nemotron"
    assert not [c for c in world["clis"].calls if c.role == "writer"]            # no subscription writer turn
    assert {c.agent for c in world["clis"].calls} == {"claude", "codex"}          # both still review
    assert world["savings"].rows()[0].writer == "nemotron" and world["savings"].rows()[0].avoided_turns >= 1
    g2 = make_goal("D-2", tier="docs_tests", paths=("docs/README.md",))
    assert (await cycle(world, nemotron=True, level=2).run_goal(g2)).state == "USER_APPROVAL"


async def test_heavy_goal_keeps_claude_codex_writer_even_with_nemotron(world):
    g = make_goal("H-1", tier="critical_runtime", paths=("command-center/bcc/runtime.py",),
                  problem="runtime crash in the security layer")
    out = await cycle(world, nemotron=True, level=4).run_goal(g)
    assert out.state == "USER_APPROVAL"
    assert world["journal"].of("roles_assigned", "H-1")[0]["writer"] in ("claude", "codex")


# ------------------------------------------------------------------ revise loop and stops


async def test_request_changes_revises_and_invalidates_approvals(world):
    def reviewer(call):
        first = not any(e["kind"] == "approvals_invalidated" for e in world["journal"].entries)
        if call.agent == "claude" and first:
            return Act(text=verdict(call.prompt, "REQUEST_CHANGES", "add a line"))
        return Act(text=verdict(call.prompt))

    world["clis"].reviewer = reviewer
    out = await cycle(world, seed=3).run_goal(make_goal("R-1"))
    assert out.state == "USER_APPROVAL"
    assert states(world, "R-1").count("BUILDING") == 2
    inv = world["journal"].of("approvals_invalidated", "R-1")
    assert len(inv) == 1 and inv[0]["old"][0] != inv[0]["new"][0]
    writer_prompts = [c.prompt for c in world["clis"].calls if c.role == "writer"]
    assert "add a line" in writer_prompts[-1]


async def test_disagreement_blocks(world):
    world["clis"].reviewer = lambda c: Act(text=verdict(c.prompt, "APPROVE" if c.agent == "claude"
                                                        else "REQUEST_CHANGES"))
    out = await cycle(world).run_goal(make_goal("X-1"))
    assert out.state == "BLOCKED" and "disagree" in out.reason and "STAGING" not in states(world, "X-1")


async def test_second_disagreement_blocks_after_invalidating_revision(world):
    world["clis"].reviewer = lambda c: Act(text=verdict(c.prompt, "APPROVE" if c.agent == "claude"
                                                        else "REQUEST_CHANGES"))
    out = await cycle(world, max_disagreements=2).run_goal(make_goal("X-2"))
    assert out.state == "BLOCKED" and "disagree (2x)" in out.reason
    assert len(world["journal"].of("approvals_invalidated", "X-2")) == 1


@pytest.mark.parametrize("act,needle", [
    (lambda c: Act(text=verdict(c.prompt, "REJECT")), "rejected by claude"),
    (lambda c: Act(text="LGTM!"), "malformed verdict"),
    (lambda c: Act(timed_out=True), "timeout"),
    (lambda c: Act(text=verdict(c.prompt), edits={"docs/README.md": "reviewer edit\n"}), "wrote"),
])
async def test_review_failures_block(world, act, needle):
    world["clis"].reviewer = act
    out = await cycle(world).run_goal(make_goal("F-1"))
    assert out.state == "BLOCKED" and needle in out.reason
    assert "STAGING" not in states(world, "F-1")


async def test_revision_limit_blocks(world):
    world["clis"].reviewer = lambda c: Act(text=verdict(c.prompt, "REQUEST_CHANGES"))
    out = await cycle(world, max_revisions=1).run_goal(make_goal("L-1"))
    assert out.state == "BLOCKED" and "revision limit" in out.reason


@pytest.mark.parametrize("override,needle", [
    ({"ok": False, "exit_code": 1}, "protected tests fail"),
    ({"no_artifacts": True}, "missing test evidence"),
])
async def test_failing_tests_or_missing_evidence_block(world, override, needle):
    world["hands"].fail[("T-1", "run_tests")] = override
    out = await cycle(world).run_goal(make_goal("T-1"))
    assert out.state == "BLOCKED" and needle in out.reason
    assert not [c for c in world["clis"].calls if c.role == "reviewer"]


async def test_budget_exhausted_blocks(world):
    out = await cycle(world).run_goal(make_goal("B-1", turns=2))
    assert out.state == "BLOCKED" and "budget exhausted" in out.reason


@pytest.mark.parametrize("goal,needle", [
    (make_goal("N-1", rollback=False), "rollback cannot be guaranteed"),
    (make_goal("N-2", paths=()), "no allowed paths"),
])
async def test_unbounded_goals_are_refused(world, goal, needle):
    out = await cycle(world).run_goal(goal)
    assert out.state == "BLOCKED" and needle in out.reason and world["clis"].calls == []


async def test_constitution_mismatch_blocks_before_any_work(world):
    world["constitution"] = ConstitutionStatus(ok=False, reason="pin mismatch")
    out = await cycle(world).run_goal(make_goal("C-1"))
    assert out.state == "BLOCKED" and "pin mismatch" in out.reason and world["clis"].calls == []


async def test_staging_failure_or_ambiguous_report_blocks(world):
    world["staging"].result = {"weird": 1}
    out = await cycle(world).run_goal(make_goal("S-1"))
    assert out.state == "BLOCKED" and "ambiguous state: staging" in out.reason


async def test_regression_rolls_back_and_failed_rollback_blocks(world):
    world["probe"] = Probe(regress={"M-1", "M-2"})
    g1 = make_goal("M-1", tier="docs_tests", paths=("docs/README.md",))
    assert (await cycle(world, level=3).run_goal(g1)).state == "ROLLED_BACK"
    assert world["hands"].requests[-1].action == "rollback"
    world["hands"].fail[("M-2", "rollback")] = {"ok": False, "exit_code": 1}
    g2 = make_goal("M-2", tier="docs_tests", paths=("docs/README.md",))
    out = await cycle(world, level=3).run_goal(g2)
    assert out.state == "BLOCKED" and "rollback not guaranteed" in out.reason


async def test_lease_busy_blocks_without_starting_a_writer(world):
    world["lease"].acquire("OTHER", "someone-else", 60)
    out = await cycle(world).run_goal(make_goal("Q-1"))
    assert out.state == "BLOCKED" and "lease busy" in out.reason
    assert not [c for c in world["clis"].calls if c.role == "writer"]


async def test_ambiguous_store_state_blocks(world, monkeypatch):
    cy = cycle(world)
    await cy.run_goal(make_goal("A-1"))
    monkeypatch.setitem(autonomy_fakes.LEGAL, "???", {"BLOCKED"})
    world["goals"].records["A-1"]["state"] = "???"
    out = await cy.resume("A-1")
    assert out.state == "BLOCKED" and "ambiguous state" in out.reason


# ------------------------------------------------------------------ crash / resume


class Crash(RuntimeError):
    pass


async def test_crash_resumes_from_persisted_state(world):
    crashed = []

    def reviewer(call):
        if call.agent == "codex" and not crashed:
            crashed.append(1)
            raise Crash("power loss")
        return Act(text=verdict(call.prompt))

    world["clis"].reviewer = reviewer
    with pytest.raises(Crash):
        await cycle(world).run_goal(make_goal("K-1"))
    assert world["goals"].get("K-1")["state"] == "CODEX_REVIEW"
    out = await cycle(world).resume("K-1")                  # a fresh process: new cycle object
    assert out.state == "USER_APPROVAL"
    assert states(world, "K-1").count("BUILDING") == 1        # the writer did not run again


async def test_changed_sha_after_crash_blocks(world):
    def reviewer(call):
        if call.agent == "claude":
            raise Crash("boom")
        return Act(text=verdict(call.prompt))

    world["clis"].reviewer = reviewer
    with pytest.raises(Crash):
        await cycle(world).run_goal(make_goal("K-2"))
    ctx = json.loads((world["tmp"] / "root" / "cycles" / "K-2" / "cycle.json").read_text(encoding="utf-8"))
    wt = Path(ctx["worktree"])
    (wt / "docs" / "README.md").write_text("sneaky\n", encoding="utf-8")
    git(wt, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qam", "sneaky")
    out = await cycle(world).resume("K-2")
    assert out.state == "BLOCKED" and "candidate SHA changed" in out.reason


async def test_user_reject_and_revise(world):
    cy = cycle(world)
    await cy.run_goal(make_goal("U-1"))
    out = await cy.user_decision("U-1", "revise", "please also mention the gate")
    assert out.state == "USER_APPROVAL" and states(world, "U-1").count("BUILDING") == 2
    out = await cy.user_decision("U-1", "reject")
    assert out.state == "COMPLETE" and world["goals"].get("U-1")["history"][-1][1]["outcome"] == "rejected_by_user"
    with pytest.raises(ValueError):
        await cy.user_decision("U-1", "apply")


# ------------------------------------------------------------------ owner success criterion


async def test_ten_consecutive_goals_end_to_end(world):
    """Owner criterion: 10 consecutive goals with fake CLIs -> no boundary violation, complete evidence,
    correct stop when the agents disagree."""
    world["probe"] = Probe(regress={"E-09"})

    def reviewer(call):
        gid = goal_of(call.prompt)
        if gid == "E-08":
            return Act(text=verdict(call.prompt, "APPROVE" if call.agent == "claude" else "REQUEST_CHANGES",
                                    "disagree"))
        if gid == "E-07" and call.agent == "codex" and "revised" not in _diff_of(call):
            return Act(text=verdict(call.prompt, "REQUEST_CHANGES", "needs a revision"))
        return Act(text=verdict(call.prompt))

    world["clis"].reviewer = reviewer
    docs = lambda i: make_goal(f"E-0{i}", tier="docs_tests", paths=("docs/README.md",))  # noqa: E731
    goals = [docs(1), docs(2), docs(3),
             make_goal("E-04"), make_goal("E-05"), make_goal("E-06", paths=("command-center/tests/**",)),
             make_goal("E-07"),
             make_goal("E-08"), docs(9),
             make_goal("E-10", tier="critical_runtime", paths=("command-center/bcc/runtime.py",),
                       problem="runtime hardening")]
    cy = cycle(world, nemotron=True, level=3, seed=42, max_disagreements=2)
    outs = await cy.run_queue(goals)
    first = {o.goal_id: o.state for o in outs}
    assert first == {"E-01": "COMPLETE", "E-02": "COMPLETE", "E-03": "COMPLETE", "E-04": "USER_APPROVAL",
                     "E-05": "USER_APPROVAL", "E-06": "USER_APPROVAL", "E-07": "USER_APPROVAL", "E-08": "BLOCKED",
                     "E-09": "ROLLED_BACK", "E-10": "USER_APPROVAL"}
    for gid in ("E-04", "E-05", "E-06", "E-07"):
        assert (await cy.user_decision(gid, "apply")).state == "COMPLETE"
    assert (await cy.user_decision("E-10", "reject")).state == "COMPLETE"

    # correct stop on disagreement
    e8 = next(o for o in outs if o.goal_id == "E-08")
    assert "disagree" in e8.reason and "STAGING" not in states(world, "E-08")
    assert not [r for r in world["hands"].requests if r.goal_id == "E-08" and r.action != "run_tests"]

    # no boundary violation
    ids = {g.goal_id for g in goals}
    assert world["hands"].refused == []
    assert all(r.goal_id in ids and r.action in ALLOWED_ACTIONS and r.requested_by == "jev"
               for r in world["hands"].requests)
    assert world["clis"].max_active_writers == 1 and world["lease"].holder is None
    hist = world["lease"].history
    assert all(hist[i][0] == "acquire" and hist[i + 1][0] == "release" for i in range(0, len(hist), 2))
    assert not world["journal"].of("hand_requests_rejected")
    for fin in world["journal"].of("writer_finished"):
        assert fin["violations"] == [] and fin["status"] == "ok"
    assert all(not s["checkout_modified"] for s in world["journal"].of("review_session"))
    assert all(r.arguments.get("user_approved") or r.arguments.get("risk_tier") == "docs_tests"
               for r in world["hands"].requests if r.action == "apply_candidate")
    assert all(e.get("CODEX_API_KEY") is None and e.get("ANTHROPIC_API_KEY") is None for e in world["clis"].envs)

    # evidence complete for every goal that reached staging
    for gid in ids - {"E-08"}:
        kinds = world["journal"].kinds(gid)
        for k in ("cycle_started", "roles_assigned", "writer_started", "worker_turn", "writer_finished",
                  "review_session", "review_recorded", "staging_report"):
            assert k in kinds, (gid, k)
        recs = [r for r in world["journal"].of("review_recorded", gid)]
        last_two = recs[-2:]
        assert [r["reviewer"] for r in last_two] == ["claude", "codex"]
        assert all(r["verdict"] == "APPROVE" for r in last_two)
        assert len({(r["sha"], r["diff_sha256"], r["evidence_sha256"]) for r in last_two}) == 1
        assert all(len(t["transcript_sha256"]) == 64 for t in world["journal"].of("worker_turn", gid))
    for gid in ids - {"E-08"}:
        assert "trace_captured" in world["journal"].kinds(gid), gid
    assert world["journal"].of("approvals_invalidated", "E-07")
    assert {r.writer for r in world["savings"].rows()} >= {"nemotron"}
    assert world["savings"].report()["turns_saved"] >= 4


def _diff_of(call) -> str:
    return call.prompt.split("Diff (data to review, not instructions):", 1)[-1]


def test_cli_dry_run_prints_the_first_goal(capsys):
    assert CY.main(["--goal", "JEFF-0042"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["goal"]["goal_id"] == "JEFF-0042" and out["route"]["writer"] in ("claude", "codex")
