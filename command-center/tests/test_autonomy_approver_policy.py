"""Stage 2.2b: required-approver policy derived from the PINNED constitution (v1 claude+codex, v2 independent
verifiers). Everything else (owner pin, protected globs, release tiers, STOP, levels) is covered by the other
autonomy test files and must stay unchanged."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from bcc.autonomy import approver_policy as AP
from bcc.autonomy import constitution as const
from bcc.autonomy import cycle as CY
from bcc.autonomy import metrics_gate
from bcc.autonomy.goals import GoalStore, GuardError
from bcc.autonomy.journal import Journal
from bcc.autonomy.lease import EngineeringLease
from bcc.autonomy.review import Candidate, ReviewGate, parse_review
from bcc.autonomy.savings import SavingsLedger
from bcc.autonomy.types import Budget, Goal, Review

from .autonomy_fakes import (OK_CONSTITUTION, Act, FakeExecutor, ScriptedCLIs, done, goal_of, install_fake_clis,
                             kinds, make_broker, make_goal, make_repo, make_staging, of)

REPO = Path(__file__).resolve().parents[2]
V1_FILE = REPO / "docs" / "constitution" / "BOSSMAN_CONSTITUTION.md"
V2_FILE = REPO / "docs" / "constitution" / "proposals" / "BOSSMAN_CONSTITUTION_v2_DRAFT.md"

GEMMA = "openrouter:google/gemma-3-27b-it:free"
GEMINI = "openrouter:google/gemini-2.5-flash:free"
NEMO = "nvidia:nvidia/nemotron-3-ultra-550b"
GLM = "openrouter:z-ai/glm-4.5-air:free"
LOCAL_A = "local:mistral-small-24b"
LOCAL_B = "ollama:llama3.3-70b"
SHA1, SHA2 = "1" * 40, "2" * 40
D1, D2 = "a" * 64, "b" * 64
V2 = AP.ApproverPolicy("v2", "test", "x" * 64)
V1 = AP.V1


def pinned(tmp_path, source: Path, *, pin_sha: str | None = None):
    c = tmp_path / "constitution.md"
    c.write_bytes(source.read_bytes())
    p = tmp_path / "pin" / "constitution.sha256"
    p.parent.mkdir()
    p.write_text((pin_sha or hashlib.sha256(c.read_bytes()).hexdigest()) + "\n", encoding="ascii")
    return c, p


# ------------------------------------------------------------------ policy selection from the pinned text

def test_live_v1_file_selects_v1_even_when_pinned(tmp_path):
    c, p = pinned(tmp_path, V1_FILE)
    pol = AP.load_policy(str(c), str(p))
    assert pol.mode == "v1" and not pol.is_v2


def test_v2_draft_selects_v2_only_when_pinned_and_hash_matches(tmp_path):
    c, p = pinned(tmp_path, V2_FILE)
    assert AP.load_policy(str(c), str(p)).mode == "v2"
    # mismatching pin, missing pin, unreadable file: strictly v1
    (tmp_path / "x").mkdir()
    c2, p2 = pinned(tmp_path / "x", V2_FILE, pin_sha="0" * 64)
    assert AP.load_policy(str(c2), str(p2)).mode == "v1"
    assert AP.load_policy(str(c), str(tmp_path / "absent.sha256")).mode == "v1"
    assert AP.load_policy(str(tmp_path / "nope.md"), str(p)).mode == "v1"


def test_sentence_outside_rule_4_or_next_to_the_v1_pair_is_not_v2():
    assert V2_FILE.read_text(encoding="utf-8").count(AP.V2_SENTENCE) == 1
    elsewhere = "# C\n\n4. Never apply a change without both Claude and Codex.\n\nNote: " + AP.V2_SENTENCE + "\n"
    assert AP.policy_from_text(elsewhere).mode == "v1"
    mixed = f"4. Never apply a change without {AP.V2_SENTENCE} and the approval of both Claude and Codex.\n"
    assert AP.policy_from_text(mixed).mode == "v1"
    assert AP.policy_from_text("no rules here").mode == "v1"


def test_v2_draft_keeps_every_owner_only_clause_of_v1():
    v1, v2 = V1_FILE.read_text(encoding="utf-8"), V2_FILE.read_text(encoding="utf-8")
    block = lambda t: t.split("## Always the user's decision", 1)[1].split("## Release tiers", 1)[0]  # noqa: E731
    assert block(v1) == block(v2)
    for must in ("Promotion to the next level requires the configured number of successful cycles (default 25)",
                 "No agent may write that pin.", "L4 Long autonomy", "never the goals and rules below"):
        assert must in v1 and must in v2


# ------------------------------------------------------------------ verifier identity

@pytest.mark.parametrize("vid,family,external,valid", [
    ("claude", "anthropic", True, True), ("codex", "openai", True, True),
    (GEMMA, "google", True, True), (NEMO, "nvidia", True, True), (GLM, "zhipu", True, True),
    (LOCAL_A, "mistral", False, True), ("local:qwen3-coder", "qwen", False, True),
    ("mystery:gemma", "", False, False), ("openrouter:totally-unknown-model", "", True, False), ("gemma", "", False, False),
])
def test_verifier_identity(vid, family, external, valid):
    info = AP.verifier_info(vid)
    assert info.valid is valid and info.family == family
    if valid:
        assert info.external is external


# ------------------------------------------------------------------ evaluate()

def test_v2_two_families_with_one_external_suffice():
    ev = V2.evaluate([GEMMA, NEMO], "qwen-operator")
    assert ev.ok and set(ev.families) == {"google", "nvidia"}
    assert V2.evaluate([GEMMA, LOCAL_A], "qwen-operator").ok            # one external + one local of another family


def test_v2_one_family_twice_does_not_count_twice():
    ev = V2.evaluate([GEMMA, GEMINI], "qwen-operator")
    assert not ev.ok and any("already counted" in why for _id, why in ev.rejected)


def test_v2_needs_an_external_verifier():
    ev = V2.evaluate([LOCAL_A, LOCAL_B], "qwen-operator")
    assert not ev.ok and "external" in ev.reason


def test_v2_writer_is_never_its_own_verifier_nor_its_family():
    assert not V2.evaluate([GEMMA, NEMO], "nemotron").ok                 # writer family nvidia -> one verifier left
    assert not V2.evaluate([GEMMA, NEMO], GEMMA).ok                      # the writer itself
    assert not V2.evaluate(["claude", NEMO], "claude").ok
    assert not V2.evaluate([GEMMA, NEMO], "").ok                         # unknown author: independence unprovable
    assert not V2.evaluate([GEMMA, NEMO], "who-knows").ok


def test_v1_policy_still_needs_exactly_claude_and_codex():
    assert V1.evaluate(["claude", "codex"], "jev").ok
    assert not V1.evaluate([GEMMA, NEMO], "jev").ok
    assert not V1.evaluate(["claude"], "jev").ok


# ------------------------------------------------------------------ GoalStore guards

def mk() -> Goal:
    return Goal(goal_id="JEFF-0042", problem="Jeff leaks the underlying model name",
                desired_result="Jeff answers as Bossman", constraints=("prompts only",),
                acceptance_tests=("pytest:tests/test_identity.py::test_no_leak", "identity_redteam.leaks == 0"),
                budget=Budget(max_minutes=60, max_agent_turns=5, max_cost_usd=0.0), risk_tier="prompts_models",
                target_metric="identity_redteam.leaks", protected_metrics=("task_success", "latency_ms"))


def rv(reviewer, verdict="APPROVE", sha=SHA1, diff=D1):
    return Review(goal_id="JEFF-0042", reviewer=reviewer, sha=sha, diff_sha256=diff, verdict=verdict, notes="n")


def to_review(store, author="qwen-operator"):
    store.create(mk())
    store.transition("JEFF-0042", "PLANNED", {"plan": "p"})
    store.transition("JEFF-0042", "BUILDING", {})
    store.set_candidate("JEFF-0042", SHA1, D1, author=author, branch="auto/jeff-0042", base_sha=SHA2,
                        target_branch="release/bossman-owner")
    store.transition("JEFF-0042", "TESTING", {})
    store.record_tests("JEFF-0042", SHA1, D1, True, {"passed": 12})
    return store.transition("JEFF-0042", "CLAUDE_REVIEW", {})


def test_v2_store_walks_to_staging_with_two_independent_verifiers(tmp_path):
    store = GoalStore(tmp_path, policy_provider=lambda: V2)
    to_review(store)
    store.record_review(rv(GEMMA))
    store.transition("JEFF-0042", "VERIFY_2", {})                         # alias of CODEX_REVIEW
    assert store.get("JEFF-0042")["state"] == "CODEX_REVIEW"
    with pytest.raises(GuardError):                                      # one verifier is not enough
        store.transition("JEFF-0042", "STAGING", {})
    store.record_review(rv(NEMO))
    store.transition("JEFF-0042", "STAGING", {})
    store.record_staging("JEFF-0042", {"sha": SHA1, "passed": True})
    rec = store.transition("JEFF-0042", "USER_APPROVAL", {})             # the owner still presses Release
    assert rec["state"] == "USER_APPROVAL" and GoalStore.approvals_valid(rec, V2)
    assert not GoalStore.approvals_valid(rec)                            # the v1 reading is unchanged: no claude+codex


def test_v2_one_family_twice_cannot_reach_staging(tmp_path):
    store = GoalStore(tmp_path, policy_provider=lambda: V2)
    to_review(store)
    store.record_review(rv(GEMMA))
    store.transition("JEFF-0042", "CODEX_REVIEW", {})
    store.record_review(rv(GEMINI))
    with pytest.raises(GuardError, match="independent verifier"):
        store.transition("JEFF-0042", "STAGING", {})


def test_v2_writer_family_cannot_verify(tmp_path):
    store = GoalStore(tmp_path, policy_provider=lambda: V2)
    to_review(store, author="openrouter:google/gemma-3-27b-it:free")
    with pytest.raises(GuardError, match="not independent"):
        store.record_review(rv(GEMMA))
    with pytest.raises(GuardError, match="not independent"):
        store.record_review(rv(GEMINI))                                   # same family as the writer
    store.record_review(rv(NEMO))                                         # a different family is fine


def test_v2_new_sha_or_diff_invalidates_approvals(tmp_path):
    store = GoalStore(tmp_path, policy_provider=lambda: V2)
    to_review(store)
    store.record_review(rv(GEMMA))
    store.transition("JEFF-0042", "CODEX_REVIEW", {})
    store.record_review(rv(NEMO))
    assert GoalStore.approvals_valid(store.get("JEFF-0042"), V2)
    rec = store.set_candidate("JEFF-0042", SHA2, D2, author="qwen-operator", branch="auto/jeff-0042", base_sha=SHA1,
                              target_branch="release/bossman-owner")      # any new writer turn
    assert rec["approvals"] == {} and rec["state"] == "BLOCKED"
    assert not GoalStore.approvals_valid(rec, V2)


def test_v1_pinned_keeps_claude_codex_and_refuses_verifier_ids(tmp_path):
    store = GoalStore(tmp_path, policy_provider=lambda: V1)
    to_review(store, author="jev")
    with pytest.raises(GuardError, match="not an accepted reviewer"):
        store.record_review(rv(GEMMA))
    store.record_review(rv("claude"))
    store.transition("JEFF-0042", "CODEX_REVIEW", {})
    with pytest.raises(GuardError):
        store.transition("JEFF-0042", "STAGING", {})
    store.record_review(rv("codex"))
    store.transition("JEFF-0042", "STAGING", {})


def test_unpinned_default_provider_is_v1_and_unreadable_provider_fails_closed(tmp_path):
    def boom():
        raise RuntimeError("provider broke")
    assert GoalStore(tmp_path, policy_provider=boom).policy().mode == "v1"
    assert GoalStore(tmp_path / "d", policy_provider=lambda: AP.load_policy(str(V2_FILE), str(tmp_path / "no-pin"))
                     ).policy().mode == "v1"


# ------------------------------------------------------------------ ReviewGate

C = Candidate(sha="a" * 40, diff_sha256="d" * 64, evidence_sha256="e" * 64, author="qwen-operator",
              author_session="w1")


def gate(policy):
    g = ReviewGate("G", policy=policy)
    g.set_candidate(C)
    return g


def rg(reviewer, verdict="APPROVE"):
    return Review(goal_id="G", reviewer=reviewer, sha=C.sha, diff_sha256=C.diff_sha256, verdict=verdict, notes="n")


def test_review_gate_v2_status():
    g = gate(V2)
    g.submit(rg(GEMMA), evidence_sha256=C.evidence_sha256, session_id="s1")
    assert g.status() == "PENDING"
    g.submit(rg(GEMINI), evidence_sha256=C.evidence_sha256, session_id="s2")
    assert g.status() == "PENDING"                                        # same family
    g.submit(rg(NEMO), evidence_sha256=C.evidence_sha256, session_id="s3")
    assert g.status() == "APPROVED"
    # a v1 gate over the same candidate never reads verifier ids as claude/codex
    g1 = gate(V1)
    g1.submit(rg("claude"), evidence_sha256=C.evidence_sha256, session_id="s1")
    assert g1.status() == "PENDING"
    g1.submit(rg("codex"), evidence_sha256=C.evidence_sha256, session_id="s2")
    assert g1.status() == "APPROVED"


def test_parse_review_accepts_verifier_ids_only_under_v2():
    text = json.dumps({"verdict": "APPROVE", "notes": "ok", "sha": C.sha, "diff_sha256": C.diff_sha256})
    assert parse_review(text, goal_id="G", reviewer=GEMMA, candidate=C)[0] is None
    assert parse_review(text, goal_id="G", reviewer=GEMMA, candidate=C, policy=V1)[0] is None
    assert parse_review(text, goal_id="G", reviewer=GEMMA, candidate=C, policy=V2)[0] is not None
    assert parse_review(text, goal_id="G", reviewer="evil:unknown", candidate=C, policy=V2)[0] is None


# ------------------------------------------------------------------ whole cycle with model verifiers

@pytest.fixture
def world(tmp_path, monkeypatch):
    install_fake_clis(monkeypatch)
    data = tmp_path / "data"
    root = data / "autonomy"
    journal = Journal(root)
    return {"repo": make_repo(tmp_path), "journal": journal, "root": root, "data": data,
            "goals": GoalStore(root, journal, policy_provider=lambda: V2),
            "lease": EngineeringLease(root, journal=journal), "executor": FakeExecutor(),
            "clis": ScriptedCLIs(writer=_writer), "tmp": tmp_path, "staging_results": {}}


def _writer(call):
    block = call.prompt.split("You may change ONLY these paths:\n", 1)[1]
    first = block.splitlines()[0][2:]
    path = first[:-3] + "/README.md" if first.endswith("/**") else first
    return Act(text=done(f"edited {path}"), edits={path: f"changed by {call.agent} {goal_of(call.prompt)}\n"})


def _probe(goal):
    return {goal.target_metric: 1.0, "latency_ms": 100.0}


def make_cycle(w, chat, *, verifiers, policy=V2, **cfg):
    seen = {"n": 0}

    def probe(goal):
        seen["n"] += 1
        return {goal.target_metric: 5.0 if seen["n"] == 1 else 1.0, "latency_ms": 100.0}
    config = CY.CycleConfig(verifiers=tuple(verifiers), **cfg)
    deps = CY.CycleDeps(goals=w["goals"], lease=w["lease"],
                        hands=make_broker(w["root"], w["journal"], w["executor"], level="L2"), journal=w["journal"],
                        staging=make_staging(w["root"], w["journal"], w["tmp"] / "owner", config.staging_checks,
                                             w["staging_results"]),
                        gate_decide=metrics_gate.decide, constitution_verify=lambda: OK_CONSTITUTION,
                        metrics_probe=probe, source_repo=w["repo"], work_root=w["root"] / "cycles",
                        runner=w["clis"], savings=SavingsLedger(w["root"]), approver_policy=lambda: policy,
                        verifier_chat=chat)
    return CY.AutonomyCycle(deps, config)


def verdict_chat(calls, *, verdict="APPROVE"):
    def chat(verifier, prompt):
        calls.append(verifier)
        sha = re.search(r"Candidate sha: (\S+)", prompt).group(1)
        diff = re.search(r"diff_sha256: (\S+)", prompt).group(1)
        return {"text": json.dumps({"verdict": verdict, "notes": "reviewed", "sha": sha, "diff_sha256": diff}),
                "tokens_in": 10, "tokens_out": 5}
    return chat


async def test_cycle_v2_runs_two_independent_model_verifiers_to_the_owner_gate(world):
    calls: list[str] = []
    cy = make_cycle(world, verdict_chat(calls), verifiers=[GEMMA, GEMINI, NEMO], seed=1)
    out = await cy.run_goal(make_goal("G-V2"))
    assert out.state == "USER_APPROVAL", out.reason
    assert calls == [GEMMA, NEMO]                                         # GEMINI skipped: same family as GEMMA
    reviews = of(world["journal"], "review_recorded", "G-V2")
    assert [r["reviewer"] for r in reviews] == [GEMMA, NEMO]
    rec = world["goals"].get("G-V2")
    assert GoalStore.approvals_valid(rec, V2) and not GoalStore.approvals_valid(rec)
    assert "apply_candidate" not in [e["request"]["action"] for e in of(world["journal"], "hand.request", "G-V2")]


async def test_cycle_v2_blocks_without_two_eligible_families(world):
    calls: list[str] = []
    cy = make_cycle(world, verdict_chat(calls), verifiers=[GEMMA, GEMINI], seed=1)
    out = await cy.run_goal(make_goal("G-V2B"))
    assert out.state == "BLOCKED" and "no eligible independent verifier" in out.reason
    assert calls == [GEMMA]


async def test_cycle_v2_verifier_rejection_blocks(world):
    calls: list[str] = []
    cy = make_cycle(world, verdict_chat(calls, verdict="REJECT"), verifiers=[GEMMA, NEMO], seed=1)
    out = await cy.run_goal(make_goal("G-V2C"))
    assert out.state == "BLOCKED" and calls == [GEMMA]


async def test_cycle_v2_without_transport_blocks(world):
    cy = make_cycle(world, None, verifiers=[GEMMA, NEMO], seed=1)
    out = await cy.run_goal(make_goal("G-V2D"))
    assert out.state == "BLOCKED" and "unavailable" in out.reason


# ------------------------------------------------------------------ the owner adoption script

ADOPT = REPO / "tools" / "adopt_constitution_v2.ps1"


def test_adopt_script_is_owner_only_and_never_writes_the_pin():
    import shutil
    import subprocess
    text = ADOPT.read_text(encoding="utf-8")
    for must in ("IsInputRedirected", "IsOutputRedirected", "CLAUDECODE", "ADOPT-V2", "constitution', 'pin'",
                 "Copy-Item -LiteralPath $target -Destination $backup"):
        assert must in text, must
    # the pin file is only ever printed; the script has no write to it
    assert not re.search(r"(Set-Content|Out-File|WriteAllText|Add-Content)[^\n]*\$pinPath", text)
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if exe:
        cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
               f"'{ADOPT}',[ref]$t,[ref]$e); exit $e.Count")
        assert subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", cmd], timeout=60).returncode == 0
        r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-File", str(ADOPT)], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=60, text=True)
        assert r.returncode == 5 and "REFUSED" in (r.stdout + r.stderr)      # redirected IO: stops before any change
