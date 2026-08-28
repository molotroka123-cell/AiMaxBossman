---
name: model-eval
description: Benchmark and validate a local or cloud model for real BOSSMAN roles, measuring speed, memory, capabilities, tool use, context behavior, stability, and cost rather than relying only on public benchmarks.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
---

# Model Evaluation

Use when a model is added, updated, quantized differently, or considered for routing.

## Record environment

- hardware
- runtime/backend
- model ID
- quant
- context setting
- batch/cache settings
- provider/endpoint
- date

## Measure separately where supported

- time to first token
- prompt/prefill throughput
- generation throughput
- RAM / VRAM / unified memory
- peak memory
- load time
- stability
- context success
- cost for cloud models

## Capability tests

Use small reproducible fixtures for:
- coding
- reasoning
- tool calling
- structured output
- vision if advertised
- long-context retrieval if relevant

A capability is not marked supported merely because provider metadata claims it; perform a live probe.

## Output

Store historical results. Recommend roles such as:
- fast worker
- coder
- vision worker
- reviewer
- heavy reasoning
- fallback

The Smart Router should consume these real measurements.
