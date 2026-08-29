from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    REJECTED = "rejected"


class MemoryKind(str, Enum):
    FACT = "fact"
    DECISION = "decision"
    FAILURE = "failure"
    PROCEDURE = "procedure"
    PREFERENCE = "preference"
    EPISODE = "episode"
    TODO = "todo"
    CONSTRAINT = "constraint"
    SUMMARY = "summary"
    # ЭТАП 2.222 — раздельные классы памяти (см. docs/stage-2.222/memory/README.md):
    # рабочая память, нерешённые вопросы/противоречия и сжатые производные хранятся
    # как отдельные kind, а не смешиваются в один JSON.
    WORKING = "working"
    UNRESOLVED = "unresolved"
    DISTILLED = "distilled"


@dataclass(slots=True)
class Document:
    document_id: str
    source_type: str
    source_uri: str
    text: str
    project: str = ""
    created_at: str = ""
    updated_at: str = ""
    author: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    sensitivity: str = "normal"
    content_hash: str = ""


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    document_id: str
    text: str
    ordinal: int
    heading: str = ""
    token_count: int = 0
    project: str = ""
    source_type: str = ""
    source_uri: str = ""
    created_at: str = ""
    updated_at: str = ""
    importance: float = 0.5
    freshness: float = 1.0
    sensitivity: str = "normal"
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryRecord:
    memory_id: str
    kind: MemoryKind
    text: str
    project: str = ""
    status: MemoryStatus = MemoryStatus.CANDIDATE
    confidence: float = 0.5
    importance: float = 0.5
    source_refs: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    last_verified_at: str = ""
    supersedes: list[str] = field(default_factory=list)
    contradicted_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RetrievalHit:
    chunk: Chunk
    lexical_score: float = 0.0
    vector_score: float = 0.0
    rerank_score: float = 0.0
    recency_score: float = 0.0
    importance_score: float = 0.0
    final_score: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContextSection:
    name: str
    text: str
    tokens: int
    priority: int
    source_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CompiledContext:
    model: str
    budget_tokens: int
    used_tokens: int
    sections: list[ContextSection]
    telemetry: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        return "\n\n".join(f"## {s.name}\n{s.text}" for s in self.sections if s.text.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "budget_tokens": self.budget_tokens,
            "used_tokens": self.used_tokens,
            "sections": [asdict(x) for x in self.sections],
            "telemetry": self.telemetry,
        }
