"""Synthetic answers must never be promoted to QLoRA evidence."""
from pathlib import Path

from tools import run_cyber_qlora_holdout_tournament as tournament


def test_static_responses_cannot_produce_model_pass():
    result = tournament.run_tournament()
    assert result["status"] == "INVALID_METHOD_SYNTHETIC_SCORER"
    assert result.get("gates", {}).get("verdict") != "PASS"
    assert "tournament_metrics" not in result


def test_cli_refuses_to_write_or_overwrite_a_receipt(tmp_path: Path):
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"existing-evidence")
    assert tournament.main(["--out", str(receipt)]) != 0
    assert receipt.read_bytes() == b"existing-evidence"
