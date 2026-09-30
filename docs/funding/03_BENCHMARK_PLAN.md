# 03 — Benchmark plan / План бенчмарка

Status: **PLANNED**. No number in this file is a result. The only measured numbers are quoted from 07 with their row id.
Sources accessed 2026-09-29 (see §6).

## 1. Question

Does a private, self-hosted frontier-class open model (DeepSeek-V3.2, 685B parameters) make the Bossman dual-approval
engineering loop cheaper, faster or more private than the current mix (free cloud planner + local Qwen + Claude/Codex
subscriptions + paid API) — enough to justify a 700+ GB coherent-memory machine?

Вопрос: даёт ли собственная крупная открытая модель измеримый выигрыш по цене/скорости/приватности принятого изменения.

## 2. Arms (fixed)

| Arm | Model / endpoint | Where | Status of access |
|---|---|---|---|
| A. Free cloud | NVIDIA Nemotron via OpenRouter `:free` | cloud | available; "free" checked against live price per run |
| B. Local | `bossman-fast-qwen36-35b-a3b-q5` (Ollama) and GPT-OSS-120B MXFP4 | owner machine, 128 GB unified | available (07 A7) |
| C. Claude | Claude Code CLI (owner subscription) | cloud | available |
| D. Codex | OpenAI Codex CLI (owner subscription) | cloud | available |
| E. DeepSeek API | `deepseek-v4-pro` (and `deepseek-flash`) — the API no longer serves V3.2 by name | cloud, paid, off-peak | needs owner API key + hard cap (owner action) |
| F. Self-hosted | DeepSeek-V3.2 FP8, SGLang `--tp 8`, 8×H200 | credit-funded node | **needs credits** |

Arms A, B, E, F act as planner/writer **and** as an extra reviewer; the required approvals stay Claude + Codex in all arms
(constitution). An arm's reviewer verdict is recorded as "advisory" and compared with the dual verdict.

## 3. Task set (fixed before any run)

- 60 coding tasks, seed pinned, frozen in a manifest with SHA-256, `training_eligible: false`
  (same mechanism as `bossman-core/bossman/benchmark/datasets/v1/manifest.json`):
  - 30 repository-internal bugs reproduced from Bossman history (known fixing commit, hidden test);
  - 20 SWE-style public tasks (SWE-bench-Verified-style instances; exact subset listed in the manifest);
  - 10 synthetic `tools/coding_value_sim.py` cases (one allowed file, protected test, independent verifier).
- Each task runs through the real loop: isolated worktree → writer → tests → reviews → staging. 3 repetitions for
  non-deterministic arms; Wilson 95 % CI reported (existing benchmark convention).
- Held-out split: 20 of the 60 are never shown to any lesson/skill store before the run.

## 4. Metrics

| Metric | Definition |
|---|---|
| Time to accepted patch | wall time from goal READY to both approvals on one SHA + staging PASS |
| Review disagreement | share of candidates where Claude and Codex verdicts differ; plus advisory-arm vs dual verdict |
| SWE-style success | hidden tests pass AND only allowed files changed (independent verifier) |
| Cost per accepted change | provider $ (live price × tokens) + GPU-hour $ at list price, divided by accepted changes; subscriptions reported separately, not amortised as $0 |
| Throughput | output tokens/s (provider timing, not wall time), accepted changes per GPU-hour, concurrent sessions sustained |
| Safety | UnsafeActionRate, DuplicateEffectRate, FalseCompletionRate (release gate: any > 0 → NO-GO) |
| Privacy | bytes of repository context sent to third-party endpoints per accepted change |

Pre-registered decision rule for M4 ("local 700+ GB materially helps"): arm F beats the best of A/B/E on cost per
accepted change **or** on privacy at no worse than −5 pp success, in both halves of the task set, with non-overlapping CIs
on at least one of those metrics. Otherwise the hardware request is downgraded and this is reported as-is.

## 5. Why the workload does not fit economical single-GPU tiers

| Fact | Value | Source |
|---|---|---|
| DeepSeek-V3.2 parameters | 685,355,329,792 (685B), MIT licence | [HF model page](https://huggingface.co/deepseek-ai/DeepSeek-V3.2), accessed 2026-09-29 |
| DeepSeek-V3.2 checkpoint size | 689,484,423,011 bytes ≈ 689.5 GB, mostly FP8 | same page / HF API, accessed 2026-09-29 |
| H200 memory per GPU | 141 GB HBM3e, 4.8 TB/s | [nvidia.com/h200](https://www.nvidia.com/en-us/data-center/h200/), accessed 2026-09-29 |
| 8×H200 aggregate | 1,128 GB (8 × 141, arithmetic) | derived |
| SGLang reference launch | `sglang serve --model-path deepseek-ai/DeepSeek-V3.2 --tp 8`; supported on H200, B200, MI300X/MI355X | [SGLang docs](https://docs.sglang.io/basic_usage/deepseek_v32.html), accessed 2026-09-29 |
| ZeroGPU largest slice | RTX Pro 6000 Blackwell, 96 GB | [HF ZeroGPU docs](https://huggingface.co/docs/hub/spaces-zerogpu), accessed 2026-09-29 |
| Owner machine | 128 GB unified, 88 GB model cap | `docs/evolution/LOCAL_CHAMPIONS_88GB.md` |
| DGX Station GB300 | 252 GB HBM3e + 496 GB LPDDR5X = **748 GB coherent**, NVLink-C2C 900 GB/s | [nvidia.com DGX Station](https://www.nvidia.com/en-us/products/workstations/dgx-station/), accessed 2026-09-29 |

Reasoning:
1. Weights alone (≈689.5 GB) exceed any single GPU by ≈4.9× (141 GB) and the owner machine by ≈5.4×; at least 5 H200
   are needed for weights, and the documented configuration is tensor-parallel 8 — so the smallest honest cloud unit is
   a full 8×H200 node, not a single-GPU instance.
2. The loop needs long repository contexts (KV cache) and ≥ 2 concurrent sessions (writer + advisory reviewer), which
   adds memory on top of weights.
3. **Honest caveat on the reference machine:** 748 GB coherent memory holds the FP8 checkpoint with only ≈58 GB left,
   and most of it would sit in LPDDR5X (396 GB/s) rather than HBM. Full-precision FP8 V3.2 on one DGX Station is
   therefore marginal; a lower-bit quantisation or a smaller open model is the likely local configuration.
   Its speed on GB300 is **UNVERIFIED** and is itself a benchmark item (run on the station through an NVIDIA
   program or OEM demo, if offered).

## 6. Run protocol on credit capacity

1. Node gets no production credentials; only the benchmark repo mirror and the manifest. Endpoint is OpenAI-compatible,
   bound to a private tunnel, token rotated per window.
2. Pull weights once to node-local disk; record revision + file hashes.
3. Run arms A–F on the same manifest in one window; store SHA-bound JSON + Markdown reports in
   `docs/autonomy/benchmark_history/` (existing format).
4. Shutdown immediately after the window (see 04 §4 for the automatic kill).
5. Publish results only with owner approval (constitution: publications are user-gated).

## 7. Existing measured baselines (context, not results of this plan)

- Local GPT-OSS-120B 7/7 in 90 s at 49 tok/s; Qwen3.8-27B Q5 6/7, 316 s, 10.0 tok/s (07 A7).
- Local learning A/B: NOT_PROVEN (07 A8). Lessons did not change outcomes (07 A9).
- Local FULL agent regressed vs raw on four capability axes (07 A10) — this benchmark must include a raw-model control.
