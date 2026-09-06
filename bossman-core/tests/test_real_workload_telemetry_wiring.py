"""Автоматическая телеметрия реальных нагрузок на терминальной границе исполнения.

Пока запись создавалась вручную, аудит железа опирался на добросовестность
оператора. Здесь проверяется обратное: выборка появляется сама, ровно одна на
терминальную задачу, и НИ ОДИН её сбой не меняет истину задачи.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bossman_v3.computer_agent.agent import UniversalComputerAgent
from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt, Observation,
                                  PolicyDecision, SideEffectClass, TypedAction,
                                  VerificationResult)
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.execution import telemetry as tm
from bossman_v3.memory import TaskJournal


class _Service:
    """Детерминированный исполнитель: `mode` задаёт, чем кончится шаг."""

    def __init__(self, root: Path, mode: str = "ok"):
        self.root, self.mode = root, mode

    def authorize(self, action, context):
        return PolicyDecision(self.mode != "denied", reason="fixture policy")

    def request(self, action, policy, context):
        return ApprovalDecision(False, reason="fixture grants no extra authority")

    def supports(self, action_type):
        return action_type == "demo.write"

    def execute(self, action):
        started = datetime.now(timezone.utc)
        if self.mode == "raise":
            raise RuntimeError("executor exploded")
        (self.root / action.args["name"]).write_text("x", encoding="utf-8")
        return ExecutionReceipt(action.action_type, started, datetime.now(timezone.utc),
                                effect_id=action.args["name"])

    def observe_fresh(self, action, receipt):
        return Observation(datetime.now(timezone.utc), "fs",
                           {"exists": (self.root / action.args["name"]).exists()})

    def verify(self, action, receipt, observation):
        if self.mode == "unverified":
            return VerificationResult(False, reason="fixture refuses to verify")
        return VerificationResult(bool(observation.state["exists"]), reason="file must exist")


def _plan(n: int = 1):
    return [PlanStep(f"s{i}", f"write s{i}",
                     TypedAction("demo.write", {"name": f"s{i}.txt"},
                                 side_effect=SideEffectClass.IDEMPOTENT_WRITE))
            for i in range(1, n + 1)]


def _run(tmp_path, mode="ok", task_id="tele", context=None, plan=None):
    work = tmp_path / "work"; work.mkdir(exist_ok=True)
    plan = plan or _plan()
    journal = TaskJournal.start(task_id=task_id, root=tmp_path / "journals",
                                plan=[(s.step_id, s.intent) for s in plan])
    service = _Service(work, mode)
    runner = CompoundRunner(UniversalComputerAgent(service, service, service, service, service),
                            journal, model="fixture-model")
    return runner, runner.run(plan, dict(context or {}))


def _corpus(tmp_path) -> list[dict]:
    path = Path(os.environ[tm.ENV_ROOT]) / tm.CORPUS_NAME
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ------------------------------------------------------- терминальные статусы

def test_a_verified_success_records_itself(tmp_path):
    runner, result = _run(tmp_path)
    assert result.completed is True
    rows = _corpus(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert (row["status"], row["verified"]) == ("passed", True)
    assert row["task_id"] == "tele" and row["schema_version"] == tm.SCHEMA_VERSION
    assert row["workload_family"] == "demo"
    assert row["duration_s"] >= 0
    assert row["provenance"]["source"] == "task_journal"


def test_a_failed_run_records_itself_as_failed(tmp_path):
    runner, result = _run(tmp_path, mode="unverified")
    assert result.completed is False
    rows = _corpus(tmp_path)
    assert len(rows) == 1 and rows[0]["status"] == "failed" and rows[0]["verified"] is False


def test_a_denied_run_records_itself_and_is_not_passed(tmp_path):
    """Отказ политики — это терминальная правда задачи, а не отсутствие выборки."""
    runner, result = _run(tmp_path, mode="denied")
    assert result.completed is False
    rows = _corpus(tmp_path)
    assert len(rows) == 1 and rows[0]["verified"] is False
    assert rows[0]["status"] in ("failed", "blocked")


def test_a_blocked_run_records_itself(tmp_path):
    """Незапущенный обязательный шаг из-за guard'а: ни одного FAILED шага — blocked."""
    plan = [PlanStep("a", "guarded", TypedAction("demo.write", {"name": "a.txt"},
                                                 side_effect=SideEffectClass.IDEMPOTENT_WRITE),
                     guard="missing")]
    runner, result = _run(tmp_path, plan=plan)
    assert result.completed is False
    rows = _corpus(tmp_path)
    assert len(rows) == 1 and rows[0]["status"] == "blocked" and rows[0]["verified"] is False


# ---------------------------------------------------------------- идемпотентность

def test_a_restart_does_not_duplicate_the_sample(tmp_path):
    """Возобновление того же прогона уточняет выборку, а не удваивает её."""
    work = tmp_path / "work"; work.mkdir()
    plan = _plan(2)
    root = tmp_path / "journals"
    j1 = TaskJournal.start(task_id="tele", root=root, plan=[(s.step_id, s.intent) for s in plan])
    bad = _Service(work, "unverified")
    CompoundRunner(UniversalComputerAgent(bad, bad, bad, bad, bad), j1, model="m").run(plan)
    assert len(_corpus(tmp_path)) == 1 and _corpus(tmp_path)[0]["status"] == "failed"

    j2 = TaskJournal.load(task_id="tele", root=root)
    good = _Service(work, "ok")
    CompoundRunner(UniversalComputerAgent(good, good, good, good, good), j2, model="m").run(plan)
    rows = _corpus(tmp_path)
    assert len(rows) == 1, "одна терминальная задача — одна выборка"
    assert rows[0]["status"] == "passed"
    # Провал не исчез бесследно: он остался в истории уточнений.
    assert [x["status"] for x in rows[0]["supersedes"]] == ["failed"]


def test_re_recording_an_identical_terminal_state_is_a_no_op(tmp_path):
    runner, _ = _run(tmp_path)
    again = tm.record_terminal_run(runner.journal, completed=True, plan=_plan())
    assert again.written is False and again.reason == "duplicate"
    assert len(_corpus(tmp_path)) == 1


# ------------------------------------------------------------------ надёжность

def _worker(root: str, index: int) -> None:
    os.environ[tm.ENV_ROOT] = root

    class _J:
        task_id = f"task-{index}"
        plan_digest = "d"
        created_at = datetime.now(timezone.utc).isoformat()
        steps: list = []
    tm.record_terminal_run(_J(), completed=False)


def test_parallel_completions_do_not_corrupt_the_corpus(tmp_path):
    """20 процессов, завершившихся одновременно, дают 20 читаемых строк."""
    root = os.environ[tm.ENV_ROOT]
    ctx = mp.get_context("spawn" if os.name == "nt" else "fork")
    procs = [ctx.Process(target=_worker, args=(root, i)) for i in range(20)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    rows = _corpus(tmp_path)
    assert len(rows) == 20, f"потеряны выборки: {len(rows)}"
    assert len({r["task_id"] for r in rows}) == 20
    assert all(r["record_type"] == tm.RECORD_TYPE for r in rows)


def test_a_telemetry_write_failure_does_not_change_the_mission_outcome(tmp_path, monkeypatch):
    """Главный инвариант: наблюдение наблюдательно."""
    def explode(*a, **k):
        raise OSError("disk on fire")
    monkeypatch.setattr(tm, "_atomic_write_all", explode)
    runner, result = _run(tmp_path)
    assert result.completed is True, "сбой телеметрии не имеет права уронить задачу"
    assert runner.last_telemetry.written is False
    assert "disk on fire" in runner.last_telemetry.error
    diagnostics = Path(os.environ[tm.ENV_ROOT]) / tm.DIAGNOSTICS_NAME
    assert diagnostics.exists(), "сбой обязан быть виден в диагностике"
    assert "telemetry_write_failed" in diagnostics.read_text(encoding="utf-8")


def test_a_defective_observer_still_cannot_fail_the_task(tmp_path, monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("observer itself is broken")
    monkeypatch.setattr(tm, "record_terminal_run", explode)
    monkeypatch.setattr("bossman_v3.execution.compound.record_terminal_run", explode)
    runner, result = _run(tmp_path)
    assert result.completed is True
    assert runner.last_telemetry.written is False


def test_a_corrupt_corpus_line_is_counted_not_swallowed(tmp_path):
    path = Path(os.environ[tm.ENV_ROOT])
    path.mkdir(parents=True, exist_ok=True)
    (path / tm.CORPUS_NAME).write_text("{not json\n", encoding="utf-8")
    runner, result = _run(tmp_path)
    assert result.completed is True
    diagnostics = (path / tm.DIAGNOSTICS_NAME).read_text(encoding="utf-8")
    assert "corpus_corrupt_lines" in diagnostics


# --------------------------------------------------------------- содержимое

def test_unmeasured_facts_are_absent_not_zero(tmp_path):
    """Ноль читается потребителем как ИЗМЕРЕННЫЙ ноль. Неизмеренного быть не должно."""
    _run(tmp_path)
    row = _corpus(tmp_path)[0]
    assert "peak_memory_gb" not in row
    assert "oom" not in row
    assert "cost_usd" not in row


def test_cost_requires_provider_evidence(tmp_path):
    _run(tmp_path, task_id="no-evidence", context={"cost_usd": 4.2})
    assert "cost_usd" not in [r for r in _corpus(tmp_path) if r["task_id"] == "no-evidence"][0]
    _run(tmp_path, task_id="with-evidence",
         context={"cost_usd": 4.2, "cost_evidence": "provider-invoice-7"})
    row = [r for r in _corpus(tmp_path) if r["task_id"] == "with-evidence"][0]
    assert row["cost_usd"] == 4.2 and row["cost_evidence"] == "provider-invoice-7"


def test_the_record_is_bounded_and_leaks_no_prompt(tmp_path):
    secret = "sk-live-" + "A" * 5000
    _run(tmp_path, context={"terminal_reason": secret, "prompt": secret,
                            "messages": [secret], "api_key": secret})
    raw = (Path(os.environ[tm.ENV_ROOT]) / tm.CORPUS_NAME).read_bytes()
    assert len(raw) <= tm.MAX_RECORD_BYTES + 512
    assert b"A" * 400 not in raw, "длинный текст обязан быть обрезан"
    row = _corpus(tmp_path)[0]
    assert "prompt" not in row and "messages" not in row and "api_key" not in row


def test_the_host_is_identified_without_leaking_its_name(tmp_path):
    import platform
    _run(tmp_path)
    row = _corpus(tmp_path)[0]
    fp = row["provenance"]["host_fingerprint"]
    assert len(fp) == 16 and platform.node() not in json.dumps(row)


def test_the_corpus_path_cannot_escape_its_root(tmp_path):
    root = tmp_path / "escape"
    root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("", encoding="utf-8")
    (root / tm.CORPUS_NAME).symlink_to(outside)
    with pytest.raises(tm.TelemetryCorruption):
        tm.corpus_path(root)


# ------------------------------------------------- совместимость с потребителем

def test_records_are_directly_consumable_by_the_hardware_audit(tmp_path):
    """Формат — не «похожий»: его читает ровно тот скрипт, который решает про железо."""
    import importlib.util
    for i in range(3):
        _run(tmp_path, task_id=f"audit-{i}")
    spec = importlib.util.spec_from_file_location(
        "rwa", Path(__file__).resolve().parents[2] / "scripts" / "real_workload_audit.py")
    rwa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rwa)
    records = rwa.load_records(Path(os.environ[tm.ENV_ROOT]) / tm.CORPUS_NAME)
    assert len(records) == 3
    assert [e for i, r in enumerate(records) for e in rwa.validate_record(r, i)] == []
    report = rwa.build_report(records)
    assert report["summary"]["tasks"] == 3
    assert report["summary"]["verified_passed"] == 3
    # Три задачи — это не основание для железа, и аудит обязан так и сказать.
    assert report["decision"]["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_a_tampered_record_cannot_launder_a_failure_into_release_evidence(tmp_path):
    """Правку корпуса нельзя выдать за успех: `verified` проверяется отдельно от `status`."""
    import importlib.util
    _run(tmp_path, mode="unverified", task_id="honest-failure")
    path = Path(os.environ[tm.ENV_ROOT]) / tm.CORPUS_NAME
    rows = _corpus(tmp_path)
    assert rows[0]["status"] == "failed"
    rows[0]["status"] = "passed"                     # ручная «победа»
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location(
        "rwa2", Path(__file__).resolve().parents[2] / "scripts" / "real_workload_audit.py")
    rwa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rwa)
    report = rwa.build_report(rwa.load_records(path))
    assert report["summary"]["passed"] == 1
    assert report["summary"]["verified_passed"] == 0, (
        "подделанный status не создаёт verified-успех")
    assert report["summary"]["verified_success_rate"] == 0.0


# ------------------------------------------- парный протокол Epoch 4

def test_the_record_carries_what_the_paired_protocol_needs(tmp_path):
    """Выборка обязана быть пригодной для парного сравнения БЕЗ ручной доработки,
    иначе «реальные нагрузки» и «производительность» снова живут в разных мирах."""
    _run(tmp_path)
    row = _corpus(tmp_path)[0]
    assert row["pair_id"] == row["plan_digest"] != ""
    assert row["mandatory_approvals"] == 0 and row["unsafe_events"] == 0
    assert row["evidence_ref"] == f"task_journal:{row['task_id']}@{row['plan_digest']}"


def test_mandatory_approvals_are_counted_apart_from_avoidable_interventions(tmp_path):
    """Согласование по проекту — требование, а не издержка. Смешать их значило бы
    штрафовать систему за то, что она спросила разрешения."""
    _run(tmp_path, context={"mandatory_approvals": 2, "human_interventions": 1})
    row = _corpus(tmp_path)[0]
    assert (row["mandatory_approvals"], row["human_interventions"]) == (2, 1)


def test_an_explicit_pair_id_overrides_the_plan_digest(tmp_path):
    _run(tmp_path, context={"pair_id": "WF-042"})
    assert _corpus(tmp_path)[0]["pair_id"] == "WF-042"
