from datetime import datetime
from bcc.v2.memory.facts import FactStore


async def test_append_does_not_fake_invalidation(env):
    store = FactStore(env.svc)
    await store.add(subject="project:x", predicate="status", statement="X is alpha",
                    mode="append", valid_at=datetime(2026, 1, 1))
    await store.add(subject="project:x", predicate="status", statement="X is beta",
                    mode="append", valid_at=datetime(2026, 2, 1))
    current = await store.search(subject="project:x", predicate="status")
    assert len(current) == 2


async def test_replace_uses_new_world_time(env):
    store = FactStore(env.svc)
    old = await store.add(subject="service:api", predicate="endpoint",
                          statement="API endpoint is v1", valid_at=datetime(2026, 1, 10))
    t2 = datetime(2026, 3, 5, 12, 30)
    new = await store.add(subject="service:api", predicate="endpoint",
                          statement="API endpoint is v2", valid_at=t2,
                          mode="replace-current")
    history = await store.history(subject="service:api", predicate="endpoint")
    old_row = next(x for x in history if x["id"] == old["id"])
    assert old_row["invalid_at"] == t2
    assert old_row["superseded_by"] == new["id"]


async def test_world_time_query(env):
    store = FactStore(env.svc)
    await store.add(subject="model:fast", predicate="provider",
                    statement="provider A", valid_at=datetime(2026, 1, 1))
    await store.add(subject="model:fast", predicate="provider",
                    statement="provider B", valid_at=datetime(2026, 2, 1),
                    mode="replace-current")
    jan = await store.as_of(world_at=datetime(2026, 1, 15), subject="model:fast")
    feb = await store.as_of(world_at=datetime(2026, 2, 15), subject="model:fast")
    assert [x["statement"] for x in jan] == ["provider A"]
    assert [x["statement"] for x in feb] == ["provider B"]
