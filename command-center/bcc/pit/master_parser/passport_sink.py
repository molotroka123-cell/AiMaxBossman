"""The one place Master Parser facts enter a passport (swappable write path).

Everything passport-schema-specific the parser needs lives here, behind a small
interface, so the Jeff Next passport model can replace ``VaultPassportSink``
without touching the collector, the batching or the UI:

``admission(person_key) -> (allowed, status)``
    may this person's conversations be analyzed at all? ``status`` is one of
    ``OK``, ``NO_MEMORY_CONSENT``, ``BLOCKED`` (owner's private Jeff blocklist).
``remote_allowed(person_key) -> bool``
    may this person's text go to the free cloud fallback?
``view(person_key) -> PassportView``
    what the passport already holds and what the person took back
    (deleted/forgotten ids, forget queries, participant-confirmed values).
``write(person_key, candidate, evidence_uids) -> bool``
    persist one ``MemoryCandidate`` with provenance through the normal consent
    gates and audit trail; False when refused (secret, sensitive, consent).
``revert(person_key, fact_id) -> bool``
    owner undo of one fact written by a run (owner-audited).

The current implementation writes through ``HighRecallCollector.ingest`` ->
``PersonaVault.append_candidate`` (memory_audit ``write``/``jeff``), exactly
like a live Jeff turn. No Telegram id is stored or logged here: the private
blocklist is read, hashed into person keys, and kept in memory only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Protocol

from ..collector import HighRecallCollector
from ..models import EvidenceKind, MemoryCandidate
from ..vault import PersonaVault
from .corpus import normalize

BLOCKLIST_ENV = "BOSSMAN_JEFF_BLOCKLIST"
# Keys that hold one current value: a different new value is a conflict for review.
SINGLE_VALUED = frozenset({
    ("communication", "primary_language"), ("communication", "answer_length"),
    ("communication", "formality"), ("communication", "date_format"),
    ("communication", "currency_format"), ("communication", "voice_text_preference"),
    ("device_software", "operating_system"), ("goals", "current_priority"),
})


def blocklist_path() -> Path:
    override = os.environ.get(BLOCKLIST_ENV, "").strip()
    if override:
        return Path(override)
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "Bossman" / "private" / "jeff_blocked_telegram_ids.txt"


def blocked_telegram_ids(path: Path | None = None) -> set[int]:
    """Owner's private Jeff blocklist (one id per line, ``#`` comments). Never logged."""
    path = Path(path) if path else blocklist_path()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return set()
    ids = set()
    for line in lines:
        value = line.split("#", 1)[0].strip()
        if value.isdigit():
            ids.add(int(value))
    return ids


class PassportView:
    """What a participant's passport already holds, and what they took back."""

    def __init__(self, vault: PersonaVault, person_key: str):
        self.ids: set[str] = set()
        self.values: set[tuple[str, str, str]] = set()
        self.single: dict[tuple[str, str], str] = {}
        self.confirmed: dict[tuple[str, str], set[str]] = {}
        for row in vault.iter_candidate_records(person_key):
            category, key = str(row.get("category", "")), str(row.get("key", ""))
            norm = normalize(str(row.get("value", "")))
            self.ids.add(str(row.get("id", "")))
            self.values.add((category, key, norm))
            if (category, key) in SINGLE_VALUED:
                self.single[(category, key)] = norm
            if str(row.get("evidence_kind")) == "confirmed":
                self.confirmed.setdefault((category, key), set()).add(norm)
        self.blocked_ids: set[str] = set()
        self.needles: list[str] = []
        base = vault.person_dir(person_key)
        for name in ("corrections.jsonl", "memory_outcomes.jsonl", PersonaVault.AUDIT_FILE):
            path = base / name
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                action = str(row.get("action") or row.get("outcome") or "")
                if action in {"delete", "forget", "FORGET_REQUESTED", "delete_all"}:
                    if row.get("candidate_id"):
                        self.blocked_ids.add(str(row["candidate_id"]))
                    for fact_id in row.get("fact_ids") or ():
                        self.blocked_ids.add(str(fact_id))
                    query = normalize(str(row.get("query") or ""))
                    if action == "forget" and len(query) >= 2:
                        self.needles.append(query)

    def remember(self, candidate: MemoryCandidate) -> None:
        norm = normalize(str(candidate.value))
        self.ids.add(candidate.id)
        self.values.add((candidate.category, candidate.key, norm))
        if (candidate.category, candidate.key) in SINGLE_VALUED:
            self.single[(candidate.category, candidate.key)] = norm

    def verdict(self, candidate: MemoryCandidate) -> tuple[str, str]:
        """(ok|known|blocked|conflict, existing value for a conflict)."""
        norm = normalize(str(candidate.value))
        if candidate.id in self.blocked_ids:
            return "blocked", ""
        if candidate.id in self.ids or (candidate.category, candidate.key, norm) in self.values:
            return "known", ""
        if any(needle in norm or needle in candidate.key.casefold() for needle in self.needles):
            return "blocked", ""
        pair = (candidate.category, candidate.key)
        if pair in self.single and self.single[pair] != norm:
            return "conflict", self.single[pair]
        if pair in self.confirmed and norm not in self.confirmed[pair] \
                and candidate.evidence_kind != EvidenceKind.EXPLICIT:
            return "conflict", sorted(self.confirmed[pair])[0]
        return "ok", ""


class PassportSink(Protocol):
    def admission(self, person_key: str) -> tuple[bool, str]: ...
    def remote_allowed(self, person_key: str) -> bool: ...
    def view(self, person_key: str) -> PassportView: ...
    def write(self, person_key: str, candidate: MemoryCandidate, evidence_uids: list[str]) -> bool: ...
    def revert(self, person_key: str, fact_id: str) -> bool: ...


class VaultPassportSink:
    """Today's passport: ``pit-v1.7/personalities/<key>/facts.jsonl`` via the collector."""

    def __init__(self, vault: PersonaVault, *, blocked_keys: Iterable[str] = (), dry_run: bool = False):
        self.vault = vault
        self.collector = HighRecallCollector(vault)
        self.blocked_keys = frozenset(blocked_keys)
        self.dry_run = dry_run

    def admission(self, person_key: str) -> tuple[bool, str]:
        if person_key in self.blocked_keys:
            return False, "BLOCKED"
        if not self.vault.consent(person_key).memory_enabled:
            return False, "NO_MEMORY_CONSENT"
        return True, "OK"

    def remote_allowed(self, person_key: str) -> bool:
        return bool(self.vault.consent(person_key).remote_processing_enabled)

    def view(self, person_key: str) -> PassportView:
        return PassportView(self.vault, person_key)

    def write(self, person_key: str, candidate: MemoryCandidate, evidence_uids: list[str]) -> bool:
        if self.dry_run:
            return True
        return bool(self.collector.ingest(person_key, [candidate]).accepted)

    def revert(self, person_key: str, fact_id: str) -> bool:
        return self.vault.delete_fact(person_key, fact_id, actor="owner", surface="master_parser")
