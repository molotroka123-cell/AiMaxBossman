# Tomorrow Runbook — 2026-09-24

## 0. Source truth

```bash
git status --short
git worktree list
git fetch --all --prune
git rev-parse origin/main
git rev-parse origin/release/bossman-owner
```

Read this package from `origin/main`.

Implementation target: fresh `release/bossman-owner` unless the owner explicitly changes the product line.

**Do not merge stale main runtime over release.**

## 1. Protect current product

Before model downloads or code:
- record release SHA;
- record installed Bossman build SHA;
- back up config/data pointers;
- do not copy secrets into repo;
- keep rollback install.

## 2. Model Fleet

Implement truthful Models/Fleet states.

Start only with:
1. Qwen3.8-27B;
2. Xing4.0-29B-A4B;
3. Qwen3.6-35B-A3B.

Get all three through:
- install/verify;
- load;
- real chat;
- strict JSON;
- tool call;
- repo task;
- unload/restart.

Then add specialists.

Do not saturate disk/RAM downloading every candidate before the fleet UX works.

## 2A. Jev Twitch OI/CVD collector — read-only market data

Source: `https://m.twitch.tv/k1m6a`.

Goal for today: collect verified local OI/CVD observations for later research, not trading.

Execution order:
1. Open the channel in the existing Bossman browser and record the final resolved URL + LIVE/OFFLINE state.
2. Keep Jev in observer/shadow or approved low-risk browser mode; do not give it trading/order authority.
3. Because Twitch metrics may be inside video/canvas, escalate extraction to the existing LOCAL screenshot + vision/OCR path instead of inventing DOM data.
4. Calibrate OI/CVD regions on 20 manually reviewed samples. Exact visible numeric accuracy must be >=95% before unattended collection.
5. Start append-only collection at ~15 s cadence while live. Every attempt gets a timestamp and status; unreadable values are null, never copied forward.
6. Store runtime data outside Git under `%LOCALAPPDATA%\Bossman\CommandCenter\market-data\twitch\k1m6a\`.
7. Prove STOP and restart/resume: no stale screenshot may become a new row and no downtime is backfilled with the last value.
8. Run at least a one-hour collection window when the stream is live and write the owner report with sample counts, OI/CVD coverage and calibration accuracy.

Hard boundary:
- no exchange login/order placement;
- no automatic buy/sell action;
- no cloud frame egress by default;
- no fabricated numeric CVD when only a graph is visible.

Full contract and schema:
[JEV_TWITCH_OI_CVD_COLLECTOR.md](JEV_TWITCH_OI_CVD_COLLECTOR.md).

## 3. WebDesigner runtime before training

Build one tiny end-to-end project:
- brief;
- generated site;
- build;
- desktop/mobile screenshots;
- visual critique;
- one patch;
- rerender;
- Playwright;
- QA report.

Plant one visual and one functional defect to prove the gates can fail.

Only when this loop passes proceed to Dataset Factory.

## 4. OSS donor audit

Fetch exact current SHAs.

Review:
- Onlook;
- Open Design;
- Open CoDesign;
- Layout;
- Build Beautiful Sites.

For each:
`PIN | LICENSE | USE | FILES/IDEAS REUSED | SECURITY REVIEW | DECISION`.

No blind vendoring.

## 5. Dataset Factory smoke

Create:
- schema;
- render environment;
- provenance manifest;
- dedup strategy;
- 50 train smoke examples;
- 20 repair examples;
- 20 preference pairs;
- frozen 20-task micro-holdout.

Run integrity tests.

## 6. Baseline

Run identical WebDesigner tasks on:
- Qwen3.8;
- Xing4;
- Qwen3.6;
- optional strong cloud/Claude reference on the frozen subset.

Select the best trainable base from evidence.

## 7. H200 preparation

Before renting:
- choose provider;
- record exact 8xH200 topology;
- calculate max spend;
- configure auto-shutdown;
- prepare pinned container;
- sync only required model/data;
- local tiny-model/adapter smoke;
- verify checkpoint upload path.

The first paid command must not be "pip install until it works".

## 8. H200 run

Sequence:
A. environment/GPU/NVLink/NCCL test;
B. base model load;
C. 100-step LoRA smoke;
D. evaluation;
E. 5k–20k Stage-1 run;
F. evaluation;
G. only then longer SFT.

Do not touch main holdout while tuning.

## 9. Candidate integration

If trained candidate improves:
- export adapter;
- checksum;
- load locally on owner PC;
- add as non-default `WEBDESIGNER_CANDIDATE`;
- run local benchmark;
- only promote after local verified results.

If no gain:
- preserve negative result;
- diagnose data/evaluator;
- do not hide it by changing holdout.

## 10. Game Studio handoff

Do not start full Game Studio until model/web runtime work is stable.

Reuse what was built:
- role router;
- render/visual verifier;
- dataset factory;
- training/eval harness;
- evidence manifests.

Game Studio substitutes Unreal/playtest for browser/render.

## End-of-day handoff

Push:
- implementation commits;
- evidence;
- model inventory;
- benchmark result;
- dataset manifest;
- H200 run report if executed.

Final table:

`TRACK | SHA | STATUS | REAL_TEST | RESULT | COST | BLOCKERS | NEXT_ACTION`.

No "DONE" if a required live test did not run.
