"""Offers collected facts to Bossman's memory through the ONE existing
memory path (``bcc.v2.memory.facts.FactStore``) — never a second, private
fact table.

Every write goes in ``mode="append"`` (the store's own additive-only
default): a later source that disagrees with an earlier one about the same
(subject, predicate) is INSERTED alongside it, never overwritten and never
silently merged — ``FactStore.search()`` then returns both, exactly the
"conflicting facts are kept" requirement. The full provenance (URL,
retrieved-at, quote, selector, page hash, robots.txt verdict) travels in the
fact's ``meta`` dict, which the store already carries untouched.

This module makes no network or DB connections of its own: the caller hands
it a live ``FactStore`` (or ``None`` to skip offering to memory entirely,
e.g. for a dry run or a unit test that only checks the collection side).
"""
from __future__ import annotations

from typing import Any, Iterable

from .models import CollectedFact


async def offer_facts_to_memory(store: Any, facts: Iterable[CollectedFact], *,
                                topic: str, source_run_id: int | None = None) -> list[dict]:
    """``store`` is a ``bcc.v2.memory.facts.FactStore`` (or any object with a
    matching ``.add(...)`` coroutine — tests use a stub). Returns the rows
    written, in the same order as ``facts``; unknown facts are skipped (an
    explicit UNKNOWN is a fact about the collection run, not a durable
    memory statement).

    One fact failing the store's own form check (e.g. its sentence does not
    literally mention every proper noun in the subject — see the И-1 note
    below) must not lose every other fact in the batch, so each write is
    isolated; the reason for a skip is attached to the returned dict under
    ``"error"`` rather than raised."""
    written: list[dict] = []
    for fact in facts:
        if fact.unknown:
            continue
        meta = dict(fact.provenance.as_dict())
        meta["collector_topic"] = topic
        try:
            # The store's own self-containment check (И-1) requires every
            # proper noun/number in subject+object to literally appear in
            # the statement text, so the statement must be the actual
            # sentence the fact was read from (the quote) — a bare value
            # like "256 GB/s" would not mention the subject and would
            # always be rejected.
            row = await store.add(
                subject=fact.subject,
                predicate=fact.predicate,
                statement=fact.provenance.quote or fact.value,
                object=fact.value,
                valid_at=fact.provenance.retrieved_at_utc,
                mode="append",             # never overwrite — see module docstring
                source_kind="collector",
                source_run_id=source_run_id,
                source_note=fact.provenance.url,
                confidence=fact.confidence,
                meta=meta,
            )
        except Exception as exc:  # noqa: BLE001 -- one rejected fact must not sink the batch
            written.append({"skipped": True, "subject": fact.subject, "predicate": fact.predicate,
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        written.append(row)
    return written
