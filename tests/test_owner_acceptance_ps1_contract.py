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
