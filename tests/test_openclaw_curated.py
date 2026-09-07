from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "integrations" / "openclaw" / "curated-skills.json"
TOOL = ROOT / "tools" / "openclaw_curated.py"


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_curated_catalog_is_fail_closed_and_pinned():
    data = _catalog()
    assert data["source"]["repository"] == "VoltAgent/awesome-openclaw-skills"
    assert len(data["source"]["commit"]) == 40
    assert data["source"]["security_status"] == "curated_not_audited"
    assert data["policy"]["auto_install"] is False
    assert data["policy"]["default_network"] == "deny"
    assert data["policy"]["write_actions"] == "ask"
    assert data["policy"]["secret_access"] == "deny"


def test_top_candidates_match_bossman_priorities():
    skills = _catalog()["skills"]
    assert [s["slug"] for s in skills[:3]] == [
        "azhua-skill-vetter",
        "arc-trust-verifier",
        "playwright-mcp",
    ]
    assert all(
        {"manual_source_review", "provenance_pin", "negative_tests"}
        <= set(skill["required_gates"])
        for skill in skills
    )


def test_validator_accepts_repository_catalog():
    run = subprocess.run(
        [sys.executable, str(TOOL), "--check"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "OPENCLAW_CURATED=PASS" in run.stdout


def test_validator_rejects_auto_install(tmp_path):
    data = _catalog()
    data["policy"]["auto_install"] = True
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(TOOL), "--catalog", str(bad), "--check"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    assert run.returncode == 2
    assert "auto-installed" in run.stdout
