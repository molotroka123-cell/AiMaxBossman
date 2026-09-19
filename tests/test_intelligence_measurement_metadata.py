"""Measurement-harness contracts, not model quality or release evidence.

The HTTP server below returns labelled synthetic replies. Miniature git trees
exercise binding/refusal; they are not passed off as the product's source SHA.
No fixture output is committed as intelligence-preservation-current.json.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import intelligence_preservation_run as runner  # noqa: E402
from intelligence_preservation_gate import CORE_METRICS, main as gate_main  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-c", "user.name=Contract Test", "-c",
                                    "user.email=contract@example.invalid", *args],
                                   cwd=repo, text=True).strip()


@pytest.fixture
def source_repo(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "source.py").write_text("first\n", encoding="utf-8")
    git(repo, "add", "source.py")
    git(repo, "commit", "-qm", "synthetic source binding fixture")
    monkeypatch.setattr(runner, "ROOT", repo)
    return repo


@pytest.fixture
def wire_server():
    state = {
        "models": [{"model": "fixture:1", "digest": "1" * 64,
                    "details": {"quantization_level": "Q8_0", "format": "gguf"}}],
        "reply": {"model": "fixture:1", "done": True,
                  "message": {"role": "assistant", "content": "4"}},
        "requests": [],
        "after_chat": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            state["requests"].append((self.path, None))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"models": state["models"]}).encode())

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, payload))
            if state.get("disconnect"):
                self.close_connection = True
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(state["reply"]).encode())
            if state["after_chat"]:
                state["after_chat"]()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()


def small_corpus(tmp_path):
    path = tmp_path / "synthetic-corpus.json"
    path.write_text(json.dumps({"dataset_id": "synthetic-contract-not-measurement", "tasks": [
        {"task_id": m, "metric": m, "prompt": f"Fixture {m}: 2+2?",
         "expect": {"kind": "equals", "value": "4"}} for m in runner.REQUIRED_METRICS
    ]}), encoding="utf-8")
    return path


def args_for(tmp_path, endpoint):
    return ["--model", "fixture:1", "--endpoint", endpoint,
            "--tasks", str(small_corpus(tmp_path)),
            "--out", str(tmp_path / "synthetic-result.json"),
            "--agent", str(ROOT / "bossman-core/agents/analyst"),
            "--full-workdir", str(tmp_path / "sandbox"), "--quiet"]


def test_sha256_metadata_is_a_full_content_digest():
    assert runner._sha("same prompt") == hashlib.sha256(b"same prompt").hexdigest()


def test_renamed_duplicate_prompt_context_is_refused(tmp_path):
    path = small_corpus(tmp_path)
    data = json.loads(path.read_text())
    duplicate = copy.deepcopy(data["tasks"][0])
    duplicate["task_id"] = "new-name-for-same-item"
    duplicate["prompt"] = " \n" + duplicate["prompt"].replace(" ", "  ") + "\t"
    data["tasks"].append(duplicate)
    path.write_text(json.dumps(data))
    with pytest.raises(runner.RunnerError, match="duplicate normalized prompt/context"):
        runner.load_tasks(path)


def test_corrected_corpus_keeps_count_and_has_explicit_distinct_lookup():
    path = ROOT / "docs/benchmark/intelligence_tasks.json"
    tasks = runner.load_tasks(path)
    assert len(tasks) == 220
    assert {m: sum(t.metric == m for t in tasks) for m in runner.REQUIRED_METRICS} == {
        m: 20 for m in runner.REQUIRED_METRICS}
    a, b = (next(t for t in tasks if t.task_id == name) for name in ("longctx-04", "longctx-x03"))
    assert a.prompt != b.prompt and a.context != b.context
    assert len(a.context.splitlines()) == len(b.context.splitlines()) == 60
    assert "Правило 23: резервный сервис архив-023 слушает порт 9151." in b.context
    assert b.expect == {"kind": "equals", "value": "9151"}
    assert runner.score(b, "9151") and not runner.score(b, "8023")
    assert json.loads(path.read_text())["dataset_id"] == "bossman-retention-v2"


def test_inadequate_corpus_refused_before_model_or_core_setup(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("guaranteed insufficient corpus must not make model calls or construct core")

    monkeypatch.setattr(runner, "ollama_identity", forbidden)
    monkeypatch.setattr(runner, "ProductionFullLane", forbidden)
    args = args_for(tmp_path, "http://127.0.0.1:1")
    assert runner.main(args) == 2
    assert "no model requests were made" in capsys.readouterr().err
    assert not (tmp_path / "synthetic-result.json").exists()


def test_capacity_uses_unchanged_core_bound_without_raising_secondary_floor():
    # Synthetic tasks only exercise capacity arithmetic, never measurement.
    tasks = [runner.Task(f"{m}-{i}", m, f"{m}-{i}", {"kind": "equals", "value": "x"})
             for m in runner.REQUIRED_METRICS for i in range(189 if m in CORE_METRICS else 20)]
    counts = runner.capacity_check(tasks)
    assert counts["reasoning_accuracy"] == 189
    assert counts["tool_selection_accuracy"] == 20
    with pytest.raises(runner.RunnerError, match="best possible paired core bound"):
        runner.capacity_check([t for t in tasks if not t.task_id.endswith("-188")])


def test_sha_override_cannot_relabel_current_source(source_repo):
    with pytest.raises(runner.RunnerError, match="not the current HEAD"):
        runner.verify_source("a" * 40)
    assert runner.verify_source()["evaluated_sha"] == git(source_repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_source_binding_reads_files_even_when_git_diff_hides_mutation(source_repo, flag):
    file = source_repo / "source.py"
    before = file.stat()
    git(source_repo, "update-index", flag, "source.py")
    file.write_text("other\n", encoding="utf-8")
    os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert git(source_repo, "diff", "HEAD") == ""  # actual negative control
    with pytest.raises(runner.RunnerError, match="workspace differs from HEAD"):
        runner.verify_source()


def test_source_binding_allows_git_normalized_windows_line_endings(source_repo):
    (source_repo / ".gitattributes").write_text("*.py text eol=crlf\n", encoding="utf-8")
    git(source_repo, "add", ".gitattributes")
    git(source_repo, "commit", "-qm", "declare Windows text checkout")
    (source_repo / "source.py").write_bytes(b"first\r\n")
    assert runner.verify_source()["evaluated_sha"] == git(source_repo, "rev-parse", "HEAD")


def test_no_model_does_not_create_a_measurement(source_repo, tmp_path, wire_server, capsys):
    endpoint, state = wire_server
    state["models"] = []
    assert runner.main(args_for(tmp_path, endpoint) + ["--allow-insufficient-samples"]) == 2
    assert "absent or ambiguous" in capsys.readouterr().err
    assert state["requests"] == [("/api/tags", None)]
    assert not (tmp_path / "synthetic-result.json").exists()


@pytest.mark.parametrize("reply,error", [
    ({"model": "different:1", "done": True, "message": {"content": "4"}}, "model identity"),
    ({"model": "fixture:1", "done": False, "message": {"content": "4"}}, "completed"),
    ({"model": "fixture:1", "done": True, "message": {"content": ""}}, "no answer"),
    ({"error": "private upstream detail"}, "invalid/error"),
])
def test_unmeasured_or_wrong_model_response_is_refused(wire_server, reply, error):
    endpoint, state = wire_server
    state["reply"] = reply
    with pytest.raises(runner.RunnerError, match=error):
        runner.ollama_client(endpoint, "fixture:1")([{"role": "user", "content": "fixture"}])


def test_disconnected_model_is_a_runner_refusal_not_a_scored_zero(wire_server):
    endpoint, state = wire_server
    state["disconnect"] = True
    with pytest.raises(runner.RunnerError, match="local model request failed"):
        runner.ollama_client(endpoint, "fixture:1")([{"role": "user", "content": "fixture"}])


def test_upstream_response_size_is_bounded(wire_server, monkeypatch):
    endpoint, state = wire_server
    monkeypatch.setattr(runner, "MAX_MODEL_RESPONSE_BYTES", 128)
    state["reply"]["message"]["content"] = "x" * 256
    with pytest.raises(runner.RunnerError, match="byte limit"):
        runner.ollama_client(endpoint, "fixture:1")([{"role": "user", "content": "fixture"}])


def test_revision_unknown_is_explicit_not_an_invented_identity(wire_server):
    endpoint, state = wire_server
    state["models"][0].pop("digest")
    identity = runner.ollama_identity(endpoint, "fixture:1")
    assert identity["model_version"] is None and identity["revision_status"] == "UNKNOWN"


def test_missing_measurement_stays_insufficient_without_changing_gate(tmp_path, capsys):
    assert gate_main([str(tmp_path / "no-measurement.json"), "--expect-sha", "a" * 40]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "INSUFFICIENT_EVIDENCE"


def test_windows_redirected_cli_help_is_readable_without_forcing_utf8(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "tools/intelligence_preservation_run.py"), "--help"],
                            cwd=tmp_path, env=dict(os.environ, PYTHONIOENCODING="cp1252"),
                            capture_output=True)
    assert result.returncode == 0, result.stderr.decode("cp1252", errors="replace")
    assert b"--gate-report" in result.stdout and b"--preflight-only" in result.stdout


@pytest.mark.parametrize("target", ["corpus", "measurement"])
def test_artifact_paths_cannot_overwrite_corpus_or_each_other(tmp_path, capsys, target):
    args = args_for(tmp_path, "http://127.0.0.1:1")
    path = tmp_path / ("synthetic-corpus.json" if target == "corpus" else "synthetic-result.json")
    original = (tmp_path / "synthetic-corpus.json").read_bytes()
    assert runner.main(args + ["--gate-report", str(path)]) == 2
    assert "distinct paths" in capsys.readouterr().err
    assert (tmp_path / "synthetic-corpus.json").read_bytes() == original
