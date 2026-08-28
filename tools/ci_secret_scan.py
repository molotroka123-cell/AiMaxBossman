"""Dependency-free accidental-secret guard for CI.

Not a full secret scanner; catches the most dangerous common patterns.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".pdf", ".zip",
               ".sqlite", ".sqlite3", ".db", ".gguf", ".safetensors"}
PATTERNS = [
    ("openai key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("wallet seed label", re.compile(r"(?i)\b(?:seed phrase|mnemonic)\b\s*[:=]\s*\S+")),
    ("obvious password", re.compile(r"(?i)\b(?:password|passwd)\b\s*[:=]\s*[\"'][^\"']{8,}[\"']")),
]


# Пометка рядом со строкой снимает срабатывание ровно для неё.
ALLOW_MARK = "ci-secret-scan: allow"


def tracked_files() -> list[Path]:
    try:
        raw = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"],
                                      stderr=subprocess.DEVNULL)
        return [ROOT / x for x in raw.decode("utf-8", "replace").split("\0") if x]
    except Exception:
        return [p for p in ROOT.rglob("*") if p.is_file()]


def main() -> int:
    findings = []
    for path in tracked_files():
        if path.suffix.lower() in SKIP_SUFFIX:
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.endswith("tools/ci_secret_scan.py"):
            continue
        lines = text.splitlines()
        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                # Осознанное исключение: тестовые «канарейки» — заведомо
                # фальшивые значения, которыми тесты ДОКАЗЫВАЮТ, что секрет не
                # утекает в снапшот. Помечаются явно, поштучно, а не отключением
                # проверки для всего каталога тестов.
                src = lines[line - 1] if 0 < line <= len(lines) else ""
                if ALLOW_MARK in src:
                    continue
                findings.append(f"{rel}:{line}: {label}")
    if findings:
        print("Potential secrets detected:", file=sys.stderr)
        for item in findings:
            print(f"  {item}", file=sys.stderr)
        return 2
    print("Secret-pattern scan: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
