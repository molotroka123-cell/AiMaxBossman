"""Explicit, small-corpus semantic memory; Markdown and SQLite stay authoritative.

Optional qdrant-client local mode is intentionally not the default: see the
measured limits in docs/research/qdrant.md. No weights are downloaded or trained.
Embeddings must come from an owner-configured numeric loopback endpoint.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import importlib.util
import ipaddress
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..v2.memory.memsearch_bridge import MemoryHit
from ..v2.memory.sqlite_index import SQLiteMemoryBackend


class QdrantUnavailable(RuntimeError):
    pass


class LoopbackEmbeddings:
    def __init__(self, *, endpoint: str, model: str, dimensions: int):
        parts = urlsplit(endpoint)
        try:
            address = ipaddress.ip_address(parts.hostname or "")
            valid = (address.is_loopback and parts.scheme == "http" and parts.port
                     and not parts.username and not parts.password
                     and not parts.query and not parts.fragment
                     and parts.path == "/v1/embeddings")
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("embeddings endpoint must be http://127.0.0.1:PORT/v1/embeddings (or numeric IPv6 loopback)")
        if not model.strip() or not 1 <= dimensions <= 4096:
            raise ValueError("explicit embedding model and dimensions (1..4096) required")
        self.endpoint, self.model, self.dimensions = endpoint, model, dimensions
        self.identity = f"{endpoint}|{model}|{dimensions}"

    async def encode(self, texts: list[str]) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
                async with client.stream("POST", self.endpoint, json={
                    "model": self.model, "input": texts, "encoding_format": "float",
                }) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw) > 8 * 1024 * 1024:
                            raise ValueError("oversized embeddings response")
            data = json.loads(raw)["data"]
            if len(data) != len(texts):
                raise ValueError("embedding count mismatch")
            ordered = sorted(data, key=lambda item: item["index"])
            if [item["index"] for item in ordered] != list(range(len(texts))):
                raise ValueError("invalid embedding indices")
            vectors = [[float(x) for x in item["embedding"]] for item in ordered]
            if any(len(v) != self.dimensions or not all(math.isfinite(x) for x in v)
                   or not any(v) for v in vectors):
                raise ValueError("invalid embedding dimensions or values")
            return vectors
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError) as exc:
            # Never echo provider response bodies, credentials, or document text.
            raise QdrantUnavailable("local embedding service failed or returned invalid vectors") from exc


class QdrantMemoryBackend(SQLiteMemoryBackend):
    """Rebuildable vector side index; explicit failure instead of stale search."""
    def __init__(self, *, index_path: Path, vector_path: Path, vault_root: Path,
                 excluded_dirs: set[str], embeddings: LoopbackEmbeddings,
                 max_chunks: int = 2000):
        if not 1 <= max_chunks <= 2000:
            raise ValueError("Qdrant local experiment supports at most 2000 chunks; use SQLite for larger vaults")
        if importlib.util.find_spec("qdrant_client") is None:
            raise QdrantUnavailable("install bossman-command-center[semantic-memory] to enable Qdrant")
        super().__init__(index_path=index_path, vault_root=vault_root, excluded_dirs=excluded_dirs)
        self.vector_path = Path(vector_path)
        self.embeddings = embeddings
        self.max_chunks = max_chunks
        self._operation_lock = asyncio.Lock()
        self._collection = "bossman_memory"

    def _rows(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT chunk_hash,source,heading,content FROM chunks ORDER BY chunk_hash LIMIT ?",
                                (self.max_chunks + 1,)).fetchall()
        if len(rows) > self.max_chunks:
            raise QdrantUnavailable("Qdrant local chunk limit exceeded; select backend=sqlite")
        return [dict(row) for row in rows]

    def _generation(self, rows: list[dict]) -> str:
        return hashlib.sha256((self.embeddings.identity + json.dumps(rows, sort_keys=True,
                              ensure_ascii=False)).encode()).hexdigest()

    def _client(self):
        from qdrant_client import QdrantClient
        self.vector_path.parent.mkdir(parents=True, exist_ok=True)
        return QdrantClient(path=str(self.vector_path))

    def _replace(self, rows: list[dict], vectors: list[list[float]], generation: str):
        from qdrant_client import models
        with closing(self._client()) as client:
            if client.collection_exists(self._collection):
                client.delete_collection(self._collection)
            client.create_collection(self._collection, vectors_config=models.VectorParams(
                size=self.embeddings.dimensions, distance=models.Distance.COSINE))
            for start in range(0, len(rows), 64):
                client.upsert(self._collection, points=[models.PointStruct(
                    id=int(row["chunk_hash"], 16), vector=vector,
                    payload={"chunk_hash": row["chunk_hash"], "generation": generation},
                ) for row, vector in zip(rows[start:start+64], vectors[start:start+64])], wait=True)
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('qdrant_generation',?)", (generation,))
            conn.commit()

    async def _sync_vectors(self):
        rows = await asyncio.to_thread(self._rows)
        generation = self._generation(rows)
        # Clear the readiness marker BEFORE touching the derived index. A failed
        # embedding call or interrupted rebuild can never expose stale evidence.
        def invalidate():
            with self._connect() as conn:
                conn.execute("DELETE FROM meta WHERE key='qdrant_generation'")
                conn.commit()
        await asyncio.to_thread(invalidate)
        vectors = []
        for start in range(0, len(rows), 32):
            vectors.extend(await self.embeddings.encode([
                row["heading"] + "\n" + row["content"] for row in rows[start:start+32]]))
        try:
            await asyncio.to_thread(self._replace, rows, vectors, generation)
        except (OSError, RuntimeError, ValueError) as exc:
            raise QdrantUnavailable("local Qdrant rebuild failed; retry memory.index after closing other users of this index") from exc
        return {"dense": True, "vector_chunks": len(rows), "backend": "qdrant"}

    async def index(self, paths: list[Path], *, force: bool = False):
        async with self._operation_lock:
            result = await super().index(paths, force=force)
            result.update(await self._sync_vectors())
            return result

    async def index_one(self, path: Path, *, force: bool = False):
        async with self._operation_lock:
            result = await super().index_one(path, force=force)
            result.update(await self._sync_vectors())
            return result

    def _query(self, vector: list[float], top_k: int, generation: str) -> list[MemoryHit]:
        with self._connect() as conn:
            marker = conn.execute("SELECT value FROM meta WHERE key='qdrant_generation'").fetchone()
            if not marker or marker[0] != generation:
                raise QdrantUnavailable("semantic index missing or stale; run memory.index")
        with closing(self._client()) as client:
            if not client.collection_exists(self._collection):
                raise QdrantUnavailable("semantic index missing; run memory.index")
            points = client.query_points(self._collection, query=vector,
                                         limit=max(1, min(top_k, 40))).points
        hits = []
        with self._connect() as conn:
            for point in points:
                payload = point.payload or {}
                if payload.get("generation") != generation:
                    raise QdrantUnavailable("semantic index interrupted; run memory.index")
                row = conn.execute("SELECT * FROM chunks WHERE chunk_hash=?",
                                   (payload.get("chunk_hash"),)).fetchone()
                if row:
                    hits.append(MemoryHit(content=row["content"], source=row["source"],
                        heading=row["heading"], chunk_hash=row["chunk_hash"],
                        score=float(point.score), metadata={"backend": "qdrant", "model": self.embeddings.model}))
        return hits

    async def search(self, query: str, *, top_k: int = 16):
        async with self._operation_lock:
            rows = await asyncio.to_thread(self._rows)
            vector = (await self.embeddings.encode([query]))[0]
            try:
                return await asyncio.to_thread(self._query, vector, top_k, self._generation(rows))
            except QdrantUnavailable:
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                raise QdrantUnavailable("local Qdrant query failed; check index availability and run memory.index") from exc

    async def stats(self):
        result = await super().stats()
        rows = await asyncio.to_thread(self._rows)
        with self._connect() as conn:
            marker = conn.execute("SELECT value FROM meta WHERE key='qdrant_generation'").fetchone()
        result.update(backend="qdrant", dense=bool(marker and marker[0] == self._generation(rows)),
                      embedding_model=self.embeddings.model, max_chunks=self.max_chunks,
                      experimental=True)
        return result
