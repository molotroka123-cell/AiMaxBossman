"""Produce measured virtual-mode evidence. Never enables live execution."""
import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "solana_volume_suite"
sys.path.insert(0, str(ROOT))
from solana_volume_suite.core.security import require_virtual_mode
from tools.ci_secret_scan import scan_paths


def source_files():
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z",
                             "--", "solana_volume_suite", "tests/test_solana_safety.py"],
                            cwd=ROOT, capture_output=True, check=True, timeout=10)
    return sorted({ROOT / item for item in result.stdout.decode().split("\0") if item})


def build_report(output, github_csv):
    require_virtual_mode()
    output.parent.mkdir(parents=True, exist_ok=True)
    junit = output.with_suffix(".junit.xml")
    command = [sys.executable, "-m", "pytest", "-q", "solana_volume_suite/tests",
               "tests/test_solana_safety.py", "--junitxml=" + str(junit)]
    env = os.environ.copy()
    # Fixtures set their own mock credentials; no provider credentials are used.
    test_status = {"status": "UNKNOWN", "scope": "suite plus root Solana safety regression"}
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=120)
        test_status["exit_code"] = result.returncode
        test_status["status"] = "PASS" if result.returncode == 0 else "FAIL"
        if junit.exists():
            suites = list(ET.parse(junit).getroot().iter("testsuite"))
            test_status.update({key: sum(int(suite.get(key, "0")) for suite in suites)
                                for key in ("tests", "failures", "errors", "skipped")})
    except subprocess.TimeoutExpired:
        test_status["status"] = "TIMEOUT"
    paths = [path for path in source_files() if path.is_file()]
    findings = scan_paths(paths, ROOT)
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    github = {"status": "NOT_RUN", "rows": 0}
    if github_csv.exists():
        with github_csv.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        github = {"status": "CSV_PRESENT_NOT_INDEPENDENTLY_VERIFIED", "rows": len(rows),
                  "path": str(github_csv), "sha256": hashlib.sha256(github_csv.read_bytes()).hexdigest()}
    return {
        "mission_id": "ASTRA_VIRTUAL_BOT_HARDCENING_AND_GITHUB_HYGIENE_001",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": digest.hexdigest(),
        "mode": {"LIVE_EXECUTION_ENABLED": False, "PAPER_TRADING": True, "GEMINI_REAL_MONEY_READY": False},
        "tests": test_status,
        "secret_scan": {"status": "PASS" if not findings else "REVIEW_REQUIRED",
                        "scope": "tracked and nonignored suite files plus root Solana regression",
                        "findings": findings, "note": "Pattern/entropy scan; not proof that all secrets or vulnerabilities are absent"},
        "github_hygiene": github,
        "remaining_limitations": [
            "No live execution backend or approved real-money operation",
            "No verified on-chain liquidity adapter; all pool checks are hypothetical",
            "Jito tip calculation is an offline heuristic, not verified current network fee data",
            "Single local worker; rate quotas and GitHub cache are in memory",
            "Full monorepo tests and dependency vulnerability audit are not part of this report",
        ],
        "gemini_instruction": "Review the evidence and GEMINI_REAL_MONEY_CHECKLIST.md. Keep virtual flags unchanged. "
                              "This build cannot launch mainnet and is not a real-money readiness certification.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SUITE / "runtime" / "gemini_report.json")
    parser.add_argument("--github-csv", type=Path, default=SUITE / "runtime" / "github_hygiene_results.csv")
    args = parser.parse_args()
    try:
        report = build_report(args.output.resolve(), args.github_csv.resolve())
    except (PermissionError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"Preparation blocked: {exc}\n")
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["tests"]["status"] == report["secret_scan"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
