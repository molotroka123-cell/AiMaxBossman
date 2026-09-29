from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

from tools import k1m6a_youtube_batch as batch

ROOT = Path(__file__).resolve().parents[1]


def test_youtube_batch_defaults_are_the_owner_window():
    assert batch.DEFAULT_CHANNEL == "https://www.youtube.com/@k1m6a/streams"
    assert batch.DEFAULT_START == "2026-08-14"
    assert batch.DEFAULT_END == "2026-08-27"


def test_youtube_discovery_filters_dates_and_duplicates(monkeypatch):
    monkeypatch.setattr(batch.shutil, "which", lambda _x: "yt-dlp")
    rows = [
        {"id": "a", "upload_date": "20260814", "title": "first"},
        {"id": "a", "upload_date": "20260814", "title": "duplicate"},
        {"id": "b", "upload_date": "20260827", "title": "last"},
        {"id": "c", "upload_date": "20260828", "title": "outside"},
    ]
    stdout = "\n".join(json.dumps(x) for x in rows)
    monkeypatch.setattr(
        batch, "_run",
        lambda *_a, **_kw: subprocess.CompletedProcess(["yt-dlp"], 0, stdout, ""),
    )
    out = batch.discover(batch.DEFAULT_CHANNEL, batch.day(batch.DEFAULT_START),
                         batch.day(batch.DEFAULT_END))
    assert [x["video_id"] for x in out] == ["a", "b"]
    assert all(x["learning_status"] == "UNVERIFIED" for x in out)


def test_setup_uses_exact_owner_model_ids_and_no_direct_openrouter_chat():
    text = (ROOT / "tools" / "bossman_15_setup.py").read_text(encoding="utf-8")
    model_source = (ROOT / "command-center" / "bcc" / "features" / "economy_swarm.py").read_text(encoding="utf-8")
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(model_source).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"NEMOTRON_MODEL", "LING_MODEL", "GLM_MODEL"}
    }
    assert assignments == {
        "NEMOTRON_MODEL": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "LING_MODEL": "inclusionai/ling-3.0-flash-fin:free",
        "GLM_MODEL": "z-ai/glm-5.3-flash",
    }
    setup_imports = [
        node for node in ast.parse(text).body
        if isinstance(node, ast.ImportFrom) and node.module == "bcc.features.economy_swarm"
    ]
    assert len(setup_imports) == 1
    assert {alias.name for alias in setup_imports[0].names} >= set(assignments)
    assert text.count("YT-Nemotron-") >= 3
    assert "openrouter.ai/api/v1/chat/completions" not in text
    assert "/api/openrouter/" in text and "/api/agents" in text


def test_economy_run_never_reads_openrouter_key_or_calls_openrouter_directly():
    text = (ROOT / "tools" / "bossman_15_economy_run.py").read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" not in text
    assert "openrouter.ai" not in text
    assert "/api/tasks" in text
    assert "/api/economy/route" in text
    assert "Aster: audit/control only; do not write code." in text
    assert "glm_calls" in text


def test_memory_sync_does_not_import_youtube_strategy_candidates():
    text = (ROOT / "tools" / "bossman_15_memory_sync.py").read_text(encoding="utf-8")
    assert "Trading hypotheses from YouTube are deliberately NOT imported" in text
    assert '"trading_candidates_imported": 0' in text
