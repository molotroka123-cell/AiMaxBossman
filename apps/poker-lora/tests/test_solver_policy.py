"""SolverPolicy answers a model input by solving the spot itself: it must reproduce the dataset's solver labels, pass the validator,
and refuse inputs it cannot solve instead of guessing."""
import copy

import pytest

from pokerlora import dataset
from pokerlora.solver_policy import NotSolvable, SolverPolicy
from pokerlora.validate import validate_output


def test_solver_policy_reproduces_the_reference_labels(small_data):
    d, _ = small_data
    rows = (dataset.load(d, "test") + dataset.load(d, "val") + dataset.load(d, "train"))[:12]
    assert rows
    pol = SolverPolicy()
    for r in rows:
        out = pol.act(r["input"])
        ok, reasons, dist = validate_output(out, r["input"])
        assert ok, reasons
        ref = r["reference"]["probs"]
        assert set(out["probs"]) == set(ref)
        for a in ref:
            assert out["probs"][a] == pytest.approx(ref[a], abs=2e-3), (r["id"], a)
        if max(ref.values()) - sorted(ref.values())[-2 if len(ref) > 1 else -1] > 0.01:
            assert out["action"] == r["reference"]["action"]


def test_study_cli_solves_a_hand_typed_spot(capsys):
    import json
    from pokerlora.cli import main
    assert main(["solve", "--board", "Ah Kd 7c 2s 9h", "--hole", "As Qs", "--pos", "IP", "--pot", "40", "--stack", "100", "--history", "OOP:check"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["to_call"] == 0 and {a["action"] for a in out["legal"]} == {"check", "bet50", "bet100", "allin"}
    assert abs(sum(out["answer"]["probs"].values()) - 1) < 1e-6 and out["answer"]["action"] in out["answer"]["probs"]
    # top pair top kicker in position after a check: the equilibrium bets (value), it does not give up the pot
    assert out["answer"]["probs"]["check"] < 0.5


def test_solver_policy_refuses_what_it_cannot_solve(small_data):
    d, _ = small_data
    r = (dataset.load(d, "test") + dataset.load(d, "train"))[0]
    pol = SolverPolicy()
    bad_hist = copy.deepcopy(r["input"]); bad_hist["history"] = [{"player": "OOP", "action": "bet33", "amount": 1.0}]
    bad_legal = copy.deepcopy(r["input"]); bad_legal["legal"] = bad_legal["legal"][:1]
    bad_hand = copy.deepcopy(r["input"]); bad_hand["ranges"]["hero"] = "AA"
    bad_hand["hero"]["hole"] = [c for c in ("7c", "2d", "7d", "2h", "7h", "2s") if c not in bad_hand["board"]][:2]
    for inp in (bad_hist, bad_legal, bad_hand):
        with pytest.raises(NotSolvable):
            pol.act(inp)
