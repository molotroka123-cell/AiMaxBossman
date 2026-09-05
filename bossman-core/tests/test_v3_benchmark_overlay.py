"""Пассивный benchmark overlay: hard-fail gate, скорер, отчёт, baseline, адаптеры
durable-истины и мост в scorecard. Событие бенчмарка ≠ доказательство."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from bossman_v3.benchmark_overlay import (HARD_FAILS, BenchmarkCollector, BenchmarkEvent, BenchmarkReport,
                                          BenchmarkScorer, HardFailGate, compare_reports, events_from_organization,
                                          events_from_task_journal, write_reports)
from bossman_v3.memory.journal import TaskJournal
from test_v3_organization_e2e import Org, _contract  # noqa: E402  (реальная организация из E2E)

ROOT = Path(__file__).resolve().parents[2]


def _e(kind, **data):
    return BenchmarkEvent(kind, "m", 0.0, data, source="test")


def test_hard_fail_gate_detects_each_required_failure():
    gate = HardFailGate()
    cases = {
        "false_success": [_e("mission.completed", side_effect_required=True, verified_side_effect=False)],
        "duplicate_side_effect": [_e("side_effect.executed", idempotency_key="k"), _e("side_effect.executed", idempotency_key="k")],
        "privacy_violation": [_e("privacy.violation")],
        "permission_bypass": [_e("permission.bypass")],
        "parent_success_with_failed_child": [_e("parent.completed", failed_required_children=["w2"])],
        "stale_evidence_accepted": [_e("verification.accepted", evidence_age_s=10_000)],
        "review_bypass": [_e("review.bypass")],
        "scope_leak": [_e("scope.leak")],
        "treasury_overrun": [_e("treasury.overrun")],
    }
    assert set(cases) == set(HARD_FAILS)
    for name, events in cases.items():
        assert gate.evaluate(events) == [name], name
    assert gate.evaluate([_e("side_effect.executed", idempotency_key="k"), _e("side_effect.executed", idempotency_key="j")]) == []
    assert gate.evaluate([_e("verification.accepted", signature_valid=False)]) == ["stale_evidence_accepted"]


def test_scorer_verified_mission_vs_false_success_and_zero_cost_metric():
    s = BenchmarkScorer()
    good = [_e("mission.interpreted", constraints_preserved=True), _e("organization.selected", team_fit="good", team_size=2, executors=1),
            _e("side_effect.executed", idempotency_key="a"), _e("verification.completed", verified=True)]
    ms = s.score_mission("m", good)
    assert ms.verified_success and ms.hard_failures == [] and ms.scores["verification_truth"] == 10.0
    bad = good + [_e("mission.completed", side_effect_required=True, verified_side_effect=False)]
    mb = s.score_mission("m", bad)
    assert not mb.verified_success and mb.hard_failures == ["false_success"] and mb.total == 0.0
    rep = s.score_report("v", "sha", "DETERMINISTIC", {"m1": good, "m2": bad})
    a = rep.aggregate
    assert a["mission_count"] == 2 and a["verified_success_count"] == 1 and a["false_success_count"] == 1
    assert a["cost_per_verified_success"] == 0.0 and a["token_value_metric"] is None      # нулевая стоимость → N/A, не деление
    assert BenchmarkScorer.token_value_metric(quality=0.8, reliability=0.9, cost=2.0) == 0.36
    assert "total_score_secondary" in a and "total_score" not in a


def test_report_files_and_baseline_regression(tmp_path):
    s = BenchmarkScorer()
    base = s.score_report("v", "aaa", "DETERMINISTIC", {"m": [_e("side_effect.executed", idempotency_key="a"), _e("verification.completed", verified=True)]})
    cur = s.score_report("v", "bbb", "DETERMINISTIC", {"m": [_e("privacy.violation")]})
    jp, mp = write_reports(cur, tmp_path)
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["aggregate"]["hard_failures"] == ["privacy_violation"] and "N/A" in mp.read_text(encoding="utf-8")
    r = compare_reports(base, cur)
    assert r.result == "CRITICAL" and r.hard_fail_delta == 1
    assert compare_reports(base, base).result == "PASS"


def test_adapters_read_only_from_real_organization_and_feed_scorecard(tmp_path):
    (tmp_path / "org").mkdir()
    org = Org(tmp_path / "org")
    from bossman_v3.organization import EXECUTOR, REVIEWER, AgentProfile, Department, Resources
    rt = org.runtime
    rt.set_organization_budget(Resources(usd=100))
    rt.register_department(Department("engineering", capabilities={"fs.write"}, budget=Resources(usd=10, tokens=100_000, compute_seconds=3600)))
    rt.register_agent(AgentProfile("coder", "engineering", {EXECUTOR}, {"fs.write"}, tier="local_small", model="glm"))
    rt.register_agent(AgentProfile("rev", "engineering", {REVIEWER}, {"fs.write"}, tier="local_small", model="llama"))
    w = org.world
    rt.receive_mission("m1", title="x", department_id="engineering", contracts=[_contract(w, "w1", ["a.txt"])])
    status = rt.run_mission("m1")
    assert status.done
    before = (rt.store.works("m1"), [r.to_dict() for r in rt.store.results("m1")])

    events = events_from_organization(rt.store, "m1")
    kinds = {e.kind for e in events}
    assert {"mission.interpreted", "organization.selected", "verification.completed", "side_effect.executed",
            "mission.completed", "parent.completed"} <= kinds
    assert all(e.source == "adapter:organization" for e in events)
    # адаптер ничего не изменил в durable-истине
    assert (rt.store.works("m1"), [r.to_dict() for r in rt.store.results("m1")]) == before
    col = BenchmarkCollector(); col.extend(events)
    report = BenchmarkScorer().score_report("v3-overlay", "test-sha", "DETERMINISTIC", col.by_mission())
    assert report.aggregate["hard_failures"] == [] and report.aggregate["verified_success_count"] == 1
    jp, _ = write_reports(report, tmp_path / "bench")

    # мост: benchmark-report.json → current-scorecard.json → README-блок
    spec = importlib.util.spec_from_file_location("upd", ROOT / "scripts" / "update_readme_scorecard.py")
    upd = importlib.util.module_from_spec(spec); spec.loader.exec_module(upd)  # type: ignore[union-attr]
    sc = tmp_path / "scorecard.json"; sc.write_text((ROOT / "docs" / "benchmark" / "current-scorecard.json").read_text(encoding="utf-8"), encoding="utf-8")
    readme = tmp_path / "README.md"; readme.write_text(f"{upd.START}\n{upd.END}\n", encoding="utf-8")
    assert upd.main(["--from-benchmark", str(jp), "--scorecard", str(sc), "--readme", str(readme), "--md", str(tmp_path / "s.md")]) == 0
    out = json.loads(sc.read_text(encoding="utf-8"))
    assert out["deterministic_counters"]["false_success_count"] == 0 and out["benchmark_report"]["git_sha"] == "test-sha"
    assert "duplicate_side_effect_count" in out["deterministic_counters"]
    # hard fail из отчёта понижает связанную ось и не даёт VERIFIED
    bad = BenchmarkScorer().score_report("v3-overlay", "test-sha", "DETERMINISTIC", {"m9": [_e("privacy.violation")]})
    jp2, _ = write_reports(bad, tmp_path / "bench2")
    assert upd.main(["--from-benchmark", str(jp2), "--scorecard", str(sc), "--readme", str(readme), "--md", str(tmp_path / "s.md")]) == 0
    out = json.loads(sc.read_text(encoding="utf-8"))
    sec = next(c for c in out["categories"] if c["category"] == "Security")
    assert sec["score"] <= 6.0 and sec["status"] != "VERIFIED" and "privacy_violation" in out["benchmark_hard_failures"]


def test_task_journal_adapter_counts_replays_as_duplicates(tmp_path):
    j = TaskJournal.start(task_id="t", plan=[("s1", "a"), ("s2", "b")], root=tmp_path)
    j.record("s1", receipt={"x": 1}, verified=True)
    ev = events_from_task_journal(j, "m")
    assert [e.kind for e in ev] == ["side_effect.executed", "verification.accepted"] and ev[0].data["signed"] is True
    assert HardFailGate().evaluate(ev) == []
    assert HardFailGate().evaluate(events_from_task_journal(j, "m", replayed_steps=["s1"])) == ["duplicate_side_effect"]
