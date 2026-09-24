# Bossman 1.5 — Jev Economy Orchestrator

Status: **IMPLEMENTED ON DEDICATED 1.5 BRANCH / NOT 1.0 RELEASE SCOPE**

Branch:
`feat/bossman-1.5-economy-orchestrator-20260924`

Goal: move most expensive repetitive engineering/trading-learning work away from
Codex/Claude and through Bossman itself, using free OpenRouter workers first and
one tightly budgeted paid finalizer only when evidence justifies it.

## Roles

| Role | Model | Cost policy | Job |
|---|---|---|---|
| N1 evidence | `nvidia/nemotron-3-ultra-550b-a55b:free` | hard free | extract evidence from typed YouTube episodes |
| N2 strategy | same Nemotron | hard free | formalize trigger/confirmation/invalidation/hypothesis |
| N3 adversary | same Nemotron | hard free | attack hindsight, leakage, incompatible OI/CVD and unsupported claims |
| Ling coder | `inclusionai/ling-3.0-flash-fin:free` | hard free | code/test/repair inside a bounded worktree |
| GLM finalizer | `z-ai/glm-5.3-flash` | paid, capped | only final unresolved verified blocker |
| Aster | external controller | no automatic inference here | audit, convergence, overall owner management |
| Jev | System-1 controller | configured Jev contract | bounded route/order/escalation decisions |

OpenRouter snapshot checked 2026-09-24:
- Nemotron 3 Ultra free: 0 USD token price, 1M context.
- Ling 3.0 Flash Fin free: 0 USD token price, 262K context, tool calling.
- GLM 5.3 Flash: 0.075 USD/M input, 0.25 USD/M output.
Pricing is still checked by the runtime budget policy; a free role reporting
non-zero cost is a hard failure.

## Code

- `command-center/bcc/economy_orchestrator.py`
  - uses Bossman `OpenAICompatAdapter` + `GovernedAdapter`;
  - no stand-alone curl inference;
  - Jev can select only from explicit pre-authorized options;
  - Jev failure falls back to deterministic free-first policy;
  - GLM has pre-call reservation + post-call actual-cost guard;
  - raw YouTube teacher output stays `UNVERIFIED`;
  - no trading/exchange write surface.

- `tools/bossman_15_economy_run.py`
  - finds public videos only inside 2026-08-14..2026-08-27;
  - calls the existing `youtube_trader_ingest_auto.py`;
  - runs all three independent Nemotron roles for every ingested video;
  - records outputs through `distill_recorder.py` as `RAW_CANDIDATE`;
  - runs a free Ling review;
  - calls paid GLM only with `--allow-glm` AND a Jev paid-route decision;
  - writes one owner manifest with model/cost/Jev/video status.

- `tools/bossman_15_ling_coder.py`
  - lets Ling inspect/edit a single explicit worktree;
  - read/write/list + bounded local tests only;
  - refuses network installers, git push/fetch/reset/checkout, shutdown and broad destructive commands;
  - model DONE is never accepted as test PASS.

## YouTube source

The owner-selected exact URL must come from:
- `--channel <exact URL>`, or
- `BOSSMAN_K1MBA_YOUTUBE_URL`.

The code intentionally does not invent a channel URL. Once supplied, discovery is
bounded to uploads dated **14–27 August 2026**.

Pipeline:

```
public YouTube URL
 -> yt-dlp metadata/captions/video
 -> local ASR/vision + typed market evidence
 -> 3 independent Nemotron free roles
 -> RAW_CANDIDATE distill records
 -> outcome/holdout verification
 -> only then skill/workflow/memory promotion
```

No raw teacher claim is promoted merely because three workers agree.

## Learning boundary

What may become reusable after independent verification:
- trading skill;
- failure pattern;
- extraction workflow;
- code repair recipe;
- evidence-backed strategy hypothesis.

What stays quarantined:
- unverified teacher opinion;
- model-generated price/CVD/OI;
- future-looking/hindsight contaminated examples;
- failed/ambiguous episodes;
- holdout answers.

Weights are **not changed** by this runtime. It builds the verified dataset,
skills, workflows and memory required for later distillation/fine-tuning.

## Spend rules

Default:
- Nemotron calls: free only.
- Ling calls: free only.
- GLM budget: `BOSSMAN_15_GLM_BUDGET_USD`, default 0.25 USD per owner run.
- Codex/Claude: not worker models in the run; use them for audit/last-resort
  product fixes only.

Jev may request GLM only after free workers expose a verified blocker. The
owner-run additionally requires `--allow-glm`; either condition missing means
zero paid finalizer calls.

## Acceptance

1. unit tests for economy routing and budget guards pass;
2. free model accidentally reporting a charge -> hard FAIL;
3. Jev unavailable -> deterministic free-first fallback;
4. three distinct Nemotron role calls occur per video;
5. YouTube output remains UNVERIFIED/RAW_CANDIDATE;
6. Ling can modify only its worktree and tests decide;
7. GLM cannot exceed configured budget;
8. no live trading/client/order capability exists;
9. exact source/model/cost/evidence identities are stored;
10. Aster/Codex can reproduce the run from the owner runbook.
