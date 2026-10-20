"""Branch 8 (Memory): retrieval correctness — behavioural, no models, no network.

Every test here exercises the real backend on a tmp vault; nothing asserts on
source text. Memory recall is retrieval context, not model learning.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

from bcc.v2.memory.local_index import LocalMemoryBackend, tokenize
from bcc.v2.memory.sqlite_index import SQLiteMemoryBackend


def _vault(tmp_path: Path, files: dict[str, str]) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    for name, text in files.items():
        (vault / name).write_text(text, encoding="utf-8")
    return vault


# ------------------------------------------------------------ unicode / Russian
def test_tokenize_folds_nfd_yo_and_case():
    nfd = unicodedata.normalize("NFD", "Синий")              # «й» = «и» + U+0306
    assert nfd != "Синий"
    assert tokenize(nfd) == tokenize("синий")
    assert tokenize("Ещё ЁЛКА") == tokenize("еще елка")
    assert tokenize("ＢＯＳＳＭＡＮ") == tokenize("bossman")  # full-width


def test_sqlite_search_finds_nfd_note_by_plain_query(tmp_path: Path):
    nfd = unicodedata.normalize("NFD", "# Склад\n\nМайка лежит на полке")
    vault = _vault(tmp_path, {"route.md": nfd, "other.md": "# Другое\n\nЁлка стоит в углу",
                              "third.md": "# Третье\n\nкабель"})
    backend = SQLiteMemoryBackend(index_path=tmp_path / "m.sqlite3", vault_root=vault)
    backend.index_sync([vault])
    assert [h.source for h in backend.search_sync("майка")] == ["route.md"]
    assert [h.source for h in backend.search_sync("елка")] == ["other.md"]


def test_tied_scores_rank_identically_in_json_and_sqlite_backends(tmp_path: Path):
    same = "# Шаблон\n\nОтчёт о смене: всё штатно"
    vault = _vault(tmp_path, {f"shift-{i}.md": same for i in range(6)})
    local = LocalMemoryBackend(index_path=tmp_path / "m.json", vault_root=vault)
    sql = SQLiteMemoryBackend(index_path=tmp_path / "m.sqlite3", vault_root=vault)
    local.index_sync([vault])
    sql.index_sync([vault])
    a = local.search_sync("отчет смена", top_k=6)
    b = sql.search_sync("отчет смена", top_k=6)
    assert len({h.score for h in a}) == 1                 # a real tie
    assert [h.chunk_hash for h in a] == sorted(h.chunk_hash for h in a)
    assert [h.chunk_hash for h in a] == [h.chunk_hash for h in b]
    # restart: a fresh backend on the same JSON file returns the same order
    again = LocalMemoryBackend(index_path=tmp_path / "m.json", vault_root=vault)
    assert [h.chunk_hash for h in again.search_sync("отчет смена", top_k=6)] == [h.chunk_hash for h in a]


def test_sqlite_index_built_by_older_tokenizer_is_rebuilt(tmp_path: Path):
    import sqlite3

    vault = _vault(tmp_path, {"tree.md": "# Праздник\n\nЁлка стоит в углу"})
    db = tmp_path / "m.sqlite3"
    backend = SQLiteMemoryBackend(index_path=db, vault_root=vault)
    backend.index_sync([vault])
    # Simulate an index written before normalization: old terms, no version row.
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE postings SET term='ёлк' WHERE term='елк'")
        conn.execute("DELETE FROM meta WHERE key='tokenizer_version'")
        conn.commit()
    finally:
        conn.close()
    assert backend.search_sync("елка") == []           # stale postings miss the query

    result = backend.index_sync([vault])                  # file bytes unchanged
    assert result["updated"] == 1 and result["skipped"] == 0
    assert [h.source for h in backend.search_sync("елка")] == ["tree.md"]
    assert backend.index_sync([vault])["skipped"] == 1    # rebuilt once, then incremental
