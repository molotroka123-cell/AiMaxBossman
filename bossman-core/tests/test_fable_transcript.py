"""FableTranscriptRecorder: durable corpus of every paid-model exchange."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bossman.apprentice.fable_transcript import FableTranscriptRecorder, recorder_from_env

BUNDLE = {"ROLE": "auditor", "PROBLEM_ID": "T-001"}
RESPONSE = '{"attacks": [], "ready": false}'
USAGE = {"model": "claude-sonnet-4-5-20250929", "input_tokens": 100, "output_tokens": 50,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
         "estimated_cost_usd": 0.001, "latency_ms": 900}


def test_record_appends_durable_jsonl_with_redaction(tmp_path: Path):
    rec = FableTranscriptRecorder(tmp_path, "M-1")
    secret_bundle = {"note": "key sk-ant-api03-AAAA1111BBBB2222CCCC3333DDDD"}  # ci-secret-scan: allow (fake canary, proves redaction)
    entry = rec.record(bundle=secret_bundle, response_text=RESPONSE,
                       usage=USAGE, request_id="req_test1", purpose="unit", stop_reason="end_turn")
    assert entry["request_id"] == "req_test1"
    raw = (tmp_path / "M-1" / "transcript.jsonl").read_text(encoding="utf-8").strip()
    parsed = json.loads(raw)
    assert parsed["schema_version"] == 1 and parsed["mission_id"] == "M-1"
    assert "sk-ant-api03-AAAA1111BBBB2222CCCC3333DDDD" not in raw, "secret must be redacted"  # ci-secret-scan: allow (fake canary)
    index = (tmp_path / "index.jsonl").read_text(encoding="utf-8").strip()
    assert "req_test1" in index


def test_corrupted_trailing_line_does_not_lose_earlier_records(tmp_path: Path):
    rec = FableTranscriptRecorder(tmp_path, "M-2")
    rec.record(bundle=BUNDLE, response_text=RESPONSE, usage=USAGE, request_id="req_a", stop_reason="end_turn")
    with open(tmp_path / "M-2" / "transcript.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"broken json...')
    rec.record(bundle=BUNDLE, response_text=RESPONSE, usage=USAGE, request_id="req_b", stop_reason="end_turn")
    entries = rec.read()
    assert [e["request_id"] for e in entries] == ["req_a", "req_b"]


def test_export_training_pairs_marks_truncation(tmp_path: Path):
    rec = FableTranscriptRecorder(tmp_path, "M-3")
    rec.record(bundle=BUNDLE, response_text="complete answer", usage=USAGE,
               request_id="req_ok", stop_reason="end_turn")
    rec.record(bundle=BUNDLE, response_text="cut off mid", usage=USAGE,
               request_id="req_cut", stop_reason="max_tokens")
    pairs = rec.export_training_pairs()
    assert len(pairs) == 2
    assert pairs[0]["meta"]["truncated"] is False
    assert pairs[1]["meta"]["truncated"] is True
    exported = (tmp_path / "M-3" / "training_pairs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(exported) == 2
    assert json.loads(exported[0])["messages"][0]["role"] == "user"


def test_recorder_from_env_off_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BOSSMAN_FABLE_TRANSCRIPT_DIR", raising=False)
    assert recorder_from_env("M") is None
    monkeypatch.setenv("BOSSMAN_FABLE_TRANSCRIPT_DIR", "X")
    assert recorder_from_env("M") is not None


def _fake_http_response(status: int = 200, body: dict | None = None, request_id: str = "req_wire"):
    class _Resp:
        def __init__(self) -> None:
            self.status_code = status
            self.headers = {"request-id": request_id}
            self._body = body or {"type": "message", "model": "claude-sonnet-4-5-20250929",
                                  "stop_reason": "end_turn",
                                  "content": [{"type": "text", "text": '{"ready": false}'}],
                                  "usage": dict(USAGE)}

        def json(self) -> dict:
            return self._body

    return _Resp()


def test_fable_direct_client_writes_transcript_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import bossman.apprentice.fable_direct as fd

    monkeypatch.setenv("BOSSMAN_FABLE_TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setenv("BOSSMAN_FABLE_TRANSCRIPT_MISSION", "WIRE-1")
    monkeypatch.setattr(fd.httpx, "post", lambda *a, **k: _fake_http_response())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test-dummy")  # ci-secret-scan: allow (fake)
    budget = fd.DirectApiBudget(tmp_path / "budget.json", total_usd=1.0,
                                mission_id="WIRE-1", owner_id="bossman")
    client = fd.FableDirectClient(budget=budget)
    client.run(BUNDLE)
    entries = FableTranscriptRecorder(tmp_path, "WIRE-1").read()
    assert len(entries) == 1
    assert entries[0]["request_id"] == "req_wire"
    assert json.loads(entries[0]["bundle"])["PROBLEM_ID"] == "T-001"


def test_recording_failure_never_breaks_paid_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import bossman.apprentice.fable_direct as fd

    # Каталог должен быть НЕЗАПИСЫВАЕМЫМ на любой ОС. "Z:/..." годился только на
    # Windows: на Linux это обычный относительный путь, каталог создавался прямо
    # в репозитории (bossman-core/Z:/...), и тест переставал проверять то, ради
    # чего написан. Путь внутрь файла не создаётся нигде: mkdir даёт OSError.
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("файл, а не каталог", encoding="utf-8")
    monkeypatch.setenv("BOSSMAN_FABLE_TRANSCRIPT_DIR", str(blocker / "transcripts"))
    monkeypatch.setenv("BOSSMAN_FABLE_TRANSCRIPT_MISSION", "WIRE-2")
    monkeypatch.setattr(fd.httpx, "post", lambda *a, **k: _fake_http_response())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test-dummy")  # ci-secret-scan: allow (fake)
    budget = fd.DirectApiBudget(tmp_path / "budget.json", total_usd=1.0,
                                mission_id="WIRE-2", owner_id="bossman")
    client = fd.FableDirectClient(budget=budget)
    parsed = client.run(BUNDLE)   # must not raise despite unwritable transcript dir
    assert parsed["log_text"]
    # И запись действительно не прошла: иначе тест был бы зелёным впустую.
    assert blocker.is_file() and not (blocker / "transcripts").exists()
