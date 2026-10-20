"""The vault search index closes every SQLite connection it opens.

``with sqlite3.connect(...) as conn`` commits or rolls back but never closes, so the
index file stayed open until garbage collection; on Windows the owner's data
directory could not be removed (owner scenario OS-99).
"""
import sqlite3

import pytest

from bcc.v2.memory import sqlite_index
from bcc.v2.memory.sqlite_index import SQLiteMemoryBackend


def _is_open(connection) -> bool:
    try:
        connection.total_changes
    except sqlite3.ProgrammingError:
        return False
    return True


@pytest.fixture
def opened(monkeypatch):
    connections = []
    real = sqlite3.connect

    def tracking(*args, **kwargs):
        connection = real(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite_index.sqlite3, "connect", tracking)
    return connections


def test_index_search_expand_remove_and_stats_close_their_connections(tmp_path, opened):
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    (vault / "notes" / "a.md").write_text("# A\n\nдедлайн в пятницу\n", encoding="utf-8")
    backend = SQLiteMemoryBackend(index_path=tmp_path / "index.sqlite", vault_root=vault)
    assert backend.index_sync([vault / "notes"])["added"] == 1
    hits = backend.search_sync("дедлайн")
    assert hits
    backend.stats_sync()
    assert backend.remove_source_sync("notes/a.md") is True
    assert opened, "the index opened no connection at all"
    assert [c for c in opened if _is_open(c)] == [], "a connection outlived its call"


def test_rerank_dedup_decay_improves_precision_at_5():
    import dataclasses
    from bcc.v2.memory.memsearch_bridge import MemoryHit
    from bcc.v2.memory.reranker import LexicalReranker

    reranker = LexicalReranker()
    query = "настройка безопасности ключей и потолка расходов"
    fresh_topics = [
        "Настройка безопасности ключей и потолка расходов: шифрование секретов в локальном сейфе vault",
        "Настройка безопасности ключей и потолка расходов: жёсткий суточный лимит облачного бюджета",
        "Настройка безопасности ключей и потолка расходов: ротация токенов мобильного пульта и терминала",
        "Настройка безопасности ключей и потолка расходов: автоматическая блокировка прогона при исчерпании квоты",
    ]
    # 5 старых дублей одной и той же устаревшей заметки + 4 уникальных свежих релевантных чанка
    candidates = [
        MemoryHit(
            content="Настройка безопасности ключей и потолка расходов: старая черновая копия",
            source=f"old/dup_{i}.md",
            heading="Безопасность ключей",
            score=5.0,
            chunk_hash=f"dup-{i}",
            metadata={"age_days": 120, "unique_relevant": (i == 0)},
        )
        for i in range(5)
    ] + [
        MemoryHit(
            content=body,
            source=f"fresh/rule_{j}.md",
            heading=f"Правило {j}",
            score=4.5,
            chunk_hash=f"fresh-{j}",
            metadata={"age_days": 1, "unique_relevant": True},
        )
        for j, body in enumerate(fresh_topics, start=1)
    ]

    # Без дедупликации и затухания все 5 мест в top-5 занимают старые дубли (unique precision@5 = 1/5 = 0.20)
    raw_top5 = reranker.rerank(query, [dataclasses.replace(c, metadata=dict(c.metadata)) for c in candidates], top_k=5)
    raw_unique_p5 = sum(1 for h in raw_top5 if h.metadata.get("unique_relevant")) / 5.0

    # С дедупликацией и временным затуханием все 5 мест в top-5 занимают уникальные релевантные чанки (5/5 = 1.00)
    dedup_top5 = reranker.rerank_dedup_decay(query, candidates, top_k=5, half_life_days=30.0, jaccard_threshold=0.75)
    dedup_unique_p5 = sum(1 for h in dedup_top5 if h.metadata.get("unique_relevant")) / 5.0

    assert raw_unique_p5 == 0.20
    assert dedup_unique_p5 == 1.00
    assert dedup_unique_p5 / raw_unique_p5 >= 1.3


def test_unified_memory_persists_and_recalls_across_independent_tasks_and_restarts(tmp_path, opened):
    """Owner requirement: prove Bossman's memory is single, unified, and persists across independent tasks
    (Task A writes -> all SQLite handles close -> new instance/restart -> Task B recalls without in-memory state)."""
    import hashlib
    import json
    from bcc.v2.memory import ObsidianVault
    from bcc.v2.memory.lifecycle_wiring import VaultNotes
    from bcc.pit.tasks import TaskStore

    data_dir = tmp_path / "bcc_data"
    vault_root = data_dir / "vault"
    (vault_root / "BOSSMAN Memory").mkdir(parents=True)
    index_path = data_dir / "memory_index.sqlite"
    pit_dir = data_dir / "pit"

    # --- 1. Task A (task_id_1) writes structured customer/project context into unified memory ---
    tstore_a = TaskStore(pit_dir)
    task_a = tstore_a.create(
        "owner",
        "Task A: Record SwapMe client preference and workspace rule",
        steps=[{"id": "save_mem", "title": "Save client tier rule", "kind": "memory_write"}],
    )
    task_id_1 = task_a["id"]
    payload_text = (
        f"source_task_id={task_id_1} | SwapMe VIP client tier rule: "
        "require_explicit_margin=True, default VIP fee_rate=0.03 (3%), settlement currency CZK."
    )
    payload_sha256 = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()

    vault_a = ObsidianVault(root=vault_root)
    backend_a = SQLiteMemoryBackend(index_path=index_path, vault_root=vault_root)
    notes_a = VaultNotes(vault_a, backend_a)
    note_ref = notes_a.write_note(
        title=f"SwapMe VIP Rule {task_id_1}",
        kind="decision",
        project="bossman-swapme",
        content=f"{payload_text}\nsha256={payload_sha256}\n",
        tags=["swapme", "vip", task_id_1],
    )
    assert note_ref.startswith("BOSSMAN Memory/")

    # Ensure every SQLite connection opened by Task A is closed cleanly before process boundary
    assert opened, "Task A opened no SQLite connections"
    assert [c for c in opened if _is_open(c)] == [], "SQLite handle leaked across Task A boundary"
    del notes_a, backend_a, vault_a, tstore_a

    # --- 2. Process / Instance Boundary: fresh backend + TaskStore on the same data_dir ---
    vault_b = ObsidianVault(root=vault_root)
    backend_b = SQLiteMemoryBackend(index_path=index_path, vault_root=vault_root)
    notes_b = VaultNotes(vault_b, backend_b)
    tstore_b = TaskStore(pit_dir)

    # --- 3. Task B (task_id_2) queries unified memory with zero prior in-memory state ---
    task_b = tstore_b.create(
        "owner",
        "Task B: Apply stored SwapMe VIP client preference",
        steps=[{"id": "recall_and_quote", "title": "Recall VIP rule from Task A", "kind": "memory_recall"}],
    )
    task_id_2 = task_b["id"]
    assert task_id_2 != task_id_1

    hits = notes_b.search(f"SwapMe VIP client tier rule {task_id_1}", top_k=5)
    assert len(hits) >= 1, "Task B failed to recall Task A's memory across restart boundary"
    top_hit = hits[0]
    assert f"source_task_id={task_id_1}" in top_hit["text"]
    assert payload_sha256 in top_hit["text"]
    assert "fee_rate=0.03" in top_hit["text"]

    # Verify raw SQLite row in chunks table directly
    check_con = sqlite3.connect(str(index_path))
    try:
        row = check_con.execute(
            "SELECT chunk_hash, source, content FROM chunks WHERE content LIKE ?",
            (f"%source_task_id={task_id_1}%",),
        ).fetchone()
    finally:
        check_con.close()
    assert row is not None
    chunk_hash, db_source, db_content = row
    assert db_source == note_ref
    assert payload_sha256 in db_content

    # Emit cross-task receipt linking task_id_1 and task_id_2
    cross_receipt = {
        "schema": "bossman.cross_task_memory_receipt.v1",
        "writer_task_id": task_id_1,
        "reader_task_id": task_id_2,
        "hit": True,
        "source_ref": db_source,
        "chunk_hash": chunk_hash,
        "payload_sha256": payload_sha256,
    }
    assert cross_receipt["writer_task_id"] == task_id_1
    assert cross_receipt["reader_task_id"] == task_id_2
    assert cross_receipt["hit"] is True
    assert [c for c in opened if _is_open(c)] == [], "SQLite handle leaked after Task B recall"

