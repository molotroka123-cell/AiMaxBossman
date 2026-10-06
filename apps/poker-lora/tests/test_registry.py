import pytest

from pokerlora import dataset
from pokerlora.policies import EquityRule, Imitation, PassiveCheckCall, RandomLegal
from pokerlora.registry import PolicyRegistry


def test_promote_a_supported_improvement_reject_a_worse_candidate_and_rollback(small_data, tmp_path):
    d, m = small_data
    tr, va, te = (dataset.load(d, s) for s in ("train", "val", "test"))
    specs = list({e["spot_id"]: e["spec"] for e in te}.values())[:4]
    reg = PolicyRegistry(tmp_path)
    reg.register("v0_random", {"kind": "baseline"}, activate=True)
    good = reg.propose("v1_equity", {"kind": "rule", "dataset_sha256": m["sha256"]}, EquityRule(), RandomLegal(0), te, specs)
    assert good.promoted and reg.active() == "v1_equity"
    bad = reg.propose("v2_passive", {"kind": "rule"}, PassiveCheckCall(), EquityRule(), te, specs)
    assert not bad.promoted and reg.active() == "v1_equity" and any("EV-loss" in r or "exploitab" in r for r in bad.reasons)
    assert reg.meta("v2_passive")["gate"]["promoted"] is False
    assert reg.rollback() == "v0_random" and reg.active() == "v0_random"
    pay = reg.tree_payload("a" * 40, "test")
    assert pay["metrics"]["active"] == "v0_random" and len(pay["sha"]) == 40


def test_an_invalid_candidate_is_rejected_by_the_validity_gate(small_data, tmp_path):
    from pokerlora.policies import Policy
    class Junk(Policy):
        name = "junk"
        def act(self, inp): return "raise!"
    d, _ = small_data
    te = dataset.load(d, "test")
    reg = PolicyRegistry(tmp_path); reg.register("v0", {}, activate=True)
    v = reg.propose("v1_junk", {}, Junk(), RandomLegal(0), te)
    assert not v.promoted and any("validity" in r for r in v.reasons) and reg.active() == "v0"
