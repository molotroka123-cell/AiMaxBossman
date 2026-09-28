"""Data shapes for the human-like collector.

Every fact and page record carries provenance explicitly — nothing here is
ever emitted "trust me": a fact without a URL, a retrieved-at timestamp, a
quote and a page hash is a bug, not a shortcut.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Sentinel value for an attribute the owner asked about that no approved
#: source answered. Never guessed, never silently dropped.
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """One line from the owner-approved source list."""

    url: str
    host: str


@dataclass(frozen=True, slots=True)
class Provenance:
    url: str
    retrieved_at_utc: str          # ISO-8601, UTC, second precision
    quote: str                     # exact substring of the page text
    selector: str                  # e.g. "innerText#s12" (sentence index)
    page_hash: str                 # sha256 of the extracted page text
    robots_txt_allowed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "retrieved_at_utc": self.retrieved_at_utc,
            "quote": self.quote,
            "selector": self.selector,
            "page_hash": self.page_hash,
            "robots_txt_allowed": self.robots_txt_allowed,
        }


@dataclass(frozen=True, slots=True)
class CollectedFact:
    """One sourced statement. Facts are never merged across sources —
    conflicting facts about the same subject/predicate are kept side by
    side; only the reader (owner, memory) decides what to trust."""

    subject: str
    predicate: str
    value: str
    confidence: float
    provenance: Provenance
    unknown: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "confidence": round(self.confidence, 3),
            "unknown": self.unknown,
            "provenance": self.provenance.as_dict(),
        }


@dataclass(slots=True)
class PageOutcome:
    """One row of the run ledger: what happened when we tried a page."""

    url: str
    host: str
    status: str                    # ok | skipped_robots | skipped_cap | skipped_error | stopped
    detail: str
    started_at_utc: str
    finished_at_utc: str
    delay_before_s: float
    facts_found: int = 0
    title: str = ""
    text_chars: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "host": self.host, "status": self.status, "detail": self.detail,
            "started_at_utc": self.started_at_utc, "finished_at_utc": self.finished_at_utc,
            "delay_before_s": round(self.delay_before_s, 2), "facts_found": self.facts_found,
            "title": self.title, "text_chars": self.text_chars,
        }


@dataclass(slots=True)
class RunSummary:
    run_id: str
    topic: str
    started_at_utc: str
    finished_at_utc: str
    pages: list[PageOutcome] = field(default_factory=list)
    facts: list[CollectedFact] = field(default_factory=list)
    refused_by_design: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "topic": self.topic,
            "started_at_utc": self.started_at_utc, "finished_at_utc": self.finished_at_utc,
            "pages": [p.as_dict() for p in self.pages],
            "facts": [f.as_dict() for f in self.facts],
            "refused_by_design": list(self.refused_by_design),
        }
