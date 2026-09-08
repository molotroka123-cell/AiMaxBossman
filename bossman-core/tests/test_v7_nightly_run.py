"""The nightly driver must not be able to make an objective pass.

Its whole value is that a verdict comes from a suite's exit code or a number in
an evidence file, never from the driver. So most of this file is adversarial:
feed it a red gate, a missing evidence file, a corrupt one, an exhausted time
budget, and check that the verdict goes the way the evidence went — and that
"could not run" never quietly becomes "passed".
"""
from __future__ import annotations

import json
import subprocess

import pytest

from bossman.v7 import nightly_run as nr


# ------------------------------------------------------------------ arguments

@pytest.mark.parametrize("text,seconds", [
    ("8h", 8 * 3600), ("90m", 5400), ("600s", 600), ("600", 600),
    ("1d", 86400), (" 2H ", 7200), ("1.5h", 5400)])
def test_a_budget_can_be_written_the_way_people_write_it(text, seconds):
    assert nr.parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "soon", "-5m", "0", "8 hours", "h"])
def test_a_budget_that_means_nothing_is_refused(text):
    """An unparsed budget silently becoming "no budget" is how a nightly run
    hangs until morning."""
    with pytest.raises(Exception):
        nr.parse_duration(text)


def test_the_brief_s_own_objective_list_is_understood():
    assert nr.select("B2,B3,B4,B5,P0") == ["P0", "B3", "B4", "B5", "B2"]


def test_objectives_run_in_canonical_order_not_typed_order():
    """The P0 goes first: a jammed queue turns every later measurement into a
    measurement of the jam."""
    assert nr.select("B2,P0")[0] == "P0"
    assert nr.select("ux,p0,b4") == ["P0", "B4", "UX"]


def test_all_means_all_and_is_the_default():
    assert nr.select("all") == nr.ALL == nr.select("")


def test_a_typo_is_refused_rather_than_silently_dropped():
    """Dropping an unknown objective would report "everything passed" for a run
    that never looked at what was asked for."""
    with pytest.raises(Exception) as exc:
        nr.select("B2,B9")
    assert "B9" in str(exc.value)


def test_every_advertised_objective_actually_exists():
    assert set(nr.ALL) == set(nr.OBJECTIVES)
    for key in nr.ALL:
        assert nr.OBJECTIVES[key].gates, key


# ------------------------------------------------------------------- roll-up

def test_one_red_gate_makes_the_objective_red():
    assert nr.roll_up([nr.Result("a", nr.PASS, ""), nr.Result("b", nr.FAIL, "")]) == nr.FAIL


def test_an_unverified_gate_is_not_a_passed_one():
    assert nr.roll_up([nr.Result("a", nr.PASS, ""), nr.Result("b", nr.NOT_RUN, "")]) == nr.NOT_RUN


def test_a_red_gate_outranks_an_unverified_one():
    assert nr.roll_up([nr.Result("a", nr.NOT_RUN, ""), nr.Result("b", nr.FAIL, "")]) == nr.FAIL


def test_an_objective_with_no_gates_never_passes():
    assert nr.roll_up([]) == nr.NOT_RUN


# --------------------------------------------------------------- gate running

def _proc(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def test_a_failing_suite_is_reported_failing(tmp_path, monkeypatch):
    monkeypatch.setattr(nr.subprocess, "run",
                        lambda *a, **k: _proc(1, "1 failed, 40 passed"))
    result = nr.pytest_gate("s", "tests/x.py").run(tmp_path, 60)
    assert result.status == nr.FAIL and "1 failed" in result.detail


def test_a_suite_that_could_not_be_collected_is_not_a_failure(tmp_path, monkeypatch):
    """pytest exits 4 on a usage error and 2 on a collection error. Calling that
    FAIL hides a missing dependency behind a red result nobody can act on."""
    monkeypatch.setattr(nr.subprocess, "run",
                        lambda *a, **k: _proc(4, "", "ERROR: file not found"))
    assert nr.pytest_gate("s", "tests/x.py").run(tmp_path, 60).status == nr.NOT_RUN


def test_a_missing_dependency_is_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(nr.subprocess, "run",
                        lambda *a, **k: _proc(1, "", "ModuleNotFoundError: No module named 'bcc'"))
    assert nr.pytest_gate("s", "tests/x.py").run(tmp_path, 60).status == nr.NOT_RUN


def test_a_gate_that_overruns_the_budget_fails(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=k.get("timeout", 0))
    monkeypatch.setattr(nr.subprocess, "run", boom)
    result = nr.pytest_gate("s", "tests/x.py").run(tmp_path, 30)
    assert result.status == nr.FAIL and "30s" in result.detail


def test_a_gate_reached_with_no_budget_left_is_not_run(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(nr.subprocess, "run", lambda *a, **k: called.append(1))
    assert nr.pytest_gate("s", "tests/x.py").run(tmp_path, 0).status == nr.NOT_RUN
    assert not called, "gate ran despite an exhausted budget"


def test_a_green_suite_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(nr.subprocess, "run", lambda *a, **k: _proc(0, "47 passed"))
    result = nr.pytest_gate("s", "tests/x.py").run(tmp_path, 60)
    assert result.status == nr.PASS and "47 passed" in result.detail


# ------------------------------------------------------------ evidence gates

def _write(root, name, payload):
    path = root / "docs" / "testing" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _metrics(root, **overrides):
    benchmarks = [
        {"benchmark": "review_deadlock", "reproduced": 4, "deadlocked_after_sweep": 0,
         "review_deadlock_rate": 0.0},
        {"benchmark": "recovery_on_expired_key", "provider_calls": 1, "retry_budget": 5,
         "escalated_to_owner": True, "pass": True},
        {"benchmark": "doc_edit/read_modify_verify", "approvals_asked": 2,
         "distinct_effect_classes": ["read", "write"], "same_effect_asked_twice": False,
         "approvals_pass": True, "tokens_total": 1840, "tokens_pass": True, "completed": True},
        {"benchmark": "doc_edit/single_write", "approvals_asked": 1,
         "distinct_effect_classes": ["write"], "same_effect_asked_twice": False,
         "approvals_pass": True, "tokens_total": 920, "tokens_pass": True, "completed": True},
    ]
    for row in benchmarks:
        row.update(overrides.get(row["benchmark"], {}))
    _write(root, "convergence-metrics-20260908.json", {"benchmarks": benchmarks})


def test_the_token_gate_refuses_a_failed_run_however_small_its_number(tmp_path):
    """Аудит §7: PASS принимал status=failed. Число у неоконченной работы — не результат."""
    _metrics(tmp_path, **{"doc_edit/single_write": {"completed": False, "tokens_total": 5}})
    status, detail = nr._verify_tokens(tmp_path)
    assert status == nr.FAIL


def test_the_token_gate_refuses_a_completed_run_whose_document_was_not_verified(tmp_path):
    """Новое свидетельство несёт `success`; completed без документа на диске — не успех."""
    _metrics(tmp_path, **{"doc_edit/single_write": {"completed": True, "success": False}})
    assert nr._verify_tokens(tmp_path)[0] == nr.FAIL
    _metrics(tmp_path, **{"doc_edit/single_write": {"completed": True, "success": True}})
    status, detail = nr._verify_tokens(tmp_path)
    assert status == nr.PASS
    assert "не A/B" in detail and "синтетический" in detail


def test_a_zero_deadlock_rate_passes(tmp_path):
    _metrics(tmp_path)
    assert nr._verify_deadlock_rate(tmp_path)[0] == nr.PASS


def test_a_single_surviving_deadlock_fails(tmp_path):
    _metrics(tmp_path, **{"review_deadlock": {"review_deadlock_rate": 0.25,
                                              "deadlocked_after_sweep": 1}})
    status, detail = nr._verify_deadlock_rate(tmp_path)
    assert status == nr.FAIL and "0.25" in detail


def test_two_questions_for_two_real_effects_pass(tmp_path):
    """The brief's target is 0-1 approvals UNLESS policy requires more for a
    specific real effect. A read lease must not cover a write."""
    _metrics(tmp_path)
    status, detail = nr._verify_approvals(tmp_path)
    assert status == nr.PASS and "read+write" in detail


def test_the_same_effect_asked_twice_is_the_storm_and_fails(tmp_path):
    _metrics(tmp_path, **{"doc_edit/single_write": {
        "approvals_asked": 2, "distinct_effect_classes": ["write"],
        "same_effect_asked_twice": True, "approvals_pass": False}})
    assert nr._verify_approvals(tmp_path)[0] == nr.FAIL


def test_a_task_that_never_finished_is_not_a_token_result(tmp_path):
    """1 840 tokens for work that stayed parked is not a measurement."""
    _metrics(tmp_path, **{"doc_edit/single_write": {"completed": False}})
    assert nr._verify_tokens(tmp_path)[0] == nr.FAIL


def test_a_run_that_burned_its_retries_instead_of_asking_fails(tmp_path):
    _metrics(tmp_path, **{"recovery_on_expired_key": {"escalated_to_owner": False,
                                                      "pass": False}})
    assert nr._verify_recovery(tmp_path)[0] == nr.FAIL


def _qa(root, **overrides):
    payload = {"systems": [{"system": s, "status": "ok"} for s in
                           ("video_studio.smoke", "web_designer.smoke",
                            "apps.smoke", "health.smoke")],
               "negative_controls_pass": True, "manual_interventions_total": 0,
               "verdict": "PASS"}
    payload.update(overrides)
    _write(root, "three-system-qa-20260908.json", payload)


def test_three_systems_answering_without_a_human_passes(tmp_path):
    _qa(tmp_path)
    assert nr._verify_three_system(tmp_path)[0] == nr.PASS


def test_a_run_a_human_had_to_rescue_fails(tmp_path):
    """The acceptance question was whether agents get there on their own."""
    _qa(tmp_path, manual_interventions_total=1)
    assert nr._verify_three_system(tmp_path)[0] == nr.FAIL


def test_a_bridge_that_stopped_refusing_what_it_must_refuse_fails(tmp_path):
    _qa(tmp_path, negative_controls_pass=False)
    assert nr._verify_three_system(tmp_path)[0] == nr.FAIL


def test_missing_evidence_is_not_run_rather_than_passed(tmp_path):
    gate = nr.Gate("g", verify=nr._verify_three_system)
    result = gate.run(tmp_path, 60)
    assert result.status == nr.NOT_RUN and "доказательств" in result.detail


def test_corrupt_evidence_fails_rather_than_passing(tmp_path):
    path = tmp_path / "docs" / "testing" / "three-system-qa-20260908.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert nr.Gate("g", verify=nr._verify_three_system).run(tmp_path, 60).status == nr.FAIL


def test_evidence_missing_the_field_being_judged_fails(tmp_path):
    _qa(tmp_path)
    payload = json.loads((tmp_path / "docs/testing/three-system-qa-20260908.json").read_text())
    del payload["manual_interventions_total"]
    _write(tmp_path, "three-system-qa-20260908.json", payload)
    assert nr.Gate("g", verify=nr._verify_three_system).run(tmp_path, 60).status == nr.FAIL


# ------------------------------------------------------------------ the run

def test_the_verdict_follows_the_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(nr.subprocess, "run", lambda *a, **k: _proc(0, "ok"))
    monkeypatch.setitem(nr.OBJECTIVES, "B4", nr.Objective("B4", "t", [nr.pytest_gate("s", "x")]))
    summary = nr.run(["B4"], root=tmp_path, budget=60, echo=lambda *_: None)
    assert summary["verdict"] == nr.PASS and summary["passed"] == 1

    monkeypatch.setattr(nr.subprocess, "run", lambda *a, **k: _proc(1, "1 failed"))
    summary = nr.run(["B4"], root=tmp_path, budget=60, echo=lambda *_: None)
    assert summary["verdict"] == nr.FAIL and summary["passed"] == 0


def test_an_unverifiable_objective_does_not_count_as_passed(tmp_path, monkeypatch):
    monkeypatch.setitem(nr.OBJECTIVES, "B4",
                        nr.Objective("B4", "t", [nr.Gate("g", verify=nr._verify_three_system)]))
    summary = nr.run(["B4"], root=tmp_path, budget=60, echo=lambda *_: None)
    assert summary["objectives"][0]["status"] == nr.NOT_RUN
    assert summary["verdict"] == nr.FAIL


def test_the_summary_records_every_gate_not_just_the_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(nr.subprocess, "run", lambda *a, **k: _proc(0, "47 passed"))
    monkeypatch.setitem(nr.OBJECTIVES, "B4", nr.Objective(
        "B4", "t", [nr.pytest_gate("one", "x"), nr.pytest_gate("two", "y")]))
    summary = nr.run(["B4"], root=tmp_path, budget=60, echo=lambda *_: None)
    assert [g["gate"] for g in summary["objectives"][0]["gates"]] == ["one", "two"]
    assert all("47 passed" in g["detail"] for g in summary["objectives"][0]["gates"])


def test_the_budget_is_shared_across_gates(tmp_path, monkeypatch):
    """A per-gate budget would let ten gates take ten times the stated limit."""
    seen = []

    def slow(*a, **k):
        seen.append(k["timeout"])
        return _proc(0, "ok")

    monkeypatch.setattr(nr.subprocess, "run", slow)
    monkeypatch.setattr(nr.time, "time", _clock([0, 0, 10, 10, 25, 25, 40]))
    monkeypatch.setitem(nr.OBJECTIVES, "B4", nr.Objective(
        "B4", "t", [nr.pytest_gate(n, "x") for n in ("a", "b", "c")]))
    nr.run(["B4"], root=tmp_path, budget=60, echo=lambda *_: None)
    assert seen == sorted(seen, reverse=True), seen


def _clock(values):
    ticks = list(values)

    def now():
        return ticks.pop(0) if ticks else 999
    return now


def test_the_checkout_is_found_by_what_it_contains(tmp_path):
    (tmp_path / "command-center").mkdir()
    (tmp_path / "bossman-core" / "bossman" / "v7").mkdir(parents=True)
    deep = tmp_path / "bossman-core" / "bossman" / "v7" / "nightly_run.py"
    deep.write_text("", encoding="utf-8")
    assert nr.repo_root(deep) == tmp_path.resolve()


def test_a_directory_that_is_not_the_checkout_is_refused(tmp_path):
    with pytest.raises(RuntimeError):
        nr.repo_root(tmp_path / "nowhere" / "x.py")


def test_list_and_dry_run_do_not_execute_anything(monkeypatch, capsys):
    monkeypatch.setattr(nr.subprocess, "run",
                        lambda *a, **k: pytest.fail("dry run executed a gate"))
    assert nr.main(["--list"]) == 0
    assert "P0" in capsys.readouterr().out
    assert nr.main(["--objectives", "B2,B3", "--dry-run"]) == 0
    assert "three_system_qa" in capsys.readouterr().out


# ------------------------------------------------- yesterday's numbers

def test_evidence_left_over_from_an_earlier_run_is_not_this_run_s_result(tmp_path, monkeypatch):
    """A stale file reads exactly like a successful one. The P0 gate that
    "measured" a deadlock rate from last night has measured nothing."""
    _qa(tmp_path)
    import os
    stale = tmp_path / "docs/testing/three-system-qa-20260908.json"
    os.utime(stale, (1, 1))
    monkeypatch.setattr(nr.subprocess, "run", lambda *a, **k: _proc(0, "ok"))
    gate = nr.Gate("g", ("-m", "x"), verify=nr._verify_three_system,
                   fresh="docs/testing/three-system-qa-20260908.json")
    result = gate.run(tmp_path, 60)
    assert result.status == nr.NOT_RUN and "старые" in result.detail


def test_fresh_evidence_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(nr.subprocess, "run", lambda *a, **k: (_qa(tmp_path), _proc(0, "ok"))[1])
    gate = nr.Gate("g", ("-m", "x"), verify=nr._verify_three_system,
                   fresh="docs/testing/three-system-qa-20260908.json")
    assert gate.run(tmp_path, 60).status == nr.PASS


def test_a_gate_may_judge_its_own_number_instead_of_the_script_s_verdict(tmp_path, monkeypatch):
    """convergence_metrics reports one verdict for several benchmarks. The P0
    gate asks whether the deadlock rate is zero — not whether every other
    benchmark in the same file also passed."""
    def write_then_fail(*a, **k):
        _metrics(tmp_path)
        return _proc(1, "verdict FAIL")

    monkeypatch.setattr(nr.subprocess, "run", write_then_fail)
    gate = nr.Gate("g", ("-m", "x"), verify=nr._verify_deadlock_rate, judge_exit=False,
                   fresh="docs/testing/convergence-metrics-20260908.json")
    assert gate.run(tmp_path, 60).status == nr.PASS


def test_a_crash_is_still_caught_when_the_exit_code_is_not_judged(tmp_path, monkeypatch):
    """judge_exit=False must not become "ignore whether the script ran"."""
    monkeypatch.setattr(nr.subprocess, "run", lambda *a, **k: _proc(1, "traceback"))
    gate = nr.Gate("g", ("-m", "x"), verify=nr._verify_deadlock_rate, judge_exit=False,
                   fresh="docs/testing/convergence-metrics-20260908.json")
    assert gate.run(tmp_path, 60).status == nr.NOT_RUN


def test_a_missing_module_is_still_not_run_even_when_the_exit_code_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(nr.subprocess, "run",
                        lambda *a, **k: _proc(1, "", "ModuleNotFoundError: bcc"))
    gate = nr.Gate("g", ("-m", "x"), verify=nr._verify_deadlock_rate, judge_exit=False)
    assert gate.run(tmp_path, 60).status == nr.NOT_RUN


def test_the_p0_and_relay_gates_actually_demand_fresh_evidence():
    """Wiring check: the guard is worthless if the real gates skip it."""
    for key in ("P0", "B2"):
        producing = [g for g in nr.OBJECTIVES[key].gates if g.argv and g.verify]
        assert producing, key
        assert all(g.fresh for g in producing), key
