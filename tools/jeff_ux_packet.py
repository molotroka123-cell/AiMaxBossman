"""Compact Jeff UX integration packet for coding agents.

Purpose:
- avoid re-reading the whole Bossman repository;
- prove the Jeff branch only touches its isolated surface;
- surface authority leaks before a model is asked to reason about the patch;
- emit the exact small targeted test list.

This tool performs NO model calls and NO writes.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_BRANCH = "integrate/bossman-1.7-unified-20260925"
JEFF_SOURCE_BRANCH = "feat/jeff-ux-voice-avatar-20260926"

RUNTIME_FILES = (
    "command-center/ui/jeff.html",
    "command-center/ui/jeff.css",
    "command-center/ui/jeff.js",
    "command-center/bcc/jeff_desktop.py",
    "command-center/bcc/pit/presentation_profile.py",
    "tools/desktop/install-jeff-shortcut.ps1",
)

TEST_FILES = (
    "command-center/tests/test_jeff_ux_isolation.py",
    "command-center/tests/test_jeff_ux_browser.py",
    "command-center/tests/test_pit_presentation_profile.py",
)

HELPER_FILES = (
    "tools/jeff_ux_packet.py",
    "tools/jeff-ux-fast-check.ps1",
    "tests/test_jeff_ux_packet.py",
)

DOC_FILES = (
    "docs/v1.7/JEFF_UX_INTEGRATION_HANDOFF_20260926.md",
    "docs/v1.7/JEFF_UX_RULES_20260926.md",
)

ALLOWED_BRANCH_FILES = frozenset((*RUNTIME_FILES, *TEST_FILES, *HELPER_FILES, *DOC_FILES))

FORBIDDEN_RUNTIME_PATTERNS = (
    ("owner task creation", re.compile(r"\bapi\.createTask\s*\(")),
    ("generic mutation escape hatch", re.compile(r"\bapi\.raw\s*\(")),
    ("owner control plane", re.compile(r"/api/control-plane")),
    ("terminal API", re.compile(r"/api/terminal")),
    ("browser control API", re.compile(r"/api/browser")),
    ("coding/OpenHands owner API", re.compile(r"/api/opencode")),
    ("shell capability", re.compile(r"shell\.exec")),
    ("computer authority", re.compile(r"computer\.control")),
)


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    line: int
    excerpt: str


@dataclass(frozen=True)
class Packet:
    canonical_branch: str
    canonical_sha: str
    jeff_branch: str
    jeff_sha: str
    merge_base: str
    changed_files: tuple[str, ...]
    unexpected_files: tuple[str, ...]
    authority_findings: tuple[Finding, ...]
    targeted_tests: tuple[str, ...]
    status: str

    def to_json(self) -> dict:
        data = asdict(self)
        data["authority_findings"] = [asdict(x) for x in self.authority_findings]
        return data


def _git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def _resolve(ref: str) -> str:
    candidates = (f"origin/{ref}", ref) if not ref.startswith("origin/") else (ref,)
    for candidate in candidates:
        out = _git("rev-parse", "--verify", candidate, check=False)
        if out:
            return out.splitlines()[-1].strip()
    return ""


def changed_files(merge_base: str, head: str) -> tuple[str, ...]:
    out = _git("diff", "--name-only", f"{merge_base}..{head}")
    return tuple(line.strip().replace("\\", "/") for line in out.splitlines() if line.strip())


def scan_authority(paths: Iterable[str]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for rel in paths:
        path = REPO_ROOT / rel
        if not path.is_file():
            findings.append(Finding(rel, "missing-runtime-file", 0, "file missing"))
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(source.splitlines(), start=1):
            for rule, pattern in FORBIDDEN_RUNTIME_PATTERNS:
                if pattern.search(line):
                    findings.append(Finding(rel, rule, lineno, line.strip()[:180]))
    return tuple(findings)


def recommended_tests() -> tuple[str, ...]:
    return (
        "python -m py_compile command-center/bcc/jeff_desktop.py command-center/bcc/pit/presentation_profile.py tools/jeff_ux_packet.py",
        "node --check command-center/ui/jeff.js",
        "python -m pytest tests/test_jeff_ux_packet.py command-center/tests/test_jeff_ux_isolation.py command-center/tests/test_pit_presentation_profile.py -q --tb=short --maxfail=1",
        "python -m pytest command-center/tests/test_jeff_ux_browser.py -q --tb=short --maxfail=1",
        "python -m pytest command-center/tests/test_pit_foundation.py command-center/tests/test_pit_runtime.py command-center/tests/test_desktop_build_identity.py -q --tb=short --maxfail=1",
    )


def build_packet(canonical: str = CANONICAL_BRANCH, jeff: str = JEFF_SOURCE_BRANCH) -> Packet:
    canonical_sha = _resolve(canonical)
    jeff_sha = _resolve(jeff)
    if not canonical_sha:
        raise RuntimeError(f"cannot resolve canonical ref: {canonical}")
    if not jeff_sha:
        raise RuntimeError(f"cannot resolve Jeff ref: {jeff}")
    base = _git("merge-base", canonical_sha, jeff_sha)
    changed = changed_files(base, jeff_sha)
    unexpected = tuple(p for p in changed if p not in ALLOWED_BRANCH_FILES)
    findings = scan_authority(RUNTIME_FILES)
    status = "PASS" if not unexpected and not findings else "BLOCKED"
    return Packet(
        canonical_branch=canonical,
        canonical_sha=canonical_sha,
        jeff_branch=jeff,
        jeff_sha=jeff_sha,
        merge_base=base,
        changed_files=changed,
        unexpected_files=unexpected,
        authority_findings=findings,
        targeted_tests=recommended_tests(),
        status=status,
    )


def render_markdown(packet: Packet) -> str:
    lines = [
        "# Jeff UX compact integration packet",
        "",
        f"STATUS={packet.status}",
        f"CANONICAL={packet.canonical_branch}@{packet.canonical_sha}",
        f"JEFF={packet.jeff_branch}@{packet.jeff_sha}",
        f"MERGE_BASE={packet.merge_base}",
        "",
        "## Changed files",
        *[f"- {p}" for p in packet.changed_files],
        "",
        "## Unexpected files",
        *([f"- {p}" for p in packet.unexpected_files] or ["- none"]),
        "",
        "## Authority findings",
        *([f"- {f.path}:{f.line} [{f.rule}] {f.excerpt}" for f in packet.authority_findings]
          or ["- none"]),
        "",
        "## Minimal targeted commands",
        *[f"- {cmd}" for cmd in packet.targeted_tests],
        "",
        "## Agent rule",
        "Do not read the whole repo unless a targeted failure points to a specific neighbour.",
        "Do not merge the Jeff branch wholesale; import isolated files onto the latest frozen/candidate Bossman SHA.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit a compact Jeff UX integration packet")
    parser.add_argument("--canonical", default=CANONICAL_BRANCH)
    parser.add_argument("--jeff", default=JEFF_SOURCE_BRANCH)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    packet = build_packet(args.canonical, args.jeff)
    if args.json:
        print(json.dumps(packet.to_json(), ensure_ascii=False, indent=2))
    else:
        print(render_markdown(packet), end="")
    return 0 if packet.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())