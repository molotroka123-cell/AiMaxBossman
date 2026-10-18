"""Self-improvement superstructure: redaction, observations, provenance, loop, limits, approval, API.

No network, no model, no paid call, no live data dir: every executor/verifier below is a pure-Python fake.
Fake secrets are assembled at runtime so this file itself carries no secret-shaped literal.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from bcc.self_improve import BLOCKED, FAIL, NOT_RUN, PASS, Limits
from bcc.self_improve import observation as obs
from bcc.self_improve.apply import apply_draft, rollback
from bcc.self_improve.approval import ApprovalRequired
from bcc.self_improve.botmap import build_botmap, render_markdown
from bcc.self_improve.delegation import (DelegationChain, DelegationStop, check_agent_spec,
                                         refuse_self_modification)
from bcc.self_improve.drafts import DraftStore
from bcc.self_improve.limits import LimitViolation, assert_limits_unchanged, guard_write_target, long_run_cap_usd
from bcc.self_improve.loop import (Budget, Candidate, ImprovementLoop, InvalidTransition, TaskSet, detect_recurring)
from bcc.self_improve.owner import issue_token
from bcc.self_improve.redact import RedactionError, assert_clean, findings, sanitize
from bcc.self_improve.sandbox import Sandbox
from bcc.self_improve.store import Store

PKG = Path(__file__).resolve().parents[1] / "bcc"

# ----------------------------------------------------------------------------- seeded fakes
FAKE_SK = "sk-" + "A1b2C3d4" * 4
FAKE_TG = "123456789:" + "AAbbCCddEE" * 4
FAKE_EMAIL = "ivan.petrov" + "@" + "example.org"
FAKE_PHONE = "+7 (916) 123-45-67"
FAKE_CHAT = "chat_id: 987654321"
FAKE_HANDLE = "@" + "friend_of_owner"
FAKE_GH = "ghp_" + "Zz9Yy8Xx7W" * 3


def seeded() -> dict:
    return {"task_id": "t1", "error_class": "timeout",
            "summary": f"fail for {FAKE_EMAIL} call {FAKE_PHONE} {FAKE_CHAT} key {FAKE_SK} bot {FAKE_TG} "
                       f"{FAKE_HANDLE} {FAKE_GH} https://t.me/privategroup Bearer abcdefghijklmnop1234",
            "message": "private chat text that must vanish", "chat_id": 987654321, "username": "ivan",
            "api_key": "whatever", "tokens": 120, "evidence_sha256": "a" * 64}


# ============================================================================== redaction
def test_sanitize_drops_and_scrubs_all_seeded_secrets():
    clean = sanitize(seeded())
    blob = json.dumps(clean, ensure_ascii=False)
    for needle in (FAKE_SK, FAKE_TG, FAKE_EMAIL, "123-45-67", "987654321", FAKE_HANDLE, FAKE_GH, "privategroup",
                   "abcdefghijklmnop1234", "private chat text"):
        assert needle not in blob, needle
    for dropped in ("message", "chat_id", "username", "api_key"):
        assert dropped not in clean
    assert clean["tokens"] == 120                      # numeric budget field is not a secret
    assert clean["evidence_sha256"] == "a" * 64       # evidence digests survive
    assert_clean(clean)


def test_assert_clean_refuses_unsanitized_data():
    with pytest.raises(RedactionError):
        assert_clean(seeded())
    assert findings({"x": FAKE_EMAIL}) == ["email"]


def test_store_never_writes_secrets_to_journal(tmp_path):
    st = Store(tmp_path)
    st.record("hypothesis", {"id": "H1", "state": "DETECTED", "note": seeded()["summary"], "message": "private"})
    raw = (tmp_path / "journal" / "journal.jsonl").read_text(encoding="utf-8")
    for needle in (FAKE_SK, FAKE_TG, FAKE_EMAIL, "private", "987654321"):
        assert needle not in raw


# ============================================================================== observation schema
def good_obs(**kw) -> dict:
    base = {"task_id": "task-1", "agent_id": "7", "source_sha": "67a2a0b5", "instruction_hash": "b" * 64,
            "instruction_version": "agent-7@bbbbbbbbbbbb", "expected_criterion": "tests pass", "outcome": FAIL,
            "error_class": "timeout", "duration_s": 3.2, "cost_usd": 0.0, "evidence_source": "api#1",
            "evidence_sha256": "c" * 64}
    base.update(kw)
    return base


def test_observation_validator():
    assert obs.validate_observation(good_obs()) == []
    assert obs.validate_observation(good_obs(duration_s="N/A", source_sha="N/A")) == []
    bad = [good_obs(outcome="completed"), good_obs(source_sha="zzz"), good_obs(instruction_hash="short"),
           good_obs(duration_s=-1), good_obs(error_class="none"), good_obs(outcome=PASS, expected_criterion="N/A"),
           good_obs(evidence_source=f"mail {FAKE_EMAIL}")]
    for rec in bad:
        assert obs.validate_observation(rec), rec
    rec = good_obs()
    del rec["evidence_sha256"]
    assert "missing field: evidence_sha256" in obs.validate_observation(rec)


def test_store_rejects_invalid_observation(tmp_path):
    with pytest.raises(ValueError):
        Store(tmp_path).record_observation(good_obs(outcome="great"))


def test_observations_from_api_hash_instruction_and_drop_text():
    agents = [{"id": 5, "system_prompt": "SECRET-PROMPT-TEXT " + FAKE_EMAIL}]
    tasks = [{"id": 9, "agent_id": 5, "status": "failed", "title": "private " + FAKE_PHONE, "prompt": "p",
              "last_run": {"id": 1, "status": "failed", "error": "action_contract/no_verified_action",
                           "result": "private result", "started_at": "2026-10-02T14:24:20", "finished_at":
                           "2026-10-02T14:24:24", "cost_usd": 0.0}},
             {"id": 10, "agent_id": 5, "status": "completed", "last_run": {"id": 2, "status": "completed"}},
             {"id": 11, "agent_id": 5, "status": "running", "last_run": {}}]
    rows = obs.observations_from_api(tasks, agents, source_sha="67a2a0b5")
    assert [r["task_id"] for r in rows] == ["task-9", "task-10"]
    assert rows[0]["outcome"] == FAIL and rows[0]["error_class"] == "action_contract"
    assert rows[0]["duration_s"] == 4.0
    assert rows[1]["outcome"] == "N/A"                  # engine "completed" is not PASS
    blob = json.dumps(rows, ensure_ascii=False)
    assert "SECRET-PROMPT-TEXT" not in blob and FAKE_EMAIL not in blob and "private" not in blob
    assert rows[0]["instruction_hash"] == obs.sha256_text(agents[0]["system_prompt"])


def test_error_classes_are_stable():
    assert obs.classify_error("Timed out after 30s") == "timeout"
    assert obs.classify_error("provider_down: нет связи") == "provider_down"
    assert obs.classify_error(None) == "unknown"


# ============================================================================== provenance
def test_journal_tamper_is_detected_and_shown(tmp_path):
    st = Store(tmp_path)
    for i in range(3):
        st.record_observation(good_obs(task_id=f"task-{i}"))
    assert st.verify().ok and st.state()["journal"]["ok"] is True
    path = tmp_path / "journal" / "journal.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("timeout", "parse", 1), encoding="utf-8")
    assert not st.verify().ok
    assert st.state()["journal"]["ok"] is False


def test_journal_truncation_is_detected(tmp_path):
    st = Store(tmp_path)
    for i in range(3):
        st.record_observation(good_obs(task_id=f"task-{i}"))
    path = tmp_path / "journal" / "journal.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines(True)
    path.write_text("".join(lines[:-1]), encoding="utf-8")
    assert not st.verify().ok


# ============================================================================== limits
def test_limits_defaults_and_cost_cap_zero():
    lim = Limits()
    assert (lim.max_delegation_depth, lim.cost_cap_usd) == (2, 0.0)
    assert Limits.make(requested_cost_cap_usd=50.0).cost_cap_usd == 0.0          # no owner cap exists -> still 0
    assert Limits.make(requested_cost_cap_usd=50.0, long_run_cap_usd=2.0).cost_cap_usd == 2.0
    assert Limits.make(requested_cost_cap_usd=1.0, long_run_cap_usd=2.0).cost_cap_usd == 1.0
    assert Limits.make(max_delegation_depth=9).max_delegation_depth == 2        # can be lowered, never raised


def test_limits_are_frozen_and_digest_guarded():
    lim = Limits()
    with pytest.raises(Exception):
        lim.cost_cap_usd = 5.0  # type: ignore[misc]
    digest = lim.digest()
    assert_limits_unchanged(lim, digest)
    with pytest.raises(LimitViolation):
        assert_limits_unchanged(Limits(max_tokens=1), digest)


def test_long_run_cap_is_read_only(tmp_path):
    (tmp_path / "autonomy").mkdir()
    f = tmp_path / "autonomy" / "long_run.json"
    f.write_text(json.dumps({"enabled": True, "caps_eur_per_day": {"glm": 2.0}}), encoding="utf-8")
    before = f.read_bytes()
    assert long_run_cap_usd(tmp_path) == 2.0
    assert f.read_bytes() == before
    assert long_run_cap_usd(tmp_path / "nowhere") is None


@pytest.mark.parametrize("p", ["x/autonomy/long_run.json", "docs/constitution/BOSSMAN_CONSTITUTION.md",
                               "a/constitution.sha256", "c/.claude/settings.json", "c/.claude/settings.local.json",
                               "command-center/bcc/permissions.py", "command-center/bcc/provider_governance.py",
                               "command-center/bcc/self_improve/limits.py", "x/provider-keys.env", "STOP"])
def test_protected_paths_are_refused(p):
    with pytest.raises(LimitViolation):
        guard_write_target(p)


def test_budget_caps(tmp_path):
    t = [0.0]
    b = Budget(Limits(max_wall_seconds=10, max_tokens=100), lambda: t[0])
    b.charge(50, 0.0)
    with pytest.raises(LimitViolation, match="cost cap"):
        b.charge(1, 0.01)                    # default cap is 0 USD
    b2 = Budget(Limits(max_tokens=100), lambda: t[0])
    with pytest.raises(LimitViolation, match="token cap"):
        b2.charge(101, 0.0)
    b3 = Budget(Limits(max_wall_seconds=10), lambda: t[0])
    t[0] = 11.0
    with pytest.raises(LimitViolation, match="wall-clock"):
        b3.charge(1, 0.0)


# ============================================================================== sandbox isolation
def test_sandbox_cannot_write_outside_or_into_live_data(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    sb = Sandbox(tmp_path / "root", "exp1", Limits(), live_data_dir=live)
    sb.write_text("ok/a.txt", "hi")
    assert sb.read_text("ok/a.txt") == "hi"
    for bad in ("../escape.txt", "a/../../escape.txt", str(tmp_path / "abs.txt"), "/etc/x", "C:/Windows/x",
                "settings.json", "sub/long_run.json"):
        with pytest.raises(LimitViolation):
            sb.write_text(bad, "x")
    assert not (tmp_path / "escape.txt").exists()
    with pytest.raises(LimitViolation):                     # the sandbox itself may not live in the live dir
        Sandbox(live, "exp2", Limits(), live_data_dir=live)
    with pytest.raises(LimitViolation):
        Sandbox(tmp_path / "root", "../x", Limits())


def test_sandbox_symlink_escape_refused(tmp_path):
    sb = Sandbox(tmp_path / "root", "exp1", Limits())
    outside = tmp_path / "outside"
    outside.mkdir()
    link = sb.dir / "lnk"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    with pytest.raises(LimitViolation):
        sb.write_text("lnk/x.txt", "x")


def test_sandbox_byte_cap_and_process_timeout_and_env(tmp_path, monkeypatch):
    sb = Sandbox(tmp_path / "root", "exp1", Limits(max_sandbox_bytes=10, max_wall_seconds=5))
    with pytest.raises(LimitViolation):
        sb.write_text("big.txt", "x" * 11)
    monkeypatch.setenv("FAKE_SECRET_TOKEN", "do-not-leak")
    sb2 = Sandbox(tmp_path / "root", "exp2", Limits(max_wall_seconds=1.5))
    out = sb2.run([sys.executable, "-I", "-c", "import os;print(os.getcwd());print(os.environ.get('FAKE_SECRET_TOKEN'))"])
    assert out["returncode"] == 0 and "None" in out["stdout"] and "do-not-leak" not in out["stdout"]
    assert Path(out["stdout"].splitlines()[0]).resolve() == sb2.dir
    slow = sb2.run([sys.executable, "-I", "-c", "import time;time.sleep(30)"], timeout=1)
    assert slow["timed_out"] is True and slow["duration_s"] < 10
    with pytest.raises(LimitViolation):
        sb2.run("echo hi")                                  # no shell strings


# ============================================================================== delegation
def test_delegation_depth_and_cycle_stop_with_logged_reason(tmp_path):
    st = Store(tmp_path)
    ch = DelegationChain(Limits(), st)
    ch.enter("a").enter("b")
    with pytest.raises(DelegationStop, match="depth 3"):
        ch.enter("c")
    ch2 = DelegationChain(Limits(max_delegation_depth=2), st)
    ch2.enter("a")
    with pytest.raises(DelegationStop, match="cycle"):
        ch2.enter("a")
    reasons = [e["payload"]["reason"] for e in st.events("delegation.stop")]
    assert len(reasons) == 2 and "cycle" in reasons[1]


def test_agent_cannot_create_broader_agent():
    creator = {"tools": ["read_file"], "permissions": {"filesystem.read": True}, "max_steps": 4, "budget_usd": 0.0}
    assert check_agent_spec(creator, {"tools": ["read_file"], "permissions": {"filesystem.read": True}, "max_steps": 2}) == []
    assert check_agent_spec(creator, {"tools": ["read_file", "terminal.run"]})
    assert check_agent_spec(creator, {"permissions": {"computer.act": True}})
    assert check_agent_spec(creator, {"max_steps": 40})
    assert check_agent_spec(creator, {"budget_usd": 1.0})


def test_agent_cannot_modify_own_permissions_or_limits(tmp_path):
    st = Store(tmp_path)
    with pytest.raises(LimitViolation, match="refused"):
        refuse_self_modification(st, "agent:7", "agent:7", "permissions")
    with pytest.raises(LimitViolation):
        guard_write_target("command-center/bcc/self_improve/limits.py")
    with pytest.raises(LimitViolation):
        DraftStore(tmp_path, Limits()).save("d1", kind="instruction", target_rel="autonomy/long_run.json",
                                            base_text=None, proposed_text="{}", author_role="r", author_model="m",
                                            justification="raise my cap")
    assert any(e["payload"].get("what") == "permissions" for e in st.events("refusal"))


# ============================================================================== loop
class Prop:
    role, model = "proposer", "free-model-a"

    def __init__(self):
        self.seen: list = []

    def propose(self, cand):
        self.seen.append(cand)
        return {"kind": "instruction", "target_rel": "agents/7.txt", "base_text": "old", "proposed_text": "new better",
                "justification": f"{cand.count} repeats of {cand.error_class}"}


class Ver:
    role, model = "verifier", "free-model-b"

    def judge(self, task, output):
        return output == task["expected"]


class Exe:
    role, model = "worker", "free-model-c"

    def __init__(self, cost=0.0, regress=()):
        self.cost, self.regress, self.calls = cost, set(regress), []

    def run(self, instruction, task, sandbox):
        assert "expected" not in task                       # the executor never sees the answers
        self.calls.append((instruction, task["id"]))
        good = (instruction == "new better") and task["id"] not in self.regress
        if instruction == "old":
            good = task["id"] in ("h0", "h1")                # baseline solves two of six
        return {"output": f"ans-{task['id']}" if good else "wrong", "tokens": 10, "cost_usd": self.cost}


def heldout(n=6):
    return TaskSet.freeze([{"id": f"h{i}", "input": f"q{i}", "expected": f"ans-h{i}"} for i in range(n)])


def fails(n=3, agent="7", cls="timeout"):
    return [good_obs(task_id=f"task-{i}", agent_id=agent, error_class=cls) for i in range(n)]


def make_loop(tmp_path, **kw):
    st = Store(tmp_path / "si")
    lp = ImprovementLoop(st, tmp_path / "si", Limits(**kw), live_data_dir=tmp_path / "live")
    return st, lp


def test_detect_requires_recurrence_threshold(tmp_path):
    assert detect_recurring(fails(2), Limits()) == []
    c = detect_recurring(fails(3) + [good_obs(agent_id="9", task_id="x")], Limits())
    assert len(c) == 1 and c[0].count == 3 and c[0].agent_id == "7"


def test_full_loop_pass_with_independent_verifier(tmp_path):
    st, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    prop = Prop()
    draft = lp.propose(cand.hypothesis_id, prop)
    assert draft and prop.seen == [cand]                    # proposer saw only the sanitized candidate
    assert not hasattr(cand, "tasks") and all(e.startswith("task-") for e in cand.example_task_ids)
    ts = heldout()
    rep = lp.run_experiment(cand.hypothesis_id, executor=Exe(), verifier=Ver(), taskset=ts, baseline_text="old")
    assert rep["status"] == PASS, rep
    assert rep["baseline"]["pass_rate"] < rep["candidate"]["pass_rate"] and rep["regressions"] == []
    assert rep["cost_usd"] == 0.0 and rep["verifier"]["model"] != rep["proposer"]["model"]
    assert lp.state_of(cand.hypothesis_id) == "AWAITING_OWNER"
    s = st.state()
    assert s["owner_decision_required"] and s["gain_claim"].startswith("PASS")
    # the draft was only written as data: no target file exists anywhere
    assert not (tmp_path / "agents").exists()
    assert (tmp_path / "si" / "drafts" / f"D-{cand.hypothesis_id}" / "v1" / "change.diff").read_text(encoding="utf-8")
    assert not (tmp_path / "si" / "experiments" / f"exp-{cand.hypothesis_id}").exists()   # sandbox cleaned
    assert st.verify().ok


def test_regression_makes_it_fail_not_pass(tmp_path):
    st, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    lp.propose(cand.hypothesis_id, Prop())
    rep = lp.run_experiment(cand.hypothesis_id, executor=Exe(regress=["h0"]), verifier=Ver(), taskset=heldout(),
                            baseline_text="old")
    assert rep["status"] == FAIL and rep["regressions"] == ["h0"]
    assert "улучшение не заявляется" in rep["claim"]
    assert lp.state_of(cand.hypothesis_id) == "REJECTED"
    assert st.state()["gain_claim"].startswith("NOT_RUN")


@pytest.mark.parametrize("verifier,why", [(None, "no independent verifier"), ("same_role", "not independent"),
                                          ("same_model", "not independent")])
def test_self_review_is_not_evidence(tmp_path, verifier, why):
    st, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    lp.propose(cand.hypothesis_id, Prop())

    class SameRole(Ver):
        role = "proposer"

    class SameModel(Ver):
        model = "free-model-a"

    v = {None: None, "same_role": SameRole(), "same_model": SameModel()}[verifier]
    exe = Exe()
    rep = lp.run_experiment(cand.hypothesis_id, executor=exe, verifier=v, taskset=heldout(), baseline_text="old")
    assert rep["status"] == NOT_RUN and why in rep["reason"]
    assert exe.calls == [] and lp.state_of(cand.hypothesis_id) == "NOT_RUN"


def test_too_small_heldout_set_is_not_run(tmp_path):
    _, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    lp.propose(cand.hypothesis_id, Prop())
    rep = lp.run_experiment(cand.hypothesis_id, executor=Exe(), verifier=Ver(), taskset=heldout(2), baseline_text="old")
    assert rep["status"] == NOT_RUN and "too small" in rep["reason"]


def test_heldout_leakage_and_tampered_hash_are_blocked(tmp_path):
    _, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    lp.propose(cand.hypothesis_id, Prop())
    leaky = TaskSet.freeze([{"id": t, "input": "q", "expected": "a"} for t in cand.example_task_ids] +
                           [{"id": f"h{i}", "input": "q", "expected": "a"} for i in range(4)])
    assert lp.run_experiment(cand.hypothesis_id, executor=Exe(), verifier=Ver(), taskset=leaky,
                             baseline_text="old")["status"] == BLOCKED
    st2, lp2 = make_loop(tmp_path / "second")
    c2 = lp2.detect(fails())[0]
    lp2.propose(c2.hypothesis_id, Prop())
    ts = heldout()
    tampered = TaskSet(tuple(dict(t, expected="x") if t["id"] == "h0" else t for t in ts.tasks), ts.set_hash)
    rep = lp2.run_experiment(c2.hypothesis_id, executor=Exe(), verifier=Ver(), taskset=tampered, baseline_text="old")
    assert rep["status"] == BLOCKED and "hash" in rep["reason"]


def test_paid_model_is_blocked_by_zero_cost_cap(tmp_path):
    _, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    lp.propose(cand.hypothesis_id, Prop())
    rep = lp.run_experiment(cand.hypothesis_id, executor=Exe(cost=0.01), verifier=Ver(), taskset=heldout(),
                            baseline_text="old")
    assert rep["status"] == BLOCKED and "cost cap" in rep["reason"]
    assert lp.state_of(cand.hypothesis_id) == "BLOCKED"


def test_token_cap_blocks(tmp_path):
    _, lp = make_loop(tmp_path, max_tokens=25)
    cand = lp.detect(fails())[0]
    lp.propose(cand.hypothesis_id, Prop())
    rep = lp.run_experiment(cand.hypothesis_id, executor=Exe(), verifier=Ver(), taskset=heldout(), baseline_text="old")
    assert rep["status"] == BLOCKED and "token cap" in rep["reason"]


def test_delegation_cycle_inside_experiment_is_blocked_and_logged(tmp_path):
    st, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    lp.propose(cand.hypothesis_id, Prop())
    ch = DelegationChain(lp.limits, st, chain=["executor:worker"])
    rep = lp.run_experiment(cand.hypothesis_id, executor=Exe(), verifier=Ver(), taskset=heldout(),
                            baseline_text="old", chain=ch)
    assert rep["status"] == BLOCKED and "delegation stopped" in rep["reason"]
    assert any("cycle" in e["payload"]["reason"] for e in st.events("delegation.stop"))


def test_baseline_is_frozen_and_reused(tmp_path):
    st, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    lp.propose(cand.hypothesis_id, Prop())
    exe = Exe()
    ts = heldout()
    lp.run_experiment(cand.hypothesis_id, executor=exe, verifier=Ver(), taskset=ts, baseline_text="old")
    assert len(st.events("baseline.frozen")) == 1
    n_base = sum(1 for c in exe.calls if c[0] == "old")
    assert n_base == len(ts.tasks)
    # a second hypothesis on the same frozen set reuses the baseline: no new "old" runs
    cand2 = detect_recurring(fails(agent="8"), lp.limits)[0]
    lp.detect(fails(agent="8"))
    lp.propose(cand2.hypothesis_id, Prop())
    exe2 = Exe()
    lp.run_experiment(cand2.hypothesis_id, executor=exe2, verifier=Ver(), taskset=ts, baseline_text="old")
    assert not [c for c in exe2.calls if c[0] == "old"]


def test_state_machine_rejects_illegal_transitions(tmp_path):
    _, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    with pytest.raises(InvalidTransition):
        lp._go(cand.hypothesis_id, "AWAITING_OWNER")        # DETECTED -> AWAITING_OWNER skips every check
    with pytest.raises(InvalidTransition):
        lp._go("H-unknown", "PROPOSED")


def test_blocked_draft_target_never_reaches_experiment(tmp_path):
    _, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]

    class Evil(Prop):
        def propose(self, cand):
            return {**super().propose(cand), "target_rel": "autonomy/long_run.json"}

    assert lp.propose(cand.hypothesis_id, Evil()) is None
    assert lp.state_of(cand.hypothesis_id) == "BLOCKED"


def test_proposer_cannot_see_heldout_tasks(tmp_path):
    _, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    prop = Prop()
    lp.propose(cand.hypothesis_id, prop)
    ts = heldout()
    blob = json.dumps(prop.seen[0].__dict__, default=str)
    assert not any(t["id"] in blob or t["expected"] in blob for t in ts.tasks)
    assert isinstance(prop.seen[0], Candidate)


# ============================================================================== approval / apply / rollback
def make_pass(tmp_path):
    st, lp = make_loop(tmp_path)
    cand = lp.detect(fails())[0]
    lp.propose(cand.hypothesis_id, Prop())
    assert lp.run_experiment(cand.hypothesis_id, executor=Exe(), verifier=Ver(), taskset=heldout(),
                             baseline_text="old")["status"] == PASS
    return st, cand.hypothesis_id, f"D-{cand.hypothesis_id}", tmp_path / "si"


def test_apply_refuses_without_owner_token_and_touches_nothing(tmp_path):
    st, hid, did, root = make_pass(tmp_path)
    apply_root = tmp_path / "shared"
    (apply_root / "agents").mkdir(parents=True)
    target = apply_root / "agents" / "7.txt"
    target.write_text("old", encoding="utf-8")
    for tok in (None, "", "guess", "x" * 40):
        with pytest.raises(ApprovalRequired):
            apply_draft(root, hypothesis_id=hid, draft_id=did, token=tok, apply_root=apply_root, actor="agent:7")
    assert target.read_text(encoding="utf-8") == "old"
    assert len(st.events("refusal")) >= 4
    with pytest.raises(LimitViolation, match="no apply_root"):
        apply_draft(root, hypothesis_id=hid, draft_id=did, token="t", apply_root=None)


def test_apply_with_token_is_reversible_and_journaled(tmp_path):
    st, hid, did, root = make_pass(tmp_path)
    apply_root = tmp_path / "shared"
    (apply_root / "agents").mkdir(parents=True)
    target = apply_root / "agents" / "7.txt"
    target.write_text("old", encoding="utf-8")
    token = issue_token(root, hypothesis_id=hid, draft_id=did)
    assert token not in (root / "owner" / "tokens.json").read_text(encoding="utf-8")   # only the hash is stored
    entry = apply_draft(root, hypothesis_id=hid, draft_id=did, token=token, apply_root=apply_root, actor="owner-cli")
    assert target.read_text(encoding="utf-8") == "new better" and "rollback" in entry["rollback"]
    with pytest.raises(ApprovalRequired):                                              # single use
        apply_draft(root, hypothesis_id=hid, draft_id=did, token=token, apply_root=apply_root)
    back = rollback(root, apply_id=entry["apply_id"], apply_root=apply_root, actor="owner-cli")
    assert back["matches_before"] and target.read_text(encoding="utf-8") == "old"
    kinds = [e["kind"] for e in st.journal.entries()]
    assert "self_improve.owner.approval" in kinds and "self_improve.apply" in kinds and "self_improve.rollback" in kinds
    assert st.verify().ok


def test_rollback_will_not_overwrite_a_later_human_edit(tmp_path):
    st, hid, did, root = make_pass(tmp_path)
    apply_root = tmp_path / "shared"
    (apply_root / "agents").mkdir(parents=True)
    (apply_root / "agents" / "7.txt").write_text("old", encoding="utf-8")
    token = issue_token(root, hypothesis_id=hid, draft_id=did)
    entry = apply_draft(root, hypothesis_id=hid, draft_id=did, token=token, apply_root=apply_root)
    (apply_root / "agents" / "7.txt").write_text("human edit", encoding="utf-8")
    with pytest.raises(LimitViolation, match="changed after apply"):
        rollback(root, apply_id=entry["apply_id"], apply_root=apply_root)
    assert (apply_root / "agents" / "7.txt").read_text(encoding="utf-8") == "human edit"


def test_token_bound_to_draft_expiry_and_stale_base(tmp_path):
    st, hid, did, root = make_pass(tmp_path)
    apply_root = tmp_path / "shared"
    (apply_root / "agents").mkdir(parents=True)
    target = apply_root / "agents" / "7.txt"
    target.write_text("old", encoding="utf-8")
    other = issue_token(root, hypothesis_id="H-other", draft_id=did)
    with pytest.raises(ApprovalRequired, match="different draft"):
        apply_draft(root, hypothesis_id=hid, draft_id=did, token=other, apply_root=apply_root)
    target.write_text("someone changed it", encoding="utf-8")
    token = issue_token(root, hypothesis_id=hid, draft_id=did)
    with pytest.raises(LimitViolation, match="changed since"):
        apply_draft(root, hypothesis_id=hid, draft_id=did, token=token, apply_root=apply_root)
    assert target.read_text(encoding="utf-8") == "someone changed it"


def test_forged_token_file_entry_without_journal_record_is_refused(tmp_path):
    from bcc.self_improve.approval import load_tokens, save_tokens, token_hash
    st, hid, did, root = make_pass(tmp_path)
    draft, _ = DraftStore(root, Limits()).load(did)
    save_tokens(root, {token_hash("forged"): {"hypothesis_id": hid, "draft_id": did,
                                              "draft_sha256": draft.proposed_sha256, "expires_at": 9e12, "used": False}})
    apply_root = tmp_path / "shared"
    apply_root.mkdir()
    with pytest.raises(ApprovalRequired, match="journal"):
        apply_draft(root, hypothesis_id=hid, draft_id=did, token="forged", apply_root=apply_root)
    assert load_tokens(root)[token_hash("forged")]["used"] is False


def test_tampered_draft_is_detected(tmp_path):
    _, hid, did, root = make_pass(tmp_path)
    (root / "drafts" / did / "v1" / "proposed.txt").write_text("evil", encoding="utf-8")
    with pytest.raises(LimitViolation, match="tampered"):
        DraftStore(root, Limits()).load(did)


def test_loop_side_modules_never_import_the_owner_issuer():
    for name in ("loop.py", "apply.py", "approval.py", "delegation.py", "sandbox.py", "drafts.py"):
        tree = ast.parse((PKG / "self_improve" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                   [(node.module or "")] + [a.name for a in node.names] if isinstance(node, ast.ImportFrom) else []
            assert not any(m.split(".")[-1] == "owner" for m in mods), (name, mods)
    feat = ast.parse((PKG / "features" / "self_improve.py").read_text(encoding="utf-8"))
    assert "issue_token" not in ast.dump(feat)
    for name in ("loop.py", "apply.py"):
        assert "bypassPermissions" not in (PKG / "self_improve" / name).read_text(encoding="utf-8")


# ============================================================================== bot map
def test_botmap_states_and_no_prompt_copy():
    agents = [{"id": 1, "name": "A", "role": "r", "system_prompt": "PROMPT-BODY", "model_id": 1, "tools": ["x"],
               "permissions": {"p": True, "q": False}, "enabled": True, "max_steps": 3, "max_tokens": 9, "budget_usd": 0.0},
              {"id": 2, "name": "B", "role": "", "system_prompt": "", "enabled": False},
              {"id": 3, "name": "C", "enabled": True},
              {"id": 4, "name": "D", "enabled": True}]
    tasks = [{"id": 1, "agent_id": 1, "status": "running", "created_at": "2026-10-18T10:00:00"},
             {"id": 2, "agent_id": 4, "status": "completed", "created_at": "2026-10-18T09:00:00"}]
    m = build_botmap(agents, tasks, [{"id": 1, "alias": "main", "status": "offline", "kind": "local"}],
                     source_sha="67a2a0b5", runtime_build="abc")
    st = {a["id"]: a["state"] for a in m["agents"]}
    assert st == {"agent:1": "running", "agent:2": "disabled", "agent:3": "never_run", "agent:4": "idle"}
    assert m["agents"][0]["owner"] == "N/A" and m["agents"][0]["limits"]["depth"] == "N/A"
    assert m["agents"][0]["data_reach"] == ["p"]
    assert "PROMPT-BODY" not in json.dumps(m) and "PROMPT-BODY" not in render_markdown(m)
    assert any(c["state"] == "N/A" for c in m["components"])                # no probe -> literal N/A
    assert build_botmap(agents, tasks, [], generated_at="x")["map_version"] == \
        build_botmap(agents, tasks, [], generated_at="y")["map_version"]  # version = content hash, not time


# ============================================================================== API (read-only)
async def test_api_state_is_read_only_and_literal(env):
    r = await env.client.get("/api/self-improve/state")
    assert r.status_code == 200 and r.json()["status"] == NOT_RUN
    r = await env.client.get("/api/self-improve/botmap")
    assert r.status_code == 200 and r.json()["status"] == NOT_RUN
    root = Path(env.settings.data_dir) / "self_improve"
    st = Store(root)
    st.record_observation(good_obs())
    j = (await env.client.get("/api/self-improve/state")).json()
    assert j["status"] == "OK" and j["observations"]["total"] == 1 and j["journal"]["ok"] is True
    assert j["gain_claim"].startswith("NOT_RUN")
    for method in ("post", "put", "patch", "delete"):
        resp = await getattr(env.client, method)("/api/self-improve/state")
        assert resp.status_code in (404, 405)
    paths = {p: ops for p, ops in env.app.openapi()["paths"].items() if "self-improve" in p}
    assert paths and all(set(ops) <= {"get", "head"} for ops in paths.values()), paths
