"""REAL_SANDBOX benchmark runtime: exercises REAL production boundaries (real
SQLite durable store across a real process restart, real LiveWorkspace on a real
git worktree with `git apply`) without any paid or external service.

Contract: launched by the benchmark runner as a separate process; prints one
JSON row; never imports fixture data; never marks itself LIVE.  The runner, not
this module, assigns the evidence class from the manifest, so a fixture cannot
promote itself into RealCapabilityScore.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .sandbox_row import verify_row

CONTRACT = "bossman-sandbox-runtime/v1"


def _row(case_id: str, seed: int, *, verified: bool, evidence: list[str], tags: list[str], **extra) -> dict:
    base = dict(case_id=case_id, seed=seed, mode="REAL_SANDBOX", contract=CONTRACT, training_eligible=False,
                verified=verified, effects=0, duplicate_effects=0, actions=0, refused=0, recoveries=0,
                tokens_in=0, tokens_out=0, cache_reads=0, cache_writes=0, cache_hits=0, latency_ms=0,
                evidence=evidence, tags=tags)
    base.update(extra)
    return base


def durable_restart(seed: int) -> dict:
    """Process A writes safety state; a genuinely NEW process B must still see it."""
    from bossman.apprentice.durable import DurableSafetyStore  # public production boundary
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="bench-durable-") as tmp:
        db = Path(tmp) / "safety.sqlite"
        store = DurableSafetyStore(db)
        sid, nonce = f"se-{seed}", f"nonce-{seed}"
        claimed, _ = store.claim_side_effect(sid)
        first_nonce = store.consume_nonce_once(nonce)
        store.set_cooldown("lead@example.test", time.time() + 3600)
        score, samples = store.record_teacher_outcome("teacher:test", -0.10, {"why": "benchmark"})
        store.close()
        probe = (
            "import json,sys;from bossman.apprentice.durable import DurableSafetyStore as S;"
            f"s=S({str(db)!r});c,_=s.claim_side_effect({sid!r});n=s.consume_nonce_once({nonce!r});"
            "cd=s.get_cooldown('lead@example.test');sc,sm=s.teacher_outcome('teacher:test');"
            "print(json.dumps({'claim_again':c,'nonce_again':n,'cooldown_active':bool(cd and cd>__import__('time').time()),'score':sc,'samples':sm}))"
        )
        proc = subprocess.run([sys.executable, "-c", probe], text=True, capture_output=True, timeout=60, check=False)
        try:
            other = json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            other = {"error": proc.stderr[-300:]}
    checks = {
        "claim_blocked_after_restart": claimed and other.get("claim_again") is False,
        "nonce_replay_refused_after_restart": first_nonce and other.get("nonce_again") is False,
        "cooldown_survives_restart": other.get("cooldown_active") is True,
        "teacher_penalty_survives_restart": other.get("score") == score and other.get("samples") == samples,
    }
    return _row("sandbox.durable_restart", seed, verified=all(checks.values()),
                evidence=[k for k, v in checks.items() if v] + ([f"failed:{k}" for k, v in checks.items() if not v]),
                tags=["DURABLE-LIVE-002", "DURABLE-LIVE-003", "DURABLE-LIVE-004", "DURABLE-LIVE-005"],
                actions=4, effects=1, refused=2, latency_ms=round((time.monotonic() - started) * 1000, 3),
                restart_probe_ok="error" not in other)


def workspace_patch_rollback(seed: int) -> dict:
    """Real git repo + real LiveWorkspace: unified diff applied by git, protected
    path refused at the workspace layer, restore() returns the tree to the snapshot."""
    from bossman.apprentice.live_workspace import LiveWorkspace, WorkspaceRefused
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="bench-ws-") as tmp:
        root = Path(tmp)
        (root / "app").mkdir(); (root / "tests").mkdir()
        (root / "app" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (root / "tests" / "test_calc.py").write_text("from app.calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        ws = LiveWorkspace(root, allowed_paths=("app/",), protected_paths=("tests/test_calc.py",))
        token = ws.snapshot()
        diff = ("--- a/app/calc.py\n+++ b/app/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n")
        ws.apply(diff)
        patched = ws.read("app/calc.py")
        refused = 0
        for bad in ({"tests/test_calc.py": "def test_add():\n    assert True\n"}, "--- a/tests/test_calc.py\n+++ b/tests/test_calc.py\n@@ -1 +1 @@\n-x\n+y\n"):
            try:
                ws.apply(bad)
            except WorkspaceRefused:
                refused += 1
        ws.restore(token)
        restored = ws.read("app/calc.py")
    checks = {"diff_applied_by_git": "a + b" in patched, "protected_test_refused_twice": refused == 2,
              "restore_returns_snapshot": "a - b" in restored}
    return _row("sandbox.workspace_patch_rollback", seed, verified=all(checks.values()),
                evidence=[k for k, v in checks.items() if v] + ([f"failed:{k}" for k, v in checks.items() if not v]),
                tags=["TEACHER-ISO-002", "TEACHER-ISO-003", "TEACHER-ISO-004", "TEACHER-ISO-005"],
                actions=3, refused=refused, latency_ms=round((time.monotonic() - started) * 1000, 3))


def _registry() -> dict:
    """Legacy cases in this module plus every capability case module."""
    from .sandbox_cases import CASES as _package_cases
    cases = {"sandbox.durable_restart": durable_restart, "sandbox.workspace_patch_rollback": workspace_patch_rollback}
    clash = set(cases) & set(_package_cases)
    if clash:
        raise RuntimeError(f"duplicate sandbox case ids: {sorted(clash)}")
    cases.update(_package_cases)
    return cases


CASES = _registry()


def run(case_id: str, seed: int) -> dict:
    if case_id not in CASES:
        raise ValueError(f"unknown sandbox case {case_id!r}")
    row = CASES[case_id](seed)
    # Second, independent pass of the trusted verifier at the process boundary:
    # a case that mutated `verified` after building its row is corrected here.
    if row.get("checks") is not None:
        row = verify_row(row)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(prog="bossman-benchmark-sandbox")
    parser.add_argument("--case", required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.case, args.seed), sort_keys=True))
    except Exception as exc:  # non-zero = failed benchmark case
        print(json.dumps({"error": type(exc).__name__, "reason": str(exc)[:500]}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
