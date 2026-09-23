"""The ONE evolution loop: phases, owner controls, budgets, lease and crash recovery.

Real Git repositories, real test processes, real subprocess kills. The student is
the mock_patch backend (DETERMINISTIC TEST MODEL, MOCK_MODEL): these tests prove
the loop's plumbing and its refusals, never model skill.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bossman-core"))
from bossman_v3.self_improvement import gate_fixture as fx  # noqa: E402
from bossman_v3.self_improvement import loop as L  # noqa: E402
from bossman_v3.self_improvement import verifier as v  # noqa: E402
from bossman_v3.self_improvement.runner import LearningStore  # noqa: E402

REGRESS_PRICE = ("import unittest\n\nfrom price import price_with_vat\n\n\n"
                 "class HalfUpRegression(unittest.TestCase):\n"
                 "    def test_half_cent_rounds_up(self):\n"
                 "        self.assertEqual(price_with_vat(1, 50), 2)\n")
GOOD_MONEY = {"edits": [fx.MONEY_FIX], "writes": {"tests/test_regress_money.py": fx.REGRESS_MONEY},
              "claim": "fixed; all green"}
GOOD_WEIGHT = {"edits": [fx.WEIGHT_FIX], "writes": {"tests/test_regress_weight.py": fx.REGRESS_WEIGHT}}
BAD_PRICE = {"edits": [fx.WEAKENED_PRICE], "claim": "price fixed, all tests green"}
GOOD_PRICE = {"edits": [fx.PRICE_FIX], "writes": {"tests/test_regress_price.py": REGRESS_PRICE}}


def git_out(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def stable_hash(repo: Path) -> str:
    digest = hashlib.sha256()
    for args in (("rev-parse", "HEAD"), ("for-each-ref",), ("status", "--porcelain", "--untracked-files=all")):
        digest.update(git_out(repo, *args).encode())
    for name in sorted(git_out(repo, "ls-files").splitlines()):
        digest.update(name.encode() + (repo / name).read_bytes())
    return digest.hexdigest()


@pytest.fixture
def campaign(tmp_path):
    repo = tmp_path / "stable"
    fx.make_repo(repo)

    def make(tasks: dict, **limits) -> tuple[L.LoopConfig, Path]:
        script = tmp_path / "mock-script.json"
        script.write_text(json.dumps({"model": "DETERMINISTIC-TEST-MODEL-loop", "tasks": tasks}), encoding="utf-8")
        cfg = L.LoopConfig(suite=str(repo / "evolution-suite.json"), repo=str(repo), backend="mock_patch",
                           mock_script=str(script), **{"max_cycles": 3, "attempt_minutes": 5.0, **limits})
        return cfg, tmp_path / "campaign"
    return repo, make


def child(work: Path, *extra: str, hold: str = "") -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "bossman-core"), str(ROOT)])}
    env.pop(L.HOLD_ENV, None)
    if hold:
        env[L.HOLD_ENV] = hold
    return subprocess.Popen([sys.executable, "-m", "bossman_v3.self_improvement.loop", "loop", "--work", str(work),
                             *extra], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def wait_for(path: Path, proc: subprocess.Popen, seconds: float = 120) -> None:
    for _ in range(int(seconds * 10)):
        if path.exists():
            return
        if proc.poll() is not None:
            raise AssertionError("loop exited before the hold point: " + proc.stdout.read().decode()[-3000:])
        time.sleep(0.1)
    raise AssertionError("hold point never reached")


def hard_kill(proc: subprocess.Popen) -> None:
    proc.kill()                      # SIGKILL / TerminateProcess: no cleanup code runs
    proc.wait(timeout=30)


def test_three_cycles_accept_reject_accept_without_touching_stable(campaign):
    repo, make = campaign
    cfg, work = make({"money": [GOOD_MONEY], "price": [BAD_PRICE], "weight": [GOOD_WEIGHT]})
    before = stable_hash(repo)
    state = L.EvolutionLoop(work, cfg).run()
    cycles = state["cycles"]
    assert state["status"] == "COMPLETED", state.get("halt_reason")
    assert [c["task_id"] for c in cycles] == ["money", "price", "weight"]
    assert [c["outcome"] for c in cycles] == ["ACCEPTED", "REJECTED_INVALID_TEST", "ACCEPTED"]
    assert "assert removed" in " ".join(cycles[1]["verdict"]["reasons"])
    assert stable_hash(repo) == before                                   # stable never written
    refs = git_out(work / "candidates.git", "for-each-ref", "--format=%(refname)", "refs/heads/evo").splitlines()
    assert sorted(refs) == sorted(c["decision"]["ref"] for c in cycles if c["outcome"] == "ACCEPTED")
    assert not any(cycles[1]["id"] in ref for ref in refs)               # the bad patch left no candidate
    third = cycles[2]["decision"]["candidate_sha"]
    assert git_out(work / "candidates.git", "rev-parse", third + "^") == cycles[0]["decision"]["candidate_sha"]
    assert state["champion_sha"] == third and state["base_sha"] == git_out(repo, "rev-parse", "HEAD")
    assert all(c["attempt"]["model_kind"] == "MOCK_MODEL" for c in cycles)
    rep = L.report(work)
    assert rep["mock_only"] and rep["student_verified_passes"] == 0 and rep["weights"] == "WEIGHTS_UNCHANGED"
    assert L.verify_cycles(work)["cycles_checked"] == 3
    store = LearningStore(work / "learning")
    statuses = sorted(c["learning_status"] for c in store.failed())
    assert statuses == ["FAILED_EXPERIMENT", "PARTIAL", "PARTIAL"]
    assert not (work / L.LEASE_FILE).exists()                            # released
    # the student's claim is kept, labelled, and not what decided anything
    claim = (work / "cycles" / cycles[1]["id"] / "attempt" / "student-claim.txt").read_text(encoding="utf-8")
    assert claim.startswith("UNTRUSTED") and "all tests green" in claim


def test_stop_and_pause_files_are_honoured_before_new_work(campaign):
    _repo, make = campaign
    cfg, work = make({"money": [GOOD_MONEY]})
    L.request_stop(work, "test")
    state = L.EvolutionLoop(work, cfg).run()
    assert state["status"] == "STOPPED" and state["cycles"] == []
    L.clear_controls(work)
    L.request_pause(work, "test")
    state = L.EvolutionLoop(work).run()
    assert state["status"] == "PAUSED" and state["cycles"] == []
    L.clear_controls(work)
    state = L.EvolutionLoop(work, overrides={"max_cycles": 1}).run()
    assert state["status"] == "COMPLETED" and state["cycles"][0]["outcome"] == "ACCEPTED"


def test_stop_during_an_attempt_cancels_it_quickly_and_resume_finishes_the_cycle(campaign):
    _repo, make = campaign
    cfg, work = make({"money": [{**GOOD_MONEY, "sleep_seconds": 60}]}, max_cycles=1)
    threading.Timer(1.5, lambda: L.request_stop(work, "test")).start()
    started = time.monotonic()
    state = L.EvolutionLoop(work, cfg).run()
    assert time.monotonic() - started < 30
    assert state["status"] == "STOPPED"
    cycle = state["cycles"][0]
    assert cycle["attempt"]["status"] == "STOPPED" and cycle["phases"]["VERIFY"]["status"] == "SKIPPED"
    L.clear_controls(work)
    state = L.EvolutionLoop(work).run()
    assert state["cycles"][0]["outcome"] == "STOPPED" and state["cycles"][0]["status"] == L.CLOSED


def test_attempt_wall_clock_budget_times_out_keeps_evidence_and_moves_on(campaign):
    _repo, make = campaign
    cfg, work = make({"money": [{**GOOD_MONEY, "sleep_seconds": 60}], "price": [GOOD_PRICE]},
                     max_cycles=2, attempt_minutes=0.03)
    state = L.EvolutionLoop(work, cfg).run()
    first, second = state["cycles"]
    assert first["outcome"] == "TIMEOUT" and (work / "cycles" / first["id"] / "attempt" / "attempt.json").is_file()
    assert second["task_id"] == "price" and second["outcome"] == "ACCEPTED"


def test_consecutive_failures_auto_pause_and_retries_are_bounded(campaign):
    _repo, make = campaign
    cfg, work = make({"money": [BAD_PRICE], "price": [BAD_PRICE], "weight": [BAD_PRICE]},
                     max_cycles=5, max_consecutive_failures=2, max_retries_per_task=1)
    state = L.EvolutionLoop(work, cfg).run()
    assert state["status"] == "AUTO_PAUSED" and state["halt_reason"] == "MAX_CONSECUTIVE_FAILURES"
    assert (work / L.PAUSE_FILE).exists() and len(state["cycles"]) == 2
    L.clear_controls(work)
    state = L.EvolutionLoop(work).run()
    assert state["status"] == "QUEUE_EXHAUSTED"                    # every task used its one retry
    assert all(t["exhausted"] for t in state["tasks"].values())


def test_disk_and_total_time_budgets_halt(campaign):
    _repo, make = campaign
    cfg, work = make({"money": [GOOD_MONEY]}, max_disk_mb=0.001)
    state = L.EvolutionLoop(work, cfg).run()
    assert state["status"] == "AUTO_PAUSED" and state["halt_reason"] == "DISK_BUDGET"
    cfg, work2 = make({"money": [GOOD_MONEY]}, total_hours=1e-7)
    state = L.EvolutionLoop(work2.with_name("campaign-2"), cfg).run()
    assert state["status"] == "BUDGET_EXHAUSTED"


def test_live_lease_refuses_a_second_loop_and_a_dead_one_is_recovered(campaign):
    _repo, make = campaign
    cfg, work = make({"money": [GOOD_MONEY]}, max_cycles=1)
    work.mkdir(parents=True)
    (work / L.LEASE_FILE).write_text(json.dumps({**L._process_identity(os.getpid()), "host": L.socket.gethostname()}))
    with pytest.raises(L.LeaseHeld):
        L.EvolutionLoop(work, cfg).run()
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (work / L.LEASE_FILE).write_text(json.dumps({"pid": dead.pid, "create_time": 1.0, "host": L.socket.gethostname()}))
    state = L.EvolutionLoop(work, cfg).run()
    assert state["status"] == "COMPLETED" and state["lease_recoveries"][0]["stale_lease"]["pid"] == dead.pid


def test_hard_kill_in_a_replayable_phase_resumes_it_and_skips_completed_ones(campaign):
    _repo, make = campaign
    cfg, work = make({"money": [GOOD_MONEY]}, max_cycles=1)
    loop = L.EvolutionLoop(work, cfg)
    loop.work.mkdir(parents=True, exist_ok=True)
    loop.init_state()
    proc = child(work, hold="1:LEARN_WRITTEN")          # killed right after the lesson was written
    wait_for(work / L.HOLD_MARKER, proc)
    hard_kill(proc)
    killed = json.loads((work / L.STATE_FILE).read_text())
    assert killed["cycles"][0]["phases"]["LEARN"]["status"] == "IN_PROGRESS"
    assert (work / L.LEASE_FILE).exists()                              # the dead loop could not release it
    (work / L.HOLD_MARKER).unlink()
    state = L.EvolutionLoop(work).run()
    cycle = state["cycles"][0]
    assert state["status"] == "COMPLETED" and cycle["outcome"] == "ACCEPTED"
    assert cycle["phases"]["ATTEMPT"]["runs"] == 1                     # not replayed
    assert cycle["phases"]["LEARN"]["runs"] == 2 and cycle["phases"]["LEARN"]["resumed_after_crash"] == 1
    assert state["tasks"]["money"]["attempts"] == 1
    assert state["lease_recoveries"] and len(LearningStore(work / "learning").failed()) == 1


def test_hard_kill_during_an_attempt_is_unknown_outcome_never_replayed_unless_redo(campaign):
    _repo, make = campaign
    cfg, work = make({"money": [GOOD_MONEY, GOOD_MONEY], "price": [GOOD_PRICE]}, max_cycles=2)
    loop = L.EvolutionLoop(work, cfg)
    loop.work.mkdir(parents=True, exist_ok=True)
    loop.init_state()
    proc = child(work, hold="1:ATTEMPT_INFLIGHT")
    wait_for(work / L.HOLD_MARKER, proc)
    hard_kill(proc)
    (work / L.HOLD_MARKER).unlink()
    state = L.EvolutionLoop(work).run()
    first = state["cycles"][0]
    assert first["outcome"] == "UNKNOWN_OUTCOME" and first["phases"]["ATTEMPT"]["status"] == "UNKNOWN_OUTCOME"
    assert first["phases"]["VERIFY"]["status"] == "SKIPPED" and first["phases"]["ACCEPT"]["status"] == "SKIPPED"
    assert (work / "cycles" / first["id"] / "attempt").is_dir()        # kept for inspection
    assert state["cycles"][1]["task_id"] == "price"                    # moved on; money not replayed blindly
    assert state["tasks"]["money"]["attempts"] == 1
    # the owner decides to replay exactly that cycle
    redo = L.EvolutionLoop(work, overrides={"max_cycles": 2}).run(redo=first["id"])
    replayed = next(c for c in redo["cycles"] if c["id"] == first["id"])
    assert replayed["outcome"] == "ACCEPTED" and replayed["redo"]["phase"] == "ATTEMPT"
    with pytest.raises(ValueError):
        L.EvolutionLoop(work).redo(redo["cycles"][1]["id"])            # only UNKNOWN_OUTCOME cycles


def test_status_is_owner_readable_and_honest_about_a_dead_loop(campaign):
    _repo, make = campaign
    cfg, work = make({"money": [GOOD_MONEY]}, max_cycles=1)
    assert L.status(work)["status"] == "NO_CAMPAIGN"
    L.EvolutionLoop(work, cfg).run()
    st = L.status(work)
    assert st["status"] == "COMPLETED" and st["verifier_verdict"] == "PASS"
    assert st["model_kind"] == "MOCK_MODEL" and st["queue"]["money"]["solved"] is True
    assert st["patch"]["student_claim_is_untrusted"] is True and st["loop_running"] is False
    state = json.loads((work / L.STATE_FILE).read_text())
    state["status"] = "RUNNING"
    (work / L.STATE_FILE).write_text(json.dumps(state))
    assert L.status(work)["status"].startswith("INTERRUPTED")


def test_json_proposers_keep_the_docker_rule_and_cloud_needs_consent():
    with pytest.raises(ValueError, match="docker"):
        L.LoopConfig(suite="s", repo="r", backend="local", model="m", executor="host").validate()
    with pytest.raises(ValueError, match="allow-cloud"):
        L.LoopConfig(suite="s", repo="r", backend="claude", model="m", executor="docker").validate()
    L.LoopConfig(suite="s", repo="r", backend="bossman_coding").validate()


def test_recipe_built_from_a_verified_diff_is_executable_grammar(campaign, tmp_path):
    repo, make = campaign
    cfg, work = make({"money": [GOOD_MONEY]}, max_cycles=1)
    state = L.EvolutionLoop(work, cfg).run()
    cycle = state["cycles"][0]
    diff = (work / "cycles" / cycle["id"] / "attempt" / "attempt.diff").read_text(encoding="utf-8")
    case = next(c for c in fx.SUITE["cases"] if c["id"] == "money")
    recipe = L.build_recipe(case, cycle, diff, "evo-money-test")
    assert recipe["required_check"]["args"]["paths"] == ["tests/test_money.py", "tests/test_regress_money.py"]
    assert {s["tool"] for s in recipe["steps"]} >= {"write_file", "edit_file", "run_tests"}
    try:
        from bcc.features.coding_recipes import validate_recipe
    except ImportError:
        pytest.skip("bcc not installed here (root CI); the product grammar is checked in command-center tests")
    assert validate_recipe(recipe) == []
