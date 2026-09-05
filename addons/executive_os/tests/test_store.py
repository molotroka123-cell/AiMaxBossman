"""Real SQLite/process tests of local state; proof verification belongs to runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from bossman_os.store import CapacityExceeded, Conflict, Store, StoreError, digest


def contract(*steps):
    return {"local_only": True, "cost_microusd": 0,
            "steps": [{"id": step, "effect_digest": digest({"action": "file.write", "target": step})}
                      for step in (steps or ("write",))]}


def receipt(snapshot, step_id="write"):
    step = next(s for s in snapshot["steps"] if s["id"] == step_id)
    return {"mission_id": snapshot["id"], "contract_digest": snapshot["contract_digest"], "step_id": step_id,
            "effect_digest": step["effect_digest"], "actor": step["actor"], "fence": step["fence"],
            "dispatch_binding": digest([snapshot["contract_digest"], step_id, step["fence"]]),
            "proof_digest": digest("host fixture; not live verification")}


def test_contract_identity_and_all_steps_required(tmp_path):
    store = Store(tmp_path / "state.sqlite", limits={"workers": 1})
    first = store.create("m", contract("a", "b"))
    assert store.create("m", contract("a", "b")) == first
    with pytest.raises(Conflict):
        store.create("m", contract("changed"))
    a = store.claim("m", "a", "worker", 0, {"workers": 1})
    done_a = store.confirm("m", "a", "worker", a["fence"], receipt(a, "a"))
    assert done_a["status"] != "verified"
    b = store.claim("m", "b", "worker", done_a["version"], {"workers": 1})
    completed = store.confirm("m", "b", "worker", b["fence"], receipt(b, "b"))
    assert completed["status"] == "verified"
    assert completed["resource_usage"]["workers"]["used"] == 0
    assert [e["kind"] for e in completed["events"]] == ["created", "claimed", "confirmed", "claimed", "confirmed"]
    assert [e["version"] for e in completed["events"]] == [0, 1, 2, 3, 4]
    assert store.list()[0] == completed


def test_receipt_cannot_confirm_identical_contract_in_another_mission(tmp_path):
    store = Store(tmp_path / "state.sqlite", {"workers": 2})
    for mission in ("a", "b"):
        store.create(mission, contract())
    a = store.claim("a", "write", "worker", 0, {"workers": 1})
    b = store.claim("b", "write", "worker", 0, {"workers": 1})
    before = store.snapshot("b")
    with pytest.raises(Conflict):
        store.confirm("b", "write", "worker", b["fence"], receipt(a))
    assert store.snapshot("b") == before


def test_unknown_restart_retains_reservation_and_refuses_normal_retry(tmp_path):
    path = tmp_path / "state.sqlite"
    store = Store(path, limits={"workers": 1})
    store.create("m", contract())
    active = store.claim("m", "write", "worker", 0, {"workers": 1})
    unknown = store.uncertain("m", "write", "worker", active["fence"])
    restarted = Store(path)
    assert restarted.snapshot("m")["status"] == "unknown"
    assert restarted.snapshot("m")["resource_usage"]["workers"]["used"] == 1
    assert restarted.recover_read()[0]["state"] == "unknown"
    with pytest.raises(Conflict):
        restarted.claim("m", "write", "new-worker", unknown["version"], {"workers": 1})
    # Host recovery confirms the existing effect; it does not execute it again.
    completed = restarted.confirm("m", "write", "worker", active["fence"], receipt(active))
    assert completed["status"] == "verified"
    assert restarted.recover_read() == []
    assert restarted.confirm("m", "write", "worker", active["fence"], receipt(active)) == completed
    assert completed["resource_usage"]["workers"]["used"] == 0


@pytest.mark.parametrize("change", ["actor", "fence", "effect", "contract", "boolean_only"])
def test_stale_or_unbound_receipt_cannot_confirm(tmp_path, change):
    store = Store(tmp_path / "state.sqlite", limits={"workers": 1})
    store.create("m", contract())
    active = store.claim("m", "write", "worker", 0, {"workers": 1})
    proof = receipt(active)
    before = store.snapshot("m")
    actor, fence = "worker", active["fence"]
    if change == "actor": actor = "other"
    if change == "fence": fence += 1
    if change == "effect": proof["effect_digest"] = "0"*64
    if change == "contract": proof["contract_digest"] = "0"*64
    if change == "boolean_only": proof = {"verified": True}
    with pytest.raises(Conflict):
        store.confirm("m", "write", actor, fence, proof)
    assert store.snapshot("m") == before
    assert store.snapshot("m")["resource_usage"]["workers"]["used"] == 1


def test_limits_are_global_ratchet_and_vector_reservation_is_atomic(tmp_path):
    path = tmp_path / "state.sqlite"
    store = Store(path, limits={"workers": 2, "ram_mb": 100})
    store.create("one", contract())
    store.create("two", contract())
    store.claim("one", "write", "worker", 0, {"workers": 1, "ram_mb": 100})
    with pytest.raises(CapacityExceeded):
        store.claim("two", "write", "worker", 0, {"workers": 1, "ram_mb": 1})
    unchanged = store.snapshot("two")
    assert unchanged["version"] == 0
    assert unchanged["resource_usage"]["workers"]["used"] == 1
    with pytest.raises(CapacityExceeded):
        store.claim("two", "write", "worker", 0, {}, limits={"workers": 3})
    lowered = Store(path, limits={"workers": 1, "ram_mb": 100})
    with pytest.raises(Conflict):
        Store(path, limits={"workers": 2, "ram_mb": 100})
    assert lowered.snapshot("one")["resource_usage"]["workers"]["limit"] == 1


@pytest.mark.parametrize("bad", [{"usd": 1}, {"workers": True}, {"workers": -1}, {"workers": 1.5}])
def test_resources_are_integer_physical_counters_only(tmp_path, bad):
    with pytest.raises(StoreError):
        Store(tmp_path / "state.sqlite", limits=bad)


def test_nonlocal_and_paid_contracts_not_admitted(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    for change in ({"local_only": False}, {"cost_microusd": 1}, {"cost_microusd": True}):
        with pytest.raises(StoreError):
            store.create("m", contract() | change)
    assert store.list() == []


@pytest.mark.parametrize("same_step", [False, True])
def test_two_processes_have_one_winner_without_oversubscription(tmp_path, same_step):
    path = tmp_path / "state.sqlite"
    store = Store(path, limits={"workers": 1})
    store.create("one", contract())
    if not same_step:
        store.create("two", contract())
    code = """
import sys
from bossman_os.store import Store, StoreError
store=Store(sys.argv[1])
print('ready', flush=True)
sys.stdin.readline()
try:
    store.claim(sys.argv[2], 'write', sys.argv[3], 0, {'workers':1})
    print('claimed', flush=True)
except StoreError:
    print('blocked', flush=True)
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    children = [subprocess.Popen([sys.executable, "-c", code, str(path), mission, actor],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env)
                for mission, actor in [("one", "a"), ("one" if same_step else "two", "b")]]
    try:
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        outcomes = []
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0, stderr
            outcomes.append(stdout.strip())
        assert sorted(outcomes) == ["blocked", "claimed"]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    restarted = Store(path)
    assert restarted.snapshot("one")["resource_usage"]["workers"]["used"] == 1
    assert len(restarted.recover_read()) == 1


def test_failed_step_keeps_resources_and_exact_event_state(tmp_path):
    store = Store(tmp_path / "state.sqlite", limits={"workers": 1})
    store.create("m", contract())
    active = store.claim("m", "write", "worker", 0, {"workers": 1})
    failed = store.fail("m", "write", "worker", active["fence"])
    assert failed["status"] == "failed"
    assert failed["resource_usage"]["workers"]["used"] == 1
    assert failed["events"][-1]["before_state"] == "running"
    assert failed["events"][-1]["after_state"] == "failed"
    with pytest.raises(Conflict):
        store.claim("m", "write", "worker", failed["version"], {"workers": 1})
