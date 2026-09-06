"""Keep the shared IR/real BCC verifier probe in automatic CC collection.

Root CI intentionally has no BCC dependencies. Its pure contract tests expose
this shared probe without collecting it there; CC is the correct owning suite.
This tests projection/readback only, not mission admission or authorization.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def test_mission_ir_projection_rechecks_real_file_post_state(tmp_path):
    source = Path(__file__).resolve().parents[2] / "tests" / "test_epoch4_mission_ir.py"
    spec = spec_from_file_location("epoch4_shared_contract_probe", source)
    assert spec is not None and spec.loader is not None
    probe = module_from_spec(spec)
    spec.loader.exec_module(probe)
    probe.verify_bcc_bridge_against_real_filesystem(tmp_path)
