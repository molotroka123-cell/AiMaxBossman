from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def redact_text(value: str) -> str:
    out = value
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[REDACTED_SECRET]", out)
    return out


def _safe_json(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(v) for v in value]
    return value


@dataclass(frozen=True)
class TeacherTrace:
    schema_version: str
    trace_id: str
    created_at: str
    session_id: str
    task_id: str | None
    teacher_provider: str
    teacher_model: str
    prompt_summary: str
    decision_summary: str
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    difficulty_tags: list[str] = field(default_factory=list)
    reusable: bool = False
    contains_private_data: bool = False
    source_prompt_sha256: str | None = None


class TeacherTraceRecorder:
    """Append-only recorder for future local-model training/evaluation data.

    This intentionally records compact decision summaries and observable tool/effect
    evidence, not hidden chain-of-thought. Records are redacted before persistence.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "teacher_traces.jsonl"

    def record(
        self,
        *,
        session_id: str,
        teacher_provider: str,
        teacher_model: str,
        prompt: str,
        prompt_summary: str,
        decision_summary: str,
        tool_events: Iterable[dict[str, Any]] = (),
        outcome: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        difficulty_tags: Iterable[str] = (),
        task_id: str | None = None,
        reusable: bool = False,
        contains_private_data: bool = False,
    ) -> TeacherTrace:
        created_at = _utc_now()
        trace_seed = f"{session_id}|{task_id or ''}|{teacher_provider}|{teacher_model}|{created_at}|{prompt}"
        trace = TeacherTrace(
            schema_version="v1",
            trace_id=_sha256_text(trace_seed)[:24],
            created_at=created_at,
            session_id=redact_text(session_id),
            task_id=redact_text(task_id) if task_id else None,
            teacher_provider=redact_text(teacher_provider),
            teacher_model=redact_text(teacher_model),
            prompt_summary=redact_text(prompt_summary),
            decision_summary=redact_text(decision_summary),
            tool_events=list(_safe_json(list(tool_events))),
            outcome=dict(_safe_json(outcome or {})),
            verification=dict(_safe_json(verification or {})),
            difficulty_tags=[redact_text(str(x)) for x in difficulty_tags],
            reusable=bool(reusable and not contains_private_data),
            contains_private_data=bool(contains_private_data),
            source_prompt_sha256=_sha256_text(prompt),
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(trace), ensure_ascii=False, sort_keys=True) + "\n")
        return trace

    def export_training_candidates(self, destination: str | os.PathLike[str]) -> int:
        """Export only explicitly reusable, privacy-clean traces.

        This is dataset preparation only. It never starts fine-tuning by itself.
        """
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with self.path.open("r", encoding="utf-8") as src, dest.open("w", encoding="utf-8") as out:
            for line in src:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("reusable") is True and row.get("contains_private_data") is False:
                    out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    count += 1
        return count
