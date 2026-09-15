"""Real qdrant local persistence; deterministic vectors test storage, not model quality."""

import httpx
import pytest

from bcc.oss.qdrant import LoopbackEmbeddings, QdrantMemoryBackend, QdrantUnavailable
from bcc.v2.memory.sqlite_index import SQLiteMemoryBackend


@pytest.mark.parametrize("endpoint", ["https://example.com/v1/embeddings", "http://localhost:8080/v1/embeddings",
    "http://127.0.0.1:8080/v1/embeddings?key=secret", "http://user:pass@127.0.0.1:8080/v1/embeddings",
    "http://169.254.169.254:80/v1/embeddings"])
def test_embedding_endpoint_refuses_remote_credentials_and_dns(endpoint):
    with pytest.raises(ValueError):
        LoopbackEmbeddings(endpoint=endpoint, model="local", dimensions=2)


async def test_embedding_response_validation_and_redaction(monkeypatch):
    original = httpx.AsyncClient
    def respond(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1]}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(respond)))
    with pytest.raises(QdrantUnavailable, match="invalid vectors"):
        await LoopbackEmbeddings(endpoint="http://127.0.0.1:8080/v1/embeddings",
                                 model="local", dimensions=2).encode(["private content"])


class FixtureEmbeddings:
    identity = "TEST_FIXTURE_ONLY|2"
    model = "test-fixture-not-a-model"
    dimensions = 2
    fail = False
    async def encode(self, texts):
        if self.fail:
            raise QdrantUnavailable("fixture unavailable")
        return [[1., 0.] if "alpha" in text else [0., 1.] for text in texts]


def backend(tmp_path, embeddings=None, **kwargs):
    pytest.importorskip("qdrant_client")
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return QdrantMemoryBackend(index_path=tmp_path / "index.sqlite3", vector_path=tmp_path / "vectors",
        vault_root=vault, excluded_dirs={".git"}, embeddings=embeddings or FixtureEmbeddings(), **kwargs)


async def test_real_qdrant_persistence_removal_and_sqlite_rollback(tmp_path):
    b = backend(tmp_path)
    a = b.vault_root / "a.md"
    a.write_text("# A\nalpha document")
    other = b.vault_root / "b.md"
    other.write_text("# B\nbeta document")
    result = await b.index([b.vault_root])
    assert result["dense"] and result["vector_chunks"] == 2
    assert (await b.search("alpha"))[0].source == "a.md"
    reopened = backend(tmp_path)
    assert (await reopened.search("beta"))[0].source == "b.md"
    other.unlink()
    await reopened.index([b.vault_root])
    assert all(hit.source != "b.md" for hit in await reopened.search("beta"))
    sqlite = SQLiteMemoryBackend(index_path=b.index_path, vault_root=b.vault_root)
    assert (await sqlite.search("alpha"))[0].source == "a.md"


async def test_failed_rebuild_cannot_return_stale_results(tmp_path):
    embedding = FixtureEmbeddings()
    b = backend(tmp_path, embedding)
    note = b.vault_root / "a.md"
    note.write_text("alpha old")
    await b.index([b.vault_root])
    embedding.fail = True
    note.write_text("alpha new")
    with pytest.raises(QdrantUnavailable):
        await b.index_one(note)
    embedding.fail = False
    with pytest.raises(QdrantUnavailable, match="stale"):
        await b.search("alpha")
    assert not (await b.stats())["dense"]
    await b.index([b.vault_root])
    assert "new" in (await b.search("alpha"))[0].content


async def test_model_switch_requires_reindex(tmp_path):
    b = backend(tmp_path)
    (b.vault_root / "a.md").write_text("alpha")
    await b.index([b.vault_root])
    switched = FixtureEmbeddings()
    switched.identity = "another-model|2"
    with pytest.raises(QdrantUnavailable, match="stale"):
        await backend(tmp_path, switched).search("alpha")


async def test_local_corpus_cap_preserves_lexical_records(tmp_path):
    b = backend(tmp_path, max_chunks=1)
    (b.vault_root / "a.md").write_text("alpha")
    (b.vault_root / "b.md").write_text("beta")
    with pytest.raises(QdrantUnavailable, match="limit"):
        await b.index([b.vault_root])
    assert (await SQLiteMemoryBackend(index_path=b.index_path).stats())["chunks"] == 2


def test_missing_optional_dependency_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr("bcc.oss.qdrant.importlib.util.find_spec", lambda name: None)
    with pytest.raises(QdrantUnavailable, match="semantic-memory"):
        QdrantMemoryBackend(index_path=tmp_path / "i", vector_path=tmp_path / "v",
            vault_root=tmp_path, excluded_dirs={".git"}, embeddings=FixtureEmbeddings())


async def test_config_routes_select_opt_in_and_report_provider_failure(env, tmp_path, monkeypatch):
    pytest.importorskip("qdrant_client")
    root = tmp_path / "owner-vault"
    root.mkdir()
    (root / "a.md").write_text("alpha remembered")
    response = await env.client.post("/api/memory/config", json={"root": str(root), "backend": "qdrant",
        "qdrant": {"endpoint": "http://127.0.0.1:8081/v1/embeddings", "model": "local", "dimensions": 2}})
    assert response.status_code == 200, response.text
    assert response.json()["backend_class"] == "QdrantMemoryBackend"
    async def unavailable(self, texts):
        raise QdrantUnavailable("local model unavailable")
    monkeypatch.setattr(LoopbackEmbeddings, "encode", unavailable)
    response = await env.client.post("/api/memory/index", json={})
    assert response.status_code == 503
    response = await env.client.post("/api/memory/config", json={"root": str(root), "backend": "sqlite"})
    assert response.status_code == 200
    response = await env.client.post("/api/memory/search", json={"query": "alpha"})
    assert response.status_code == 200 and response.json()["items"]


async def test_backend_update_preserves_exclusions_and_private_bridge_config(env, tmp_path):
    from bcc.features.tools_memory import load_config
    root = tmp_path / "owner-vault"
    root.mkdir()
    initial = {"root": str(root), "backend": "sqlite", "index_folders": ["notes"],
               "excludes": ["private"], "memsearch": {"env": {"API_KEY": "private-test-value"}}}
    response = await env.client.post("/api/memory/config", json=initial)
    assert response.status_code == 200, response.text
    assert response.json()["excludes"] == ["private"]
    assert "private-test-value" not in response.text
    response = await env.client.post("/api/memory/config", json={"root": str(root), "backend": "local-json"})
    assert response.status_code == 200, response.text
    saved = await load_config(env.svc)
    assert saved["excludes"] == ["private"]
    assert saved["index_folders"] == ["notes"]
    assert saved["memsearch"] == initial["memsearch"]


@pytest.mark.parametrize("bad", [None, [], "bad", 42])
async def test_config_rejects_malformed_qdrant_objects(env, tmp_path, bad):
    response = await env.client.post("/api/memory/config", json={"root": str(tmp_path), "qdrant": bad})
    assert response.status_code == 400


@pytest.mark.parametrize("field,value", [("dimensions", {}), ("max_chunks", []), ("dimensions", "nan")])
async def test_config_rejects_bad_qdrant_values(env, tmp_path, field, value):
    cfg = {"endpoint": "http://127.0.0.1:8081/v1/embeddings", "model": "local", "dimensions": 2}
    cfg[field] = value
    response = await env.client.post("/api/memory/config", json={"root": str(tmp_path), "backend": "qdrant", "qdrant": cfg})
    assert response.status_code == 400
