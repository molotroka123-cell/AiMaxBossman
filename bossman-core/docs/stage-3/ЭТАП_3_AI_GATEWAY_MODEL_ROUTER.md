# ЭТАП 3 — BOSSMAN AI Gateway + Model Router

## Цель

Один приватный API между всеми приложениями BOSSMAN и реальными model runtimes. Клиенты используют стабильные aliases (`bossman-fast`, `bossman-smart`, `bossman-code`, `bossman-vision`, `bossman-embed`) и не знают, где физически запущена модель.

## Поток

```text
iPhone / BOSSMAN / Video Factory / Context Engine
                     ↓
              BOSSMAN Gateway
                     ↓
       auth → rate limit → route → queue
                     ↓
          local runtime / fallback
                     ↓
              telemetry / health
```

## Что уже реализовано в коде Stage 3

- FastAPI gateway;
- OpenAI-compatible `/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `/v1/models`;
- model aliases and capability routing;
- ordered fallback between backends;
- health cache/probes;
- per-backend concurrency semaphore and bounded queue wait;
- streaming passthrough;
- bearer clients, key-from-env, constant-time hashed matching;
- per-client aliases and in-memory token-bucket rate limiting;
- metrics and token usage accounting;
- process/RAM hooks (psutil when available);
- body-size guard;
- config-driven Ollama/LM Studio/OpenRouter-compatible backends;
- shutdown closes upstream clients;
- tests with real FastAPI application and mocked OpenAI-compatible upstreams.

## Security boundary

Gateway binds to `127.0.0.1` by default. Do not expose it directly to the public Internet. A future iPhone client must connect over a private tunnel (WireGuard/Tailscale-class design) plus HTTPS and device-bound authentication. API keys belong in environment/secret storage, never YAML committed to Git.

## Important routing rule

Applications request a capability alias, not a raw provider model. This lets BOSSMAN replace models without rewriting clients.

## Streaming fallback rule

Fallback is safe only before bytes are emitted. Once a stream has sent output, the gateway never silently starts a different model because that could merge two answers. It terminates the stream with an error event.

## Stage 3.1 after basic integration

Claude should adapt Bossman's existing `llm.py` to point to Gateway instead of individual local/cloud endpoints, while preserving backwards compatibility until tests pass. Context Engine embeddings should then use `bossman-embed`; ComputerUse multimodal fallback should use `bossman-vision`.

## Stage 3.2 / future phone foundation

Do not build the iOS UI in this stage, but preserve extension points for device enrollment, revocation, short-lived sessions, push approvals, voice and files. Long-lived static API keys are sufficient for local integration testing only, not the final iPhone trust model.
