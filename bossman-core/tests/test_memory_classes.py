"""ЭТАП 2.222 — раздельные классы памяти, decision superseding, failure memory,
provenance, contradiction, dedup, restart persistence.

Память — не один JSON: классы (facts/project/decisions/constraints/procedures/
episodic/working/failure/unresolved/distilled) разделены по kind. Решения имеют
стабильный ID (DEC-0001), superseded не удаляется. Каждая durable-запись несёт
provenance (source, timestamp, content hash, confidence, verification).
Противоречия хранятся, не перезатираются молча.
"""
from pathlib import Path

from bossman.context_engine import ContextStore, MemoryKind, MemoryManager, MemoryStatus


def _mgr(tmp_path: Path):
    store = ContextStore(tmp_path / "context.db")
    return store, MemoryManager(store)


def test_decision_stable_ids_and_superseding(tmp_path):
    store, mem = _mgr(tmp_path)
    d1 = mem.decision("Используем llama.cpp KV-cache порядок блоков.", project="p", source_refs=["spec:10"])
    d2 = mem.decision("Переходим на hybrid retrieval после reranking.", project="p", source_refs=["spec:11"])
    assert d1.memory_id == "DEC-0001"
    assert d2.memory_id == "DEC-0002"
    mem.promote(d1.memory_id, verified=True)
    mem.promote(d2.memory_id, verified=True)
    mem.supersede(d1.memory_id, d2.memory_id)
    # superseded НЕ удаляется — остаётся в store с историей
    all_rows = store.memories("p", tuple(MemoryStatus))
    ids = {m.memory_id: m for m in all_rows}
    assert ids["DEC-0001"].status == MemoryStatus.SUPERSEDED
    assert "DEC-0001" in ids["DEC-0002"].supersedes
    # обычная выборка активной памяти не возвращает superseded
    active = {m.memory_id for m in store.memories("p", (MemoryStatus.ACTIVE,))}
    assert "DEC-0002" in active and "DEC-0001" not in active
    store.close()


def test_failure_memory_structured_and_retrieved_first(tmp_path):
    store, mem = _mgr(tmp_path)
    f = mem.failure("compact терял числа", cause="свободный LLM-summarize",
                    fix="extractive-first + anchor survival",
                    verification="tests/test_compact_structured.py::test_small_budget",
                    project="p", source_refs=["chat:1"])
    assert f.kind == MemoryKind.FAILURE
    assert f.memory_id.startswith("FAIL-")
    for part in ("compact терял числа", "свободный LLM-summarize",
                 "extractive-first", "test_small_budget"):
        assert part in f.text
    mem.promote(f.memory_id, verified=True)
    hits = mem.retrieve_failures("compact числа", project="p")
    assert any(h.memory_id == f.memory_id for h in hits)
    store.close()


def test_provenance_recorded(tmp_path):
    store, mem = _mgr(tmp_path)
    d = mem.decision("Провенанс обязателен для durable memory.", project="p",
                     source_refs=["audit:3"], verification="review:alice")
    prov = d.metadata.get("provenance")
    assert prov is not None
    assert prov["source"] == ["audit:3"]
    assert prov["timestamp"]
    assert prov["content_hash"]
    assert "confidence" in prov
    assert prov["verification"] == "review:alice"
    store.close()


def test_contradiction_stored_not_overwritten(tmp_path):
    store, mem = _mgr(tmp_path)
    a = mem.constraint("Облачные вызовы разрешены для этого агента.", project="p", source_refs=["s:1"])
    mem.promote(a.memory_id, verified=True)
    b = mem.constraint("Облачные вызовы не разрешены для этого агента.", project="p", source_refs=["s:2"])
    # второй помечается disputed и ссылается на первый, первый не тронут
    assert b.status == MemoryStatus.DISPUTED
    assert a.memory_id in b.contradicted_by
    a_now = next(m for m in store.memories("p", tuple(MemoryStatus)) if m.memory_id == a.memory_id)
    assert a_now.status == MemoryStatus.ACTIVE
    assert a_now.text == "Облачные вызовы разрешены для этого агента."
    store.close()


def test_dedup_same_text_same_id(tmp_path):
    store, mem = _mgr(tmp_path)
    m1 = mem.candidate(MemoryKind.FACT, "Окно модели 65536.", project="p")
    m2 = mem.candidate(MemoryKind.FACT, "Окно модели 65536.", project="p")
    assert m1.memory_id == m2.memory_id
    rows = store.memories("p", tuple(MemoryStatus))
    assert sum(1 for m in rows if m.memory_id == m1.memory_id) == 1
    store.close()


def test_memory_class_namespaces_separated(tmp_path):
    store, mem = _mgr(tmp_path)
    mem.fact("Стабильный факт.", project="p")
    mem.constraint("Ограничение.", project="p")
    mem.procedure("Проверенный workflow.", project="p")
    mem.episode("Эпизод сессии.", project="p")
    mem.working("Рабочая заметка.", project="p")
    mem.unresolved("Открытый вопрос.", project="p")
    mem.distilled("Сжатая производная.", project="p")
    rows = store.memories("p", tuple(MemoryStatus))
    kinds = {m.kind for m in rows}
    assert {MemoryKind.FACT, MemoryKind.CONSTRAINT, MemoryKind.PROCEDURE,
            MemoryKind.EPISODE, MemoryKind.WORKING, MemoryKind.UNRESOLVED,
            MemoryKind.DISTILLED} <= kinds
    # каждый класс отделён по kind, не смешан
    facts = [m for m in rows if m.kind == MemoryKind.FACT]
    assert len(facts) == 1 and facts[0].text == "Стабильный факт."
    store.close()


def test_restart_persistence_and_decision_counter(tmp_path):
    store, mem = _mgr(tmp_path)
    mem.decision("Решение до перезапуска.", project="p", source_refs=["s:1"])
    store.close()
    # переоткрываем тот же путь
    store2 = ContextStore(tmp_path / "context.db")
    mem2 = MemoryManager(store2)
    rows = store2.memories("p", tuple(MemoryStatus))
    assert any(m.memory_id == "DEC-0001" for m in rows)
    # счётчик решений продолжается после restart
    d2 = mem2.decision("Решение после перезапуска.", project="p", source_refs=["s:2"])
    assert d2.memory_id == "DEC-0002"
    store2.close()
