"""Offering facts to memory goes through the ONE existing path
(bcc.v2.memory.facts.FactStore) with full provenance in ``meta``, and never
overwrites: two sources that disagree both remain queryable afterwards.

Uses the same ``env`` fixture (temp-SQLite Services) as the rest of the
memory test suite (see test_v22_facts.py) — no owner data, no network.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bcc.collector.memory_bridge import offer_facts_to_memory
from bcc.collector.models import CollectedFact, Provenance
from bcc.db import facts as facts_t
from bcc.v2.memory.facts import FactStore


def _fact(subject, predicate, value, url, quote, *, retrieved_at="2026-01-01T00:00:00Z"):
    prov = Provenance(url=url, retrieved_at_utc=retrieved_at, quote=quote,
                      selector="innerText#s1", page_hash="a" * 64, robots_txt_allowed=True)
    return CollectedFact(subject=subject, predicate=predicate, value=value, confidence=0.7,
                         provenance=prov)


async def test_offered_fact_carries_full_provenance_in_meta(env):
    store = FactStore(env.svc)
    fact = _fact("amd ryzen ai max+ 395", "bandwidth", "256 GB/s",
                "https://en.wikipedia.org/wiki/Ryzen",
                "the AMD Ryzen AI Max+ 395 has a peak memory bandwidth of 256 GB/s")
    written = await offer_facts_to_memory(store, [fact], topic="memory bandwidth")
    assert len(written) == 1
    fact_id = written[0]["id"]
    # public_fact() (what .add()/.search() return) deliberately strips
    # meta/source_note before showing a fact to a model or the UI — so the
    # provenance this test cares about is read back from the DB row itself,
    # the same row write_fact() persisted.
    async with env.svc.db.session() as session:
        row = (await session.execute(
            sa.select(facts_t).where(facts_t.c.id == fact_id))).mappings().first()
    assert row["source_kind"] == "collector"
    assert row["source_note"] == "https://en.wikipedia.org/wiki/Ryzen"
    meta = row["meta"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    assert meta["url"] == "https://en.wikipedia.org/wiki/Ryzen"
    assert meta["quote"] == "the AMD Ryzen AI Max+ 395 has a peak memory bandwidth of 256 GB/s"
    assert meta["page_hash"] == "a" * 64
    assert meta["robots_txt_allowed"] is True
    assert meta["collector_topic"] == "memory bandwidth"


async def test_conflicting_facts_are_both_kept_in_memory_not_merged(env):
    store = FactStore(env.svc)
    fact_a = _fact("chip x", "bandwidth", "256 GB/s", "https://a.example/page",
                   "bandwidth is 256 GB/s")
    fact_b = _fact("chip x", "bandwidth", "273 GB/s", "https://b.example/page",
                   "bandwidth is 273 GB/s")
    await offer_facts_to_memory(store, [fact_a, fact_b], topic="t")
    rows = await store.search(subject="chip x", predicate="bandwidth")
    values = {r["object"] for r in rows}
    assert values == {"256 GB/s", "273 GB/s"}, (
        "both sourced values must remain queryable — no silent merge/overwrite")


async def test_unknown_facts_are_never_written_to_memory(env):
    store = FactStore(env.svc)
    unknown_prov = Provenance(url="", retrieved_at_utc="2026-01-01T00:00:00Z", quote="",
                              selector="", page_hash="", robots_txt_allowed=True)
    unknown_fact = CollectedFact(subject="x", predicate="tdp", value="UNKNOWN", confidence=0.0,
                                 provenance=unknown_prov, unknown=True)
    written = await offer_facts_to_memory(store, [unknown_fact], topic="t")
    assert written == []
    rows = await store.search(subject="x", predicate="tdp")
    assert rows == []
