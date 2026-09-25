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

---

## SAME-RUN HANDOFF: 1.5 → 1.6

После закрытия 1.5 или достижения `V1_5_NEAR_COMPLETE` этот же owner-run сразу переходит в отдельную ветку:
[feat/bossman-1.6-secret-intake-20260925](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-secret-intake-20260925)

`V1_5_NEAR_COMPLETE`: software P0 = 0, release-blocking P1 = 0, основные targeted/regression/hidden tests зелёные, canonical SHA зафиксирован; остаток только owner-live/soak/внешняя среда.

Не ждать отдельного следующего дня. Цель одного owner-run:
`BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED`.

Evidence 1.5 и 1.6 сохраняются раздельно по своим SHA.

---

## Immediate handoff to Bossman 1.6

As soon as Bossman 1.5 reaches OWNER_READY closure — or only explicitly non-blocking P2 / optional OWNER_REQUIRED items remain — continue the SAME owner run in:

[Bossman 1.6 self-evolution branch](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-self-evolution-20260925)

Start with `BOSSMAN_1_6_START_HERE.md`.

One-run objective:

`BOSSMAN_1_5_OWNER_READY -> 1.6 HANDOFF -> BOSSMAN_SELF_EVOLUTION_RUNNING`

Do not use this handoff to hide an open 1.5 P0 or release-blocking P1. The goal is to close both stages in one continuous owner run whenever the critical 1.5 gates allow it.
