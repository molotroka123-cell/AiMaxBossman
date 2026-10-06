import json

import numpy as np
import pytest

from pokerlora import dataset, sft
from pokerlora.validate import validate_input, validate_output, validate_reference


def test_split_is_by_board_with_no_leak(small_data):
    d, m = small_data
    assert m["board_leak"] == [] and sum(m["examples"].values()) > 200
    boards = {s: {e["board_key"] for e in dataset.load(d, s)} for s in ("train", "val", "test")}
    assert not boards["train"] & boards["test"] and not boards["train"] & boards["val"] and not boards["val"] & boards["test"]
    assert all(dataset.split_of(b) == s for s, bs in boards.items() for b in bs)


def test_every_example_validates_and_hides_what_a_player_cannot_see(small_data):
    d, _ = small_data
    for s in ("train", "val", "test"):
        for e in dataset.load(d, s):
            assert validate_input(e["input"]) == [] and validate_reference(e["reference"], e["input"]) == []
            flat = json.dumps(e["input"]).lower()
            for bad in ("villain_hole", "opponent_cards", "deck", "ev_by_action", "probs", "equity\""):
                assert bad not in flat
            assert set(e["input"]) == {"task", "format", "units", "hero", "board", "pot_at_river_start", "effective_stack", "pot_now", "hero_in_this_street",
                                       "to_call", "history", "ranges", "legal"}


def test_input_validator_rejects_hidden_fields_duplicates_and_missing_legal(small_data):
    d, _ = small_data
    inp = dataset.load(d, "train")[0]["input"]
    assert validate_input({**inp, "villain_hole": ["As", "Kd"]})
    bad = json.loads(json.dumps(inp)); bad["board"][0] = bad["hero"]["hole"][0]
    assert any("duplicate" in r for r in validate_input(bad))
    assert validate_input({**inp, "legal": []})


def test_reference_is_a_distribution_over_legal_actions_and_support_actions_are_near_indifferent(small_data):
    d, _ = small_data
    checked = 0
    for e in dataset.load(d, "test"):
        r, inp = e["reference"], e["input"]
        assert abs(sum(r["probs"].values()) - 1) < 1e-3 and set(r["probs"]) <= {a["action"] for a in inp["legal"]}
        ev = r["ev_by_action"]; top = max(ev.values())
        for a, p in r["probs"].items():
            if p >= 0.2:                                   # a clearly used action must not be far worse than the best (equilibrium indifference)
                assert top - ev[a] <= 0.06 * inp["pot_now"] + 0.25, (a, ev)
                checked += 1
    assert checked > 50


def test_sft_export_uses_train_and_val_only_and_leaks_no_solver_internals(small_data, tmp_path):
    d, _ = small_data
    n = sft.write(d, tmp_path)
    assert set(n) == {"train", "val"} and not (tmp_path / "sft_test.jsonl").exists()
    with pytest.raises(ValueError):
        sft.write(d, tmp_path, splits=("test",))
    row = json.loads((tmp_path / "sft_train.jsonl").read_text().splitlines()[0])
    ans = json.loads(row["messages"][2]["content"])
    assert set(ans) == {"action", "size", "probs", "explanation"} and "ev_by_action" not in row["messages"][2]["content"]
    user = row["messages"][1]["content"]
    assert "ev_by_action" not in user and "solver" not in user.lower()
    ok, reasons, dist = validate_output(ans, json.loads(user))
    assert ok, reasons


def test_dataset_hash_is_reproducible(tmp_path):
    a = dataset.build(6, 9, tmp_path / "a", per_node_cap=3, workers=1)
    b = dataset.build(6, 9, tmp_path / "b", per_node_cap=3, workers=1)
    assert a["sha256"] == b["sha256"]


def test_model_output_validation_rules(small_data):
    d, _ = small_data
    e = dataset.load(d, "train")[0]; inp = e["input"]
    legal = {a["action"]: a["amount"] for a in inp["legal"]}
    act = next(iter(legal))
    assert validate_output({"action": act, "size": legal[act]}, inp)[0]
    assert not validate_output({"action": "teleport", "size": 1}, inp)[0]
    assert not validate_output({"action": act, "size": legal[act] + 50}, inp)[0]                 # the clicker/model may not change the amount
    assert not validate_output({"action": act, "probs": {act: 0.2}}, inp)[0]
    assert not validate_output("no json", inp)[0]
