"""Stage 10 — независимое состязательное ревью патча.

Ревьюер отдельный от исполнителя и настроен ИСКАТЬ причины отклонить. Проверки
детерминированные, без модели: секреты в диффе, следы prompt-injection, попытки
тронуть границы (approvals, cloud_policy, CI/секреты), отсутствие доказательств.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import obs
from .models import Patch, Verdict
from .planner import detect_injection

# Пути, изменение которых меняет границы безопасности → всегда на ревью человеку.
SENSITIVE_PATHS = (
    ".github/workflows", "agents/", "db/schema.sql", ".env", "settings.json",
    "bossman/approvals.py", "bossman/llm.py", "bossman/sandbox/",
)


@dataclass(slots=True)
class ReviewResult:
    approved: bool
    findings: tuple[str, ...] = ()
    notes: str = ""


@dataclass(slots=True)
class AdversarialReviewer:
    """Отклоняет по умолчанию: пустой патч или патч без доказательств не проходит."""
    require_evidence: bool = True
    extra_sensitive: tuple[str, ...] = field(default_factory=tuple)

    def review(self, patch: Patch, *, evidence_verdict: Verdict) -> ReviewResult:
        findings: list[str] = []

        if not patch.diff.strip():
            findings.append("пустой патч: изменений нет")

        if self.require_evidence and evidence_verdict is not Verdict.PASS:
            findings.append(f"нет доказательств успеха (verdict={evidence_verdict.value})")

        # Секрет в диффе — редакция изменила бы текст.
        if obs.redact(patch.diff) != patch.diff:
            findings.append("в патче обнаружен секрет")

        inj = detect_injection(patch.diff)
        if inj:
            findings.append(f"следы prompt-injection в патче: {', '.join(inj[:3])}")

        touched = tuple(patch.files)
        for path in (*SENSITIVE_PATHS, *self.extra_sensitive):
            if any(path in f for f in touched):
                findings.append(f"затронута граница безопасности: {path}")

        return ReviewResult(approved=not findings, findings=tuple(findings),
                            notes="ревью пройдено" if not findings else "требуется правка")
