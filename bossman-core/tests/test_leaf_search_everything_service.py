"""authored_by_lane (opsplug): bossman.search_everything.service - one store, secrets refused, lifecycle idempotent."""
import pytest

from bossman.context_engine import ContextEngine
from bossman.search_everything.engine import SearchDocument
from bossman.search_everything.service import (
    SearchService, build_subsystem, get_active_service, set_active_service,
)

SECRET_TEXT = "deploy token sk-abcdef0123456789abcdef0123"   # ci-secret-scan: allow


@pytest.fixture
def svc(tmp_path):
    s = SearchService(engine=ContextEngine(tmp_path / "svc.db"))
    yield s
    s.engine.close()


def test_index_text_then_search_returns_a_hit_with_provenance(svc):
    svc.index_text("the resource brain admits workloads before the pool is exhausted",
                   source_uri="doc://rb", source_type="repo", project="p")
    svc.index_text("completely unrelated cooking recipe", source_uri="doc://food", project="p")
    hits = svc.search("resource brain admits workloads", project="p")
    assert hits and hits[0].document.id == "doc://rb" and hits[0].document.source == "repo"
    assert svc.search("resource brain", project="other-project") == []


def test_secret_text_is_refused_and_counted_never_indexed(svc):
    got = svc.index_text(SECRET_TEXT, source_uri="doc://leak", project="p")
    assert got is None
    assert svc.search("deploy token", project="p") == []
    stats = svc.index_documents([SearchDocument("ok", "browser context memory", "repo", "p"),
                                 SearchDocument("bad", SECRET_TEXT, "repo", "p")])
    assert stats["indexed"] == 1 and stats["refused"] == 1


def test_index_tree_skips_secret_files_on_disk(svc, tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "notes.md").write_text("browser automation notes for the owner", encoding="utf-8")
    (root / ".env").write_text("API_KEY=" + "x" * 30, encoding="utf-8")
    stats = svc.index_tree(root, project="p")
    assert stats["indexed"] == 1 and stats["refused"] == 0
    assert [h.document.id for h in svc.search("browser automation", project="p")] == ["notes.md"]
    assert svc.search("API_KEY", project="p") == []


def test_sensitive_documents_are_not_returned_without_the_right(svc):
    svc.index_text("private salary discussion text", source_uri="doc://hr", project="p", sensitivity="secret_ok_but_private")
    svc.index_text("public salary guide text", source_uri="doc://pub", project="p")
    default_hits = {h.document.id for h in svc.search("salary", project="p")}
    assert "doc://pub" in default_hits and "doc://hr" not in default_hits


async def test_lifecycle_is_idempotent_and_registers_as_the_active_service(svc):
    assert svc.name == "search_everything" and svc.critical is False
    set_active_service(None)
    await svc.validate()
    await svc.validate()
    await svc.start()
    await svc.start()
    assert get_active_service() is svc
    await svc.stop()
    await svc.stop()
    assert get_active_service() is None


def test_build_subsystem_is_lazy_and_returns_a_service(monkeypatch, tmp_path):
    from bossman.search_everything import service as mod
    monkeypatch.setattr(mod.settings, "context_db", tmp_path / "lazy.db", raising=False)
    sub = build_subsystem()
    assert isinstance(sub, SearchService) and sub._search is None      # nothing opened until first use
