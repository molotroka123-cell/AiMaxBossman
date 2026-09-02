"""Episode recording, sanitization, semantic anchors, memory over LearningStore."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from learning import trace  # noqa: E402
from bossman.apprentice import flags  # noqa: E402
from bossman.apprentice.errors import FlagDisabled, SecretInRecord  # noqa: E402
from bossman.apprentice.models import ApprenticeTask, PlanStep, SemanticTarget  # noqa: E402
from bossman.apprentice.recording import (ApprenticeMemory, EpisodeRecorder, assert_sanitized, negative_lesson,  # noqa: E402
                                          semantic_anchors, skill_schema)
from bossman.computer_operator.models import ActionKind  # noqa: E402
import test_apprentice_core as core  # noqa: E402


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(flags.MASTER, "1")
    monkeypatch.setenv(flags.SKILL_RECORDING, "1")


def _run(world=None, steps=None, **kw):
    w = world or core._world()
    task = ApprenticeTask.create("save a note", session_id="sess_1", run_id="run_1", head_sha="abc123",
                                 environment="env:notes-1.0", task_type="notes.save")
    rec = EpisodeRecorder(task=task, agent="apprentice", model="planner:sim", principal_id="apprentice:planner",
                          app="Notes", app_version="1.0")
    eng, planner, act = core._engine(w, steps, on_record=rec.on_record, **kw)
    res = eng.run(task)
    return task, rec, res


def test_episode_is_factual_sanitized_and_schema_valid(on, tmp_path):
    task, rec, res = _run()
    ep = rec.finish(res)
    assert ep["record_type"] == "episode" and ep["learning_status"] == "UNVERIFIED"      # success != verified
    assert trace.validate(ep, schema=skill_schema()) == []
    assert ep["checkpoints_reached"] == ["saved"] and len(ep["action_records"]) == 2
    anchors = ep["semantic_anchors"]
    assert {a["name"] for a in anchors} == {"Body", "Save"} and all("x" not in a and "y" not in a for a in anchors)
    mem = ApprenticeMemory(tmp_path / "mem")
    stored = mem.record_episode(ep)
    assert stored["version"] == 1 and mem.episodes()[0]["task_id"] == task.task_id
    assert mem.skills() == []                                                            # raw episode is not a skill


def test_failed_episode_keeps_errors_and_recovery(on):
    w = core._world(); w.app = "Calc"; w.title = "Calc"
    task, rec, res = _run(w)
    ep = rec.finish(res)
    assert res.ok and any(e["error_code"] == "wrong_window" for e in ep["errors"])
    assert ep["recovery"] and ep["recovery"][0]["error_code"] == "wrong_window"


def test_recording_flag_off_writes_nothing(on, monkeypatch, tmp_path):
    monkeypatch.delenv(flags.SKILL_RECORDING)
    task, rec, res = _run()
    mem = ApprenticeMemory(tmp_path / "mem")
    assert mem.record_episode(rec.finish(res)) is None and not (tmp_path / "mem" / "failed_experiments.jsonl").exists()


def test_episode_with_secret_is_rejected_from_memory(on, tmp_path):
    task, rec, res = _run()
    ep = rec.finish(res)
    bad = json.loads(json.dumps(ep))
    bad["action_records"][0]["action"]["args_redacted"]["note"] = "token=BOSSMAN_TEST_SECRET_ghp0123"
    mem = ApprenticeMemory(tmp_path / "mem")
    with pytest.raises(SecretInRecord):
        mem.record_episode(bad)
    bad2 = json.loads(json.dumps(ep)); bad2["chain_of_thought"] = "I think..."
    with pytest.raises(SecretInRecord):
        mem.record_episode(bad2)
    assert mem.episodes() == []
    # the store's own validator is the second net
    bad3 = json.loads(json.dumps(ep)); bad3["hidden_reasoning"] = "x"
    assert any("forbidden field" in e for e in trace.validate(bad3, schema=skill_schema()))


def test_engine_drops_record_that_still_carries_secret(on, monkeypatch):
    """Even if the planner smuggles a secret-looking value into a non-sensitive step, the recorder refuses."""
    w = core._world()
    steps = core._steps()
    steps[0] = PlanStep("s1", ActionKind.TYPE, core.NOTES, SemanticTarget("textbox", "Body"), text="hi",
                        args={"note": "BOSSMAN_TEST_SECRET_akia01"})
    task = ApprenticeTask.create("g", session_id="s", run_id="r")
    rec = EpisodeRecorder(task=task, agent="a", model="m", principal_id="p")
    eng, _, _ = core._engine(w, steps, on_record=rec.on_record)
    res = eng.run(task)
    assert all("BOSSMAN_TEST_SECRET_" not in json.dumps(r.to_dict()) for r in res.records)


def test_corrupted_trace_is_skipped_not_applied(on, tmp_path):
    task, rec, res = _run()
    mem = ApprenticeMemory(tmp_path / "mem")
    mem.record_episode(rec.finish(res))
    path = tmp_path / "mem" / "failed_experiments.jsonl"
    good = path.read_text(encoding="utf-8")
    broken = json.loads(good.strip().splitlines()[0]); broken.pop("environment"); broken["task_id"] = "broken"
    path.write_text(good + json.dumps(broken) + "\n" + '{"task_id": "trunc', encoding="utf-8")
    mem2 = ApprenticeMemory(tmp_path / "mem")
    eps = mem2.episodes()
    assert [e["task_id"] for e in eps] == [task.task_id]
    assert mem2.skipped_invalid == 1 and mem2.corrupt_lines >= 1


def test_negative_lesson_is_typed_and_consultable(on, tmp_path):
    w = core._world(); w.app = "Calc"; w.title = "Calc"
    task, rec, res = _run(w)
    bad = next(r for r in rec.records if r["error_code"] == "wrong_window")
    lesson = negative_lesson(task=task, record=bad, why_dangerous="typing into the wrong window leaks the note",
                             agent="apprentice", model="m", principal_id="p", verified_by=["human:owner"])
    assert trace.validate(lesson, schema=skill_schema()) == []
    mem = ApprenticeMemory(tmp_path / "mem")
    mem.record_lesson(lesson)
    got = mem.lessons()
    assert got and got[0]["target_label"] == "textbox:Body" and got[0]["action_kind"] == "TYPE"


def test_evidence_export_needs_flag_and_is_redacted(on, monkeypatch, tmp_path):
    task, rec, res = _run()
    mem = ApprenticeMemory(tmp_path / "mem")
    mem.record_episode(rec.finish(res))
    with pytest.raises(FlagDisabled):
        mem.export_evidence_bundle(task.task_id)
    monkeypatch.setenv(flags.EVIDENCE_EXPORT, "1")
    bundle = mem.export_evidence_bundle(task.task_id)
    assert bundle["episodes"] and bundle["skills"] == []
    assert_sanitized(bundle, where="test")


def test_semantic_anchor_extraction_ignores_targetless_records():
    assert semantic_anchors([{"semantic_target": {"role": "", "name": ""}, "application": {"app": "x"}, "action": {}}]) == []
