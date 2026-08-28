---
name: openrouter-sync
description: Synchronize OpenRouter models into BOSSMAN with pricing, context, modalities, capabilities and live compatibility probes, while preserving user aliases and routing policies.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
---

# OpenRouter Catalog Sync

Use when connecting or refreshing the OpenRouter provider.

## API

Base URL:

`https://openrouter.ai/api/v1`

Authenticate with Bearer API key.

Fetch model catalog from `/models`.

## Import fields when returned

- model id
- display name
- context length
- architecture / modalities
- pricing
- supported/default parameters
- endpoint/details link
- created timestamp

Do not overwrite user-defined aliases or role assignments during refresh.

## Live compatibility probes

Metadata is advisory. For models selected for use, probe:
- normal chat completion
- streaming
- tools/function calling if advertised
- structured output if advertised
- image input if advertised

Store:
- advertised capability
- verified capability
- last verified timestamp
- failure detail

## Routing

BOSSMAN routing is canonical.
OpenRouter provider routing and model fallbacks may be used inside a BOSSMAN route, but the UI must show both layers clearly.

Avoid importing hundreds of models as active favorites. Synchronize the catalog, then let the user filter/pin models by:
- coding
- tools
- vision
- context
- price
- latency/throughput
- provider/privacy requirements

## Refresh behavior

Refresh catalog periodically or on demand.
If a remote model disappears, mark it stale/unavailable; do not silently delete history.
