# Existing Bossman `llm.py` adapter

The current repository already uses an OpenAI-compatible LiteLLM URL. That means Stage 3 can be introduced with a very small compatibility change instead of rewriting runner/agents.

Current pattern is effectively:

```text
POST {LITELLM_URL}/chat/completions
```

Stage 3 supports the same path when base URL is configured as:

```text
http://bossman-gateway:8765/v1
```

However, the current implementation uses `agent.api_key or litellm_master_key` as the upstream bearer. Gateway client authentication is a different trust boundary. Claude must change this so Bossman Core authenticates to Gateway with `BOSSMAN_GATEWAY_CORE_KEY`; agent identity/permissions stay inside Core and must not be represented by provider keys.

Recommended transition:

1. keep current `chat(...)` public function signature;
2. keep cloud approval checks before any cloud-eligible routing;
3. replace direct provider/LiteLLM request with `GatewayClient`;
4. preserve `model_calls` and `cloud_calls` accounting;
5. map existing agent model aliases to Stage 3 aliases or explicitly add legacy aliases to gateway config during migration;
6. update `vision_caption` to request `bossman-vision`;
7. never let generic `bossman-smart` silently use cloud for a `cloud_policy=never` agent. This requires policy-aware aliases or a request policy signal enforced server-side in the final integration.

## Critical cloud-policy note

The Gateway's generic fallback mechanism is intentionally provider-agnostic. Existing Bossman has stronger per-agent cloud policy. During integration, cloud routing must remain gated. Safe patterns include:

- separate aliases such as `bossman-smart-local` and `bossman-smart-cloud-fallback`, with Core only choosing the latter after policy/approval; or
- add a signed/internal routing-policy field recognized by Gateway.

Do **not** simply put OpenRouter behind `bossman-smart` and bypass existing `cloud_policy` semantics.
