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
