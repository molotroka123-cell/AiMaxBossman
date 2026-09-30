# Prompt Cache integration

## Architecture

Production requests remain on the single authority path:
`runner -> llm.chat -> GatewayClient -> Stage3 Gateway -> cloud policy -> Cost Governor -> provider backend`.
Only backends explicitly configured with `kind: openrouter` receive cache metadata.

## Provider behavior

- Bossman derives an opaque `bossman-pc-<sha256>` ID from agent + run; it is stable per run, differs across runs, contains no source values, and is below OpenRouter's 256-character limit.
- Claude/Anthropic Chat Completions use an explicit breakpoint on the final leading system/developer block, preserving OpenRouter provider choice. Responses API falls back to top-level automatic caching.
- TTL is `5m` by default; `BOSSMAN_PROMPT_CACHE_TTL=1h` or backend config selects the longer human-in-loop policy.
- Unsupported providers are unchanged. Cache-metadata 400/422 responses retry the original payload once under the same policy and budget reservation.

## Cost and telemetry

Cost Governor remains `reserve -> provider call -> commit/release`. Cold-write price is included in the reservation. `usage.cost` is preferred; otherwise non-zero read/write rates are applied. Metrics expose hits, cached/fresh/write tokens, actual/baseline/saved cost, affinity, provider/model/TTL/state/miss reason, and hash-only prefix stability. No prompts or cache contents are stored.

## Verification and limitations

Unit/integration coverage includes session IDs, both TTLs, explicit cache control, prefix stability, fail-open, usage/cost accounting, cloud policy, Cost Governor, and streaming. Live OpenRouter verification was `SKIP_EXTERNAL_CREDENTIAL` on 2026-08-30; no live PASS is claimed. Metrics are process-local and reset when Gateway restarts.

Contracts checked against:
- https://openrouter.ai/docs/guides/best-practices/prompt-caching
- https://openrouter.ai/docs/cookbook/administration/usage-accounting
- https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-6
