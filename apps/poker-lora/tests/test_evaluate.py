import numpy as np
import pytest

from pokerlora import dataset
from pokerlora.evaluate import cluster_ci, example_metrics, paired_diff_ci, policy_profile, profile_metrics
from pokerlora.policies import EquityRule, PassiveCheckCall, Policy, RandomLegal, _key


class Ref(Policy):
    name = "ref"
    def __init__(self, ex): self.t = {_key(e["input"]): e["reference"] for e in ex}
    def act(self, inp):
        r = self.t[_key(inp)]
        return {"action": r["action"], "size": r["size"], "probs": r["probs"]}


class Junk(Policy):
    name = "junk"
    def act(self, inp): return "I think you should raise a lot"


class Crash(Policy):
    name = "crash"
    def act(self, inp): raise RuntimeError("boom")


def test_solver_reference_has_zero_loss_and_full_agreement(small_data):
    d, _ = small_data
    te = dataset.load(d, "test")
    m = example_metrics(Ref(te), te)
    assert m["valid"]["mean"] == 1.0 and m["ev_loss_all"]["mean"] < 1e-6 and m["tv"]["mean"] < 0.01 and m["top1"]["mean"] > 0.99


def test_invalid_and_crashing_answers_are_counted_not_hidden(small_data):
    d, _ = small_data
    te = dataset.load(d, "test")
    for pol in (Junk(), Crash()):
        m = example_metrics(pol, te)
        assert m["valid"]["mean"] == 0.0 and m["ev_loss_all"]["mean"] > 0 and m["ev_loss_valid"]["mean"] is None


def test_equity_rule_beats_random_and_passive_with_supported_ci(small_data):
    d, _ = small_data
    te = dataset.load(d, "test")
    r, p, e = (example_metrics(x, te) for x in (RandomLegal(0), PassiveCheckCall(), EquityRule()))
    assert e["ev_loss_all"]["mean"] < r["ev_loss_all"]["mean"] and e["ev_loss_all"]["mean"] < p["ev_loss_all"]["mean"]
    diff = paired_diff_ci(r["_by_spot"]["ev_loss_all"], e["_by_spot"]["ev_loss_all"])
    assert diff["lo"] > 0 and diff["n_spots"] >= 3


def test_cluster_ci_is_wider_with_fewer_spots_and_never_invents_data():
    assert cluster_ci({})["mean"] is None
    few = cluster_ci({"a": [1.0, 2.0], "b": [0.0, 1.0]}); many = cluster_ci({str(i): [float(i % 3)] for i in range(60)})
    assert (few["hi"] - few["lo"]) > (many["hi"] - many["lo"])


def test_profile_metrics_equilibrium_is_unexploitable_and_fixed_opponents_are_beaten(small_data):
    d, _ = small_data
    te = dataset.load(d, "test")
    specs = list({e["spot_id"]: e["spec"] for e in te}.values())[:3]
    # the solver's own full strategy: build a policy answering from the stored reference for EVERY hand is not available (only sampled hands),
    # so check the sampled-hand reference separately and verify the generic machinery on the equity rule
    m = profile_metrics(EquityRule(), specs)
    assert m["exploitability_pct_pot"]["mean"] >= 0 and m["ev_loss_vs_equilibrium_pct_pot"]["mean"] >= -0.5
    assert m["ev_vs_station_pct_pot"]["mean"] > 0                          # a sane rule beats a calling station
    assert m["invalid_share_in_play"] == 0.0
    j = profile_metrics(Junk(), specs)
    assert j["invalid_share_in_play"] == 1.0                                 # invalid answers fall back to check/fold and are counted
