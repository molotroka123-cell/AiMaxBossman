from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from bcc.features import v15_self_repair as repair


def test_classify_accepts_code_failure_and_extracts_test_path():
    row = repair.classify(
        "Traceback\nFile command-center/bcc/foo.py line 10\n"
        "AssertionError\nFAILED command-center/tests/test_foo.py::test_case"
    )
    assert row is not None
    assert "command-center/bcc/foo.py" in row["paths"]
    assert "command-center/tests/test_foo.py::test_case" in row["tests"]
    assert len(row["signature"]) == 16


def test_classify_rejects_owner_network_and_access_boundaries():
    for error in (
        "network unavailable while provider request timed out",
        "captcha detected; waiting for owner",
        "approval required for submit",
        "owner_input request pending",
        "rate limit quota exhausted",
        "login required",
        "task stopped",
    ):
        assert repair.classify(error) is None, error


class Engine:
    def __init__(self): self.hook = None
    def add_hook(self, name, fn, critical=False):
        assert name == "on_failure" and critical is False
        self.hook = fn


class Bus:
    def __init__(self): self.rows = []
    async def emit(self, kind, **data): self.rows.append((kind, data))


def test_setup_writes_redacted_deduplicated_repair_evidence(tmp_path, monkeypatch):
    engine, bus = Engine(), Bus()
    svc = SimpleNamespace(
        settings=SimpleNamespace(data_dir=tmp_path),
        engine=engine, bus=bus,
    )
    asyncio.run(repair._setup(svc))
    assert engine.hook is not None

    # Redactor is tested elsewhere; make its output deterministic here.
    monkeypatch.setattr(repair, "redact_text", lambda x: x.replace("secret-value", "[REDACTED]"))
    error = (
        "Traceback secret-value\n"
        "File tools/demo.py line 5\n"
        "AssertionError\nFAILED tests/test_demo.py::test_x"
    )
    asyncio.run(engine.hook({"id": 7}, 11, error))
    asyncio.run(engine.hook({"id": 8}, 12, error))

    path = tmp_path / "v1.5" / "self-repair" / "inbox.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[-1]["occurrence"] == 2
    assert rows[-1]["status"] == "QUEUED"
    assert "secret-value" not in path.read_text(encoding="utf-8")
    assert bus.rows[-1][0] == "v15.self_repair.queued"
    assert rows[-1]["tests"] == ["tests/test_demo.py::test_x"]


def test_latest_status_closes_repair_until_a_new_failure_arrives(tmp_path):
    path = tmp_path / "inbox.jsonl"
    queued = {"signature": "abc", "status": "QUEUED", "occurrence": 1}
    done = {"signature": "abc", "status": "TARGETED_TESTED_CANDIDATE", "occurrence": 1}
    path.write_text(json.dumps(queued) + "\n" + json.dumps(done) + "\n", encoding="utf-8")
    assert repair._latest_pending(path) == []
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"signature": "abc", "status": "QUEUED", "occurrence": 2}) + "\n")
    rows = repair._latest_pending(path)
    assert len(rows) == 1 and rows[0]["occurrence"] == 2


def test_tick_starts_only_one_runtime_repair_worker(tmp_path, monkeypatch):
    base = tmp_path / "v1.5" / "self-repair"
    base.mkdir(parents=True)
    (base / "inbox.jsonl").write_text(
        json.dumps({"signature": "abc", "status": "QUEUED"}) + "\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    script = tmp_path / "repair.py"
    script.write_text("# test", encoding="utf-8")

    class Proc:
        pid = 4321
        returncode = None
        def poll(self): return None

    calls = []
    monkeypatch.setattr(repair, "_REPAIR_PROC", None)
    monkeypatch.setattr(repair, "_owner_repo", lambda _svc: repo)
    monkeypatch.setattr(repair, "_runner", lambda: script)
    monkeypatch.setattr(repair.subprocess, "Popen", lambda argv, **kw: calls.append(argv) or Proc())
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), bus=Bus())

    asyncio.run(repair._tick(svc))
    asyncio.run(repair._tick(svc))
    assert len(calls) == 1
    assert "repair" in calls[0]
    assert (base / "worker.pid").read_text(encoding="utf-8") == "4321"
    assert any(kind == "v15.self_repair.worker_started" for kind, _ in bus.rows)
