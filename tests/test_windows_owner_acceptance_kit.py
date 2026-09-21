"""Regression of the real support-copy/ZIP path, not a Windows runtime attestation."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("owner_kit_builder", ROOT / "tools/build_windows_bundle.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

REQUIRED = (
    "INSTALL.md", "OWNER_ACCEPTANCE.md", "KNOWN_LIMITATIONS.md", "owner-acceptance.ps1",
    "tests/owner_hardware/README.md", "tests/owner_hardware/manifest.json",
    "tests/owner_hardware/HOTSPOTS_AND_HOTFIX_PLAYBOOK.md",
    "tests/owner_hardware/MODEL_STACK_2026-09-20.md",
    "tests/owner_hardware/CLOUD_STACK_2026-09-20.md",
    "tests/owner_scenarios/OWNER_HARDWARE_FIRST_RUN.md",
)


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "source checkout"
    root.mkdir()
    for name in REQUIRED:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"UTF-8 owner fixture: {name} / проверка\n", encoding="utf-8")
    manifest = {"schema_version": 3, "cases": [
        {"id": f"HW-{number:02d}", "checks": ["verify_result"]} for number in range(1, 14)
    ]}
    (root / "tests/owner_hardware/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    owner_run = root / "docs/v8/owner-final-run"
    owner_run.mkdir(parents=True)
    for name in builder.OWNER_RUN_FILES:
        (owner_run / name).write_text(f"existing owner run: {name}\n", encoding="utf-8")
    support = root / "support.py"
    support.write_text("# existing support script\n", encoding="utf-8")
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder, "OWNER_RUN_SOURCE", owner_run)
    monkeypatch.setattr(builder, "SUPPORT_SCRIPTS", ((support, "support.py"),))
    return root


@pytest.mark.parametrize("name", REQUIRED)
def test_support_stage_ships_each_required_owner_file(source, tmp_path, name):
    home = tmp_path / "unpacked candidate with spaces"
    builder.install_support(home / "app-support")
    assert (home / name).is_file(), f"owner kit omitted {name}"
    assert (home / name).read_bytes() == (source / name).read_bytes()


@pytest.mark.parametrize("name", REQUIRED)
def test_missing_owner_protocol_input_fails_build(source, tmp_path, name):
    (source / name).unlink()
    with pytest.raises(RuntimeError, match="owner acceptance"):
        builder.install_support(tmp_path / "candidate/app-support")


@pytest.mark.parametrize("kind", ["missing_case", "duplicate_case", "malformed_json", "no_checks"])
def test_incomplete_hardware_protocol_is_not_shipped(source, tmp_path, kind):
    path = source / "tests/owner_hardware/manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if kind == "missing_case":
        data["cases"].pop()
    elif kind == "duplicate_case":
        data["cases"][-1] = data["cases"][0]
    elif kind == "no_checks":
        data["cases"][-1]["checks"] = []
    path.write_text("{" if kind == "malformed_json" else json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="owner acceptance"):
        builder.install_support(tmp_path / "candidate/app-support")


def test_support_keeps_existing_owner_run(source, tmp_path):
    home = tmp_path / "candidate"
    result = builder.install_support(home / "app-support")
    assert result["path"] == "app-support/owner-final-run"
    assert set(result["files"]) == set(builder.OWNER_RUN_FILES)
    assert (home / "app-support/support.py").read_bytes() == (source / "support.py").read_bytes()
    for name in builder.OWNER_RUN_FILES:
        assert result["files"][name] == builder.sha256_file(home / result["path"] / name)


def test_owner_protocol_survives_real_zip_roundtrip(source, tmp_path):
    home = tmp_path / "BOSSMAN-Windows-x64-test"
    builder.install_support(home / "app-support")
    archive = tmp_path / "support-only-regression.zip"
    builder.write_archive(home, archive, 1700000000)
    with zipfile.ZipFile(archive) as zf:
        assert zf.testzip() is None
        for name in REQUIRED:
            assert zf.read(f"{home.name}/{name}") == (source / name).read_bytes()
        protocol = json.loads(zf.read(f"{home.name}/tests/owner_hardware/manifest.json"))
        assert [case["id"] for case in protocol["cases"]] == [f"HW-{i:02d}" for i in range(1, 14)]
