from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from v6_baseline import (  # noqa: E402
    BaselineError,
    ProcessSnapshot,
    build_baseline_report,
    distribution,
    resolve_source_identity,
    sample_process_tree,
    summarize_process_snapshots,
)

SHA = "1" * 40
TREE = "2" * 40


def test_exact_source_identity_is_required_and_mismatch_fails_closed(tmp_path):
    identity = resolve_source_identity(
        repo_root=tmp_path, source_sha=SHA, tree_sha=TREE, expected_sha=SHA
    )
    assert identity["source_sha"] == SHA
    assert identity["tree_sha"] == TREE
    assert identity["exact_sha_match"] is True

    with pytest.raises(BaselineError, match="source SHA mismatch"):
        resolve_source_identity(
            repo_root=tmp_path,
            source_sha=SHA,
            tree_sha=TREE,
            expected_sha="3" * 40,
        )
    with pytest.raises(BaselineError, match="exact 40-character"):
        resolve_source_identity(repo_root=tmp_path, source_sha="abc", tree_sha=TREE)


def test_distribution_retains_every_sample_and_reports_tail():
    report = distribution([10, 20, 30, 40, 100])
    assert report["median"] == 30
    assert report["p95"] == 100
    assert report["worst"] == 100
    assert report["samples"] == [10, 20, 30, 40, 100]
    assert report["outliers_removed"] == 0


def test_process_summary_uses_tree_rss_and_unclamped_cpu_scale():
    report = summarize_process_snapshots(
        [
            ProcessSnapshot(10.0, 100 * 1024 * 1024, 2, 1.0),
            ProcessSnapshot(11.0, 140 * 1024 * 1024, 3, 2.5),
            ProcessSnapshot(12.0, 120 * 1024 * 1024, 2, 3.0),
        ]
    )
    assert report["rss_hwm_mb"] == 140.0
    assert report["process_count"]["worst"] == 3.0
    assert report["cpu_pct_one_core_scale"]["samples"][0] == 150.0
    assert report["outliers_removed"] == 0


def test_missing_psutil_is_explicitly_unavailable(monkeypatch):
    import v6_baseline

    monkeypatch.setattr(v6_baseline, "_psutil_module", lambda: None)
    report = sample_process_tree(pid=1, duration_s=1, interval_s=1)
    assert report["status"] == "UNAVAILABLE"
    assert report["reason"] == "psutil_not_installed"
    assert report["process_samples"] == 0


def test_baseline_never_invents_gpu_model_or_human_numbers(tmp_path):
    def fake_sampler(**kwargs):
        return {"status": "MEASURED", "rss_hwm_mb": 321.0}

    report = build_baseline_report(
        pid=99,
        duration_s=5,
        interval_s=1,
        source_sha=SHA,
        tree_sha=TREE,
        expected_sha=SHA,
        repo_root=tmp_path,
        process_sampler=fake_sampler,
    )
    assert report["behavior_changed"] is False
    assert report["measurement"]["process_tree"]["rss_hwm_mb"] == 321.0
    assert report["measurement"]["gpu"]["status"] == "NOT_RUN"
    assert report["measurement"]["model_runtime"]["status"] == "NOT_RUN"
    assert report["claims"]["human_comparison"] == "NOT_RUN"
    assert report["claims"]["ram_vram_double_counted"] is False


def test_cli_writes_exact_sha_artifact_without_git_checkout(tmp_path):
    out = tmp_path / "baseline.json"
    script = ROOT / "tools" / "v6_baseline.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--duration",
            "0.01",
            "--interval",
            "0.005",
            "--source-sha",
            SHA,
            "--tree-sha",
            TREE,
            "--expect-sha",
            SHA,
            "--json-out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["source"]["source_sha"] == SHA
    assert report["source"]["tree_sha"] == TREE
    assert report["source"]["exact_sha_match"] is True
    assert report["phase"] == "V6_PHASE_0_MEASUREMENT_ONLY"
