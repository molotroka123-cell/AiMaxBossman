# Bossman Tomorrow Package — 2026-09-24

Owner goal: turn today's research into an executable plan for the local Bossman machine and, when useful, rented 8xH200 compute.

## North Star

Create a local-first Bossman specialist that can produce high-quality production websites and improve through measured training:

```
brief / reference / Figma / screenshot
  -> design-system context
  -> planner + frontend coder
  -> real browser render
  -> visual critic
  -> functional/accessibility/performance checks
  -> targeted patch
  -> repeat
  -> verified site artifact
```

The model is only one component. The loop, renderer, critic, design context and verifier are first-class product components.

## Non-goals

- no foundation-model pretraining from scratch;
- no "train on the whole internet";
- no blind copying of proprietary websites;
- no claim of Claude parity without blind holdout;
- no training-data leakage from holdout into skills/memory;
- no full vendoring of AGPL/source-available projects into Bossman core without an explicit licensing decision;
- no paid GPU rental before the benchmark/data/training harness can reproduce a local smoke run.

## Workstreams

| Workstream | Output |
|---|---|
| Model Fleet | Real models in Bossman UX/CLI with truthful load/test/resource status |
| WebDesigner runtime | End-to-end site build + render + critique + repair |
| OSS reuse | Pinned, licensed donor/adaptor ledger |
| Dataset Factory | Clean SFT + preference examples + immutable holdout |
| 8xH200 | Reproducible LoRA/SFT and optional preference tuning |
| Evaluation | Blind comparison vs base, current Bossman, and Claude reference |
| Evidence | exact model/data/config/code identities and rendered artifacts |
| Jev Twitch market collector | Local verified OI/CVD observations from `k1m6a`; raw evidence + time series, no trading |

## Acceptance ladder

`SPEC_READY -> BASELINE_FROZEN -> RUNTIME_LOOP_PASS -> DATASET_V1_FROZEN -> H200_SMOKE_PASS -> SFT_CANDIDATE_PASS -> BLIND_GAIN_PASS -> CLAUDE_PARITY_EXPERIMENT`

Every rung requires artifacts and reproducible commands. Skipping a rung does not inherit its PASS.

## Initial model strategy

Do not choose the training base by popularity.

Run the same WebDesigner baseline on:
- Qwen3.8-27B;
- Xing4.0-29B-A4B;
- Qwen3.6-35B-A3B;
- Occamy-1.0 where compatible with the coding/tool loop.

The strongest trainable candidate becomes Designer/Coder. A separate multimodal critic may be better than forcing the coding model itself to be the visual judge.

## H200 reality

8xH200 SXM provides about 1.1 TB aggregate HBM capacity (141 GB/GPU) and very high memory bandwidth. This makes 30B/70B PEFT experiments straightforward relative to workstation hardware, but it does not create a good model without a good dataset and evaluator.

Use rented GPUs for training iterations, not for discovering basic data bugs.

## Source-of-truth documents

- Product: [WEBDESIGNER_PRODUCT_TZ.md](WEBDESIGNER_PRODUCT_TZ.md)
- Models: [MODEL_FLEET_GREEN.md](MODEL_FLEET_GREEN.md)
- OSS: [OPEN_SOURCE_SOURCE_LEDGER.md](OPEN_SOURCE_SOURCE_LEDGER.md)
- Data: [DATASET_FACTORY.md](DATASET_FACTORY.md)
- Training: [H200_TRAINING_PLAN.md](H200_TRAINING_PLAN.md)
- Evaluation: [EVALUATION_AND_HOLDOUT.md](EVALUATION_AND_HOLDOUT.md)
- Integration: [BOSSMAN_RUNTIME_INTEGRATION.md](BOSSMAN_RUNTIME_INTEGRATION.md)
- Execution: [TOMORROW_RUNBOOK.md](TOMORROW_RUNBOOK.md)
- Jev Twitch OI/CVD collector: [JEV_TWITCH_OI_CVD_COLLECTOR.md](JEV_TWITCH_OI_CVD_COLLECTOR.md)
- Claude: [CLAUDE_MASTER_PROMPT.md](CLAUDE_MASTER_PROMPT.md)

- Game Studio next target: [GAME_STUDIO_NEXT.md](GAME_STUDIO_NEXT.md)
- Today's branch/work snapshot: [TODAY_HANDOFF_2026-09-23.md](TODAY_HANDOFF_2026-09-23.md)
