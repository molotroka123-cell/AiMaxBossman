# ЭТАП 2.222 — Integration Guide for Claude Code

## 1. Do not rewrite Stage 1

ComputerUse v1.2 remains an independent layer. Apply Stage 2.222 after the existing ComputerUse patch. Context Engine must not change browser security/approval behavior.

## 2. Copy implementation

Run:

```bash
python scripts/apply_stage_2_222.py /path/to/AiMaxBossman
```

This copies `bossman/context_engine`, tests, docs and `skills/compact/SKILL.md`.

## 3. Inspect actual LLM call path

Claude must find the one or more locations where messages/tools are assembled before local/cloud LLM calls. Do not guess filenames. Wire a `ContextCompiler` immediately before model invocation, with existing system prompt and task state kept separate.

Recommended lifecycle:

```python
store = ContextStore(runtime_dir / "context/context.db")
embedder = ProductionLocalEmbedder(...)
retriever = HybridRetriever(store, embedder, ProductionReranker(...))
memory = MemoryManager(store, plugins=[StoreMemoryPlugin(store), ...])
compiler = ContextCompiler(retriever, memory)
```

Create these long-lived per process, not once per request.

## 4. Production embedding adapter

Replace `HashEmbedder` in runtime only. Keep it in tests/fallback. Adapter requirements: local, multilingual RU/EN, batched calls, deterministic model/version metadata, dimension validation, timeout, health check. Store embedding model version in chunk metadata before introducing background re-index migration.

## 5. Production reranker

Start with current deterministic reranker to validate integration, then plug in a local cross-encoder. Reranker failure must degrade to hybrid search, not crash the agent.

## 6. Context refresh

Index project files incrementally by content hash. Do not re-embed unchanged chunks. On git branch/commit change, update only affected sources. Maintain source URI + content hash so old provenance can be diagnosed.

## 7. Memory plugin integration

Existing memory/Obsidian/plugin systems should implement the tiny `MemoryPlugin` protocol rather than being merged into one DB immediately. Read-only bridges are acceptable. Sensitive plugin stores must apply their own access policy before returning records.

## 8. Compact skill hook

Trigger options:

- manual `/compact` or dashboard action;
- before cross-agent handoff;
- when context utilization crosses configurable threshold (suggest 70–80% initially);
- before session archival.

Automatic compaction must not silently discard raw transcript. Store resulting handoff as an artifact with refs to raw session.

## 9. Resource controls

Context engine must expose queue depth, DB size, embedding job count, indexing throughput and token telemetry. Fine-tuning/data lab is separate and may not steal memory from active context service without resource-manager approval.

## 10. Shutdown

Close SQLite connection cleanly on process shutdown. Any future embedding server must have explicit lifecycle start/stop and health reporting.

## 11. Gate

Run:

```bash
pytest -q bossman-core/tests/test_context_engine_stage_2_222.py bossman-core/tests/test_compact_plugins.py
```

Then add repo-specific integration tests proving actual LLM request assembly receives compiled context and that the raw transcript remains retrievable.
