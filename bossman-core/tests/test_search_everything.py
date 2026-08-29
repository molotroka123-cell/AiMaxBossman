"""Тесты этапа 5 — Search Everything.

Все тесты работают БЕЗ внешних сервисов: движок поиска строится на временном/
in-memory сторе context_engine с детерминированным HashEmbedder. Тесты доказывают
центральные инварианты этапа:
  * поиск идёт через единый стор context_engine (второго RAG нет);
  * provenance/source сохраняются (acceptance-форма);
  * sensitivity gate закрывает утечку: секретный чанк не выдаётся без права;
  * secret exclusion: секрет-подобный файл не входит в индекс;
  * сбой реранкера деградирует к hybrid, а не роняет агента;
  * крупный документ режется на чанки (hit — это чанк, не весь файл);
  * инструмент search.query зарегистрирован и вызывается.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bossman.context_engine import ContextEngine
from bossman.search_everything import (
    SearchDocument,
    SearchEngine,
    SearchHit,
    SearchService,
    SecretPolicy,
    filesystem_documents,
    register_tools,
    set_active_service,
)


def _engine(tmp_path: Path, **kw) -> SearchEngine:
    return SearchEngine(db_path=tmp_path / "context.db", **kw)


# --- 1) upsert + search + provenance (зеркало acceptance-теста) ---------------

def test_public_api_shapes_exposed():
    # Форма результата должна экспортироваться из пакета.
    assert SearchEngine and SearchDocument and SearchHit


def test_upsert_search_preserves_provenance(tmp_path):
    e = _engine(tmp_path)
    e.upsert([
        SearchDocument("1", "Bossman browser context memory", "repo", "p"),
        SearchDocument("2", "unrelated", "repo", "p"),
    ])
    hits = e.search("browser memory", project="p")
    assert hits, "релевантный документ не найден"
    top = hits[0]
    assert top.document.source == "repo"          # source сохранён
    assert top.document.id == "1"                 # provenance по source_uri
    assert top.document.metadata.get("content_hash")  # provenance-хэш едет с чанком
    assert top.score > 0
    e.close()


def test_backed_by_context_engine_single_store(tmp_path):
    # Доказательство отсутствия второго стора: данные лежат в ContextStore
    # context_engine (таблица chunks), а не в приватном dict SearchEngine.
    e = _engine(tmp_path)
    e.upsert([SearchDocument("1", "browser context memory", "repo", "p")])
    assert not hasattr(e, "docs"), "SearchEngine не должен держать собственный корпус"
    rows = e.engine.store.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert rows >= 1, "чанки должны храниться в едином сторе context_engine"
    e.close()


# --- 2) sensitivity gate: утечка закрыта ---------------------------------------

def test_sensitivity_gate_blocks_leak(tmp_path):
    e = _engine(tmp_path)
    e.upsert([
        SearchDocument("pub", "browser public plan notes", "repo", "p", {"sensitivity": "normal"}),
        SearchDocument("sec", "browser secret roadmap notes", "repo", "p", {"sensitivity": "secret"}),
    ])
    # Вызывающий без права (allow по умолчанию — несекретные уровни).
    default = e.search("browser notes", project="p")
    assert default, "несекретный документ должен находиться"
    assert all(h.document.id != "sec" for h in default), "УТЕЧКА: sensitive выдан без права"
    # Вызывающий с правом (расширенный allow-list).
    permitted = e.search("browser notes", project="p", sensitivity_allow=("normal", "secret"))
    assert any(h.document.id == "sec" for h in permitted), "правомочный вызов не получил sensitive"
    e.close()


# --- 3) secret exclusion: секрет не входит в индекс ----------------------------

def test_secret_file_refused_by_connector(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("def f():\n    return 'browser index docs'\n")
    (root / ".env").write_text("API_KEY=sk-abcdef0123456789abcdef\n")  # ci-secret-scan: allow
    (root / "secrets.json").write_text('{"token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"}')  # ci-secret-scan: allow
    (root / "config.pem").write_text("-----BEGIN PRIVATE KEY-----\nZZZ\n-----END PRIVATE KEY-----\n")  # ci-secret-scan: allow
    ids = [d.id for d in filesystem_documents(root, project="p")]
    assert ids == ["app.py"], f"коннектор выдал секрет-подобные файлы: {ids}"
    # Прямая проверка политики.
    pol = SecretPolicy()
    assert pol.is_secret(path=".env")
    assert pol.is_secret(path="secrets.json")
    assert pol.is_secret(path="config.pem")
    assert pol.is_secret_content("here is sk-abcdef0123456789abcdef token")  # ci-secret-scan: allow


def test_secret_content_refused_at_ingest(tmp_path):
    # Adversarial: секрет замаскирован под обычный SearchDocument — ingest отклоняет.
    e = _engine(tmp_path)
    got = e.upsert([SearchDocument(
        "sneaky.txt", "please index my key sk-abcdef0123456789abcdef now", "repo", "p",  # ci-secret-scan: allow
        {"sensitivity": "normal"},
    )])
    assert got == [], "секрет-подобное содержимое не должно индексироваться"
    res = e.search("index my key", project="p", sensitivity_allow=("normal", "secret", "public"))
    assert not res, "УТЕЧКА: секрет доступен через поиск"
    e.close()


# --- 4) reranker degradation ---------------------------------------------------

class _BoomReranker:
    def rerank(self, query, hits):
        raise RuntimeError("cross-encoder OOM")


def test_reranker_failure_degrades_to_hybrid(tmp_path):
    e = _engine(tmp_path, reranker=_BoomReranker())  # обёрнут SafeReranker внутри
    e.upsert([
        SearchDocument("a", "browser context memory engine", "repo", "p"),
        SearchDocument("b", "some other note", "repo", "p"),
    ])
    hits = e.search("browser memory", project="p")  # не должно бросать
    assert hits and hits[0].document.id == "a", "сбой реранкера должен деградировать к hybrid"
    e.close()


# --- 5) chunking: hit — это чанк, а не весь файл --------------------------------

def test_large_document_is_chunked(tmp_path):
    big = "Bossman browser context memory subsystem indexes documents for retrieval. " * 300
    e = _engine(tmp_path)
    e.upsert([SearchDocument("big.md", big, "markdown", "p")])
    assert e.engine.telemetry()["chunks"] > 1, "крупный документ не разбит на чанки"
    hit = e.search("browser memory retrieval", project="p")[0]
    assert len(hit.document.text) < len(big), "вернулся весь файл, а не чанк"
    e.close()


# --- 6) incremental re-index by content_hash -----------------------------------

def test_incremental_reindex_skips_unchanged(tmp_path):
    e = _engine(tmp_path)
    first = e.upsert([SearchDocument("f1", "browser memory alpha", "repo", "p")])
    chunks1 = e.engine.telemetry()["chunks"]
    again = e.upsert([SearchDocument("f1", "browser memory alpha", "repo", "p")])
    chunks2 = e.engine.telemetry()["chunks"]
    assert len(first) == 1 and len(again) == 0 and chunks1 == chunks2
    changed = e.upsert([SearchDocument("f1", "browser memory beta changed", "repo", "p")])
    assert len(changed) == 1, "изменённый документ должен переиндексироваться"
    e.close()


# --- 7) зарегистрированный инструмент search.query -----------------------------

def test_search_query_tool_callable(tmp_path):
    from bossman.toolkit import REGISTRY, ToolContext

    register_tools()
    assert "search.query" in REGISTRY and "search.index" in REGISTRY

    svc = SearchService(engine=ContextEngine(tmp_path / "svc.db"))
    svc.index_text("browser context memory tool result", source_uri="t1",
                   source_type="repo", project="p")
    set_active_service(svc)
    try:
        tool = REGISTRY["search.query"]
        result = asyncio.run(tool.handler({"q": "browser memory", "project": "p"},
                                          ToolContext(agent="test")))
        assert not result.error
        assert "t1" in result.render(), "инструмент не вернул найденный документ"
        # search.index тоже вызывается и отклоняет секрет.
        idx = REGISTRY["search.index"]
        refused = asyncio.run(idx.handler(
            {"text": "leak sk-abcdef0123456789abcdef", "source_uri": "bad"},  # ci-secret-scan: allow
            ToolContext(agent="test")))
        assert refused.error, "search.index должен отклонить секрет"
    finally:
        set_active_service(None)


# --- 8) subsystem lifecycle idempotent + degrade-safe --------------------------

def test_subsystem_lifecycle_idempotent(tmp_path):
    from bossman.lifecycle import Subsystem

    svc = SearchService(engine=ContextEngine(tmp_path / "life.db"))
    assert isinstance(svc, Subsystem)
    assert svc.name == "search_everything" and svc.critical is False
    asyncio.run(svc.validate())
    asyncio.run(svc.validate())
    asyncio.run(svc.start())
    asyncio.run(svc.start())
    asyncio.run(svc.stop())
    asyncio.run(svc.stop())
