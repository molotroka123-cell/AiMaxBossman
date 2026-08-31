"""Repo Security Scanner layer — статический допуск изменений в репозитории.

Слой НАД существующим `tools/ci_secret_scan.py` (он остаётся авторитетом по
секрет-паттернам). Здесь добавлены проверки, которых там нет: shell-примитивы,
небезопасная десериализация, отключение TLS-проверки и «чувствительные пути».
Ничего не чинит сам — возвращает находки для ревью/approval.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SENSITIVE_PATHS = (".github/workflows", "db/schema.sql", "perimeter", "approvals",
                   "secrets", "policy", "cybersec")

_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("shell_true", re.compile(r"\bshell\s*=\s*True\b"), "critical"),
    ("os_system", re.compile(r"\bos\.system\s*\("), "critical"),
    ("eval_exec", re.compile(r"(?<![\w.])(?:eval|exec)\s*\("), "high"),
    ("pickle_loads", re.compile(r"\bpickle\.loads?\s*\("), "high"),
    ("yaml_unsafe", re.compile(r"\byaml\.load\s*\((?![^)]*SafeLoader)"), "high"),
    ("tls_disabled", re.compile(r"\bverify\s*=\s*False\b|\bcheck_hostname\s*=\s*False\b"), "critical"),
    ("curl_pipe_sh", re.compile(r"curl[^\n]{0,60}\|\s*(?:sh|bash)"), "critical"),
)


@dataclass(frozen=True)
class RepoFinding:
    path: str
    line: int
    rule: str
    severity: str
    excerpt: str


@dataclass(frozen=True)
class RepoScanReport:
    findings: tuple[RepoFinding, ...] = ()
    sensitive_paths_touched: tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


def scan_text(path: str, text: str) -> tuple[RepoFinding, ...]:
    out: list[RepoFinding] = []
    for i, line in enumerate((text or "").splitlines(), start=1):
        for rule, rx, sev in _RULES:
            if rx.search(line):
                out.append(RepoFinding(path, i, rule, sev, line.strip()[:160]))
    return tuple(out)


def scan_paths(paths: list[str], *, root: str | Path = ".") -> RepoScanReport:
    """Просканировать конкретные файлы (обычно — изменённые в diff)."""
    root_p = Path(root)
    findings: list[RepoFinding] = []
    touched: list[str] = []
    for rel in paths:
        if any(s in rel.replace("\\", "/") for s in SENSITIVE_PATHS):
            touched.append(rel)
        f = root_p / rel
        try:
            if f.is_file() and f.suffix in {".py", ".js", ".sh", ".yml", ".yaml", ".toml"}:
                findings.extend(scan_text(rel, f.read_text("utf-8", "replace")))
        except OSError:
            continue
    return RepoScanReport(tuple(findings), tuple(sorted(set(touched))))
