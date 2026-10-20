"""Branch 8 (Memory): temporal facts — retrieval correctness, behavioural (real DB via `env`)."""
from __future__ import annotations

from bcc.v2.memory.facts import FactStore


async def test_text_query_is_applied_before_the_limit(env):
    store = FactStore(env.svc)
    for day in range(1, 7):
        await store.add(subject=f"b08:host{day}", predicate="state",
                        statement=f"b08 host{day} is healthy", valid_at=f"2026-01-0{day}")
    await store.add(subject="b08:needle", predicate="state",
                    statement="b08: Ёмкость диска RCNEEDLE исчерпана", valid_at="2026-02-01")

    hits = await store.search(query="емкость диска rcneedle", limit=2)
    assert [h["subject"] for h in hits] == ["b08:needle"]
    hits = await store.as_of(world_at="2026-03-01", query="RCNEEDLE", limit=1)
    assert [h["subject"] for h in hits] == ["b08:needle"]
    # limit still bounds the answer
    assert len(await store.search(query="healthy", limit=2)) == 2
