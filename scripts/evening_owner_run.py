#!/usr/bin/env python3
"""One exact-SHA entry point for the owner's evening acceptance run.

This wrapper exists so evidence from different commits cannot be mixed by
accident. It verifies the shared convergence branch, requires a clean checkout,
checks the live remote branch head, pins both interactive harnesses to
SHA-specific state directories, records a machine manifest, and only then
starts the live checks.

Usage on the owner Windows machine from the repository root:

    python scripts/evening_owner_run.py preflight
    python scripts/evening_owner_run.py run
    python scripts/evening_owner_run.py status
    python scripts/evening_owner_run.py report

CI may use ``--ci`` to verify the harness without live credentials or a branch
checkout (Actions normally checks out a detached exact SHA).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
CANONICAL_BRANCH = "claude/bossman-final-completion-kymr05"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class PreflightError(RuntimeError):
    """The owner run cannot produce trustworthy evidence yet."""


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=check,
    )


def _exact_identity(*, ci: bool, require_remote: bool) -> dict[str, Any]:
    try:
        head = _git("rev-parse", "HEAD").stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError(f"cannot read Git HEAD: {exc}") from exc
    if not _SHA_RE.fullmatch(head):
        raise PreflightError(f"invalid Git HEAD: {head!r}")

    dirty = _git("status", "--porcelain", "--untracked-files=normal").stdout.strip()
    if dirty:
        preview = "\n".join(dirty.splitlines()[:20])
        raise PreflightError(
            "working tree is not clean; owner evidence must be bound to committed code:\n" + preview
        )

    branch = _git("branch", "--show-current").stdout.strip()
    remote_head: str | None = None
    if ci:
        expected = (os.environ.get("BOSSMAN_ACCEPTANCE_SHA")
                    or os.environ.get("GITHUB_SHA", "")).strip()
        if expected and expected != head:
            raise PreflightError(f"CI SHA mismatch: expected={expected}, HEAD={head}")
    else:
        if branch != CANONICAL_BRANCH:
            raise PreflightError(
                f"wrong branch: {branch or '<detached>'}; expected {CANONICAL_BRANCH}"
            )
        if require_remote:
            try:
                remote = _git(
                    "ls-remote", "--heads", "origin", f"refs/heads/{CANONICAL_BRANCH}"
                ).stdout.strip().split()
            except (OSError, subprocess.SubprocessError) as exc:
                raise PreflightError(f"cannot query origin/{CANONICAL_BRANCH}: {exc}") from exc
            if len(remote) != 2 or remote[1] != f"refs/heads/{CANONICAL_BRANCH}":
                raise PreflightError(f"origin/{CANONICAL_BRANCH} could not be resolved exactly")
            remote_head = remote[0]
            if not _SHA_RE.fullmatch(remote_head):
                raise PreflightError(f"invalid remote SHA: {remote_head!r}")
            if remote_head != head:
                raise PreflightError(
                    "local checkout is not the current shared-branch HEAD: "
                    f"local={head}, remote={remote_head}. Pull/rebase before acceptance."
                )

    return {"sha": head, "branch": branch or "DETACHED", "remote_head": remote_head}


def _roots(sha: str) -> tuple[Path, Path, Path]:
    base = REPO / ".bossman-state" / "acceptance" / sha
    evening = base / "evening"
    breaker = base / "breaker"
    evening.mkdir(parents=True, exist_ok=True)
    breaker.mkdir(parents=True, exist_ok=True)
    return base, evening, breaker


def _environment(identity: dict[str, Any]) -> tuple[dict[str, str], Path]:
    base, evening, breaker = _roots(identity["sha"])
    env = os.environ.copy()
    env["BOSSMAN_ACCEPTANCE_ROOT"] = str(evening)
    env["BOSSMAN_BREAKER_ROOT"] = str(breaker)
    env["BOSSMAN_ACCEPTANCE_SHA"] = identity["sha"]
    return env, base


def _run_python(args: list[str], *, env: dict[str, str]) -> int:
    return subprocess.run([sys.executable, *args], cwd=REPO, env=env, check=False).returncode


def _doctor(env: dict[str, str]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "bossman_doctor.py"), "--json"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PreflightError(
            "bossman_doctor did not return JSON: " + (proc.stderr or proc.stdout)[:500]
        ) from exc
    if not isinstance(report, dict):
        raise PreflightError("bossman_doctor returned a non-object report")
    return report


def _write_manifest(identity: dict[str, Any], base: Path, doctor: dict[str, Any] | None) -> Path:
    manifest = {
        "schema_version": 1,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": identity,
        "machine": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "doctor": doctor,
    }
    path = base / "OWNER_RUN_MANIFEST.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _verify_harnesses(env: dict[str, str]) -> None:
    checks = [
        ["scripts/evening_acceptance.py", "verify"],
        ["scripts/owner_breaker.py", "verify"],
    ]
    failed = []
    for args in checks:
        rc = _run_python(args, env=env)
        if rc:
            failed.append(" ".join(args))
    if failed:
        raise PreflightError("acceptance harness self-check failed: " + ", ".join(failed))


def _preflight(
    *, ci: bool, require_remote: bool = True, require_doctor: bool = True
) -> tuple[dict[str, Any], dict[str, str], Path, dict[str, Any] | None]:
    identity = _exact_identity(ci=ci, require_remote=require_remote)
    env, base = _environment(identity)
    _verify_harnesses(env)
    doctor: dict[str, Any] | None = None
    if require_doctor and not ci:
        doctor = _doctor(env)
        blocked = int(doctor.get("blocked") or 0)
        if blocked:
            _write_manifest(identity, base, doctor)
            raise PreflightError(
                f"bossman_doctor has {blocked} BLOCKED checks; fix them before the live run"
            )
    manifest = _write_manifest(identity, base, doctor)
    print(f"OWNER_ACCEPTANCE_SHA={identity['sha']}")
    print(f"OWNER_ACCEPTANCE_BRANCH={identity['branch']}")
    print(f"OWNER_ACCEPTANCE_MANIFEST={manifest}")
    return identity, env, base, doctor


def cmd_preflight(args: argparse.Namespace) -> int:
    _preflight(ci=args.ci)
    print("OWNER_ACCEPTANCE_PREFLIGHT=PASS")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    identity, env, base, _ = _preflight(ci=False)
    evening_report = base / "EVENING_ACCEPTANCE_RESULT.md"
    breaker_report = base / "OWNER_BREAKER_RESULT.md"

    print(f"\nPinned live run: {identity['sha']}\n")
    rc_evening = _run_python(
        ["scripts/evening_acceptance.py", "run", "--md-out", str(evening_report)], env=env
    )
    # Collect the narrower owner-only breaker even when the broad scenario run
    # found a failure; both evidence sets are useful for correction.
    rc_breaker_run = _run_python(["scripts/owner_breaker.py", "run"], env=env)
    rc_breaker_report = _run_python(
        ["scripts/owner_breaker.py", "report", "--md-out", str(breaker_report)], env=env
    )
    rc = 0 if rc_evening == rc_breaker_run == rc_breaker_report == 0 else 1
    print(f"\nOWNER_ACCEPTANCE_SHA={identity['sha']}")
    print(f"OWNER_ACCEPTANCE_RESULT={'PASS' if rc == 0 else 'INCOMPLETE_OR_FAIL'}")
    print(f"OWNER_ACCEPTANCE_EVIDENCE={base}")
    return rc


def cmd_status(args: argparse.Namespace) -> int:
    identity, env, base, _ = _preflight(ci=False, require_remote=False, require_doctor=False)
    rc1 = _run_python(["scripts/evening_acceptance.py", "status"], env=env)
    rc2 = _run_python(["scripts/owner_breaker.py", "status"], env=env)
    print(f"\nSHA={identity['sha']}  evidence={base}")
    return 0 if rc1 == rc2 == 0 else 1


def cmd_report(args: argparse.Namespace) -> int:
    identity, env, base, _ = _preflight(ci=False, require_remote=False, require_doctor=False)
    rc1 = _run_python(
        ["scripts/evening_acceptance.py", "report", "--md-out", str(base / "EVENING_ACCEPTANCE_RESULT.md")],
        env=env,
    )
    rc2 = _run_python(
        ["scripts/owner_breaker.py", "report", "--md-out", str(base / "OWNER_BREAKER_RESULT.md")],
        env=env,
    )
    print(f"\nSHA={identity['sha']}  evidence={base}")
    return 0 if rc1 == rc2 == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--ci", action="store_true",
        help="detached exact-SHA self-check: skip remote branch and live-credential doctor",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("preflight", help="verify exact SHA, branch, doctor and both harnesses").set_defaults(func=cmd_preflight)
    sub.add_parser("run", help="preflight, then run both live acceptance harnesses").set_defaults(func=cmd_run)
    sub.add_parser("status", help="show status for this exact SHA only").set_defaults(func=cmd_status)
    sub.add_parser("report", help="write both reports under this exact SHA").set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        if args.ci and args.command != "preflight":
            raise PreflightError("--ci is only valid with preflight")
        return args.func(args)
    except PreflightError as exc:
        print(f"OWNER_ACCEPTANCE_PREFLIGHT=FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
