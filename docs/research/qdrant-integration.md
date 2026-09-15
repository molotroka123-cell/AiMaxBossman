# Optional Qdrant semantic memory

SQLite remains the default derived index and Markdown/Obsidian remains the source
of truth. The previous measurements in `qdrant.md` still apply: Python local mode
is not an upgrade for a large corpus. This opt-in experiment is capped at 2,000
chunks and rebuilds vectors after indexing; it is intended to verify semantic
retrieval with an explicitly configured local embedding model, not replace the
fast lexical path or train model weights.

Install the optional `bossman-command-center[semantic-memory]` extra. It pins
`qdrant-client==1.15.1`; no embedding weights or Qdrant daemon are installed.
Run an embedding-capable local server, for example llama-server with its
embedding mode enabled. A chat-only server is insufficient. Configure the exact
model name and actual output dimension. The adapter does not download models,
add an API key, or send vault contents to cloud services.

Using the existing authenticated settings API, POST `/api/memory/config`:

```json
{
  "root": "C:/Users/OWNER/Documents/BossmanVault",
  "backend": "qdrant",
  "qdrant": {
    "endpoint": "http://127.0.0.1:8081/v1/embeddings",
    "model": "YOUR_EMBEDDING_MODEL",
    "dimensions": 768,
    "max_chunks": 2000
  }
}
```

Replace the example dimension with the actual model output size. Then POST
`/api/memory/index`, followed by `/api/memory/search` with `{"query":"..."}`.
Existing model tools `memory.index/search/expand/write/stats` use the selected
backend. Vector search results retain SQLite source/chunk identifiers and remain
external data rather than instructions. The lexical reranker is disabled only
for this explicitly selected semantic backend, to preserve its ranking.

A model/config change, embedding failure, or interrupted rebuild makes semantic
search unavailable until a successful reindex. Saved Markdown and SQLite records
remain intact. Return to `backend: "sqlite"` using the same vault configuration
for ordinary lexical search. Each Qdrant operation closes its file handles before
returning, allowing index reopening and backend changes.

Validation: focused tests use the actual pinned Qdrant local engine and disk
persistence, with deterministic fixture vectors solely to validate storage,
ranking, reopening, deletion, failed rebuild handling, model changes, and rollback
to SQLite. HTTP response validation and the numeric-loopback-only network
boundary are tested separately. No live embedding model quality, Windows hardware
performance, or model training result is claimed by these tests.

Upstream reference: https://github.com/qdrant/qdrant-client

## Windows persistence repair

The pinned client's collection deletion does not explicitly close its SQLite
storage before `rmtree(ignore_errors=True)`. On Windows, failed directory removal
can leave old points behind when a collection is recreated. Rebuilds now use the
public point-deletion API instead, then close and reopen the store and verify the
complete persisted chunk set and generation before recording readiness. Separate
collection schemas support dimension changes, capped at four schemas to bound
retained local state. The original undimensioned collection, if present from an
earlier installation, is not queried and counts toward that cap.
