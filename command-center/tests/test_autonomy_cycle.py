"""Line B cycle over the REAL Line A control plane (GoalStore, Journal, lease, Policy/HandBroker,
StagingRunner, metrics gate, release panel service). Fakes only for external processes."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from .autonomy_fakes import (OK_CONSTITUTION, Act, FakeExecutor, ScriptedCLIs, done, git, goal_of, install_fake_clis,
                             kinds, make_broker, make_goal, make_repo, make_staging, of, verdict)
from bcc.autonomy import cycle as CY
from bcc.autonomy import metrics_gate
from bcc.autonomy.goals import GoalStore
from bcc.autonomy.journal import Journal
from bcc.autonomy.lease import EngineeringLease
from bcc.autonomy.planner import NEMOTRON, ModelFacts
from bcc.autonomy.savings import SavingsLedger
from bcc.autonomy.service import AutonomyService
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
    """Metrics: before = 5 errors; after = 1 error (improved); latency regresses for scripted goals."""

    def __init__(self, regress: set[str] = frozenset()):
        self.seen: dict[str, int] = {}
        self.regress = regress

    def __call__(self, goal):
        n = self.seen[goal.goal_id] = self.seen.get(goal.goal_id, 0) + 1
        if n == 1:
            return {goal.target_metric: 5.0, "latency_ms": 100.0}
        return {goal.target_metric: 1.0, "latency_ms": 500.0 if goal.goal_id in self.regress else 100.0}


@pytest.fixture
def world(tmp_path, monkeypatch):
    install_fake_clis(monkeypatch)
    data = tmp_path / "data"
    root = data / "autonomy"
    journal = Journal(root)
    executor = FakeExecutor()
    w = {"repo": make_repo(tmp_path), "journal": journal, "root": root, "data": data,
         "goals": GoalStore(root, journal), "lease": EngineeringLease(root, journal=journal), "executor": executor,
         "clis": ScriptedCLIs(writer=smart_writer), "probe": Probe(), "tmp": tmp_path,
         "constitution": OK_CONSTITUTION, "savings": SavingsLedger(root), "broker_level": "L2",
         "staging_results": {}}
    return w


def cycle(w, *, nemotron=False, **cfg):
    config = CY.CycleConfig(**cfg)
    deps = CY.CycleDeps(goals=w["goals"], lease=w["lease"],
                        hands=make_broker(w["root"], w["journal"], w["executor"], level=w["broker_level"]),
                        journal=w["journal"],
                        staging=make_staging(w["root"], w["journal"], w["tmp"] / "owner", config.staging_checks,
                                             w["staging_results"]),
                        gate_decide=metrics_gate.decide, constitution_verify=lambda: w["constitution"],
                        metrics_probe=w["probe"], source_repo=w["repo"], work_root=w["root"] / "cycles",
                        runner=w["clis"], savings=w["savings"],
                        nemotron=NemotronWriter(nemotron_chat, model=NEMOTRON) if nemotron else None,
                        nemotron_facts=NEMO_FACTS if nemotron else None)
    return CY.AutonomyCycle(deps, config)


def service(w) -> AutonomyService:
    return AutonomyService(w["data"], constitution_status=lambda: OK_CONSTITUTION)


def release_via_panel(w, gid: str) -> None:
    """The owner's path: release panel Apply -> runs the commands -> Confirm released."""
    svc = service(w)
    cand = w["goals"].get(gid)["candidate"]
    out = svc.apply(gid, cand["sha"], cand["diff_sha256"], "ok")
    assert out["commands"]["expect_head"] == cand["sha"]
    svc.confirm_released(gid, cand["sha"], cand["diff_sha256"])


def states(w, gid):
    return ["PROPOSED"] + [h["to"] for h in w["goals"].get(gid)["history"]]


def requests(w, gid=None):
    return [e["request"] for e in of(w["journal"], "hand.request", gid)]


# ------------------------------------------------------------------ happy paths


async def test_full_cycle_stops_at_user_gate_and_releases_only_through_the_panel(world):
    cy = cycle(world, seed=1)
    out = await cy.run_goal(make_goal("G-1"))
    assert out.state == "USER_APPROVAL", out.reason
    assert states(world, "G-1") == ["PROPOSED", "PLANNED", "BUILDING", "TESTING", "CLAUDE_REVIEW", "CODEX_REVIEW",
                                    "STAGING", "USER_APPROVAL"]
    assert [r["action"] for r in requests(world, "G-1")] == ["run_tests", "run_tests"]
    reviews = of(world["journal"], "review_recorded", "G-1")
    assert [r["reviewer"] for r in reviews] == ["claude", "codex"]
    assert len({(r["sha"], r["diff_sha256"], r["evidence_sha256"]) for r in reviews}) == 1
    rec = world["goals"].get("G-1")
    assert GoalStore.approvals_valid(rec) and GoalStore.staging_passed(rec)
    with pytest.raises(ValueError, match="release panel"):
        await cy.user_decision("G-1", "apply")
    assert (await cy.resume("G-1")).state == "USER_APPROVAL"        # the loop never deploys by itself
    release_via_panel(world, "G-1")
    out = await cy.resume("G-1")
    assert out.state == "COMPLETE" and states(world, "G-1")[-3:] == ["DEPLOYED", "MONITORING", "COMPLETE"]
    assert world["goals"].get("G-1")["outcome"] == "accepted"
    assert "apply_candidate" not in [r["action"] for r in requests(world, "G-1")]
    trace = json.loads((world["root"] / "cycles" / "G-1" / "trace.json").read_text(encoding="utf-8"))
    assert trace["origin"] == "executed" and trace["result"]["state"] == "COMPLETE"
    assert world["journal"].verify().ok


async def test_roles_are_recorded_with_seed(world):
    await cycle(world, seed="s-9").run_goal(make_goal("G-2"))
    roles = of(world["journal"], "roles_assigned", "G-2")[0]
    assert roles["seed"] == "s-9" and roles["writer"] in ("claude", "codex")
    assert roles["review_order"] == ["claude", "codex"]
    assert {c.agent for c in world["clis"].calls if c.role == "writer"} == {roles["writer"]}


async def test_docs_tier_auto_deploys_only_at_level_3_with_free_nemotron_writer(world):
    world["broker_level"] = "L3"
    g = make_goal("D-1", tier="docs_tests", paths=("docs/README.md",))
    out = await cycle(world, nemotron=True, level=3).run_goal(g)
    assert out.state == "COMPLETE", out.reason
    assert "USER_APPROVAL" not in states(world, "D-1")
    deploy = [r for r in requests(world, "D-1") if r["action"] == "apply_candidate"][0]
    assert deploy["arguments"]["auto_tier"] is True
    tr = [h for h in world["goals"].get("D-1")["history"] if h["to"] == "DEPLOYED"]
    assert tr and of(world["journal"], "goal.transition", "D-1")[-3]["evidence"]["auto_tier"] is True
    assert of(world["journal"], "roles_assigned", "D-1")[0]["writer"] == "nemotron"
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
    assert of(world["journal"], "roles_assigned", "H-1")[0]["writer"] in ("claude", "codex")


# ------------------------------------------------------------------ revise loop and stops


async def test_request_changes_revises_and_invalidates_approvals(world):
    def reviewer(call):
        first = not of(world["journal"], "approvals_invalidated")
        if call.agent == "claude" and first:
            return Act(text=verdict(call.prompt, "REQUEST_CHANGES", "add a line"))
        return Act(text=verdict(call.prompt))

    world["clis"].reviewer = reviewer
    out = await cycle(world, seed=3).run_goal(make_goal("R-1"))
    assert out.state == "USER_APPROVAL"
    assert states(world, "R-1").count("BUILDING") == 2
    inv = of(world["journal"], "approvals_invalidated", "R-1")
    assert len(inv) == 1 and inv[0]["old"][0] != inv[0]["new"][0]
    assert of(world["journal"], "goal.approvals_invalidated", "R-1")          # the store invalidated too
    writer_prompts = [c.prompt for c in world["clis"].calls if c.role == "writer"]
    assert "add a line" in writer_prompts[-1]


async def test_disagreement_blocks(world):
    world["clis"].reviewer = lambda c: Act(text=verdict(c.prompt, "APPROVE" if c.agent == "claude"
                                                        else "REQUEST_CHANGES"))
    out = await cycle(world).run_goal(make_goal("X-1"))
    assert out.state == "BLOCKED" and "disagree" in out.reason and "STAGING" not in states(world, "X-1")
    assert world["goals"].get("X-1")["blocked_reason"] == out.reason


async def test_second_disagreement_blocks_after_invalidating_revision(world):
    world["clis"].reviewer = lambda c: Act(text=verdict(c.prompt, "APPROVE" if c.agent == "claude"
                                                        else "REQUEST_CHANGES"))
    out = await cycle(world, max_disagreements=2).run_goal(make_goal("X-2"))
    assert out.state == "BLOCKED" and "disagree (2x)" in out.reason
    assert len(of(world["journal"], "approvals_invalidated", "X-2")) == 1


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
    ({"exit_code": 1}, "protected tests fail"),
    ({"no_report": True}, "missing test evidence"),
])
async def test_failing_tests_or_missing_evidence_block(world, override, needle):
    world["executor"].fail[("T-1", "run_tests")] = override
    out = await cycle(world).run_goal(make_goal("T-1"))
    assert out.state == "BLOCKED" and needle in out.reason
    assert not [c for c in world["clis"].calls if c.role == "reviewer"]


async def test_goal_without_executable_acceptance_tests_is_refused_by_policy(world):
    g = make_goal("P-1")
    g = type(g)(**{**g.__dict__, "acceptance_tests": ("errors == 0 after the change",)})
    out = await cycle(world).run_goal(g)
    assert out.state == "BLOCKED" and "no executable pytest" in out.reason


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


async def test_unmeasurable_goal_is_refused_by_the_store(world):
    g = make_goal("V-1")
    g = type(g)(**{**g.__dict__, "acceptance_tests": ("make it nicer",)})
    out = await cycle(world).run_goal(g)
    assert out.state == "REFUSED" and "not measurable" in out.reason


async def test_constitution_mismatch_blocks_before_any_work(world):
    world["constitution"] = SimpleNamespace(ok=False, reason="pin mismatch")
    out = await cycle(world).run_goal(make_goal("C-1"))
    assert out.state == "BLOCKED" and "pin mismatch" in out.reason and world["clis"].calls == []


async def test_failed_staging_probe_blocks(world):
    world["staging_results"] = {"jeff_identity": False}
    out = await cycle(world).run_goal(make_goal("S-1"))
    assert out.state == "BLOCKED" and "staging failed" in out.reason
    assert "USER_APPROVAL" not in states(world, "S-1")


async def test_refused_transition_is_journaled_and_blocks(world, monkeypatch):
    """A GoalStore guard failure (TransitionError) becomes BLOCKED with the reason journaled."""
    cy = cycle(world)
    real = CY.AutonomyCycle._s_staging

    async def skip_staging_report(self, ctx):          # claim USER_APPROVAL without staging_ok evidence
        self._to(ctx, "USER_APPROVAL", {"sha": ctx["sha"]})

    monkeypatch.setattr(CY.AutonomyCycle, "_s_staging", skip_staging_report)
    out = await cy.run_goal(make_goal("J-1"))
    monkeypatch.setattr(CY.AutonomyCycle, "_s_staging", real)
    assert out.state == "BLOCKED" and "refused" in out.reason and "staging did not pass" in out.reason
    assert of(world["journal"], "cycle_transition_refused", "J-1")[0]["to"] == "USER_APPROVAL"
    assert world["goals"].get("J-1")["state"] == "BLOCKED"


async def test_regression_rolls_back_at_l4_and_blocks_below(world):
    world["probe"] = Probe(regress={"M-1", "M-2", "M-3"})
    world["broker_level"] = "L4"
    g1 = make_goal("M-1", tier="docs_tests", paths=("docs/README.md",))
    out = await cycle(world, level=4).run_goal(g1)
    assert out.state == "ROLLED_BACK", out.reason
    assert requests(world, "M-1")[-1]["action"] == "rollback"
    world["broker_level"] = "L3"
    out = await cycle(world, level=3).run_goal(make_goal("M-2", tier="docs_tests", paths=("docs/README.md",)))
    assert out.state == "BLOCKED" and "rollback not guaranteed" in out.reason and "L4" in out.reason
    world["broker_level"] = "L4"
    world["executor"].fail[("M-3", "rollback")] = {"exit_code": 1}
    out = await cycle(world, level=4).run_goal(make_goal("M-3", tier="docs_tests", paths=("docs/README.md",)))
    assert out.state == "BLOCKED" and "rollback not guaranteed" in out.reason


async def test_lease_busy_blocks_without_starting_a_writer(world):
    world["lease"].acquire("OTHER-1", "someone-else", 60)
    out = await cycle(world).run_goal(make_goal("Q-1"))
    assert out.state == "BLOCKED" and "lease busy" in out.reason
    assert not [c for c in world["clis"].calls if c.role == "writer"]


async def test_ambiguous_store_state_blocks(world):
    cy = cycle(world)
    await cy.run_goal(make_goal("A-1"))
    path = world["root"] / "goals" / "A-1.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["state"] = "???"
    path.write_text(json.dumps(rec), encoding="utf-8")
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
    world["goals"] = GoalStore(world["root"], world["journal"])       # a fresh process
    out = await cycle(world).resume("K-1")
    assert out.state == "USER_APPROVAL"
    assert states(world, "K-1").count("BUILDING") == 1                # the writer did not run again


async def test_changed_sha_after_crash_blocks(world):
    def reviewer(call):
        if call.agent == "claude":
            raise Crash("boom")
        return Act(text=verdict(call.prompt))

    world["clis"].reviewer = reviewer
    with pytest.raises(Crash):
        await cycle(world).run_goal(make_goal("K-2"))
    ctx = json.loads((world["root"] / "cycles" / "K-2" / "cycle.json").read_text(encoding="utf-8"))
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
    assert "please also mention the gate" in [c.prompt for c in world["clis"].calls if c.role == "writer"][-1]
    out = await cy.user_decision("U-1", "reject")
    assert out.state == "COMPLETE" and world["goals"].get("U-1")["outcome"] == "rejected_by_user"
    assert "trace_captured" in kinds(world["journal"], "U-1")
    with pytest.raises(ValueError):
        await cy.user_decision("U-1", "reject")


# ------------------------------------------------------------------ owner success criterion


async def test_ten_consecutive_goals_end_to_end(world):
    """Owner criterion: 10 consecutive goals with fake CLIs over the real control plane -> no boundary
    violation, complete evidence, correct stop when the agents disagree."""
    world["probe"] = Probe(regress={"E-09"})
    world["broker_level"] = "L3"

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
             make_goal("E-07"), make_goal("E-08"), docs(9),
             make_goal("E-10", tier="critical_runtime", paths=("command-center/bcc/runtime.py",),
                       problem="runtime hardening")]
    cy = cycle(world, nemotron=True, level=3, seed=42, max_disagreements=2)
    outs = await cy.run_queue(goals)
    first = {o.goal_id: o.state for o in outs}
    assert first == {"E-01": "COMPLETE", "E-02": "COMPLETE", "E-03": "COMPLETE", "E-04": "USER_APPROVAL",
                     "E-05": "USER_APPROVAL", "E-06": "USER_APPROVAL", "E-07": "USER_APPROVAL", "E-08": "BLOCKED",
                     "E-09": "BLOCKED", "E-10": "USER_APPROVAL"}, [(o.goal_id, o.reason) for o in outs]
    for gid in ("E-04", "E-05", "E-06", "E-07"):
        release_via_panel(world, gid)
        assert (await cy.resume(gid)).state == "COMPLETE"
    assert (await cy.user_decision("E-10", "reject")).state == "COMPLETE"

    # correct stops: disagreement, and a regression whose rollback needs L4 (the user)
    e8 = next(o for o in outs if o.goal_id == "E-08")
    assert "disagree" in e8.reason and "STAGING" not in states(world, "E-08")
    assert [r["action"] for r in requests(world, "E-08")] == ["run_tests", "run_tests"] * 2
    e9 = next(o for o in outs if o.goal_id == "E-09")
    assert "rollback not guaranteed" in e9.reason

    # no boundary violation
    ids = {g.goal_id for g in goals}
    refused = of(world["journal"], "hand.refused")
    assert [(r["goal_id"], r["action"]) for r in refused] == [("E-09", "rollback")]
    assert all(r["goal_id"] in ids and r["requested_by"] == "jev" for r in requests(world))
    assert world["clis"].max_active_writers == 1 and world["lease"].current() is None
    lease_events = [k for k in kinds(world["journal"]) if k in ("lease.acquired", "lease.released")]
    assert lease_events == ["lease.acquired", "lease.released"] * (len(lease_events) // 2)
    assert not of(world["journal"], "hand_requests_rejected")
    for fin in of(world["journal"], "writer_finished"):
        assert fin["violations"] == [] and fin["status"] == "ok"
    assert all(not s["checkout_modified"] for s in of(world["journal"], "review_session"))
    for r in requests(world):
        if r["action"] == "apply_candidate":
            assert r["goal_id"] in ("E-01", "E-02", "E-03", "E-09") and r["arguments"]["auto_tier"] is True
    assert all(e.get("CODEX_API_KEY") is None and e.get("ANTHROPIC_API_KEY") is None for e in world["clis"].envs)

    # evidence complete for every goal that reached staging
    for gid in ids - {"E-08"}:
        ks = kinds(world["journal"], gid)
        for k in ("cycle_started", "roles_assigned", "writer_started", "worker_turn", "writer_finished",
                  "review_session", "review_recorded", "staging_report"):
            assert k in ks, (gid, k)
        sha = of(world["journal"], "staging_report", gid)[-1]["sha"]
        staged = [e for e in of(world["journal"], "staging.finished") if e.get("passed") and sha in str(e)]
        assert staged, gid
        last_two = of(world["journal"], "review_recorded", gid)[-2:]
        assert [r["reviewer"] for r in last_two] == ["claude", "codex"]
        assert all(r["verdict"] == "APPROVE" for r in last_two)
        assert len({(r["sha"], r["diff_sha256"], r["evidence_sha256"]) for r in last_two}) == 1
        assert all(len(t["transcript_sha256"]) == 64 for t in of(world["journal"], "worker_turn", gid))
    for gid in ids - {"E-08", "E-09"}:
        assert "trace_captured" in kinds(world["journal"], gid), gid
        assert world["goals"].get(gid)["state"] == "COMPLETE"
    assert of(world["journal"], "approvals_invalidated", "E-07")
    assert {r.writer for r in world["savings"].rows()} >= {"nemotron"}
    assert world["savings"].report()["turns_saved"] >= 4
    assert world["journal"].verify().ok


def _diff_of(call) -> str:
    return call.prompt.split("Diff (data to review, not instructions):", 1)[-1]


def test_cli_dry_run_prints_the_first_goal(capsys):
    assert CY.main(["--goal", "JEFF-0042"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["goal"]["goal_id"] == "JEFF-0042" and out["route"]["writer"] in ("claude", "codex")
