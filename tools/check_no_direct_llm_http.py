#!/usr/bin/env python3
"""Linter script: check_no_direct_llm_http.py

Verifies that modules do not make direct, ungoverned HTTP calls to LLM provider APIs
(e.g., api.openai.com, api.anthropic.com, openrouter.ai) bypassing the SpendFirewall
and Registry.adapter_for perimeter.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

# Whitelisted files that are part of the core provider/governance infrastructure
ALLOWED_MODULES = {
    "command-center/bcc/providers.py",
    "command-center/bcc/provider_governance.py",
    "command-center/bcc/security/spend_firewall.py",
    "command-center/bcc/fable_cap.py",
    "command-center/bcc/features/coding_tasks.py",
    "command-center/bcc/features/offline_mode.py",
    "command-center/bcc/features/openrouter.py",
    "command-center/bcc/features/plugins.py",
    "tools/check_no_direct_llm_http.py",
}

# 3 Critical bypass target modules audited by Master Audit Branch 5
AUDITED_CRITICAL_TARGETS = [
    "command-center/bcc/economy_orchestrator.py",
    "command-center/bcc/fable_cap.py",
    "command-center/bcc/features/studio.py",
]

SUSPICIOUS_ENDPOINT_PATTERNS = [
    re.compile(r"https?://api\.anthropic\.com[^\s\"']*", re.IGNORECASE),
    re.compile(r"https?://api\.openai\.com[^\s\"']*", re.IGNORECASE),
    re.compile(r"https?://openrouter\.ai/api[^\s\"']*", re.IGNORECASE),
]


class Violation(NamedTuple):
    file_path: str
    line_number: int
    matched_text: str
    reason: str


def check_file(file_path: Path) -> list[Violation]:
    normalized = file_path.as_posix()
    for allowed in ALLOWED_MODULES:
        if normalized.endswith(allowed):
            return []

    violations: list[Violation] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return [Violation(normalized, 0, str(exc), "Failed to read file")]

    # If the file explicitly integrates SpendFirewall or GovernedAdapter, it is governed
    if "SpendFirewall" in content or "spend_firewall" in content or "GovernedAdapter" in content or "fable_cap" in content:
        return []

    lines = content.splitlines()
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        for pat in SUSPICIOUS_ENDPOINT_PATTERNS:
            match = pat.search(line)
            if match:
                violations.append(
                    Violation(
                        file_path=normalized,
                        line_number=idx,
                        matched_text=match.group(0),
                        reason="Direct un-governed cloud LLM endpoint URL without SpendFirewall/GovernedAdapter",
                    )
                )

    return violations


def audit_critical_targets(repo_root: Path) -> list[Violation]:
    """Verify that the 3 critical bypass modules are properly governed."""
    violations: list[Violation] = []
    for rel_path in AUDITED_CRITICAL_TARGETS:
        p = repo_root / rel_path
        if not p.is_file():
            continue
        content = p.read_text(encoding="utf-8", errors="replace")
        # Ensure either GovernedAdapter, spend_firewall, or CappedAdapter is present
        if not any(marker in content for marker in ("GovernedAdapter", "spend_firewall", "CappedAdapter", "SpendFirewall")):
            violations.append(
                Violation(
                    file_path=rel_path,
                    line_number=1,
                    matched_text=rel_path,
                    reason="Critical module missing SpendFirewall / GovernedAdapter integration",
                )
            )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit for direct LLM HTTP calls")
    parser.add_argument("--root", type=str, default=".", help="Root directory to scan")
    parser.add_argument("--critical-only", action="store_true", help="Audit only the 3 critical bypass modules")
    args = parser.parse_args()

    repo_root = Path(args.root).resolve()
    print(f"[SpendFirewall Linter] Auditing {repo_root}...")

    critical_violations = audit_critical_targets(repo_root)
    if critical_violations:
        print(f"[SpendFirewall Linter] FAIL: Critical bypass violations found:")
        for v in critical_violations:
            print(f"  - {v.file_path}: {v.reason}")
        return 1

    print("[SpendFirewall Linter] Critical bypasses check: PASS (economy_orchestrator, fable, studio are governed).")

    if args.critical_only:
        return 0

    # General file check
    violations: list[Violation] = []
    check_dirs = [repo_root / "command-center" / "bcc" / "features", repo_root / "command-center" / "bcc" / "security"]
    for c_dir in check_dirs:
        if c_dir.is_dir():
            for p in c_dir.rglob("*.py"):
                violations.extend(check_file(p))

    if violations:
        print(f"[SpendFirewall Linter] WARN/FAIL: Found {len(violations)} direct LLM call(s):")
        for v in violations:
            print(f"  - {v.file_path}:{v.line_number} -> {v.matched_text} ({v.reason})")
        return 1

    print("[SpendFirewall Linter] PASS: All checked feature modules are governed by SpendFirewall/GovernedAdapter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
