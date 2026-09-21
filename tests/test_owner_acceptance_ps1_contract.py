from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "owner-acceptance.ps1"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_owner_acceptance_prefers_bundled_runtime() -> None:
    text = _text()
    assert "$PSScriptRoot" in text
    assert 'runtime\\python.exe' in text
    assert "ENGINEERING_ONLY" in text


def test_owner_acceptance_deletes_stale_evidence_before_running() -> None:
    text = _text()
    remove_json = text.index("Remove-Item -Force $out")
    invoke = text.index("& $python -I -m bcc.owner_acceptance")
    assert remove_json < invoke
    assert "current owner-acceptance run produced no JSON result" in text


def test_owner_acceptance_points_to_all_thirteen_hardware_cases() -> None:
    text = _text()
    assert "HW-01..HW-13" in text
    assert "HW-01..HW-12" not in text
    assert "MVCR" in text


def test_missing_configuration_also_invalidates_old_success() -> None:
    text = _text()
    configuration_check = text.index("if (-not $env:BCC_DATA_DIR)")
    assert text.index("Remove-Item -Force $out") < configuration_check
    assert text.index("Remove-Item -Force $mdOut") < configuration_check


def test_installed_bundle_must_not_fall_back_to_global_python() -> None:
    text = _text()
    global_python = text.index("Get-Command python")
    assert text.index("FAIL: installed bundle runtime is missing") < global_python
    assert 'Join-Path $root "MANIFEST.json"' in text


def test_receipt_must_match_bundle_identity() -> None:
    text = _text()
    assert "$data.source_sha -cne $manifest.source_sha" in text
    assert "FAIL: owner-acceptance receipt does not match this bundle" in text


def test_core_receipt_cannot_claim_hardware_certification() -> None:
    text = _text()
    assert "Hardware certification: NOT_PERFORMED" in text
    assert "Process exit code: $code" in text
    assert "$data.status -eq 'PASS'" in text
    assert "$code -ne 0" in text
