"""Production FULL lane metadata with synthetic loopback transport.

Lives in Core CI because the production lane requires Core dependencies; the
root harness contracts remain runnable without adding skips or model packages.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from test_intelligence_measurement_metadata import (  # noqa: E402, F401
    args_for, git, runner, source_repo, wire_server,
)

def test_runner_records_observed_identity_prompts_config_and_exact_sha(source_repo, tmp_path, wire_server):
    endpoint, state = wire_server
    args = args_for(tmp_path, endpoint) + ["--allow-insufficient-samples",
                                         "--gate-report", str(tmp_path / "synthetic-gate.json")]
    # This uses the genuine FULL production lane with a labelled synthetic
    # HTTP server. It tests evidence serialization, not intelligence retention.
    assert runner.main(args) == 2  # one item/metric is insufficient, even here
    result = json.loads((tmp_path / "synthetic-result.json").read_text())
    assert result["evaluated_sha"] == git(source_repo, "rev-parse", "HEAD")
    assert result["provider"] == "ollama" and result["model"] == "fixture:1"
    assert result["model_version"] == "sha256:" + "1" * 64
    assert result["quantization"] == "Q8_0"
    assert datetime.fromisoformat(result["started_at"]) <= datetime.fromisoformat(result["finished_at"])
    assert result["configuration"]["temperature"] == 0.0
    assert result["configuration"]["seed"] == 7
    assert result["configuration"]["lane_order"] == list(runner.MODES)
    corpus = (tmp_path / "synthetic-corpus.json").read_bytes()
    assert result["corpus"]["file_sha256"] == hashlib.sha256(corpus).hexdigest()
    assert len(result["corpus"]["tasks"]) == 11
    assert result["prompt_templates"]["system"] == runner.SYSTEM_PROMPT
    assert result["lanes"]["full"]["system_prompt_sha256"] == runner._sha(result["prompt_templates"]["full_system"])
    assert result["diagnostic_only"] is True
    requests = [payload for path, payload in state["requests"] if path == "/api/chat"]
    assert len(requests) == 44
    assert {p["model"] for p in requests} == {"fixture:1"}
    assert all(p["options"] == {"temperature": 0.0, "seed": 7} for p in requests)
    assert result["request_sha256"] == [runner._json_sha(p) for p in requests]
    assert json.loads((tmp_path / "synthetic-gate.json").read_text())["status"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("change", ["revision", "head", "corpus"])
def test_changed_experiment_is_not_published(source_repo, tmp_path, wire_server, change, capsys):
    endpoint, state = wire_server
    args = args_for(tmp_path, endpoint) + ["--allow-insufficient-samples"]

    def mutate():
        state["after_chat"] = None
        if change == "revision":
            state["models"][0]["digest"] = "2" * 64
        elif change == "head":
            git(source_repo, "commit", "--allow-empty", "-qm", "moved during synthetic run")
        else:
            path = tmp_path / "synthetic-corpus.json"
            path.write_text(path.read_text() + "\n")

    state["after_chat"] = mutate
    assert runner.main(args) == 2
    assert not (tmp_path / "synthetic-result.json").exists()
    assert "INSUFFICIENT_EVIDENCE" in capsys.readouterr().err
