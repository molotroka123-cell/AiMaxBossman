# OpenRouter Integration — locked implementation requirements

Current code already supports OpenAI-compatible endpoints and LiteLLM has an OpenRouter fallback.

V2 must make OpenRouter first-class.

## API

Default base:

`https://openrouter.ai/api/v1`

Catalog:

`GET /models`

Chat:

`POST /chat/completions`

## Store remote catalog separately from user-pinned active models

Recommended entity:

```text
provider_catalog_models
- provider_id
- remote_id
- display_name
- context_window
- pricing
- architecture
- modalities
- supported_parameters
- raw_metadata
- advertised_caps
- verified_caps
- last_synced_at
- stale
```

Do not import hundreds of remote entries as active BOSSMAN models automatically.

## Sync

- Upsert catalog by provider + remote_id.
- Missing entries after refresh => stale, not delete.
- User aliases/history/role assignments stay intact.
- Pinning creates or links a BOSSMAN model row.

## Capability verification

Metadata is advisory.

For pinned models run cheap probes for:
- chat
- tools
- structured output
- vision when advertised
- streaming

Store:
- advertised
- verified
- verified_at
- failure detail

## Tool calling

The current generic Command Center `OpenAICompatAdapter.chat()` only forwards normal generation params.

V2 must preserve the full assistant message and `tool_calls`, not flatten every reply to text.

Do not lose:
- `tool_calls[].id`
- function name
- JSON arguments
- finish reason

## Routing

BOSSMAN Smart Router is the top layer.

OpenRouter provider routing may be set in a request as a lower-level routing policy.

Always log:
- requested model
- returned model
- chosen provider when response metadata exposes it
- cost/tokens
