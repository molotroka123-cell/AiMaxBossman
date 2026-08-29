# Stage 3 Integration Guide for Claude Code

1. Read current `bossman/llm.py`, config, API lifecycle and runner before editing.
2. Copy `bossman/gateway/` into current core.
3. Add the `bossman-gateway` console script and optional `psutil` dependency.
4. Copy `config/gateway.example.yaml` to an example/config docs location; never commit populated secrets.
5. Run Stage 3 tests before touching existing LLM code.
6. Add a thin Gateway client adapter to existing `llm.py`. Preserve its public interface so runner/agents do not need a rewrite.
7. Route Context Engine embedding calls through alias `bossman-embed` where compatible.
8. Route multimodal/vision work through `bossman-vision` where compatible.
9. Wire gateway shutdown into the actual process lifecycle if embedded in the same process. If run as a separate service, use its FastAPI lifespan.
10. Do not expose port publicly. Phone access is a later stage behind private networking and stronger device auth.
11. Run existing Core tests + Stage 1 + Stage 2.222 + Stage 3 tests.
12. Produce an audit MD and record exact model aliases/backends used in test environment without recording credentials.

## Required acceptance tests

- auth failure/success;
- alias isolation;
- capability routing;
- fallback on unavailable primary;
- concurrency does not exceed configured backend limit;
- queue timeout is bounded;
- streaming works and does not combine two models after partial output;
- shutdown closes clients;
- metrics account requests/tokens;
- existing Bossman agent can call through gateway;
- Context Engine can request embeddings through gateway;
- vision call selects a vision-capable target;
- no secret values appear in repo or logs.
