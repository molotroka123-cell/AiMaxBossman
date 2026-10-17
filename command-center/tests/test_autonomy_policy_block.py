"""Stage 2.2 package A: the machine-readable ```bossman-policy block. A NEW owner constitution must be understood by
the autonomy gate without a code change, and the block can never lower an invariant of the build.

Only TEST pin files and test data dirs are used here (never the real pin or the live constitution)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bcc.autonomy import approver_policy as AP
from bcc.autonomy import cli as autonomy_cli
from bcc.autonomy.goals import GoalStore, GuardError
from bcc.autonomy.journal import Journal
from bcc.autonomy.lease import EngineeringLease

from .autonomy_fakes import kinds, make_goal, of
from .test_autonomy_approver_policy import (GEMMA, NEMO, V1_FILE, V1_TEXT, V2_FILE, make_cycle, verdict_chat,  # noqa: F401
                                            world)

BODY = "# Bossman Constitution (user-owned)\n\n## Top goals\n\n{rule4}\n\n## Enforcement\n\nThe user pins this file.\n"
RULE_V1 = "4. Never apply a change without both Claude and Codex approving the same immutable SHA."
RULE_V2 = f"4. Never apply a change without {AP.V2_SENTENCE}: tests plus two verifier models."
BLOCK_INDEP = {"schema_version": 1, "approvers": {"mode": "independent", "min_independent": 2,
                                                   "require_external": True},
               "writer_may_verify": False, "auto_tier_requires": ["tests", "verifiers"], "max_autonomy_level": "L2"}


def doc(block=None, rule4="", raw: str | None = None) -> str:
    text = BODY.format(rule4=rule4)
    if raw is not None:
        return text + "\n```bossman-policy\n" + raw + "\n```\n"
    if block is not None:
        return text + "\n```bossman-policy\n" + json.dumps(block, indent=1) + "\n```\n"
    return text


def pol(text: str) -> AP.ApproverPolicy:
    return AP.policy_from_text(text, sha="s" * 64)


def block(**over) -> dict:
    b = json.loads(json.dumps(BLOCK_INDEP))
    for k, v in over.items():
        if k == "approvers":
            b["approvers"].update(v)
        elif v is None:
            b.pop(k, None)
        else:
            b[k] = v
    return b


# ------------------------------------------------------------------ legacy detection is unchanged

def test_v1_text_without_block_is_v1_named_claude_codex():
    p = pol(doc(rule4=RULE_V1))
    assert p.mode == "v1" and p.source == "legacy" and p.rule.startswith("legacy")
    assert pol(V1_TEXT).mode == "v1"
    assert pol(V1_FILE.read_text(encoding="utf-8")).mode == "v2"


def test_v2_sentence_without_block_is_independent():
    p = pol(doc(rule4=RULE_V2))
    assert p.mode == "v2" and p.source == "legacy" and p.min_independent == 2 and p.require_external
    assert pol(V2_FILE.read_text(encoding="utf-8").split("## Policy block")[0]).mode == "v2"


def test_shipped_v2_proposal_block_matches_its_text():
    text = V2_FILE.read_text(encoding="utf-8")
    p = pol(text)
    assert p.mode == "v2" and p.source == "block" and p.max_level == "L2" and not p.warnings
    assert text.count(AP.V2_SENTENCE) == 1 and "DRAFT" not in text.split("## Top goals")[0]
    legacy_only = AP.policy_from_text(text.split("```bossman-policy")[0], sha="")
    assert legacy_only.mode == p.mode                          # the block restates, never contradicts, the text


# ------------------------------------------------------------------ the block governs

def test_block_only_constitution_independent():
    p = pol(doc(block=BLOCK_INDEP))
    assert p.mode == "v2" and p.source == "block" and p.min_independent == 2 and p.require_external
    assert p.auto_tier_requires == ("tests", "verifiers") and not p.writer_may_verify and p.max_level == "L2"


def test_block_named_is_v1_even_when_text_is_silent():
    p = pol(doc(block={"schema_version": 1, "approvers": {"mode": "named", "named": ["claude", "codex"]}}))
    assert p.mode == "v1" and p.source == "block"
    assert p.evaluate(["claude", "codex"], "jev").ok and not p.evaluate([GEMMA, NEMO], "jev").ok


def test_block_lower_level_is_allowed_higher_is_capped_at_l2():
    assert pol(doc(block=block(max_autonomy_level="L1"))).max_level == "L1"
    p = pol(doc(block=block(max_autonomy_level="L4")))
    assert p.mode == "v2" and p.max_level == "L2" and any("L2 start cap" in w for w in p.warnings)


# ------------------------------------------------------------------ everything else fails closed to v1

def assert_closed(text: str, needle: str) -> AP.ApproverPolicy:
    p = pol(text)
    assert p.mode == "v1" and p.source == "fallback" and not p.is_v2, p
    assert any(needle in w for w in p.warnings), p.warnings
    assert p.evaluate([GEMMA, NEMO], "jev").ok is False        # the independent set is NOT enough under the fallback
    return p


def test_invalid_json_fails_closed():
    assert_closed(doc(raw='{"schema_version": 1, "approvers": '), "invalid JSON")
    assert_closed(doc(raw="[1, 2]"), "JSON object")


def test_invalid_json_overrides_a_v2_sentence_in_the_text():
    assert_closed(doc(rule4=RULE_V2, raw="{nope"), "invalid JSON")


def test_unknown_mode_unknown_schema_unknown_keys_fail_closed():
    assert_closed(doc(block=block(approvers={"mode": "anything-goes"})), "unknown approvers.mode")
    assert_closed(doc(block=block(schema_version=2)), "schema_version")
    assert_closed(doc(block=block(skip_verifiers=True)), "unknown key")
    assert_closed(doc(block=block(approvers={"quorum": 1})), "unknown approvers key")


@pytest.mark.parametrize("key", ["owner_decisions", "stop", "protected_globs", "pin", "release_tiers",
                                 "disable_stop", "money", "journal"])
def test_block_cannot_touch_owner_decisions_stop_globs_pin(key):
    for value in (False, [], "off", {"enabled": False}):
        p = assert_closed(doc(block=block(**{key: value})), "cannot be changed by the block")
        assert p.mode == "v1"


def test_weaker_than_allowed_is_refused():
    assert_closed(doc(block=block(max_autonomy_level="L5")), "max_autonomy_level")
    assert_closed(doc(block=block(max_autonomy_level="admin")), "max_autonomy_level")
    assert_closed(doc(block=block(approvers={"min_independent": 1})), "below the floor")
    assert_closed(doc(block=block(approvers={"min_independent": 0})), "below the floor")
    assert_closed(doc(block=block(approvers={"require_external": False})), "require_external")
    assert_closed(doc(block=block(auto_tier_requires=["tests"])), "auto_tier_requires")
    assert_closed(doc(block=block(auto_tier_requires=[])), "auto_tier_requires")
    assert_closed(doc(block=block(approvers={"mode": "named", "named": ["claude"]})), "named")


def test_writer_as_verifier_is_refused_and_never_counted():
    assert_closed(doc(block=block(writer_may_verify=True)), "writer_may_verify")
    p = pol(doc(block=BLOCK_INDEP))
    assert not p.evaluate([GEMMA, NEMO], GEMMA).ok            # the writer itself
    assert not p.evaluate([GEMMA, NEMO], "nemotron").ok       # the writer's family
    assert p.evaluate([GEMMA, NEMO], "qwen-operator").ok


def test_contradiction_between_block_and_rule_4_and_duplicate_blocks_fail_closed():
    assert_closed(doc(block=BLOCK_INDEP, rule4=RULE_V1), "contradictory")
    assert_closed(doc(block={"schema_version": 1, "approvers": {"mode": "named"}}, rule4=RULE_V2), "contradictory")
    twice = doc(block=BLOCK_INDEP) + "\n```bossman-policy\n" + json.dumps(BLOCK_INDEP) + "\n```\n"
    assert_closed(twice, "more than one")
    assert_closed(doc(block=None) + "\n```bossman-policy\n{}\n", "not closed")


def test_min_independent_above_the_build_is_refused_not_stalled():
    assert_closed(doc(block=block(approvers={"min_independent": 3})), "above what this build runs")


# ------------------------------------------------------------------ pin: the block is read ONLY from the pinned file

def pinned(tmp_path, text: str, *, pin_sha: str | None = None):
    c = tmp_path / "constitution.md"
    c.write_bytes(text.encode("utf-8"))
    p = tmp_path / "pin" / "constitution.sha256"
    p.parent.mkdir(exist_ok=True)
    p.write_text((pin_sha or hashlib.sha256(c.read_bytes()).hexdigest()) + "\n", encoding="ascii")
    return c, p


def test_pinned_block_is_used_pin_mismatch_unpinned_unreadable_are_v1(tmp_path):
    text = doc(block=BLOCK_INDEP)
    c, p = pinned(tmp_path, text)
    assert AP.load_policy(str(c), str(p)).mode == "v2"
    (tmp_path / "m").mkdir()
    c2, p2 = pinned(tmp_path / "m", text, pin_sha="0" * 64)
    assert AP.load_policy(str(c2), str(p2)).mode == "v1"
    assert AP.load_policy(str(c), str(tmp_path / "no-pin")).mode == "v1"
    assert AP.load_policy(str(tmp_path / "gone.md"), str(p)).mode == "v1"
    c.write_bytes((text + "\n<!-- edited after pinning -->\n").encode("utf-8"))      # edited: pin no longer matches
    assert AP.load_policy(str(c), str(p)).mode == "v1"


# ------------------------------------------------------------------ explain (no pinning)

def test_explain_cli_prints_effective_policy_and_pins_nothing(tmp_path, capsys):
    f = tmp_path / "new_constitution.md"
    f.write_text(doc(block=block(max_autonomy_level="L3")), encoding="utf-8")
    pin = tmp_path / "pin" / "constitution.sha256"
    rc = autonomy_cli.main(["--pin-path", str(pin), "constitution", "explain", "--path", str(f)])
    out = capsys.readouterr().out
    assert rc == 0 and not pin.exists() and not pin.parent.exists()
    for needle in ("EFFECTIVE POLICY", "v2", "min verifiers:      2", "external required:  yes", "start level:        L2",
                   "NOT pinned", "L2 start cap", "NEVER changed by any constitution text", "owner STOP"):
        assert needle in out, needle
    rc = autonomy_cli.main(["--json", "--pin-path", str(pin), "constitution", "explain", "--path", str(f)])
    rep = json.loads(capsys.readouterr().out)
    assert rc == 0 and rep["policy"]["mode"] == "v2" and rep["pinned"] is False and rep["start_level"] == "L2"
    f.write_text(doc(raw="{broken"), encoding="utf-8")
    autonomy_cli.main(["--json", "--pin-path", str(pin), "constitution", "explain", "--path", str(f)])
    rep = json.loads(capsys.readouterr().out)
    assert rep["policy"]["mode"] == "v1" and rep["policy"]["source"] == "fallback" and rep["policy"]["warnings"]
    assert autonomy_cli.main(["constitution", "explain", "--path", str(tmp_path / "missing.md")]) != 0


def test_adopt_script_prints_explain_before_asking():
    text = (Path(__file__).resolve().parents[2] / "tools" / "adopt_constitution_v2.ps1").read_text(encoding="utf-8")
    assert text.index("constitution explain") < text.index("Read-Host \"Type $Word")


# ------------------------------------------------------------------ end to end: block constitution, test pin

async def test_cycle_with_pinned_block_constitution_reaches_owner_gate_and_needs_release(world, tmp_path):
    c, p = pinned(tmp_path, doc(block=BLOCK_INDEP))                    # TEST pin, never the real one
    provider = lambda: AP.load_policy(str(c), str(p))                  # noqa: E731
    assert provider().mode == "v2"
    goals = GoalStore(world["root"], world["journal"], policy_provider=provider)
    world["goals"] = goals
    calls: list[str] = []
    cy = make_cycle(world, verdict_chat(calls), verifiers=[GEMMA, NEMO], policy=provider(), seed=1)
    out = await cy.run_goal(make_goal("G-BLK"))
    assert out.state == "USER_APPROVAL", out.reason
    assert calls == [GEMMA, NEMO]                                      # two different-family fake verifiers
    rec = goals.get("G-BLK")
    cand = rec["candidate"]
    assert GoalStore.approvals_valid(rec, provider()) and goals.staging_passed(rec)       # candidate ready
    # promotion still needs the owner's Release: nothing was applied, no decision exists yet
    assert rec["state"] == "USER_APPROVAL" and not rec.get("user_decision")
    assert "apply_candidate" not in [e["request"]["action"] for e in of(world["journal"], "hand.request", "G-BLK")]
    with pytest.raises(GuardError):                                    # a Release for another SHA is refused
        goals.record_user_decision("G-BLK", "apply", sha="f" * 40, diff_sha256=cand["diff_sha256"])
    assert not goals.get("G-BLK").get("user_decision")
    assert "goal.user_decision" not in kinds(world["journal"])
    # the same pinned text edited afterwards (pin mismatch) drops every approval to the strict v1 reading
    c.write_bytes(c.read_bytes() + b"\n")
    assert not GoalStore.approvals_valid(goals.get("G-BLK"), AP.load_policy(str(c), str(p)))
