"""Adversarial Challenge Test Suite for Milestone 2 (D-030 & D-031).

Empirical challenger: challenger_m2_1 (critic, specialist).
Strict review and stress harness testing of pc.self_modify propose and rollback boundaries.

Verification Scope:
1. D-030 (pc.self_modify propose):
   - Probe propose with malicious/untrusted code and test payloads
   - Verify zero execution occurs on host (file write, subprocess, exfiltration canaries)
   - Verify probe_os_sandbox() blocks execution and returns status="verification_blocked_unisolated"
   - Verify fail-closed behavior across autonomy levels L0..L3 and even with approved=True
   - Verify targets outside workspace and immutable core targets are strictly rejected

2. D-031 (pc.self_modify rollback):
   - Probe rollback with path traversal (../../) in mod_id and in applied_files
   - Probe rollback with malformed, invalid, and non-regex mod_ids
   - Probe rollback with paths pointing to immutable core security files:
     * approvals.py
     * permissions.py
     * secrets.py
     * auth.py
     * control_plane.py
     * docs/constitution/*
   - Verify all unsafe target paths, foreign paths, and corrupted backup records are rejected
     with status="blocked_unsafe_rollback_record"
   - Verify target files are never deleted and never overwritten under attack

3. Positive Control:
   - Legitimate proposal inside workspace rolls back cleanly with status="rolled_back"
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# Ensure command-center and tests root are on sys.path
TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

from test_pc_agent_master_p0_p5 import _DummySvc  # noqa: E402
from bcc.features import pc_agent  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Adversarial challenge targets Windows host security")


def _run_step(svc: Any, rt: pc_agent.PCAgentRuntime, args: dict[str, Any], *, approved: bool = False, task_id: int = 801) -> dict[str, Any]:
    return asyncio.run(
        pc_agent.execute_pc_step(
            svc,
            rt,
            task_id=task_id,
            run_id=task_id,
            step_idx=1,
            tool="pc.self_modify",
            args=args,
            approved=approved,
        )
    )


# ==============================================================================
# SECTION 1: D-030 pc.self_modify propose Adversarial Challenge
# ==============================================================================

class TestProposeAdversarialBoundaries:
    """Stress tests probing untrusted execution and isolation boundaries during propose."""

    def test_probe_os_sandbox_returns_false_on_host(self) -> None:
        """Verify probe_os_sandbox() returns False on this Windows 11 host."""
        assert pc_agent.probe_os_sandbox() is False, (
            "probe_os_sandbox() must return False on host lacking container sandbox"
        )

    def test_propose_malicious_import_time_canary_does_not_execute(self, tmp_path: Path) -> None:
        """Adversarial Payload: Python code executing at module import / load time.
        Must NOT be executed on host; canary file must NOT be created."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime
        canary = tmp_path / "CANARY_IMPORT_EXEC.txt"

        malicious_test = (
            "import pathlib\n"
            f"pathlib.Path(r'{canary}').write_text('PWNED_IMPORT', encoding='utf-8')\n\n"
            "def test_exploit():\n"
            "    assert True\n"
        )

        res = _run_step(
            svc,
            rt,
            {
                "action": "propose",
                "topic": "adv-import",
                "target_file": "skills/adv_import.py",
                "code": "# synthetic skill code\n",
                "test_code": malicious_test,
            },
        )

        assert not canary.exists(), "CRITICAL: Import-time canary executed on host!"
        assert res["status"] == "verification_blocked_unisolated"
        assert res["tests_passed"] is False
        assert res["negative_control_passed"] is False
        assert res["approval_id"] is None
        assert "NOT_RUN:" in res["pytest_output"]

    def test_propose_malicious_subprocess_payload_does_not_execute(self, tmp_path: Path) -> None:
        """Adversarial Payload: Subprocess command execution via cmd.exe.
        Must NOT be executed on host; canary file must NOT be created."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime
        canary = tmp_path / "CANARY_SUBPROCESS.txt"

        malicious_test = (
            "import subprocess\n"
            f"subprocess.run(['cmd.exe', '/c', 'echo subproc > {canary}'], check=False)\n\n"
            "def test_dummy():\n"
            "    pass\n"
        )

        res = _run_step(
            svc,
            rt,
            {
                "action": "propose",
                "topic": "adv-subproc",
                "target_file": "skills/adv_subproc.py",
                "code": "# synthetic\n",
                "test_code": malicious_test,
            },
        )

        assert not canary.exists(), "CRITICAL: Subprocess payload executed on host!"
        assert res["status"] == "verification_blocked_unisolated"
        assert res["tests_passed"] is False

    def test_propose_env_exfiltration_payload_does_not_execute(self, tmp_path: Path) -> None:
        """Adversarial Payload: Attempting to dump environment variables to disk.
        Must NOT execute on host."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime
        exfil_file = tmp_path / "EXFILTRATED_ENV.txt"

        malicious_code = (
            "import os, pathlib\n"
            f"pathlib.Path(r'{exfil_file}').write_text(str(dict(os.environ)), encoding='utf-8')\n"
        )
        malicious_test = (
            "from candidate_skill import *\n"
            "def test_noop(): pass\n"
        )

        res = _run_step(
            svc,
            rt,
            {
                "action": "propose",
                "topic": "adv-exfil",
                "target_file": "skills/adv_exfil.py",
                "code": malicious_code,
                "test_code": malicious_test,
            },
        )

        assert not exfil_file.exists(), "CRITICAL: Environment exfiltration code executed on host!"
        assert res["status"] == "verification_blocked_unisolated"

    def test_propose_fail_closed_even_with_approved_and_apply_after_approval(self, tmp_path: Path) -> None:
        """Probe: Caller sends approved=True and apply_after_approval=True with malicious test.
        Must FAIL CLOSED: target file must NOT be written and status must remain blocked."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime
        target = rt.workspace_dir / "skills" / "pwned_target.py"
        canary = tmp_path / "CANARY_APPROVED.txt"

        malicious_test = (
            "import pathlib\n"
            f"pathlib.Path(r'{canary}').write_text('Bypassed with approved=True', encoding='utf-8')\n"
        )

        res = _run_step(
            svc,
            rt,
            {
                "action": "propose",
                "topic": "pwned-target",
                "target_file": "skills/pwned_target.py",
                "code": "# malicious replacement\n",
                "test_code": malicious_test,
                "apply_after_approval": True,
            },
            approved=True,
        )

        assert not canary.exists(), "CRITICAL: Malicious test executed when approved=True!"
        assert not target.exists(), "CRITICAL: Target file was written despite unisolated sandbox!"
        assert res["status"] == "verification_blocked_unisolated"
        assert res["tests_passed"] is False

    @pytest.mark.parametrize("autonomy_level", ["L0", "L1", "L2", "L3"])
    def test_propose_fail_closed_across_all_autonomy_levels(self, tmp_path: Path, autonomy_level: str) -> None:
        """Verify fail-closed invariant holds uniformly across all autonomy levels L0..L3:
        - L0: strictly denied at policy gate (read-only)
        - L1 unapproved: gated as T1 ask
        - L1 approved, L2, L3: enters propose step and fails closed via probe_os_sandbox()
        - Under ALL levels: zero host execution occurs (canary never created)."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime
        rt.set_autonomy_level(autonomy_level, actor="owner")

        canary = tmp_path / f"CANARY_{autonomy_level}.txt"
        test_code = (
            "import pathlib\n"
            f"pathlib.Path(r'{canary}').write_text('{autonomy_level}', encoding='utf-8')\n"
        )

        res = _run_step(
            svc,
            rt,
            {
                "action": "propose",
                "topic": f"autonomy-{autonomy_level}",
                "target_file": f"skills/autonomy_{autonomy_level}.py",
                "code": "# pass\n",
                "test_code": test_code,
            },
            approved=(autonomy_level == "L1"),  # test approved under L1 to verify probe_os_sandbox gating
        )

        assert not canary.exists(), f"Canary executed under autonomy level {autonomy_level}!"
        if autonomy_level == "L0":
            assert res.get("decision") == "deny"
            assert res.get("category") == "autonomy_l0_readonly"
        else:
            assert res.get("status") == "verification_blocked_unisolated"
            assert res.get("tests_passed") is False

    def test_propose_rejects_target_outside_workspace(self, tmp_path: Path) -> None:
        """Probe propose target_file path escaping workspace root (traversal or foreign root)."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        # Traversal target
        res_traversal = _run_step(
            svc,
            rt,
            {
                "action": "propose",
                "topic": "escape-traversal",
                "target_file": "../../outside_ws.py",
                "code": "# payload\n",
            },
        )
        assert res_traversal["status"] == "blocked_outside_workspace"

        # Foreign absolute target
        res_abs = _run_step(
            svc,
            rt,
            {
                "action": "propose",
                "topic": "escape-abs",
                "target_file": str(tmp_path.parent / "foreign_abs.py"),
                "code": "# payload\n",
            },
        )
        assert res_abs["status"] == "blocked_outside_workspace"

    def test_propose_rejects_immutable_core_target_immediately(self, tmp_path: Path) -> None:
        """Probe propose target_file pointing directly to immutable core security file."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        res = _run_step(
            svc,
            rt,
            {
                "action": "propose",
                "topic": "attack-permissions",
                "target_file": "command-center/bcc/permissions.py",
                "code": "# malicious permissions override\n",
            },
        )
        assert res["tier"] == "T3"
        assert res["decision"] == "deny"
        assert res["owner_alert"] is True


# ==============================================================================
# SECTION 2: D-031 pc.self_modify rollback Adversarial Challenge
# ==============================================================================

class TestRollbackAdversarialBoundaries:
    """Stress tests probing rollback validation, path traversal, malformed mod_ids,
    and protection of immutable core security files."""

    @pytest.mark.parametrize(
        "bad_mod_id",
        [
            "../../",
            "..\\..\\",
            "../../../etc/passwd",
            "..",
            "sm-../../../escape",
            "sm-escape/../../something",
            "invalid_mod_id",
            "sm-",
            "sm-with spaces",
            "sm-UPPERCASE_NOT_ALLOWED",
            "sm-has!special#chars",
            "sm-" + ("a" * 100),  # > 80 chars
            "sm-has\x00nullbyte",
            "/absolute/unix/path",
            "C:\\Windows\\System32",
            "\\\\unc\\share\\mod",
        ],
    )
    def test_rollback_rejects_malformed_and_traversal_mod_ids(self, tmp_path: Path, bad_mod_id: str) -> None:
        """Probe: malformed mod_ids or path traversal mod_ids must be rejected with status='blocked_invalid_mod_id'."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": bad_mod_id},
            approved=True,
        )

        assert res.get("ok") is False
        assert res.get("status") == "blocked_invalid_mod_id"
        assert "Недопустимый mod_id" in res.get("error", "") or "выходит за каталог" in res.get("error", "")

    def test_rollback_rejects_nonexistent_valid_mod_id(self, tmp_path: Path) -> None:
        """Probe: syntactically valid mod_id that does not exist on disk."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": "sm-valid-format-but-nonexistent"},
            approved=True,
        )

        assert res.get("ok") is False
        assert res.get("status") == "blocked_not_found"

    def test_rollback_rejects_corrupted_proposal_json(self, tmp_path: Path) -> None:
        """Probe: proposal.json with corrupted or invalid JSON content."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime
        mod_id = "sm-corrupted-json-test"
        mod_dir = rt.self_mod_dir / mod_id
        mod_dir.mkdir(parents=True, exist_ok=True)
        (mod_dir / "proposal.json").write_text("{malformed: json, unterminated", encoding="utf-8")

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": mod_id},
            approved=True,
        )

        assert res.get("ok") is False
        assert res.get("status") == "blocked_invalid_proposal"

    def test_rollback_rejects_path_traversal_in_applied_files(self, tmp_path: Path) -> None:
        """Probe: Forged proposal attempting path traversal (../../) in applied_files.
        Must be rejected with status='blocked_unsafe_rollback_record'. Target must NOT be deleted."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        # Create a victim file outside the workspace root
        victim = tmp_path / "foreign_victim.txt"
        victim.write_text("CRITICAL_FOREIGN_DATA", encoding="utf-8")

        mod_id = "sm-forged-traversal-01"
        mod_dir = rt.self_mod_dir / mod_id
        mod_dir.mkdir(parents=True, exist_ok=True)

        forged_proposal = {
            "mod_id": mod_id,
            "status": "applied",
            "applied_files": [
                {
                    "target_path": str(victim),  # outside rt.workspace_dir
                    "existed_before": False,
                    "backup_path": None,
                }
            ],
        }
        (mod_dir / "proposal.json").write_text(json.dumps(forged_proposal), encoding="utf-8")

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": mod_id},
            approved=True,
        )

        assert res.get("ok") is False
        assert res.get("status") == "blocked_unsafe_rollback_record"
        assert victim.exists(), "CRITICAL: Foreign victim file was deleted by rollback!"
        assert victim.read_text(encoding="utf-8") == "CRITICAL_FOREIGN_DATA"

    @pytest.mark.parametrize(
        "core_target_subpath",
        [
            "approvals.py",
            "permissions.py",
            "secrets.py",
            "command-center/bcc/approvals.py",
            "command-center/bcc/permissions.py",
            "command-center/bcc/secrets.py",
            "command-center/bcc/auth.py",
            "command-center/bcc/features/control_plane.py",
            "docs/constitution/constitution.md",
            "docs/constitution/constitution.sha256",
        ],
    )
    def test_rollback_rejects_immutable_core_deletion_attack(self, tmp_path: Path, core_target_subpath: str) -> None:
        """Probe: Forged proposal claiming an immutable core security file was added (existed_before=False)
        and requesting rollback to delete it.
        Must be rejected with status='blocked_unsafe_rollback_record'; file MUST NOT be deleted."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        # Place target inside workspace_dir to pass the workspace boundary check
        target_path = rt.workspace_dir / core_target_subpath
        target_path.parent.mkdir(parents=True, exist_ok=True)
        original_content = f"# IMMUTABLE CORE CANARY: {core_target_subpath}"
        target_path.write_text(original_content, encoding="utf-8")

        mod_id = f"sm-attack-delete-{abs(hash(core_target_subpath)) % 1000000}"
        mod_dir = rt.self_mod_dir / mod_id
        mod_dir.mkdir(parents=True, exist_ok=True)

        forged_proposal = {
            "mod_id": mod_id,
            "status": "applied",
            "applied_files": [
                {
                    "target_path": str(target_path),
                    "existed_before": False,
                    "backup_path": None,
                }
            ],
        }
        (mod_dir / "proposal.json").write_text(json.dumps(forged_proposal), encoding="utf-8")

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": mod_id},
            approved=True,
        )

        assert res.get("ok") is False
        assert res.get("status") == "blocked_unsafe_rollback_record", (
            f"Expected blocked_unsafe_rollback_record for {core_target_subpath}, got {res.get('status')}"
        )
        assert target_path.exists(), f"CRITICAL: Immutable core file {core_target_subpath} was deleted!"
        assert target_path.read_text(encoding="utf-8") == original_content

    @pytest.mark.parametrize(
        "core_target_subpath",
        [
            "approvals.py",
            "permissions.py",
            "secrets.py",
            "command-center/bcc/approvals.py",
            "command-center/bcc/permissions.py",
            "command-center/bcc/secrets.py",
            "command-center/bcc/auth.py",
            "command-center/bcc/features/control_plane.py",
            "docs/constitution/constitution.md",
            "docs/constitution/constitution.sha256",
        ],
    )
    def test_rollback_rejects_immutable_core_overwrite_attack(self, tmp_path: Path, core_target_subpath: str) -> None:
        """Probe: Forged proposal claiming an immutable core security file existed (existed_before=True)
        with a forged backup in mod_dir, requesting rollback to overwrite it.
        Must be rejected with status='blocked_unsafe_rollback_record'; file MUST NOT be overwritten."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        target_path = rt.workspace_dir / core_target_subpath
        target_path.parent.mkdir(parents=True, exist_ok=True)
        original_content = f"# GENUINE IMMUTABLE CORE: {core_target_subpath}"
        target_path.write_text(original_content, encoding="utf-8")

        mod_id = f"sm-attack-overwrite-{abs(hash(core_target_subpath)) % 1000000}"
        mod_dir = rt.self_mod_dir / mod_id
        mod_dir.mkdir(parents=True, exist_ok=True)

        # Forged malicious backup
        forged_backup = mod_dir / "backup-malicious.py"
        forged_backup.write_text("# MALICIOUS OVERWRITE PAYLOAD", encoding="utf-8")

        forged_proposal = {
            "mod_id": mod_id,
            "status": "applied",
            "applied_files": [
                {
                    "target_path": str(target_path),
                    "existed_before": True,
                    "backup_path": str(forged_backup),
                }
            ],
        }
        (mod_dir / "proposal.json").write_text(json.dumps(forged_proposal), encoding="utf-8")

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": mod_id},
            approved=True,
        )

        assert res.get("ok") is False
        assert res.get("status") == "blocked_unsafe_rollback_record", (
            f"Expected blocked_unsafe_rollback_record for {core_target_subpath}, got {res.get('status')}"
        )
        assert target_path.read_text(encoding="utf-8") == original_content, (
            f"CRITICAL: Immutable core file {core_target_subpath} was overwritten!"
        )

    def test_rollback_rejects_backup_path_traversal_outside_mod_dir(self, tmp_path: Path) -> None:
        """Probe: Forged proposal pointing backup_path outside mod_dir (traversal or foreign path)."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        target = rt.workspace_dir / "safe_file.txt"
        target.write_text("safe content", encoding="utf-8")

        mod_id = "sm-backup-traversal"
        mod_dir = rt.self_mod_dir / mod_id
        mod_dir.mkdir(parents=True, exist_ok=True)

        forged_proposal = {
            "mod_id": mod_id,
            "status": "applied",
            "applied_files": [
                {
                    "target_path": str(target),
                    "existed_before": True,
                    "backup_path": str(tmp_path / "foreign_backup.txt"),  # outside mod_dir
                }
            ],
        }
        (mod_dir / "proposal.json").write_text(json.dumps(forged_proposal), encoding="utf-8")

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": mod_id},
            approved=True,
        )

        assert res.get("ok") is False
        assert res.get("status") == "blocked_unsafe_rollback_record"

    def test_rollback_rejects_missing_backup_path_when_existed_before(self, tmp_path: Path) -> None:
        """Probe: existed_before=True but backup_path is None or missing file."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        target = rt.workspace_dir / "safe_file2.txt"
        target.write_text("safe content", encoding="utf-8")

        mod_id = "sm-missing-backup"
        mod_dir = rt.self_mod_dir / mod_id
        mod_dir.mkdir(parents=True, exist_ok=True)

        forged_proposal = {
            "mod_id": mod_id,
            "status": "applied",
            "applied_files": [
                {
                    "target_path": str(target),
                    "existed_before": True,
                    "backup_path": str(mod_dir / "nonexistent_backup.txt"),
                }
            ],
        }
        (mod_dir / "proposal.json").write_text(json.dumps(forged_proposal), encoding="utf-8")

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": mod_id},
            approved=True,
        )

        assert res.get("ok") is False
        assert res.get("status") == "blocked_unsafe_rollback_record"

    def test_rollback_rejects_malformed_applied_files_entry(self, tmp_path: Path) -> None:
        """Probe: applied_files containing non-dict or dict without target_path."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        mod_id = "sm-malformed-records"
        mod_dir = rt.self_mod_dir / mod_id
        mod_dir.mkdir(parents=True, exist_ok=True)

        forged_proposal = {
            "mod_id": mod_id,
            "status": "applied",
            "applied_files": ["not-a-dict", {"no_target_path": True}],
        }
        (mod_dir / "proposal.json").write_text(json.dumps(forged_proposal), encoding="utf-8")

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": mod_id},
            approved=True,
        )

        assert res.get("ok") is False
        assert res.get("status") == "blocked_unsafe_rollback_record"


# ==============================================================================
# SECTION 3: Positive Control — Legitimate Workspace Rollback
# ==============================================================================

class TestRollbackPositiveControl:
    """Verifies that legitimate rollback within workspace functions correctly."""

    def test_legitimate_rollback_restores_file(self, tmp_path: Path) -> None:
        """A valid proposal inside workspace with valid backup in mod_dir rolls back cleanly."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        target = rt.workspace_dir / "skills" / "my_valid_skill.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# MODIFIED BY SKILL", encoding="utf-8")

        mod_id = "sm-legit-rollback-test"
        mod_dir = rt.self_mod_dir / mod_id
        mod_dir.mkdir(parents=True, exist_ok=True)

        backup = mod_dir / "backup-my_valid_skill.py"
        backup.write_text("# ORIGINAL VALID CONTENT", encoding="utf-8")

        proposal = {
            "mod_id": mod_id,
            "status": "applied",
            "applied_files": [
                {
                    "target_path": str(target),
                    "existed_before": True,
                    "backup_path": str(backup),
                }
            ],
        }
        (mod_dir / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": mod_id},
            approved=True,
        )

        assert res.get("ok") is True
        assert res.get("status") == "rolled_back"
        assert target.read_text(encoding="utf-8") == "# ORIGINAL VALID CONTENT"

    def test_legitimate_rollback_deletes_newly_created_file(self, tmp_path: Path) -> None:
        """A newly added file (existed_before=False) is cleanly unlinked on rollback."""
        svc = _DummySvc(tmp_path)
        rt = svc._pc_agent_runtime

        target = rt.workspace_dir / "skills" / "newly_added_skill.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# NEW SKILL", encoding="utf-8")

        mod_id = "sm-legit-delete-new"
        mod_dir = rt.self_mod_dir / mod_id
        mod_dir.mkdir(parents=True, exist_ok=True)

        proposal = {
            "mod_id": mod_id,
            "status": "applied",
            "applied_files": [
                {
                    "target_path": str(target),
                    "existed_before": False,
                    "backup_path": None,
                }
            ],
        }
        (mod_dir / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")

        res = _run_step(
            svc,
            rt,
            {"action": "rollback", "mod_id": mod_id},
            approved=True,
        )

        assert res.get("ok") is True
        assert res.get("status") == "rolled_back"
        assert not target.exists(), "Newly created file should be unlinked upon rollback"
