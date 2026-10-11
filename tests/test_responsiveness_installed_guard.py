"""PREP-08: a source-only probe cannot certify an installed application.

These are bounded offline negative controls, not Windows/performance evidence.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location(
        "responsiveness_installed_guard_probe", REPO / "tools" / "responsiveness_probe.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("soak_seconds", [0.0, 0.01, 3600.0])
def test_installed_refuses_before_source_import_or_any_measurement(
    probe, monkeypatch, tmp_path, soak_seconds
):
    budget = probe.load_budget()
    original_budget = copy.deepcopy(budget)
    original_path = list(sys.path)
    checkpoint = tmp_path / "must-not-start.json"

    def forbidden(*args, **kwargs):
        raise AssertionError("installed mode attempted source/runtime measurement")

    for name in ("_load", "_routes", "_seed_gallery", "_tree_rss_mib", "_tree_cpu_seconds"):
        monkeypatch.setattr(probe, name, forbidden)
    try:
        report = probe.measure("installed", soak_seconds, budget, checkpoint)
        assert sys.path == original_path
    finally:
        sys.path[:] = original_path

    assert report["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert report["mode"] == "installed"
    assert report["completed"] is False
    assert report["measurement_executed"] is False
    assert report["reference_only"] is False
    assert report["blocker"] == "installed_launcher_not_implemented"
    assert len(report["lines"]) == len(budget["budgets"])
    for row in report["lines"]:
        assert row["measured"] is None
        assert row["verdict"] == "INSUFFICIENT_EVIDENCE"
        assert row["limit"] == budget["budgets"][row["metric"]]["limit"]
    assert report["budget_fixed_at"] == budget["fixed_at"]
    assert budget == original_budget
    assert not checkpoint.exists(), "refusal must not look like a started soak"


def test_installed_cli_writes_incomplete_json_and_exits_two(probe, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("CLI fell back to the source application")

    monkeypatch.setattr(probe, "_load", forbidden)
    target = tmp_path / "report" / "installed.json"
    original_path = list(sys.path)
    try:
        code = probe.main(["--mode", "installed", "--soak-seconds", "3600",
                           "--json", str(target)])
    finally:
        sys.path[:] = original_path
    assert code == 2
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert report["completed"] is False
    assert report["measurement_executed"] is False
    assert all(row["measured"] is None for row in report["lines"])


def test_reference_entrypoint_is_not_disabled_by_installed_guard(probe, monkeypatch):
    class ReferenceReached(Exception):
        pass

    def stop_at_source_import(*args, **kwargs):
        raise ReferenceReached

    monkeypatch.setattr(probe, "_load", stop_at_source_import)
    original_path = list(sys.path)
    try:
        with pytest.raises(ReferenceReached):
            probe.measure("reference", 0.0, probe.load_budget())
    finally:
        sys.path[:] = original_path


@pytest.mark.parametrize("mode", ["", "windows"])
def test_unknown_programmatic_mode_cannot_claim_installed_evidence(probe, monkeypatch, mode):
    def forbidden(*args, **kwargs):
        raise AssertionError("unknown mode reached source application")

    monkeypatch.setattr(probe, "_load", forbidden)
    original_path = list(sys.path)
    try:
        with pytest.raises(ValueError, match="unsupported measurement mode"):
            probe.measure(mode, 0.0, probe.load_budget())
        assert sys.path == original_path
    finally:
        sys.path[:] = original_path
