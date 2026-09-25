# Bossman 1.5 — Owner Model Routing Stack

Date: 2026-09-25

Machine-readable source of truth: `config/v1.5/model-routing-stack.json`.

## Owner routing intent

```text
OWNER
  │
  ▼
BOSSMAN / JEV
  ├── simple task      → Qwen3.6
  ├── code / project   → Qwen3.8 → Xing / Occamy workers
  ├── screen / browser → Nex-N2.5
  ├── very hard task   → Flash-Next
  ├── verifier         → gpt-oss-120B
  ├── image            → Qwen-Image-2.1
  └── video            → LTX-2.5 / Wan
```

This is routing intent, not a claim that every alias is installed or compatible.
At the beginning of the owner run Aster resolves aliases to exact local model IDs, runtime, quantization and endpoint, then executes the role-specific checks.

## Live truth states

- `GREEN` — loaded and passed the required capability checks.
- `DEGRADED` — usable but missed a non-critical target or required fallback.
- `BLOCKED` — unavailable/incompatible.
- `UNVERIFIED` — no live evidence.

No silent alias replacement. A fallback must pass the same role gate.

## Self-improvement startup

Self-improvement does not wait for every specialist to be GREEN.
Minimum safe start: one verified coding route, one independent verifier, Jev/controller, STOP/budget/candidate isolation, and clean source identity.
Once those pass, Bossman starts the bounded self-improvement campaign while missing specialists/provider capacity are handled in parallel.

## Cloud economy overlay

The local stack is primary when sufficient. Extra capacity remains free-first: 3× Nemotron roles, free Ling coder/tester, and bounded paid GLM finalizer.
Cloud capacity never silently becomes the owner local default stack.
