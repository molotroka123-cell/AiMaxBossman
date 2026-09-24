from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_ling_worker_path_is_bounded_and_network_commands_are_refused(tmp_path):
    mod = load("bossman_15_ling_coder_test", "tools/bossman_15_ling_coder.py")
    (tmp_path / "ok.txt").write_text("ok", encoding="utf-8")
    assert mod.safe(tmp_path, "ok.txt").read_text(encoding="utf-8") == "ok"
    with pytest.raises(ValueError):
        mod.safe(tmp_path, "../escape.txt")
    assert mod.execute(tmp_path, "run_tests", {"command": "curl https://example.com"}).startswith("REFUSED")
    assert mod.execute(tmp_path, "run_tests", {"command": "git push origin HEAD"}).startswith("REFUSED")


def test_learning_compiler_rejects_raw_or_self_verified_learning():
    mod = load("bossman_15_learning_compile_test", "tools/bossman_15_learning_compile.py")
    raw = {
        "attempt_id": "a", "task_id": "t", "project_id": "p",
        "failure_observation": "failed", "correction": "Use fresh evidence before retrying.",
        "recipe": ["observe", "verify"], "check": "pytest -q",
        "provenance": {"who": "nemotron", "evidence_refs": ["run:1"]},
        "transfer": {"passed": False, "verifier_principal": "aster", "head_sha": "a" * 40},
    }
    with pytest.raises(ValueError, match="transfer"):
        mod.require(raw)
    raw["transfer"]["passed"] = True
    raw["transfer"]["verifier_principal"] = "nemotron"
    with pytest.raises(ValueError, match="independent"):
        mod.require(raw)


def test_learning_compiler_accepts_only_complete_independent_transfer_record():
    mod = load("bossman_15_learning_compile_ok", "tools/bossman_15_learning_compile.py")
    rec = {
        "attempt_id": "a", "task_id": "t", "project_id": "p",
        "failure_observation": "tool retry used stale path",
        "correction": "Re-read the corrected root-relative path before escalating.",
        "recipe": ["read failure", "correct path", "retry read", "verify output"],
        "check": "python -m pytest -q tests/test_case.py",
        "provenance": {"who": "ling", "evidence_refs": ["pytest:pass"]},
        "transfer": {
            "passed": True, "verifier_principal": "aster",
            "independence_class": "independent", "head_sha": "b" * 40,
            "environment": "owner-windows",
        },
    }
    mod.require(rec)
