"""Red Team Self-Modify Invariants (2026-10-27 audit & ASTRA OS sandbox freeze).

Ensures that:
1. Candidate Python proposals NEVER execute untrusted code on the unisolated host.
2. Rollback cannot delete or overwrite foreign files outside the sandbox.
3. Mod IDs cannot escape self_mod_dir.
4. Host without verified hermetic container fails closed (verification_blocked_unisolated).
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_pc_agent_selfmod_claude import (
    test_propose_with_agent_supplied_test_code_is_not_auto,
    test_agent_supplied_test_code_does_not_run_without_approval,
    test_negative_control_default_template_proposal_still_works,
    test_forged_proposal_cannot_make_rollback_delete_foreign_file,
    test_rollback_mod_id_cannot_escape_self_mod_dir,
)
from bcc.security.astra_sandbox import verify_self_modify_candidate_isolation

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="политика ПК-агента рассчитана на Windows")


def test_redteam_astra_sandbox_fail_closed_contract():
    """Red Team invariant: candidate Python execution is blocked without hermetic container."""
    res = verify_self_modify_candidate_isolation()
    assert res["allowed"] is False
    assert res["status"] == "verification_blocked_unisolated"
    assert res["tests_passed"] is False
