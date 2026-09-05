"""CLOSURE-002 §11 — сквозной детерминированный E2E (первичная приёмка):

OWNER MISSION → Organization → команда + контракт → Fleet размещение → аренда + fence
→ локальный транспорт узла → V3 ExecutionBridge → реальный побочный эффект (файл с
уникальным содержимым) → свежее наблюдение → верификация → подписанная улика TaskJournal
→ flight VERIFIED → ребёнок VERIFIED → независимое ревью → родитель COMPLETE
→ событие бенчмарка (пассивный overlay) → улика в scorecard (--from-benchmark).

Отрицательный сценарий: исполнитель «пишет», но требуемого эффекта нет → ребёнок FAILED,
родитель НЕ COMPLETE, бенчмарк без hard fail'ов (false_success не зачтён). Рестарт,
fencing и приватность — в test_v3_fleet_e2e.py / test_fence_fl01.py (реестр:
test_v3_fleet_safety_proofs.py).
"""
from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import bossman._shared  # noqa: F401
from bossman_shared.action_receipt import ActionReceipt
from bossman_v3.benchmark_overlay import (BenchmarkCollector, BenchmarkScorer, events_from_fleet,
                                          events_from_organization, write_reports)
from bossman_v3.fleet import FlightState
from bossman_v3.memory.journal import TaskJournal
from bossman_v3.organization import MissionState, RiskTier, TaskState
from test_v3_fleet_e2e import Stack, _contract

ROOT = Path(__file__).resolve().parents[2]


def _scorecard_tools():
    spec = importlib.util.spec_from_file_location("upd", ROOT / "scripts" / "update_readme_scorecard.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _benchmark(s: Stack, mission_id: str):
    col = BenchmarkCollector()
    col.extend(events_from_organization(s.org.store, mission_id))
    col.extend(events_from_fleet(s.plane, s.org.store, mission_id))
    return BenchmarkScorer().score_report("v3-overlay", "e2e", "DETERMINISTIC", col.by_mission())


def test_cross_layer_positive_chain_to_scorecard_evidence(tmp_path):
    (tmp_path / "stack").mkdir()
    s = Stack(tmp_path / "stack"); w = s.world
    unique = f"payload-{uuid.uuid4().hex}"
    # уникальное содержимое: узел пишет его, а свежее чтение обязано вернуть ровно его
    c1, c2 = _contract(w, "w1", ["a.txt"]), _contract(w, "w2", ["b.txt"], deps=["w1"], risk=RiskTier.MEDIUM)
    for st in c1.steps:
        st["action"]["args"]["content"] = unique
    s.org.receive_mission("m1", title="cross-layer", department_id="engineering", contracts=[c1, c2])
    status = s.org.run_mission("m1")

    # родитель COMPLETE только когда оба ребёнка VERIFIED и прошли независимое ревью
    assert status.done and status.state == MissionState.COMPLETED.value and status.verified_results == ("w1", "w2")
    assert w.side_effects() == 2 and (w.root / "b.txt").exists()
    assert (w.root / "a.txt").read_text(encoding="utf-8") == unique                # свежее чтение = ровно то содержимое
    r1, r2 = s.org.store.result("w1"), s.org.store.result("w2")
    assert r1.verified and all(e.signature_valid() for e in r1.evidence)          # улики подписаны (EH-01)
    assert r2.reviewed_by == "rev" and r2.metadata["review"]["independent"] is True
    # fleet: полёт VERIFIED через законную цепочку, аренда с fence, мутации учтены один раз
    f1 = s.plane.flights.get("w1")
    assert f1.state == FlightState.VERIFIED and f1.fence >= 1 and f1.node_id in ("node-1", "node-2")
    assert "PLACED" in [h["to"] for h in f1.history] and "VERIFIED" == f1.history[-1]["to"]
    assert len(s.plane.store.verified_mutations()) == 2 and s.plane.flights.duplicate_preventions == 0
    # журнал V3: закрытые шаги подписаны; ActionReceipt (v2 E2E): fence, свежее post_state, verified
    j = TaskJournal.load(task_id="m1__w1", root=tmp_path / "stack" / "journals")
    assert all(st.signature_valid("m1__w1") for st in j.finished()) and len(j.finished()) == 1
    rec = ActionReceipt.from_dict(j.finished()[0].receipt)
    assert rec.fencing_token == f1.fence and rec.observation_type == "post_state" and rec.verified() and rec.fresh()[0]
    assert rec.executor_status == "executed" and rec.verification_status == "VERIFIED"
    # бенчмарк (пассивный) → отчёт → scorecard
    report = _benchmark(s, "m1")
    assert report.aggregate["hard_failures"] == [] and report.aggregate["verified_success_count"] == 1
    assert report.aggregate["local_execution_rate"] == 1.0 and report.aggregate["privacy_violation_count"] == 0
    jp, _ = write_reports(report, tmp_path / "bench")
    upd = _scorecard_tools()
    sc = tmp_path / "sc.json"; sc.write_text((ROOT / "docs/benchmark/current-scorecard.json").read_text(encoding="utf-8"), encoding="utf-8")
    readme = tmp_path / "README.md"; readme.write_text(f"{upd.START}\n{upd.END}\n", encoding="utf-8")
    assert upd.main(["--from-benchmark", str(jp), "--scorecard", str(sc), "--readme", str(readme), "--md", str(tmp_path / "s.md")]) == 0
    out = json.loads(sc.read_text(encoding="utf-8"))
    assert out["deterministic_counters"]["verified_success_count"] == 1 and out["benchmark_hard_failures"] == []
    assert "Benchmark hard failures:** none observed" in readme.read_text(encoding="utf-8")
    # durable-истина не изменилась от бенчмарка
    assert s.org.store.result("w1").to_dict() == r1.to_dict()


def test_cross_layer_effect_absent_child_failed_parent_not_complete(tmp_path):
    (tmp_path / "stack").mkdir()
    s = Stack(tmp_path / "stack"); w = s.world
    c = _contract(w, "w1", ["need.txt"], max_attempts=1)
    # инструмент честно отработает и создаст other.txt (узел это подтвердит), но КОНТРАКТ
    # требует улику need.txt — заявление «готово» без требуемого эффекта не проходит validate()
    c.steps[0]["action"]["args"]["name"] = "other.txt"
    c.steps[0]["action"]["args"]["expect"]["target"] = str(w.root / "other.txt")
    s.org.receive_mission("m1", title="absent", department_id="engineering", contracts=[c])
    status = s.org.run_mission("m1")
    assert not status.done and status.state != MissionState.COMPLETED.value
    assert s.org.store.work("w1")["state"] == TaskState.FAILED.value
    assert not (w.root / "need.txt").exists()
    r = s.org.store.result("w1")
    assert r.success is False and r.verified is False and r.executed is True          # EXECUTED, но не VERIFIED
    # receipt честно говорит: исполнено, пост-состояние other.txt подтверждено, но требуемого эффекта нет
    j = TaskJournal.load(task_id="m1__w1", root=tmp_path / "stack" / "journals")
    recs = [ActionReceipt.from_dict(st.receipt) for st in j.finished()]
    assert recs and all(x.executor_status == "executed" for x in recs)
    report = _benchmark(s, "m1")
    assert report.aggregate["hard_failures"] == [] and report.aggregate["verified_success_count"] == 0
    assert report.aggregate["false_success_count"] == 0                        # false success не зачтён никем
