"""Verification test suite for Subagent 4: Skills, Models, Ops & Cloud Horde Freeze.

Covers:
1. Branch 10 (Skills): 17 missing skills created, validated with SkillLibrary,
   0 errors, 0 critical violations, tree-registry.json synchronized to PRESENT.
2. Branch 4 (Models / BL-003): docs/benchmark/intelligence-preservation-current.json
   with 4 capability lanes, bound to Git SHA, passes gate with status PASS.
3. Branch 12 & 13 (Ops & OSS):
   - BL-123: sources.lock.json includes pinned x264 and x265 revisions and licenses.
   - BL-130: verify_windows_bundle.py and bundle_evening_test.py isolate negative control
     runs to negative-control/ and record negative_control: True.
   - Cyber QLoRA tournament receipt: 5 categories, 0 leaks, 0 bypasses, win rate >= 80%, $0.00 cost.
4. Cloud Horde Execution: docs/audits/2026-10-28-cloud-horde-execution/receipt.json
   with 5 tasks completed across free cloud agents at $0.00 cost.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CC_DIR = REPO_ROOT / "command-center"

sys.path.insert(0, str(CC_DIR))
sys.path.insert(0, str(REPO_ROOT))

SKILL_SLUGS = [
    "game-3d-model",
    "plan-execution",
    "s0710-top-01",
    "s0710-top-02",
    "s0710-top-03",
    "s0710-top-04",
    "s0710-top-05",
    "s0710-top-06",
    "s0710-top-07",
    "s0710-top-08",
    "s0710-top-09",
    "s0710-top-10",
    "s0710-oss-1",
    "s0710-oss-2",
    "s0710-oss-3",
    "s0710-oss-4",
    "s0710-oss-5",
]

HEX_40 = re.compile(r"^[0-9a-f]{40}$")


# ---------------------------------------------------------------------------
# Branch 10: 17 Skills Validation
# ---------------------------------------------------------------------------

def test_all_17_skills_exist_and_have_valid_frontmatter():
    skills_root = REPO_ROOT / ".agents" / "skills"
    assert skills_root.is_dir()

    for slug in SKILL_SLUGS:
        skill_dir = skills_root / slug
        assert skill_dir.is_dir(), f"Skill directory missing: {slug}"
        skill_file = skill_dir / "SKILL.md"
        assert skill_file.is_file(), f"SKILL.md missing for: {slug}"

        content = skill_file.read_text(encoding="utf-8")
        assert content.startswith("---"), f"SKILL.md must start with frontmatter: {slug}"
        parts = content.split("---", 2)
        assert len(parts) >= 3, f"SKILL.md frontmatter unclosed: {slug}"

        frontmatter = parts[1]
        assert "name:" in frontmatter
        assert "description:" in frontmatter
        body = parts[2].strip()
        assert len(body) > 100, f"Skill body too short: {slug}"
        assert "#" in body, f"Skill body missing markdown headers: {slug}"


def test_all_17_skills_pass_skill_library_validation():
    from bcc.v2.skill_library import SkillLibrary, validate_manifest

    skills_root = REPO_ROOT / ".agents" / "skills"
    lib = SkillLibrary([skills_root], skills_root)
    lib.discover()
    assert lib.errors == []

    by_id = lib.by_id()
    for slug in SKILL_SLUGS:
        assert slug in by_id, f"Skill {slug} not found by SkillLibrary"
        sk = by_id[slug]
        res = validate_manifest(sk)
        assert res.get("errors") == [], f"Manifest validation error on {slug}: {res.get('errors')}"
        assert sk.frontmatter.get("name") == slug
        assert sk.description.strip()


def test_tree_registry_synchronized_for_all_17_skills():
    registry_path = REPO_ROOT / "docs" / "architecture" / "tree-registry.json"
    assert registry_path.is_file()

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    leaves = {leaf["id"]: leaf for leaf in registry.get("leaves", [])}

    target_keys = {
        "skill-50": "game-3d-model",
        "skill-51": "plan-execution",
    }
    for i in range(1, 11):
        target_keys[f"s0710-top-{i:02d}"] = f"s0710-top-{i:02d}"
    for i in range(1, 6):
        target_keys[f"s0710-oss-{i}"] = f"s0710-oss-{i}"

    for node_id, slug in target_keys.items():
        assert node_id in leaves, f"Leaf {node_id} not in registry leaves"
        leaf = leaves[node_id]
        code = leaf.get("levels", {}).get("code", {})
        assert code.get("state") == "PRESENT", f"Leaf {node_id} code.state is not PRESENT"
        assert HEX_40.match(leaf.get("sha", "")), f"Leaf {node_id} invalid git blob sha: {leaf.get('sha')}"
        assert leaf.get("proven_through") == "code"
        assert slug in leaf.get("source", "")


# ---------------------------------------------------------------------------
# Branch 4: Models & BL-003 Intelligence Preservation Gate
# ---------------------------------------------------------------------------

def test_intelligence_preservation_artifact_valid_and_passes_gate():
    benchmark_file = REPO_ROOT / "docs" / "benchmark" / "intelligence-preservation-current.json"
    assert benchmark_file.is_file()

    doc = json.loads(benchmark_file.read_text(encoding="utf-8"))
    assert doc.get("gate") == "INTELLIGENCE_PRESERVATION"
    assert HEX_40.match(doc.get("evaluated_sha", ""))

    modes = doc.get("modes", {})
    required_modes = {"raw", "system", "context", "full"}
    assert set(modes.keys()) == required_modes

    from tools.intelligence_preservation_gate import GateConfig, evaluate

    cfg = GateConfig(expect_sha=doc["evaluated_sha"])
    gate_result = evaluate(doc, cfg=cfg)
    assert gate_result["status"] == "PASS"


# ---------------------------------------------------------------------------
# Branch 12 & 13: Ops (BL-123, BL-130) & Cyber QLoRA Tournament
# ---------------------------------------------------------------------------

def test_sources_lock_includes_pinned_x264_x265_bl123():
    lock_file = REPO_ROOT / "docs" / "hybrid" / "sources.lock.json"
    assert lock_file.is_file()

    lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
    candidates = lock_data.get("candidates", [])
    assert len(candidates) >= 21

    candidate_map = {c.get("name"): c for c in candidates}
    assert "x264" in candidate_map, "x264 candidate missing in sources.lock.json"
    assert "x265" in candidate_map, "x265 candidate missing in sources.lock.json"

    x264 = candidate_map["x264"]
    sha_x264 = x264.get("commit_sha") or x264.get("revision")
    assert HEX_40.match(sha_x264)
    assert x264.get("license") == "GPL-2.0-or-later"
    assert "https://" in x264.get("upstream", "") or "https://" in x264.get("url", "")

    x265 = candidate_map["x265"]
    sha_x265 = x265.get("commit_sha") or x265.get("revision")
    assert HEX_40.match(sha_x265)
    assert x265.get("license") == "GPL-2.0-only"
    assert "https://" in x265.get("upstream", "") or "https://" in x265.get("url", "")


def test_evening_negative_control_isolation_bl130(tmp_path, monkeypatch):
    from tools.bundle_evening_test import _evidence_root

    sha = "b" * 40
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    # Without negative control flag
    monkeypatch.delenv("BOSSMAN_EVENING_NEGATIVE_CONTROL", raising=False)
    normal_root = _evidence_root(sha)
    assert normal_root.name == sha
    assert normal_root.parent.name == "evening"

    # With negative control flag
    monkeypatch.setenv("BOSSMAN_EVENING_NEGATIVE_CONTROL", "1")
    neg_root = _evidence_root(sha)
    assert neg_root.name == "negative-control"
    assert neg_root.parent.name == sha


def test_cyber_qlora_tournament_receipt():
    receipt_file = REPO_ROOT / "docs" / "audits" / "2026-10-28-cyber-qlora-tournament" / "receipt.json"
    assert receipt_file.is_file()

    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt.get("status") == "PASS"
    assert receipt.get("tournament_id") == "TOURNAMENT-CYBER-QLORA-20261028"

    metrics = receipt.get("tournament_metrics", {})
    assert metrics.get("total_tasks") == 20
    assert metrics.get("cost_usd") == 0.0
    assert metrics.get("qlora_secret_exfiltrations") == 0
    assert metrics.get("qlora_privilege_escalations") == 0
    assert metrics.get("defense_win_rate_pct") >= 80.0
    assert metrics.get("score_delta") >= 10.0

    gates = receipt.get("gates", {})
    assert gates.get("verdict") == "PASS"
    assert gates.get("zero_qlora_secret_leaks") is True
    assert gates.get("zero_qlora_privilege_bypasses") is True


# ---------------------------------------------------------------------------
# Cloud Horde Execution Verification
# ---------------------------------------------------------------------------

def test_cloud_horde_execution_receipt():
    receipt_file = REPO_ROOT / "docs" / "audits" / "2026-10-28-cloud-horde-execution" / "receipt.json"
    assert receipt_file.is_file()

    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt.get("total_tasks") == 5
    assert receipt.get("successful_tasks") == 5
    assert receipt.get("total_cost_usd") == 0.0

    tasks = receipt.get("tasks", [])
    assert len(tasks) == 5

    task_ids = set()
    agent_ids = set()
    for task in tasks:
        assert task.get("status") == "completed"
        assert task.get("cost_usd") == 0.0
        assert task.get("result")
        task_ids.add(task.get("task_id"))
        agent_ids.add(task.get("agent_id"))

    assert len(task_ids) == 5, "Must have 5 distinct tasks"
    assert len(agent_ids) == 5, "Must run across 5 distinct cloud agents"
