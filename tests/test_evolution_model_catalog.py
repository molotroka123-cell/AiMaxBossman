"""Research pins and the operational downloader must not quietly diverge."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("model_fetch", ROOT / "tools/model_fetch.py")
model_fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model_fetch)


def test_all_three_research_champions_are_exact_pinned_optional_candidates():
    research = json.loads((ROOT / "config/evolution/local-champions.json").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "tools/model_profiles.json").read_text(encoding="utf-8"))
    model_fetch.validate_manifest(catalog)
    by_id = {profile["id"]: profile for profile in catalog["profiles"]}
    assert len(research["models"]) == 3
    assert research["target"]["model_runtime_cap_bytes"] == 88_000_000_000
    for model in research["models"]:
        profile = by_id[model["id"]]
        artifact = model["artifact"]
        assert profile["status"] == "PINNED"
        assert profile["optional"] and profile["activation"] == "PENDING_OWNER_HARDWARE"
        assert profile["source"]["hf_repo"] == artifact["repo_id"]
        assert profile["source"]["revision"] == artifact["revision"]
        assert profile["memory_plan"] == model["memory_plan"]
        assert profile["hardware_limit_bytes"] == research["target"]["model_runtime_cap_bytes"]
        assert profile["min_free_disk"]["bytes"] == artifact["total_size_bytes"]
        assert [(f["hf_path"], f["sha256"], f["size_bytes"]) for f in profile["files"]] == [
            (f["path"], f["sha256"], f["size_bytes"]) for f in artifact["files"]
        ]
    assert set(catalog["download_order"]["steps"][-1]["profiles"]) == {m["id"] for m in research["models"]}


def test_over_cap_champion_is_rejected_before_a_download():
    manifest = json.loads((ROOT / "tools/model_profiles.json").read_text(encoding="utf-8"))
    over = copy.deepcopy(manifest)
    target = next(p for p in over["profiles"] if p["id"] == "qwen_coder_worker")
    target["memory_plan"]["estimated_working_envelope_bytes"] = 88_000_000_001
    with pytest.raises(ValueError, match="memory envelope must fit"):
        model_fetch.validate_manifest(over)
