import json

from bcc.v2.teacher_trace import TeacherTraceRecorder, redact_text


def test_redacts_common_secrets():
    text = "api_key=abc123 token:xyz password=qwerty Bearer deadbeef"
    redacted = redact_text(text)
    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert "qwerty" not in redacted
    assert "deadbeef" not in redacted
    assert redacted.count("[REDACTED_SECRET]") >= 4


def test_private_trace_cannot_become_reusable(tmp_path):
    recorder = TeacherTraceRecorder(tmp_path)
    trace = recorder.record(
        session_id="s1",
        teacher_provider="anthropic",
        teacher_model="opus",
        prompt="fix hard bug",
        prompt_summary="debug repository",
        decision_summary="inspect failing boundary then verify",
        reusable=True,
        contains_private_data=True,
    )
    assert trace.reusable is False
    assert trace.contains_private_data is True


def test_export_only_clean_explicit_training_candidates(tmp_path):
    recorder = TeacherTraceRecorder(tmp_path / "logs")
    recorder.record(
        session_id="s1",
        teacher_provider="anthropic",
        teacher_model="opus",
        prompt="task one",
        prompt_summary="task one",
        decision_summary="summary one",
        outcome={"status": "success"},
        verification={"tests": "pass"},
        reusable=True,
    )
    recorder.record(
        session_id="s2",
        teacher_provider="openai",
        teacher_model="gpt",
        prompt="task two",
        prompt_summary="task two",
        decision_summary="summary two",
        reusable=False,
    )
    destination = tmp_path / "dataset" / "train.jsonl"
    assert recorder.export_training_candidates(destination) == 1
    rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["reusable"] is True
    assert rows[0]["contains_private_data"] is False


def test_records_tools_outcomes_and_verification_without_raw_cot(tmp_path):
    recorder = TeacherTraceRecorder(tmp_path)
    trace = recorder.record(
        session_id="run-1",
        task_id="task-7",
        teacher_provider="anthropic",
        teacher_model="claude-opus",
        prompt="repair the canary bug",
        prompt_summary="repair canary lifecycle",
        decision_summary="use one cohort identity and verify restart rollback",
        tool_events=[{"tool": "pytest", "result": "39 passed"}],
        outcome={"status": "success", "commit": "abc123"},
        verification={"n8": "pass", "rollback": "pass"},
        difficulty_tags=["debugging", "multi-step", "repo"],
        reusable=True,
    )
    assert trace.decision_summary
    assert trace.tool_events[0]["tool"] == "pytest"
    assert trace.verification["rollback"] == "pass"
    assert trace.source_prompt_sha256
